import fcntl
import hashlib
import json
import os
import queue
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

sys.dont_write_bytecode = True
from websocket import MiniWebSocket
from usage import SESSION_INTERVAL, UsageMonitor, load_layout, save_layout, valid_layout


RELEASES_URL = "https://api.github.com/repos/hou3301-byte/antigravity-agent-progress/releases/latest"
RELEASE_TAG = re.compile(r"v([0-9]{1,6})\.([0-9]{1,6})\.([0-9]{1,6})")
UPDATE_INTERVAL = 2 * 60 * 60


def release_version(tag):
    match = RELEASE_TAG.fullmatch(tag) if isinstance(tag, str) else None
    return tuple(map(int, match.groups())) if match else ()


def fetch_release_tag():
    response = subprocess.run(
        ["/usr/bin/curl", "-4fsSL", "--connect-timeout", "4", "--max-time", "6", "--max-filesize",
         "524288", RELEASES_URL],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=True, timeout=8,
    )
    release = json.loads(response.stdout)
    if isinstance(release, dict) and release.get("draft") is False and release.get("prerelease") is False:
        tag = release.get("tag_name")
        if release_version(tag):
            return tag
    return None


def check_release_update(cache_path, installed):
    try:
        with cache_path.open(encoding="utf-8") as stream:
            cache = json.loads(stream.read(4096))
        if not isinstance(cache, dict):
            cache = {}
    except (OSError, ValueError):
        cache = {}

    now = time.time()
    checked = cache.get("checked_at")
    tag = cache.get("tag")
    if not isinstance(checked, (int, float)) or not 0 <= now - checked < UPDATE_INTERVAL:
        checked = now
        try:
            tag = fetch_release_tag()
        except (OSError, ValueError, subprocess.SubprocessError):
            pass
        try:
            cache_path.write_text(json.dumps({"checked_at": checked, "tag": tag}), encoding="utf-8")
        except OSError:
            pass
    return (tag if release_version(tag) > installed else None), checked + UPDATE_INTERVAL


def read_targets(port):
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(f"http://127.0.0.1:{port}/json/list", timeout=1) as response:
        targets = json.loads(response.read(2 * 1024 * 1024))
    return targets if isinstance(targets, list) else []


def page_address(target, port):
    if not isinstance(target, dict) or target.get("type") != "page":
        return None
    if not str(target.get("title", "")).lower().endswith("antigravity"):
        return None
    page = urllib.parse.urlsplit(target.get("url", ""))
    websocket = urllib.parse.urlsplit(target.get("webSocketDebuggerUrl", ""))
    if page.scheme != "https" or page.hostname not in {"127.0.0.1", "localhost"} or page.username:
        return None
    if websocket.scheme != "ws" or websocket.hostname != "127.0.0.1" or websocket.port != port:
        return None
    if not websocket.path.startswith("/devtools/page/"):
        return None
    return f"{page.scheme}://{page.netloc}", websocket.path


FENCED_CODE = re.compile(r"```[\s\S]*?```|~~~[\s\S]*?~~~")
INLINE_CODE = re.compile(r"`[^`]*`")
TODO_LINE = re.compile(r"^[ \t]*[-*][ \t]+\[([ /xX])\][ \t]+(.+?)[ \t]*$")
TODO_PROGRESS = re.compile(r"<!--\s*todo:(\d+)/(\d+)(?:\s+(blocked))?\s*-->")
TODO_DONE = re.compile(r"<!--\s*todo:done\s*-->")
MARK_TO_STATE = {" ": "pending", "/": "running", "x": "done", "X": "done"}
BACKGROUND_STARTED = re.compile(r"^Tool is running as a background task with task id: (\S+)\s*$", re.MULTILINE)
BACKGROUND_FINISHED = re.compile(r'\bsender=(\S+) priority=\S+ content=Task id "\1" finished with result:')


INLINE_CODE_SPAN = re.compile(r"`([^`\n]+)`")


def _clean_label(text):
    text = INLINE_CODE_SPAN.sub(r"\1", text)
    return " ".join(text.split())[:160]


