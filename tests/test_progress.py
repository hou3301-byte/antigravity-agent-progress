import json
import runpy
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
VENDOR_LAUNCHER = str(ROOT / "vendor/antigravity-zh-launcher")
if VENDOR_LAUNCHER not in sys.path:
    sys.path.insert(0, VENDOR_LAUNCHER)
PROGRESS = runpy.run_path(str(ROOT / "agent-progress/scripts/progress.py"))
LAUNCHER = runpy.run_path(str(ROOT / "scripts/launcher.py"))


class ProgressTest(unittest.TestCase):
    def test_hook_lifecycle_tracks_only_active_and_running_steps(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            state_file = directory / ".system_generated/agent-progress/state.json"

            def send(kind, **payload):
                PROGRESS["process_event"](kind, {"artifactDirectoryPath": str(directory), **payload})

            def read():
                return json.loads(state_file.read_text())

            send("invoke", invocationNum=0)
            turn = read()["started_at"]
            self.assertTrue(read()["active"])
            self.assertEqual(read()["running"], [])

            tool = {"name": "run_command", "args": {"CommandLine": "private-secret"}}
            send("pre", stepIdx=4, toolCall=tool)
            self.assertEqual([item["step"] for item in read()["running"]], [4])
            self.assertNotIn("private-secret", state_file.read_text())

            send("post", stepIdx=4, toolCall=tool)
            send("invoke", invocationNum=1)
            self.assertEqual(read()["started_at"], turn)
            self.assertEqual(read()["running"], [])

            send("pre", stepIdx=5, toolCall=tool)
            send("stop", fullyIdle=True)
            self.assertFalse(read()["active"])
            self.assertEqual(read()["running"], [])
            self.assertEqual(list(directory.glob("agent-progress-*.html")), [])

            send("invoke", invocationNum=0)
            self.assertGreater(read()["started_at"], turn)
            self.assertTrue(read()["active"])

    def test_transcript_todo_parsing_and_turn_reset(self):
        with tempfile.TemporaryDirectory() as temporary:
            brain = Path(temporary).resolve()
            conv = "11111111-2222-3333-4444-555555555555"
            conv_dir = brain / conv
            logs = conv_dir / ".system_generated/logs"
            state_dir = conv_dir / ".system_generated/agent-progress"
            logs.mkdir(parents=True)
            state_dir.mkdir(parents=True)
            transcript = logs / "transcript.jsonl"
            state_file = state_dir / "state.json"
            state_file.write_text(json.dumps({"started_at": 100.0, "active": True, "running": [{"step": 1, "start": 102.0}]}))

            def write_lines(entries):
                transcript.write_text("\n".join(json.dumps(e, ensure_ascii=False) for e in entries) + "\n", encoding="utf-8")

            cache = {}
            write_lines([
                {"type": "USER_INPUT", "content": "帮我重构"},
                {"type": "PLANNER_RESPONSE", "content": (
                    "开始改造：\n"
                    "- [/] 检查 `config.json` 配置\n"
                    "- [ ] 改造核心代码\n"
                    "- [ ] 运行单元测试\n"
                )},
                {"type": "SYSTEM_MESSAGE", "content": "Wakeup notice"},
            ])
            snap = LAUNCHER["read_progress"](f"https://127.0.0.1:8000/c/{conv}", brain, cache)
            self.assertTrue(snap["active"])
            self.assertEqual(snap["running_start"], 102.0)
            self.assertFalse(snap["done"])
            self.assertEqual(
                snap["items"],
                [
                    {"label": "检查 config.json 配置", "state": "running"},
                    {"label": "改造核心代码", "state": "pending"},
                    {"label": "运行单元测试", "state": "pending"},
                ],
            )

            # Advance to phase 2, while ignoring inline/fenced code fake comments
            write_lines([
                {"type": "USER_INPUT", "content": "帮我重构"},
                {"type": "PLANNER_RESPONSE", "content": (
                    "- [/] 检查 `config.json` 配置\n"
                    "- [ ] 改造核心代码\n"
                    "- [ ] 运行单元测试\n"
                )},
                {"type": "PLANNER_RESPONSE", "content": (
                    "例如示例 `<!-- todo:3/3 -->` 不应生效。\n"
                    "```markdown\n<!-- todo:done -->\n```\n"
                    "配置已确认，开始改造核心代码。<!-- todo:2/3 -->"
                )},
            ])
            snap = LAUNCHER["read_progress"](f"https://127.0.0.1:8000/c/{conv}", brain, cache)
            self.assertEqual([i["state"] for i in snap["items"]], ["done", "running", "pending"])
            self.assertFalse(snap["done"])

            # Blocked in phase 3
            write_lines([
                {"type": "USER_INPUT", "content": "帮我重构"},
                {"type": "PLANNER_RESPONSE", "content": "- [/] A\n- [ ] B\n- [ ] C"},
                {"type": "PLANNER_RESPONSE", "content": "阻塞了。<!-- todo:3/3 blocked -->"},
            ])
            snap = LAUNCHER["read_progress"](f"https://127.0.0.1:8000/c/{conv}", brain, cache)
            self.assertEqual([i["state"] for i in snap["items"]], ["done", "done", "blocked"])

            # Done hides card
            write_lines([
                {"type": "USER_INPUT", "content": "帮我重构"},
                {"type": "PLANNER_RESPONSE", "content": "- [/] A\n- [ ] B\n- [ ] C"},
                {"type": "PLANNER_RESPONSE", "content": "全部完成。<!-- todo:done -->"},
            ])
            snap = LAUNCHER["read_progress"](f"https://127.0.0.1:8000/c/{conv}", brain, cache)
            self.assertTrue(snap["done"])
            self.assertEqual([i["state"] for i in snap["items"]], ["done", "done", "done"])

            # New user turn with static markdown checklist (no [/], no markers) must NOT hijack
            state_file.write_text(json.dumps({"started_at": 200.0, "active": False, "running": []}))
            write_lines([
                {"type": "USER_INPUT", "content": "帮我重构"},
                {"type": "PLANNER_RESPONSE", "content": "- [/] A\n- [ ] B\n- [ ] C\n<!-- todo:done -->"},
                {"type": "USER_INPUT", "content": "列出注意事项"},
                {"type": "PLANNER_RESPONSE", "content": "- [ ] 第一条注意\n- [x] 第二条注意"},
            ])
            snap = LAUNCHER["read_progress"](f"https://127.0.0.1:8000/c/{conv}", brain, cache)
            self.assertIsNone(snap)


if __name__ == "__main__":
    unittest.main()
