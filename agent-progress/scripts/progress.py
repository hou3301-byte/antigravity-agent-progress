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
TASK_ICONS = {"pending": "○", "running": "◉", "done": "✓", "blocked": "!", "paused": "Ⅱ"}
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
        status = "paused" if item["state"] == "running" and not state["active"] else item["state"]
        label = TASK_STATES.get(status, "已暂停")
        tasks.append(f'<li><span class="agy-task-label"><span class="agy-task-icon" data-state="{status}" '
                     f'aria-hidden="true">{TASK_ICONS[status]}</span><span>{html.escape(item["label"])}</span></span>'
                     f'<span class="agy-progress-status" data-state="{status}">{label}</span></li>')
    completed = sum(item["state"] == "done" for item in items)

    def tool_status(tool):
        status, start, end = tool["state"], tool.get("start"), tool.get("end")
        duration = f"{max(0, end - start):.1f} 秒" if start is not None and end is not None else ""
        clock = f' data-since="{start}"' if start is not None and status == "running" else ""
        return (f'<span class="agy-progress-status" data-state="{status}">'
                f'{TOOL_STATES[status]} <time{clock}>{duration}</time></span>')

    activity = ""
    if tools:
        running = [tool for tool in tools if tool["state"] == "running"]
        latest = (running or tools)[-1]
        extra = f'<span class="agy-progress-status" data-state="running">+{len(running) - 1}</span>' if len(running) > 1 else ""
        rows = "".join(f'<li><code>{html.escape(tool["name"])}</code>{tool_status(tool)}</li>' for tool in tools[-8:])
        activity = (f'<details class="agy-tool-activity"><summary><span class="agy-tool-label">工具活动</span>'
                    f'<span class="agy-tool-recent"><code>{html.escape(latest["name"])}</code>{tool_status(latest)}{extra}</span>'
                    f'</summary><ul class="agy-tool-list" aria-label="最近工具调用">{rows}</ul></details>')
    stylesheet = Path(__file__).resolve().parents[1] / "assets/progress.css"
    return f'''<!doctype html>
<html lang="zh-CN">
<meta charset="utf-8">
<meta name="color-scheme" content="light dark">
<title>执行进度</title>
<style>
body {{ margin: 0; background: transparent; font: 14px system-ui, sans-serif; }}
{stylesheet.read_text(encoding="utf-8")}
</style>
<main class="agy-progress-panel" data-agent-progress-card="{state.get('run') or ''}">
  <section class="agy-progress-card" aria-label="执行进度">
    <header><strong>执行进度</strong><span class="agy-progress-status">{completed} / {len(items)} 项完成</span></header>
    <progress class="agy-progress-meter" aria-label="任务完成度" value="{completed}" max="{len(items)}"></progress>
    <ul>{''.join(tasks)}</ul>
  </section>
  {activity}
</main>
<script>
const clocks = document.querySelectorAll('time[data-since]');
function tick() {{
  for (const clock of clocks) {{
    const elapsed = Math.max(0, Date.now() / 1000 - Number(clock.dataset.since));
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
    if not artifacts.is_dir():
        return
    tool = event.get("toolCall") or {}
    args = tool.get("args") or {}
    target = None
    if tool.get("name") in {"write_file", "write_to_file"}:
        target = args.get("Path") or args.get("TargetFile")
    snapshot = Path(target) if isinstance(target, str) else None
    match = CARD_NAME.fullmatch(snapshot.name) if snapshot and snapshot.parent == artifacts else None
    if match and (kind != "post" or event.get("error") or snapshot.is_symlink()):
        return

    if kind == "invoke" and event.get("invocationNum") != 0:
        return
    data = artifacts / ".system_generated" / "agent-progress"
    if match:
        source = args.get("Content") or args.get("CodeContent")
        if not isinstance(source, str):
            source = snapshot.read_text(encoding="utf-8")
        items = read_items(source)
        if items is None:
            return
    elif kind in {"post", "stop"} and not data.is_dir():
        return

    data.mkdir(parents=True, exist_ok=True, mode=0o700)
    timestamp = time.time()
    with (data / "lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state_file = data / "state.json"
        state = json.loads(state_file.read_text(encoding="utf-8")) if state_file.exists() else None
        if kind == "invoke" or ((match or kind == "pre") and not (state and state["active"])):
            state = {"run": None, "started_at": timestamp, "items": [], "tools": [], "active": True}
        if match:
            run = match[1]
            if state["run"] not in (None, run):
                return
            state.update(run=run, items=items)
        elif kind == "invoke":
            pass
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

        if state["run"]:
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