def _extract_todo_events(no_fence):
    """Extract ordered checklist blocks and hidden todo marker events from markdown."""
    events = []
    offset = 0
    block = []
    block_start = 0

    def flush_block():
        if 2 <= len(block) <= 8:
            events.append((block_start, "list", list(block)))
        block.clear()

    for line in no_fence.splitlines(keepends=True):
        matched = TODO_LINE.match(line.rstrip("\r\n"))
        if matched:
            if not block:
                block_start = offset
            block.append((matched.group(1), _clean_label(matched.group(2))))
        else:
            flush_block()
        offset += len(line)
    flush_block()

    marker_text = INLINE_CODE.sub(lambda m: " " * len(m.group(0)), no_fence)
    for matched in TODO_PROGRESS.finditer(marker_text):
        events.append((matched.start(), "progress", (
            int(matched.group(1)), int(matched.group(2)), matched.group(3) == "blocked")))
    for matched in TODO_DONE.finditer(marker_text):
        events.append((matched.start(), "done", None))
    events.sort(key=lambda entry: entry[0])
    return events


def parse_transcript_todo(transcript_path, cache=None):
    """Parse the active turn's TodoWrite checklist and hidden progress markers from transcript.jsonl."""
    path = Path(transcript_path)
    try:
        stat = path.stat()
    except OSError:
        return None
    stamp = (stat.st_mtime_ns, stat.st_size)
    cache_key = str(path)
    if cache is not None:
        cached = cache.get(cache_key)
        if cached and cached[0] == stamp:
            return cached[1]

    entries = []
    try:
        with path.open(encoding="utf-8", errors="ignore") as stream:
            for raw_line in stream:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    entry = json.loads(raw_line)
                except ValueError:
                    continue
                if isinstance(entry, dict):
                    entries.append(entry)
    except OSError:
        return None

    last_user_idx = -1
    for idx, entry in enumerate(entries):
        if entry.get("type") == "USER_INPUT":
            last_user_idx = idx

    responses = []
    background = {}
    for entry in entries[last_user_idx + 1:]:
        content = entry.get("content")
        if not isinstance(content, str) or not content:
            continue
        kind = entry.get("type")
        if kind == "PLANNER_RESPONSE":
            responses.append(_extract_todo_events(FENCED_CODE.sub("", content)))
        elif kind == "GENERIC" and entry.get("status") == "RUNNING":
            match = BACKGROUND_STARTED.search(content)
            if match:
                try:
                    background[match[1]] = datetime.fromisoformat(entry["created_at"].replace("Z", "+00:00")).timestamp()
                except (KeyError, AttributeError, ValueError, OverflowError):
                    continue
        elif kind == "SYSTEM_MESSAGE":
            for match in BACKGROUND_FINISHED.finditer(content):
                background.pop(match[1], None)

    has_markers = any(kind in {"progress", "done"} for events in responses for _, kind, _ in events)
    items = None
    sources = {}
    done = False

    for events in responses:
        for _, kind, payload in events:
            if kind == "list":
                has_active_mark = any(mark == "/" for mark, _ in payload)
                is_same_checklist = items and [label for _, label in payload] == [item["label"] for item in items]
                completed_checklist = is_same_checklist and all(mark.lower() == "x" for mark, _ in payload)
                if has_active_mark or (items is None and has_markers) or completed_checklist:
                    items = [{"label": label, "state": MARK_TO_STATE[mark]} for mark, label in payload]
                    labels = tuple(item["label"] for item in items)
                    sources[labels] = None
                    done = bool(completed_checklist)
            elif kind == "progress" and items:
                current_step, total, is_blocked = payload
                if total != len(items) or not 1 <= current_step <= total:
                    continue
                active_idx = current_step - 1
                for idx, item in enumerate(items):
                    if idx < active_idx:
                        item["state"] = "done"
                    elif idx == active_idx:
                        item["state"] = "blocked" if is_blocked else "running"
                    elif item["state"] in {"running", "blocked"}:
                        item["state"] = "recheck"
                done = False
            elif kind == "done" and items:
                for item in items:
                    item["state"] = "done"
                done = True

    result = ({"items": items or [], "done": done, "sources": list(sources), "background_count": len(background),
               "background_start": min(background.values(), default=None)} if items or background else None)
    if cache is not None:
        cache[cache_key] = (stamp, result)
    return result


def conversation_id(raw_url):
    route = urllib.parse.urlsplit(raw_url).path
    match = re.fullmatch(r"/c/([0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12})", route)
    return match[1] if match else None


