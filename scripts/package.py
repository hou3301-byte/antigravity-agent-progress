import hashlib
import json
import os
import plistlib
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path


APP_NAME = "Antigravity 中文.app"
LAUNCHER_ID = "io.github.hou3301-byte.antigravity-agent-progress.zh"
PROGRESS = "agent-progress"
CHINESE = "antigravity-zh-prompt"


def fingerprint(root):
    digest = hashlib.sha256()
    for entry in sorted(root.rglob("*")):
        if entry.is_symlink():
            return None
        if entry.is_file():
            digest.update(str(entry.relative_to(root)).encode() + b"\0")
            digest.update(hashlib.sha256(entry.read_bytes()).digest())
    return digest.hexdigest()


def is_owned_launcher(app):
    try:
        with (app / "Contents" / "Info.plist").open("rb") as file:
            return not app.is_symlink() and plistlib.load(file).get("CFBundleIdentifier") == LAUNCHER_ID
    except (OSError, ValueError, plistlib.InvalidFileException):
        return False


def write_launcher(app, original, state):
    contents = app / "Contents"
    macos, resources = contents / "MacOS", contents / "Resources"
    macos.mkdir(parents=True)
    resources.mkdir()
    shim = macos / "launcher"
    command = ["/usr/bin/python3", str(state / "runtime" / "launcher.py"),
               str(original / "Contents" / "MacOS" / "Antigravity"), str(state)]
    shim.write_text("#!/bin/bash\nexec " + " ".join(map(shlex.quote, command)) + "\n", encoding="utf-8")
    shim.chmod(0o755)
    plist = {"CFBundleIdentifier": LAUNCHER_ID, "CFBundleExecutable": "launcher",
             "CFBundleName": "Antigravity 中文", "CFBundleDisplayName": "Antigravity 中文",
             "CFBundlePackageType": "APPL", "CFBundleVersion": "1", "LSUIElement": True}
    icon = original / "Contents" / "Resources" / "icon.icns"
    if icon.is_file():
        shutil.copyfile(icon, resources / "icon.icns")
        plist["CFBundleIconFile"] = "icon.icns"
    with (contents / "Info.plist").open("wb") as file:
        plistlib.dump(plist, file)


def install(bundle, destination, state, home, include_chinese, include_launcher, system_apps=Path("/Applications")):
    record = state / "install.json"
    tracked = json.loads(record.read_text(encoding="utf-8")) if record.is_file() else {"plugins": {}, "launcher": False}
    apps = home / "Applications"
    app = apps / APP_NAME
    launcher_exists = any(x.exists() or x.is_symlink() for x in (system_apps / APP_NAME, app))
    original = None
    if include_launcher and not launcher_exists:
        original = next((x / "Antigravity.app" for x in (system_apps, apps)
                         if (x / "Antigravity.app/Contents/MacOS/Antigravity").is_file()), None)
        if original is None:
            raise RuntimeError("找不到官方 Antigravity.app，请先安装桌面版，或用 --plugins-only 只装插件。")

    destination.mkdir(parents=True, exist_ok=True)
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with tempfile.TemporaryDirectory(prefix=".staging-", dir=state) as temp:
        stage = Path(temp)
        sources = [bundle / PROGRESS]
        if include_chinese:
            sources.append(bundle / "vendor" / CHINESE)
        publisher = json.loads((bundle / PROGRESS / "plugin.json").read_text(encoding="utf-8"))
        pending = {}
        replacements = {}
        for source in sources:
            staged = stage / source.name
            shutil.copytree(source, staged)
            manifest_path = staged / "plugin.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            if manifest["name"] != source.name:
                raise RuntimeError(f"插件安装包有误 {source.name}")
            if source.name == CHINESE:
                manifest.pop("disabled", None)
                for field in ("author", "version", "homepage", "repository"):
                    manifest[field] = publisher[field]
                manifest["displayName"] = "简体中文规则"
                manifest["description"] = "让 Antigravity 使用简体中文规划、汇报和回复。规则源自 guuguo，由本组合包维护。"
                manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
            target = destination / source.name
            expected = fingerprint(staged)
            if target.exists() or target.is_symlink():
                current = fingerprint(target) if target.is_dir() and not target.is_symlink() else None
                if current == expected:
                    tracked["plugins"][source.name] = expected
                    print(f"保留已安装的插件 {source.name}")
                    continue
                if current and current == tracked["plugins"].get(source.name):
                    replacements[target] = current
                elif source.name == CHINESE:
                    print(f"保留独立安装或有本地修改的插件 {source.name}")
                    continue
                else:
                    raise RuntimeError(f"已有未登记或有本地修改的进度插件，请先备份并移走 {target}")
            pending[target] = staged
            tracked["plugins"][source.name] = expected

        if original:
            apps.mkdir(parents=True, exist_ok=True)
            installed_runtime = state / "runtime"
            if not tracked["launcher"] or not (installed_runtime / "launcher.py").is_file():
                runtime = stage / "runtime"
                shutil.copytree(bundle / "vendor" / "antigravity-zh-launcher", runtime)
                shutil.copyfile(bundle / "scripts" / "launcher.py", runtime / "launcher.py")
                pending[installed_runtime] = runtime
            with tempfile.TemporaryDirectory(prefix=".progress-launcher-", dir=apps) as shortcut_temp:
                staged_app = Path(shortcut_temp) / APP_NAME
                write_launcher(staged_app, original, state)
                tracked["launcher"] = True
                pending[app] = staged_app
                finish_install(bundle, pending, replacements, tracked, state, stage)
        else:
            if include_launcher and launcher_exists:
                print("复用已有的「Antigravity 中文」入口。")
            finish_install(bundle, pending, replacements, tracked, state, stage)


