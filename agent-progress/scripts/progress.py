import fcntl
import html
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path


CARD_NAME = re.compile(r"agent-progress-([a-z0-9]{3,20})\.html\Z")
INPUT = re.compile(r'<script type="application/json" id="agent-progress-input">(.*?)</script>', re.S)
TASK_STATES = {"pending": "待办", "running": "进行中", "done": "完成", "blocked": "阻塞"}
TOOL_STATES = {"running": "调用中", "done": "已返回", "error": "出错", "interrupted": "未收到结束事件"}


def write_atomic(path, content):
    with tempfile.NamedTemporaryFile(dir=path.parent, mode="w", encoding="utf-8", delete=False) as file:
        temp_path = Path(file.name)
        try:
            file.write(content)
            file.close()
            os.replace(temp_path, path)
        finally:
            if temp_path.exists():
                temp_path.unlink()


def read_items(source):
    match = INPUT.search(source)
    if not match:
        return None
    items = json.loads(match.group(1))["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 8:
        return None
    for item in items:
        if not isinstance(item, dict):
            return None
        if not isinstance(item.get("label"), str) or item.get("state") not in TASK_STATES:
            return None
    return [{"label": item["label"].strip()[:160], "state": item["state"]} for item in items]


def render(state):
    items, tools = state["items"], state["tools"]
    tasks = []
    for item in items:
        status = item["state"]
        tasks.append(f'<li><span>{html.escape(item["label"])}</span>'
                     f'<span class="{status}">{TASK_STATES[status]}</span></li>')
    completed = sum(item["state"] == "done" for item in items)
    rows = []
    for tool in tools[-5:]:
        status, start, end = tool["state"], tool.get("start"), tool.get("end")
        duration = f"{max(0, end - start):.1f} 秒" if start is not None and end is not None else ""
        clock = f' data-start="{start}"' if start is not None and status == "running" else ""
        rows.append(f'<li><code>{html.escape(tool["name"])}</code><span class="{status}">'
                    f'{TOOL_STATES[status]} <time{clock}>{duration}</time></span></li>')
    tool_list = f'<h3>工具调用</h3><ul>{"".join(rows)}</ul>' if rows else ""
    return f'''<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<title>执行进度</title>
<style>
body {{ margin: 0; color: var(--foreground, CanvasText); background: transparent; font: 14px system-ui, sans-serif; }}
main {{ padding: 6px 0; }}
h2, h3, ul {{ margin: 0; }}
h2 {{ font-size: 14px; font-weight: 650; }}
h3 {{ font-size: 12px; padding: 8px 0 3px; font-weight: 650; }}
ul {{ padding: 0; list-style: none; }}
header, li {{ display: flex; align-items: baseline; justify-content: space-between; gap: 14px; }}
header {{ padding: 0 0 7px; border-bottom: 1px solid var(--card-border, GrayText); }}
li {{ padding: 4px 0; }}
li > :first-child {{ overflow-wrap: anywhere; min-width: 0; }}
li > :last-child {{ flex: none; }}
header span, li > :last-child {{ color: var(--muted-foreground, GrayText); font-size: 12px; font-variant-numeric: tabular-nums; }}
li > .running {{ color: var(--primary, LinkText); }}
li > .done {{ color: var(--success, CanvasText); }}
li > .blocked, li > .error {{ color: var(--error, CanvasText); }}
code {{ font-size: 12px; }}
</style>
<main>
  <header><h2>执行进度</h2><span>{completed} / {len(items)} 项完成</span></header>
  <ul>{''.join(tasks)}</ul>
  {tool_list}
</main>
<script>
const clocks = document.querySelectorAll('time[data-start]');
function tick() {{
  for (const clock of clocks) {{
    const elapsed = Math.max(0, Date.now() / 1000 - Number(clock.dataset.start));
    clock.textContent = Math.floor(elapsed) + ' 秒';
  }}
}}
tick();
if (clocks.length) setInterval(tick, 500);
</script>
</html>'''


def process_event(kind, event):
    directory = event.get("artifactDirectoryPath")
    if not isinstance(directory, str) or not Path(directory).is_absolute():
        return
    artifacts = Path(directory).resolve()
    tool = event.get("toolCall") or {}
    args = tool.get("args") or {}
    target = None
    if tool.get("name") in {"write_file", "write_to_file"}:
        target = args.get("Path") or args.get("TargetFile")
    snapshot = Path(target) if isinstance(target, str) else None
    match = CARD_NAME.fullmatch(snapshot.name) if snapshot and snapshot.parent == artifacts else None
    if match and (kind != "post" or event.get("error") or snapshot.is_symlink()):
        return

    data = artifacts / ".system_generated" / "agent-progress"
    if match:
        source = args.get("Content") or args.get("CodeContent")
        if not isinstance(source, str):
            source = snapshot.read_text(encoding="utf-8")
        items = read_items(source)
        if items is None:
            return
        data.mkdir(parents=True, exist_ok=True, mode=0o700)
    elif not data.is_dir():
        return

    timestamp = time.time()
    with (data / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_file = data / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
        if match:
            run = match[1]
            tools = state["tools"] if state and state["run"] == run else []
            state = {"run": run, "items": items, "tools": tools, "active": True}
        elif not state or not state["active"]:
            return
        elif kind == "stop":
            if event.get("fullyIdle") is False:
                return
            state["active"] = False
            for entry in state["tools"]:
                if entry["state"] == "running":
                    entry["state"] = "interrupted"
        else:
            name, step = tool.get("name"), event.get("stepIdx")
            if not isinstance(name, str) or not isinstance(step, int):
                return
            calls = state["tools"]
            entry = next((item for item in reversed(calls) if item["step"] == step), None)
            if kind == "pre":
                if entry:
                    return
                calls.append({"step": step, "name": name[:100], "state": "running", "start": timestamp})
            elif entry is None:
                calls.append({"step": step, "name": name[:100], "state": "error" if event.get("error") else "done"})
            elif entry["state"] == "running":
                entry.update(state="error" if event.get("error") else "done", end=timestamp)
            state["tools"] = [item for item in calls[:-20] if item["state"] == "running"] + calls[-20:]

        output = artifacts / f'agent-progress-{state["run"]}.html'
        if output.is_file() and not output.is_symlink():
            write_atomic(output, render(state))
            write_atomic(state_file, json.dumps(state, ensure_ascii=False))


if __name__ == "__main__":
    kind = sys.argv[1]
    try:
        process_event(kind, json.load(sys.stdin))
    except (OSError, KeyError, TypeError, ValueError) as error:
        print(f"agent-progress: {type(error).__name__}", file=sys.stderr)
    print(json.dumps({"decision": "allow"} if kind == "pre" else {}))