def read_progress(raw_url, brain, cache=None):
    conversation = conversation_id(raw_url)
    if not conversation:
        return None
    started_at = 0
    active = False
    outcome = None
    had_failure = False
    has_validation = False
    waiting_background = False
    running_start = None
    state_file = brain / conversation / ".system_generated/agent-progress/state.json"
    try:
        with state_file.open(encoding="utf-8") as stream:
            state = json.loads(stream.read(128 * 1024))
        if isinstance(state, dict):
            if isinstance(state.get("started_at"), (int, float)):
                started_at = state["started_at"]
            active = bool(state.get("active"))
            outcome = state.get("outcome")
            had_failure = state.get("had_failure") is True
            has_validation = state.get("has_validation") is True
            waiting_background = state.get("waiting_background") is True
            running = state.get("running")
            if active and isinstance(running, list):
                starts = [item["start"] for item in running
                          if isinstance(item, dict) and isinstance(item.get("start"), (int, float))]
                if starts:
                    running_start = starts[-1]
    except (OSError, ValueError, TypeError):
        pass

    todo = parse_transcript_todo(brain / conversation / ".system_generated/logs/transcript.jsonl", cache)
    items = todo["items"] if todo else []
    done = bool(todo["done"]) if todo else False
    background_count = todo["background_count"] if todo else 0
    if not (items or active or background_count or had_failure or has_validation or outcome in ("failed", "limit", "canceled")):
        return None
    return {
        "conversation": conversation,
        "started_at": started_at,
        "active": active,
        "outcome": outcome,
        "had_failure": had_failure,
        "has_validation": has_validation,
        "waiting_background": waiting_background,
        "running_start": running_start,
        "background_count": background_count,
        "background_start": todo["background_start"] if todo else None,
        "items": items,
        "done": done,
        "sources": todo["sources"] if todo else [],
    }


def update_page(port, address, overlay, fingerprint, progress, release_notice, usage, layout):
    origin, path = address
    client = MiniWebSocket(port, path, timeout=1)
    try:
        guard = f"location.origin === {json.dumps(origin)}"
        check = (f"{guard} && window.__observedDocument === document && Boolean(window.__agyProgress) && "
                 "Boolean(window.__agyUsage) && "
                 f"window.__antigravityZhPatchFingerprint === {json.dumps(fingerprint)}")
        result = client.call("Runtime.evaluate", {"expression": check, "returnByValue": True}, timeout=1)
        if result.get("result", {}).get("value") is not True:
            code = (f"if ({guard}) {{ window.__antigravityZhPatchRequestedFingerprint = "
                    f"{json.dumps(fingerprint)};\n{overlay}\n}}")
            client.call("Runtime.evaluate", {"expression": code}, timeout=2)
        code = (f"{guard} ? (() => {{"
                f"window.__agyProgress?.publish({json.dumps(progress)});"
                f"return {{displayed: Boolean(window.__agyProgress?.showUpdate?.({json.dumps(release_notice)})),"
                f"layout: window.__agyUsage?.publish({json.dumps(usage)}, {json.dumps(layout)})}};"
                "})() : null")
        result = client.call("Runtime.evaluate", {"expression": code, "returnByValue": True}, timeout=1)
        return result.get("result", {}).get("value") or {}
    finally:
        client.close()


