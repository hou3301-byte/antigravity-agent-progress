import fcntl
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path


VALIDATION_COMMAND = re.compile(
    r"(?:^|[;&|\n])\s*(?:[\w./-]*/)?(?:"
    r"python(?:3(?:\.\d+)?)?\s+-m\s+(?:pytest|unittest)|pytest|go\s+test|cargo\s+test"
    r"|(?:npm|pnpm|yarn)\s+(?:run\s+)?test|(?:mvnw?|gradlew?)\s+(?:test|verify|check)"
    r"|node\s+--test|vitest|jest)(?=\s|$|[;&|])"
)


def write_atomic(path, content):
    """Atomically replace a file in the same directory."""
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as file:
        temp_path = Path(file.name)
        try:
            file.write(content)
            file.close()
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


def process_event(kind, event):
    """Record active turn state and currently running tool start timestamps."""
    directory = event.get("artifactDirectoryPath")
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        return
    artifacts = Path(directory).resolve()
    if not artifacts.is_dir():
        return
    if kind == "invoke" and event.get("invocationNum") != 0:
        return

    data = artifacts / ".system_generated" / "agent-progress"
    if kind in {"post", "stop"} and not data.is_dir():
        return

    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = time.time()
    with (data / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_file = data / "state.json"
        try:
            state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
        except ValueError:
            state = None
        if not isinstance(state, dict):
            state = None

        if kind == "invoke" or (kind == "pre" and not (state and state.get("active"))):
            state = {"started_at": timestamp, "active": True, "running": []}
        elif not state or not state.get("active"):
            return

        if kind == "stop":
            reason = event.get("terminationReason")
            if event.get("error") or reason == "error":
                state["outcome"] = "failed"
            elif reason == "max_steps_exceeded":
                state["outcome"] = "limit"
            elif reason in ("cancel", "canceled", "cancelled", "user_cancelled", "user_canceled", "interrupted"):
                state["outcome"] = "canceled"
            else:
                state["outcome"] = "ended"
            state["running"] = []
            if event.get("fullyIdle") is False:
                state["waiting_background"] = True
            else:
                state["active"] = False
                state.pop("waiting_background", None)
        elif kind in {"pre", "post"}:
            step = event.get("stepIdx")
            if not isinstance(step, int):
                return
            running = state.get("running")
            if not isinstance(running, list):
                running = []
            if kind == "pre":
                state.pop("waiting_background", None)
                state.pop("outcome", None)
                if not any(isinstance(item, dict) and item.get("step") == step for item in running):
                    running.append({"step": step, "start": timestamp})
                state["running"] = running
            else:
                state["running"] = [item for item in running if isinstance(item, dict) and item.get("step") != step]
                if event.get("error"):
                    state["had_failure"] = True
                tool = event.get("toolCall") or {}
                if isinstance(tool, dict) and tool.get("name") == "run_command":
                    args = tool.get("args")
                    command = args.get("CommandLine") if isinstance(args, dict) else None
                    if isinstance(command, str) and VALIDATION_COMMAND.search(command):
                        state["has_validation"] = True

        write_atomic(state_file, json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    kind = sys.argv[1]
    try:
        process_event(kind, json.load(sys.stdin))
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(f"agent-progress: {type(error).__name__}", file=sys.stderr)
    print(json.dumps({"decision": "allow"} if kind == "pre" else {}))
