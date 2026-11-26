import http.client
import json
import os
import re
import subprocess
import tempfile
import time
from datetime import datetime


QUOTA_INTERVAL = 30
SESSION_INTERVAL = 3
RATE_WINDOW = 15 * 60
RPC_LIMIT = 8 * 1024 * 1024
TRANSCRIPT_LIMIT = 24 * 1024 * 1024
POOL_LABELS = {"gemini": "Gemini", "argon": "Gemini 4 Argon", "carbon": "Gemini 4 Carbon",
               "third-party": "Claude / GPT"}
METRICS = ("speed", "ttft", "average_speed", "tokens", "cache", "remaining")
SECTIONS = ("quota", "metrics")
DEFAULT_LAYOUT = {"metrics": list(METRICS), "hidden": ["average_speed", "cache"], "sections": list(SECTIONS)}


def valid_layout(value):
    if not isinstance(value, dict) or set(value) != DEFAULT_LAYOUT.keys():
        return False
    for key, available in (("metrics", METRICS), ("hidden", METRICS), ("sections", SECTIONS)):
        items = value[key]
        if not isinstance(items, list) or len(items) > len(available) or any(item not in available for item in items):
            return False
        if len(set(items)) != len(items) or (key != "hidden" and len(items) != len(available)):
            return False
    return True


def load_layout(path):
    try:
        with path.open(encoding="utf-8") as stream:
            value = json.loads(stream.read(2048))
        if valid_layout(value):
            return value
    except (OSError, ValueError):
        pass
    return DEFAULT_LAYOUT


def save_layout(path, layout):
    with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
        staged = stream.name
        try:
            json.dump(layout, stream)
            stream.close()
            os.replace(staged, path)
        finally:
            if os.path.exists(staged):
                os.unlink(staged)


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        value = re.sub(r"\.(\d+)", lambda match: "." + match[1][:6].ljust(6, "0"), value, count=1)
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return parsed.timestamp() if parsed.tzinfo is not None else None
    except (ValueError, OverflowError):
        return None


def number(value):
    if isinstance(value, str) and len(value) < 40 and re.fullmatch(r"\d+(?:\.\d+)?", value):
        value = float(value) if "." in value else int(value)
    if type(value) in (int, float) and 0 <= value < 1e15:
        return value
    return None


def pool_key(*names):
    words = set(re.findall(r"[a-z0-9]+", " ".join(str(name or "") for name in names).lower()))
    for match, key in (({"argon"}, "argon"), ({"carbon"}, "carbon"),
                       ({"3p", "claude", "gpt"}, "third-party"), ({"gemini"}, "gemini")):
        if words & match:
            return key
    return None


def quota_buckets(payload, now):
    payload = payload.get("response", payload)
    if not isinstance(payload, dict):
        return []
    groups = payload.get("groups") or [{"buckets": payload.get("buckets", [])}]
    result = {}
    for group in groups:
        name = group.get("displayName") or ""
        for bucket in group.get("buckets") or []:
            bucket_id = bucket.get("bucketId") or ""
            label = bucket.get("displayName") or ""
            pool = pool_key(name, bucket_id, label) or name or bucket_id or label
            if not pool:
                continue
            window = bucket.get("window") or ""
            hint = f"{window} {bucket_id} {label}".lower()
            if re.search(r"\b5h\b|five[ -]hour", hint):
                window = "5h"
            elif re.search(r"\bweek(?:ly)?\b|\b7d\b", hint):
                window = "weekly"
            reset = timestamp(bucket.get("resetTime"))
            fraction = number(bucket.get("remainingFraction"))
            if "remainingFraction" not in bucket and reset and reset > now:
                fraction = 0  # Proto3 may omit the default zero value.
            if fraction is None or fraction > 1:
                continue
            key = (pool, window, bucket_id or label)
            result[key] = {"id": "/".join(key), "pool": pool, "label": POOL_LABELS.get(pool, name or pool),
                           "window": window or label, "remaining": fraction * 100, "reset": reset}
    pool_order = list(POOL_LABELS)
    return sorted(result.values(), key=lambda b: (pool_order.index(b["pool"]) if b["pool"] in pool_order else len(pool_order),
                                                  b["label"], {"5h": 0, "weekly": 1}.get(b["window"], 2)))