def open_session(executable, resources, state):
    if subprocess.run(["/usr/bin/pgrep", "-x", "Antigravity"], stdout=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError("请先保存工作并退出正在运行的 Antigravity（⌘Q），再打开中文入口。")

    locale = json.loads((resources / "zh-CN.json").read_text(encoding="utf-8"))
    template = (resources / "overlay.js").read_text(encoding="utf-8")
    translation = template.replace("/*__AGY_LOCALE__*/", json.dumps(locale, ensure_ascii=True))
    progress = (resources / "progress-overlay.js").read_text(encoding="utf-8")
    stylesheet = (resources / "progress.css").read_text(encoding="utf-8")
    overlay = translation + "\n;\n" + progress.replace("/*__AGY_PROGRESS_CSS__*/ ''", json.dumps(stylesheet))
    usage_script = (resources / "usage-overlay.js").read_text(encoding="utf-8")
    usage_styles = (resources / "usage.css").read_text(encoding="utf-8")
    overlay += "\n;\n" + usage_script.replace("/*__AGY_USAGE_CSS__*/ ''", json.dumps(usage_styles))
    fingerprint = hashlib.sha256(overlay.encode()).hexdigest()[:16]
    brain = Path.home() / ".gemini/antigravity/brain"
    transcript_cache = {}
    try:
        installed = release_version("v" + (resources / "version.txt").read_text(encoding="ascii").strip())
    except (OSError, UnicodeError):
        installed = ()
    notified_path = state / "release-notified"
    try:
        notified = release_version(notified_path.read_text(encoding="ascii").strip())
    except (OSError, UnicodeError):
        notified = ()
    updates = queue.SimpleQueue()
    next_check = 0
    release_notice = None
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    app = subprocess.Popen(
        [str(executable), f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    usage_monitor = UsageMonitor(brain.parent, app.pid)
    usage_layout_path = state / "usage-layout.json"
    usage_layout = load_layout(usage_layout_path)
    usage_updates = queue.SimpleQueue()
    usage_snapshots = {}
    usage_pending = False
    usage_next = 0

    def refresh_usage(conversations):
        try:
            usage_updates.put(usage_monitor.refresh(conversations))
        except (OSError, ValueError, TypeError, AttributeError):
            usage_updates.put({})

    deadline = time.monotonic() + 20
    connected = False
    while app.poll() is None:
        now = time.time()
        if installed and now >= next_check:
            thread = threading.Thread(
                target=lambda: updates.put(check_release_update(state / "release-check.json", installed)),
                daemon=True,
            )
            thread.start()
            next_check = now + UPDATE_INTERVAL
        try:
            candidate, next_check = updates.get_nowait()
            release_notice = candidate if release_version(candidate) > notified else None
        except queue.Empty:
            pass
        try:
            targets = read_targets(port)
            connected = True
        except (OSError, ValueError):
            if not connected and time.monotonic() > deadline:
                raise RuntimeError("未能连接 Antigravity 的本地调试窗口。退出后可用官方图标启动。")
            targets = []
        pages = []
        for target in targets:
            try:
                address = page_address(target, port)
                if address:
                    pages.append((target["url"], address))
            except (ValueError, TypeError):
                continue
        try:
            usage_snapshots = usage_updates.get_nowait()
            usage_pending = False
        except queue.Empty:
            pass
        if pages and not usage_pending and time.monotonic() >= usage_next:
            conversations = {conversation_id(url) for url, _ in pages} - {None}
            threading.Thread(target=refresh_usage, args=(conversations,), daemon=True).start()
            usage_pending = True
            usage_next = time.monotonic() + SESSION_INTERVAL
        for url, address in pages:
            try:
                usage = usage_snapshots.get(conversation_id(url), usage_snapshots.get(None))
                change = update_page(port, address, overlay, fingerprint, read_progress(url, brain, transcript_cache),
                                     release_notice, usage, usage_layout)
                if change.get("displayed") and release_notice:
                    notified = release_version(release_notice)
                    try:
                        notified_path.write_text(release_notice + "\n", encoding="ascii")
                    except OSError:
                        pass
                    release_notice = None
                updated_layout = change.get("layout")
                if valid_layout(updated_layout) and updated_layout != usage_layout:
                    usage_layout = updated_layout
                    save_layout(usage_layout_path, usage_layout)
            except (OSError, ValueError, EOFError, RuntimeError):
                continue
        time.sleep(0.5)


def main(executable, state):
    resources = Path(__file__).parent
    if not executable.is_file() or not state.is_dir():
        raise RuntimeError("找不到 Antigravity 或安装记录。请重新安装组合包。")
    pid_file = state / "launcher.pid"
    with (state / "launcher.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        pid_file.write_text(str(os.getpid()), encoding="ascii")
        try:
            open_session(executable, resources, state)
        finally:
            pid_file.unlink(missing_ok=True)


def stop_session(_signal, _frame):
    raise SystemExit(0)


if __name__ == "__main__":
    signal.signal(signal.SIGTERM, stop_session)
    try:
        main(*map(Path, sys.argv[1:3]))
    except (OSError, RuntimeError, ValueError) as error:
        subprocess.run(["/usr/bin/osascript", "-e", "on run argv", "-e",
                        'display alert "Antigravity 中文" message (item 1 of argv) as critical',
                        "-e", "end run", "--", str(error)], check=False)
        sys.exit(1)
