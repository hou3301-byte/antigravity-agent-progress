import json
import plistlib
import runpy
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PACKAGE = runpy.run_path(str(ROOT / "scripts" / "package.py"))


class InstallationTest(unittest.TestCase):
    def setUp(self):
        workspace = tempfile.TemporaryDirectory(prefix="progress-install-test-")
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name)
        self.home = self.root / "Test Home"
        self.system_apps = self.root / "System Applications"
        self.official = self.system_apps / "Antigravity.app/Contents/MacOS/Antigravity"
        self.official.parent.mkdir(parents=True)
        self.official.write_bytes(b"official executable")
        self.destination = self.home / ".gemini/config/plugins"
        self.state = self.destination.parent / "antigravity-agent-progress-install"
        self.app = self.home / "Applications/Antigravity 中文.app"
        self.system_link = self.system_apps / "Antigravity 中文.app"
        self.config = self.destination.parent / "config.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_bytes(b'{"plugins":{"other":{"enabled":true}},"setting":42}\n')

    def install(self):
        PACKAGE["install"](ROOT, self.destination, self.state, self.home, True, True, self.system_apps)

    def uninstall(self, dry_run=False):
        PACKAGE["uninstall"](self.destination, self.state, self.home, dry_run, self.system_apps)

    def test_install_and_uninstall_owned_files(self):
        saved_config = self.config.read_bytes()
        self.install()
        self.install()
        with (self.app / "Contents/Info.plist").open("rb") as file:
            self.assertEqual(plistlib.load(file)["CFBundleIdentifier"], PACKAGE["LAUNCHER_ID"])
        self.assertTrue(self.system_link.is_symlink())
        self.assertEqual(self.system_link.resolve(), self.app.resolve())
        shell = self.app / "Contents/MacOS/launcher"
        subprocess.run(["/bin/bash", "-n", str(shell)], check=True)
        self.assertIn(str(self.official), shell.read_text())
        self.assertTrue((self.state / "runtime/overlay.js").is_file())
        self.assertTrue((self.state / "runtime/progress-overlay.js").is_file())
        self.assertEqual((self.state / "runtime/progress.css").read_bytes(),
                         (ROOT / "agent-progress/assets/progress.css").read_bytes())
        self.assertTrue((self.state / "uninstall.sh").is_file())
        self.uninstall(dry_run=True)
        self.assertTrue(self.app.is_dir())
        self.assertTrue(self.system_link.is_symlink())
        self.uninstall()
        self.uninstall()
        self.assertFalse(self.app.exists())
        self.assertFalse(self.system_link.is_symlink())
        self.assertFalse(self.state.exists())
        self.assertEqual(list(self.destination.iterdir()), [])
        self.assertEqual(self.config.read_bytes(), saved_config)
        self.assertEqual(self.official.read_bytes(), b"official executable")

    def test_keep_existing_launcher_and_chinese_plugin(self):
        upstream_app = self.system_apps / "Antigravity 中文.app"
        upstream_app.mkdir()
        chinese = self.destination / "antigravity-zh-prompt"
        chinese.mkdir(parents=True)
        original = b'{"name":"antigravity-zh-prompt","disabled":true}'
        (chinese / "plugin.json").write_bytes(original)
        self.install()
        self.assertFalse(self.app.exists())
        self.uninstall()
        self.assertTrue(upstream_app.is_dir())
        self.assertEqual((chinese / "plugin.json").read_bytes(), original)

    def test_upgrade_managed_plugin_metadata(self):
        self.install()
        overlay = self.state / "runtime/progress-overlay.js"
        overlay.unlink()
        record_path = self.state / "install.json"
        record = json.loads(record_path.read_text())
        for name in record["plugins"]:
            plugin = self.destination / name
            path = plugin / "plugin.json"
            old = json.loads(path.read_text())
            old.update({"author": {"name": "Previous release"}, "version": "0.2.0"})
            path.write_text(json.dumps(old))
            record["plugins"][name] = PACKAGE["fingerprint"](plugin)
        record_path.write_text(json.dumps(record))

        self.install()
        self.assertTrue(overlay.is_file())
        expected = json.loads((ROOT / "agent-progress/plugin.json").read_text())
        for name in record["plugins"]:
            actual = json.loads((self.destination / name / "plugin.json").read_text())
            for field in ("author", "version", "homepage", "repository"):
                self.assertEqual(actual[field], expected[field])
        chinese = self.destination / "antigravity-zh-prompt"
        upstream = ROOT / "vendor/antigravity-zh-prompt"
        for path in ("LICENSE", "rules/AGENTS.md"):
            self.assertEqual((chinese / path).read_bytes(), (upstream / path).read_bytes())
        self.uninstall()
        self.assertEqual(list(self.destination.iterdir()), [])

    def test_uninstall_moves_modified_owned_plugin_to_backup(self):
        self.install()
        progress = self.destination / "agent-progress"
        (progress / "personal.txt").write_text("my changes")
        self.uninstall()
        self.assertFalse(progress.exists())
        backups = list(self.state.parent.glob("agent-progress-backup-*/agent-progress/personal.txt"))
        self.assertEqual([file.read_text() for file in backups], ["my changes"])

    def test_uninstall_stops_owned_watcher(self):
        self.install()
        launcher = self.state / "runtime/launcher.py"
        marker = self.state / "launcher.pid"
        launcher.write_text("import os, time\nfrom pathlib import Path\n"
                            f"Path({str(marker)!r}).write_text(str(os.getpid()))\n"
                            "time.sleep(30)\n")
        process = subprocess.Popen([sys.executable, str(launcher), "test"])
        try:
            deadline = time.monotonic() + 3
            while not marker.is_file() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertEqual(marker.read_text(), str(process.pid))
            self.uninstall()
            self.assertLess(process.wait(timeout=3), 0)
        finally:
            if process.poll() is None:
                process.terminate()
                process.wait(timeout=3)

    def test_workspace_shell_offline_uninstall(self):
        workspace = self.root / "A Project"
        workspace.mkdir()
        subprocess.run(["/bin/bash", str(ROOT / "install.sh"), "--workspace", str(workspace)], check=True)
        settings = workspace / ".agents"
        local_state = settings / "antigravity-agent-progress-install"
        self.assertFalse(json.loads((local_state / "install.json").read_text())["launcher"])
        subprocess.run(["/bin/bash", str(local_state / "uninstall.sh"), "--workspace", str(workspace)], check=True)
        self.assertFalse(local_state.exists())
        self.assertEqual(list((settings / "plugins").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