def quota_rates(buckets, history, now):
    next_history = {}
    for bucket in buckets:
        key, reset, remaining = bucket["id"], bucket["reset"], bucket["remaining"]
        if not reset or reset <= now:
            continue
        samples = [s for s in history.get(key, []) if s[0] >= now - RATE_WINDOW]
        if samples and (samples[-1][1] != reset or remaining > samples[-1][2] + 1e-6):
            samples = []
        samples.append((now, reset, remaining))
        next_history[key] = samples
        elapsed = now - samples[0][0]
        spent = samples[0][2] - remaining
        if len(samples) >= 3 and elapsed >= 60 and spent > 1e-6:
            bucket["rate"] = spent * 3600 / elapsed
    return next_history


def quota_snapshot(buckets, fetched, stale, session, now):
    result = []
    for bucket in buckets:
        item = dict(bucket)
        expired = item["reset"] is not None and item["reset"] <= now
        item["stale"] = stale or expired
        if item["stale"]:
            item.pop("rate", None)
        if (session and not item["stale"] and item.get("rate") and session.get("generating_since")
                and session.get("pool") == item["pool"] and 0 <= now - fetched <= QUOTA_INTERVAL * 2):
            pending_seconds = max(0, now - max(fetched, session["generating_since"]))
            estimate = min(item["remaining"], item["rate"] * pending_seconds / 3600)
            if estimate >= .005:
                item["estimate"] = estimate
        result.append(item)
    return result


def transcript_totals(path, cached):
    try:
        stat = path.stat()
        stamp = (stat.st_mtime_ns, stat.st_size)
        if cached[0] == stamp:
            return cached
        if stat.st_size > TRANSCRIPT_LIMIT:
            return stamp, {}
        totals = {"input": 0, "cache_read": 0, "output": 0}
        seen = set()
        with path.open(encoding="utf-8", errors="replace") as stream:
            for line in stream:
                try:
                    entry = json.loads(line)
                except ValueError:
                    continue
                if not isinstance(entry, dict) or entry.get("type") not in ("PLANNER_RESPONSE", "CHECKPOINT"):
                    continue
                values = [number(entry.get(k)) for k in ("input_tokens", "cache_read_tokens", "output_tokens")]
                if not any(value is not None for value in values):
                    continue
                index = entry.get("step_index")
                if type(index) is int:
                    if index in seen:
                        continue
                    seen.add(index)
                for key, value in zip(totals, values):
                    totals[key] += value or 0
        return stamp, totals if sum(totals.values()) else {}
    except OSError:
        return None, {}


def step_metrics(steps, offset, generators, running, models, pools):
    recent = []
    latest_model = None
    started = None
    active_pool = None
    for index, step in enumerate(steps, offset):
        if step.get("type") not in ("CORTEX_STEP_TYPE_PLANNER_RESPONSE", "PLANNER_RESPONSE"):
            continue
        meta = step.get("metadata") or {}
        usage = meta.get("modelUsage") or {}
        status = step.get("status") or ""
        chat = generators.get(index, {})
        model = usage.get("model") or meta.get("generatorModel") or chat.get("model") or ""
        model = models.get(model, chat.get("modelDisplayName") or model)
        if model and not model.startswith("MODEL_"):
            latest_model = model
        start = timestamp(meta.get("createdAt"))
        finish = timestamp(meta.get("finishedGeneratingAt"))
        if running and start and not finish and status.rsplit("_", 1)[-1] in ("RUNNING", "GENERATING"):
            started = start
            active_pool = pool_key(model)
        else:
            started = None
            active_pool = None
        count = number(usage.get("outputTokens"))
        if status not in ("DONE", "CORTEX_STEP_STATUS_DONE") or not count or not start or not finish:
            continue
        elapsed = finish - start
        if not .25 <= elapsed <= 600:
            continue
        value = chat.get("timeToFirstToken")
        ttft = None
        if isinstance(value, str) and re.fullmatch(r"\d+(?:\.\d+)?s", value):
            seconds = number(float(value[:-1]))
            if seconds and seconds <= elapsed + .05:
                ttft = seconds
        recent.append((count, elapsed, ttft))
    if active_pool in ("argon", "carbon") and active_pool not in pools:
        active_pool = "gemini"
    latest = recent[-1] if recent else None
    return {"model": latest_model, "running": running, "generating_since": started, "pool": active_pool,
            "speed": latest[0] / latest[1] if latest else None, "ttft": latest[2] if latest else None,
            "average_speed": (sum(r[0] for r in recent) / sum(r[1] for r in recent)) if recent else None}


