import fcntl
import json
import os
import sys
import tempfile
import time
from pathlib import Path


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
            if event.get("fullyIdle") is False:
                return
            state["active"] = False
            state["running"] = []
        elif kind in {"pre", "post"}:
            step = event.get("stepIdx")
            if not isinstance(step, int):
                return
            running = state.get("running")
            if not isinstance(running, list):
                running = []
            if kind == "pre":
                if not any(isinstance(item, dict) and item.get("step") == step for item in running):
                    running.append({"step": step, "start": timestamp})
                state["running"] = running
            else:
                state["running"] = [item for item in running if isinstance(item, dict) and item.get("step") != step]

        write_atomic(state_file, json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    kind = sys.argv[1]
    try:
        process_event(kind, json.load(sys.stdin))
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(f"agent-progress: {type(error).__name__}", file=sys.stderr)
    print(json.dumps({"decision": "allow"} if kind == "pre" else {}))