def finish_install(bundle, pending, replacements, tracked, state, stage):
    created = []
    previous = {}
    try:
        for target, source in pending.items():
            if target in replacements:
                if target.is_symlink() or not target.is_dir() or fingerprint(target) != replacements[target]:
                    raise RuntimeError(f"升级期间文件发生变化 {target}")
                backup = stage / f".previous-{target.name}"
                target.rename(backup)
                previous[target] = backup
            elif target.exists() or target.is_symlink():
                raise FileExistsError(target)
            source.rename(target)
            created.append(target)
        for source, name in ((bundle / "scripts" / "package.py", "package.py"),
                             (bundle / "uninstall.sh", "uninstall.sh")):
            saved = stage / name
            shutil.copyfile(source, saved)
            saved.replace(state / name)
        manifest = stage / "install.json"
        manifest.write_text(json.dumps(tracked, ensure_ascii=False, indent=2), encoding="utf-8")
        manifest.replace(state / "install.json")
    except (OSError, RuntimeError):
        for target in reversed(created):
            target.rename(pending[target])
        for target, backup in previous.items():
            backup.rename(target)
        raise
    for target in created:
        if target != state / "runtime":
            print(f"{'已更新' if target in replacements else '已安装'} {target}")


def stop_owned_session(state):
    marker = state / "launcher.pid"
    if not marker.is_file():
        return
    try:
        pid = int(marker.read_text(encoding="ascii"))
    except ValueError:
        return
    if pid < 2:
        return
    launcher = str(state / "runtime" / "launcher.py")

    def owns_pid():
        result = subprocess.run(["/bin/ps", "-p", str(pid), "-o", "args="],
                                text=True, capture_output=True)
        return result.returncode == 0 and f" {launcher} " in result.stdout

    if not owns_pid():
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    for _ in range(30):
        if not owns_pid():
            return
        time.sleep(0.1)
    raise RuntimeError("中文入口的监视进程还未退出，请退出 Antigravity 后重试卸载。")


def uninstall(destination, state, home, dry_run):
    record = state / "install.json"
    if not record.is_file():
        print("这个安装位置没有组合包的卸载记录。")
        return
    tracked = json.loads(record.read_text(encoding="utf-8"))
    if not dry_run:
        stop_owned_session(state)
    for name, expected in tracked["plugins"].items():
        if name not in {PROGRESS, CHINESE}:
            continue
        target = destination / name
        if not target.is_dir() or target.is_symlink():
            continue
        try:
            current = json.loads((target / "plugin.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            print(f"跳过无法确认归属的目录 {target}")
            continue
        if current.get("name") != name:
            continue
        modified = fingerprint(target) != expected
        print(f"{'备份并移出' if modified else '移除'} {target}")
        if not dry_run:
            if modified:
                backup = Path(tempfile.mkdtemp(prefix="agent-progress-backup-", dir=state.parent))
                target.rename(backup / name)
                print(f"已备份到 {backup / name}")
            else:
                shutil.rmtree(target)

    app = home / "Applications" / APP_NAME
    if tracked["launcher"] and is_owned_launcher(app):
        print(f"移除 {app}")
        if not dry_run:
            shutil.rmtree(app)
    if not dry_run:
        shutil.rmtree(state)
        print("卸载完成。若 Antigravity 还在运行，按 ⌘Q 退出，再点官方 Antigravity 图标。")


if __name__ == "__main__":
    action, target, *args = sys.argv[1:]
    home = Path.home()
    destination = Path(target)
    state = destination.parent / "antigravity-agent-progress-install"
    try:
        if action == "install":
            bundle, chinese, launcher = args
            install(Path(bundle), destination, state, home, chinese == "1", launcher == "1")
        else:
            uninstall(destination, state, home, args == ["--dry-run"])
    except (OSError, RuntimeError, ValueError) as error:
        sys.exit(str(error))