def desktop_endpoint(directory, app_pid):
    parents = {}
    processes = subprocess.run(["/bin/ps", "-axo", "pid=,ppid="], capture_output=True, text=True,
                               check=True, timeout=2).stdout
    for row in processes.splitlines():
        pid, parent = map(int, row.split())
        parents[pid] = parent
    candidates = []
    for path in directory.glob("hub_*.json"):
        try:
            file_stat = path.stat()
            if file_stat.st_size > 4096 or path.is_symlink() or file_stat.st_uid != os.getuid():
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
            pid = data.get("pid")
            port, token = data.get("httpPort"), data.get("csrfToken")
            if type(pid) is not int or type(port) is not int or not 1 <= port <= 65535:
                continue
            if not isinstance(token, str) or not re.fullmatch(r"[!-~]{1,512}", token):
                continue
            parent = pid
            while parent in parents and parent > 1 and parent != app_pid:
                parent = parents[parent]
            if parent == app_pid:
                candidates.append((file_stat.st_mtime_ns, pid, port, token))
        except (OSError, ValueError, AttributeError):
            continue
    return max(candidates)[1:] if candidates else None


def rpc(endpoint, method, body):
    _, port, token = endpoint
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        conn.request("POST", f"/exa.language_server_pb.LanguageServerService/{method}", json.dumps(body), {
            "Content-Type": "application/json", "Connect-Protocol-Version": "1", "X-Codeium-Csrf-Token": token,
        })
        response = conn.getresponse()
        if response.status != 200:
            raise ValueError(f"{method}: HTTP {response.status}")
        raw = response.read(RPC_LIMIT + 1)
        if len(raw) > RPC_LIMIT:
            raise ValueError(f"{method}: response too large")
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f"{method}: invalid response")
        return data
    finally:
        conn.close()


