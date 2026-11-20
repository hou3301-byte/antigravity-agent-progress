import fcntl
import hashlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

from websocket import MiniWebSocket


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


def update_page(port, address, overlay, fingerprint):
    origin, path = address
    client = MiniWebSocket(port, path, timeout=1)
    try:
        guard = f"location.origin === {json.dumps(origin)}"
        check = (f"{guard} && window.__observedDocument === document && "
                 f"window.__antigravityZhPatchFingerprint === {json.dumps(fingerprint)}")
        result = client.call("Runtime.evaluate", {"expression": check, "returnByValue": True}, timeout=1)
        if result.get("result", {}).get("value") is True:
            return
        code = (f"if ({guard}) {{ window.__antigravityZhPatchRequestedFingerprint = "
                f"{json.dumps(fingerprint)};\n{overlay}\n}}")
        client.call("Runtime.evaluate", {"expression": code}, timeout=2)
    finally:
        client.close()


def open_session(executable, resources):
    if subprocess.run(["/usr/bin/pgrep", "-x", "Antigravity"], stdout=subprocess.DEVNULL).returncode == 0:
        raise RuntimeError("请先保存工作并退出正在运行的 Antigravity（⌘Q），再打开中文入口。")

    locale = json.loads((resources / "zh-CN.json").read_text(encoding="utf-8"))
    template = (resources / "overlay.js").read_text(encoding="utf-8")
    overlay = template.replace("/*__AGY_LOCALE__*/", json.dumps(locale, ensure_ascii=True))
    fingerprint = hashlib.sha256(overlay.encode()).hexdigest()[:16]
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]

    app = subprocess.Popen(
        [str(executable), f"--remote-debugging-port={port}", "--remote-debugging-address=127.0.0.1"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    deadline = time.monotonic() + 20
    connected = False
    while app.poll() is None:
        try:
            targets = read_targets(port)
            connected = True
        except (OSError, ValueError):
            if not connected and time.monotonic() > deadline:
                raise RuntimeError("未能连接 Antigravity 的本地调试窗口。退出后可用官方图标启动。")
            targets = []
        for target in targets:
            try:
                address = page_address(target, port)
                if address:
                    update_page(port, address, overlay, fingerprint)
            except (OSError, ValueError, EOFError, RuntimeError):
                continue
        time.sleep(1)


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
            open_session(executable, resources)
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