class UsageMonitor:
    def __init__(self, root, app_pid):
        self.root = root
        self.app_pid = app_pid
        self.endpoint = None
        self.next_discovery = 0
        self.clear()

    def clear(self):
        self.account = None
        self.plan = None
        self.models = {}
        self.buckets = []
        self.history = {}
        self.fetched = 0
        self.stale = False
        self.next_quota = 0
        self.sessions = {}

    def refresh_quotas(self, now):
        def user_status():
            user = rpc(self.endpoint, "GetUserStatus", {}).get("userStatus") or {}
            return user, user.get("email") or user.get("name")

        try:
            _, before = user_status()
        except (OSError, ValueError, http.client.HTTPException):
            before = None
        if not before or before != self.account:
            self.clear()
            self.account = before
        self.next_quota = now + QUOTA_INTERVAL
        if not before:
            return
        try:
            official = rpc(self.endpoint, "RetrieveUserQuotaSummary", {"forceRefresh": True})
        except (OSError, ValueError, http.client.HTTPException):
            official = {}
        try:
            user, after = user_status()
        except (OSError, ValueError, http.client.HTTPException):
            after = None
        if after != before:
            self.clear()
            self.next_quota = now + SESSION_INTERVAL
            return
        self.plan = ((user.get("planStatus") or {}).get("planInfo") or {}).get("planName")
        configs = (user.get("cascadeModelConfigData") or {}).get("clientModelConfigs") or []
        self.models = {key: model["label"] for model in configs if model.get("label")
                       for key in (model.get("modelId"), (model.get("modelOrAlias") or {}).get("model")) if key}
        buckets = quota_buckets(official, now)
        if buckets:
            self.history = quota_rates(buckets, self.history, now)
            self.buckets = buckets
            self.fetched = now
        self.stale = not buckets

    def refresh_session(self, conversation, summary):
        state = self.sessions.setdefault(conversation, {})
        path = self.root / "brain" / conversation / ".system_generated/logs/transcript.jsonl"
        state["totals"] = transcript_totals(path, state.get("totals", (None, {})))
        running = summary.get("status") == "CASCADE_RUN_STATUS_RUNNING"
        count = summary.get("stepCount", 0)
        stamp = (summary.get("trajectoryId"), summary.get("lastModifiedTime"), count, running)
        if not summary:
            state.pop("metrics", None)
            state.pop("stamp", None)
        if not summary or (not running and state.get("stamp") == stamp):
            return
        offset = max(0, count - 48)
        usage = {"running": running}
        try:
            steps = rpc(self.endpoint, "GetCascadeTrajectorySteps", {
                "cascadeId": conversation, "stepOffset": offset, "disableRehydration": True,
                "trajectoryVerbosity": "CLIENT_TRAJECTORY_VERBOSITY_PROD_UI",
            }).get("steps", [])
            previous = state.get("stamp")
            same_trajectory = previous and previous[0] == stamp[0] and previous[2] <= count
            cached = {k: v for k, v in state.get("generators", {}).items() if k >= offset} if same_trajectory else {}
            generator_offset = max(0, state.get("generator_count", 0) - 3) if same_trajectory else 0
            try:
                generators = rpc(self.endpoint, "GetCascadeTrajectoryGeneratorMetadata", {
                    "cascadeId": conversation, "generatorMetadataOffset": generator_offset,
                }).get("generatorMetadata", [])
                for generator in generators:
                    chat = generator.get("chatModel") or {}
                    if not chat:
                        continue
                    for index in generator.get("stepIndices") or []:
                        if index >= offset:
                            cached[index] = {key: chat[key] for key in ("model", "modelDisplayName", "timeToFirstToken")
                                             if key in chat}
                state.update(generators=cached, generator_count=generator_offset + len(generators))
                state["stamp"] = stamp
            except (OSError, ValueError, http.client.HTTPException):
                pass
            usage = step_metrics(steps, offset, cached, running, self.models, {b["pool"] for b in self.buckets})
        except (OSError, ValueError, http.client.HTTPException):
            state.pop("stamp", None)
        state["metrics"] = usage

    def refresh(self, conversations):
        now = time.time()
        if now >= self.next_discovery:
            self.next_discovery = now + (15 if self.endpoint else SESSION_INTERVAL)
            try:
                endpoint = desktop_endpoint(self.root / "daemon", self.app_pid)
            except (OSError, ValueError, subprocess.SubprocessError):
                endpoint = self.endpoint
            if endpoint != self.endpoint:
                self.clear()
                self.endpoint = endpoint
        if self.endpoint and now >= self.next_quota:
            self.refresh_quotas(now)
        summaries = {}
        if self.endpoint and conversations:
            try:
                summaries = rpc(self.endpoint, "GetAllCascadeTrajectories", {
                    "excludeSubtrajectories": True,
                }).get("trajectorySummaries") or {}
            except (OSError, ValueError, http.client.HTTPException):
                self.next_discovery = 0
        self.sessions = {key: session for key, session in self.sessions.items() if key in conversations}
        for conversation in conversations:
            self.refresh_session(conversation, summaries.get(conversation, {}))
        snapshots = {}
        for conversation in (None, *conversations):
            state = self.sessions.get(conversation, {})
            session = {**state.get("metrics", {}), **state.get("totals", (None, {}))[1]}
            snapshots[conversation] = {"conversation": conversation, "plan": self.plan, "fetched": self.fetched,
                                       "stale": self.stale, "session": session,
                                       "buckets": quota_snapshot(self.buckets, self.fetched, self.stale, session, now)}
        return snapshots
