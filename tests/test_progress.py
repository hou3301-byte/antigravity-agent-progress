import json
import runpy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
VENDOR_LAUNCHER = str(ROOT / "vendor/antigravity-zh-launcher")
if VENDOR_LAUNCHER not in sys.path:
    sys.path.insert(0, VENDOR_LAUNCHER)
sys.path.insert(0, str(ROOT / "scripts"))
PROGRESS = runpy.run_path(str(ROOT / "agent-progress/scripts/progress.py"))
LAUNCHER = runpy.run_path(str(ROOT / "scripts/launcher.py"))


class ProgressTest(unittest.TestCase):
    def test_update_check_uses_a_two_hour_cache_and_compares_release_versions(self):
        check = LAUNCHER["check_release_update"]
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "release-check.json"
            checked_at = 1000
            next_check = checked_at + 7200
            with patch.object(LAUNCHER["time"], "time", return_value=checked_at):
                with patch.dict(check.__globals__, {"fetch_release_tag": lambda: "v0.10.0"}):
                    self.assertEqual(check(cache, (0, 9, 0)), ("v0.10.0", next_check))
            with patch.object(LAUNCHER["time"], "time", return_value=next_check - 1):
                with patch.dict(check.__globals__, {"fetch_release_tag": lambda: self.fail("cache missed")}):
                    self.assertEqual(check(cache, (0, 9, 0)), ("v0.10.0", next_check))
                    self.assertEqual(check(cache, (0, 10, 0)), (None, next_check))
                    self.assertEqual(check(cache, (0, 11, 0)), (None, next_check))

            def offline():
                raise subprocess.TimeoutExpired("curl", 6)

            with patch.object(LAUNCHER["time"], "time", return_value=next_check):
                with patch.dict(check.__globals__, {"fetch_release_tag": offline}):
                    self.assertEqual(check(cache, (0, 9, 0)), ("v0.10.0", next_check + 7200))
            self.assertEqual(json.loads(cache.read_text())["checked_at"], next_check)

    def test_release_check_rejects_drafts_prereleases_and_nonversion_tags(self):
        fetch = LAUNCHER["fetch_release_tag"]
        stable = {"tag_name": "v0.10.0", "draft": False, "prerelease": False}
        for patch_data, expected in [({}, "v0.10.0"), ({"draft": True}, None), ({"prerelease": True}, None),
                                     ({"tag_name": "v0.10.0-beta"}, None), ({"tag_name": "../../etc"}, None)]:
            with self.subTest(patch_data=patch_data):
                response = subprocess.CompletedProcess([], 0, stdout=json.dumps({**stable, **patch_data}).encode())
                with patch.object(subprocess, "run", return_value=response):
                    self.assertEqual(fetch(), expected)

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
            send("pre", stepIdx=6, toolCall=tool)
            send("post", stepIdx=6, toolCall={"name": "run_command", "args": {"CommandLine": "cat pytest.ini"}})
            self.assertFalse(read().get("has_validation", False))
            send("invoke", invocationNum=1)
            self.assertEqual(read()["started_at"], turn)
            self.assertEqual(read()["running"], [])

            send("pre", stepIdx=5, toolCall=tool)
            send("post", stepIdx=5, error="private error detail",
                 toolCall={"name": "run_command", "args": {"CommandLine": "python -m pytest private-test-path"}})
            self.assertTrue(read()["had_failure"])
            self.assertTrue(read()["has_validation"])
            self.assertNotIn("private", state_file.read_text())
            send("stop", fullyIdle=False, terminationReason="model_stop")
            self.assertTrue(read()["active"])
            self.assertTrue(read()["waiting_background"])
            self.assertEqual(read()["outcome"], "ended")
            send("stop", fullyIdle=True, terminationReason="error", error="private detail")
            self.assertFalse(read()["active"])
            self.assertFalse(read().get("waiting_background", False))
            self.assertEqual(read()["outcome"], "failed")
            self.assertNotIn("private", state_file.read_text())
            self.assertEqual(read()["running"], [])
            self.assertEqual(list(directory.glob("agent-progress-*.html")), [])

            send("invoke", invocationNum=0)
            self.assertGreater(read()["started_at"], turn)
            self.assertTrue(read()["active"])
            self.assertFalse(read().get("had_failure", False))
            self.assertFalse(read().get("has_validation", False))

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
            self.assertEqual(snap["sources"], [("检查 config.json 配置", "改造核心代码", "运行单元测试")])
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

            write_lines([
                {"type": "USER_INPUT", "content": "帮我重构"},
                {"type": "PLANNER_RESPONSE", "content": "- [/] A\n- [ ] B\n- [ ] C"},
                {"type": "PLANNER_RESPONSE", "content": "<!-- todo:3/3 -->"},
                {"type": "PLANNER_RESPONSE", "content": "验证失败，重新修复。<!-- todo:2/3 -->"},
            ])
            snap = LAUNCHER["read_progress"](f"https://127.0.0.1:8000/c/{conv}", brain, cache)
            self.assertEqual([i["state"] for i in snap["items"]], ["done", "running", "recheck"])

            with transcript.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"type": "PLANNER_RESPONSE", "content": "- [x] A\n- [x] B\n- [x] C"}) + "\n")
            snap = LAUNCHER["read_progress"](f"https://127.0.0.1:8000/c/{conv}", brain, cache)
            self.assertTrue(snap["done"])
            self.assertEqual([i["state"] for i in snap["items"]], ["done", "done", "done"])

            # A replacement plan still owns the earlier checklist in the same turn.
            write_lines([
                {"type": "USER_INPUT", "content": "帮我重构"},
                {"type": "PLANNER_RESPONSE", "content": "- [ ] 草案A\n- [ ] 草案B\n- [ ] 草案C"},
                {"type": "PLANNER_RESPONSE", "content": "- [/] A\n- [ ] B\n- [ ] C"},
                {"type": "PLANNER_RESPONSE", "content": "<!-- todo:2/3 -->"},
            ])
            snap = LAUNCHER["read_progress"](f"https://127.0.0.1:8000/c/{conv}", brain, cache)
            self.assertEqual(snap["sources"], [("草案A", "草案B", "草案C"), ("A", "B", "C")])
            self.assertEqual([item["state"] for item in snap["items"]], ["done", "running", "pending"])

            # Blocked in phase 3
            write_lines([
                {"type": "USER_INPUT", "content": "帮我重构"},
                {"type": "PLANNER_RESPONSE", "content": "- [/] A\n- [ ] B\n- [ ] C"},
                {"type": "PLANNER_RESPONSE", "content": "阻塞了。<!-- todo:3/3 blocked -->"},
            ])
            snap = LAUNCHER["read_progress"](f"https://127.0.0.1:8000/c/{conv}", brain, cache)
            self.assertEqual([i["state"] for i in snap["items"]], ["done", "done", "blocked"])

            # The completed checklist remains available to expand.
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

    def test_background_clock_clears_on_completion_and_ignores_invalid_phase_markers(self):
        with tempfile.TemporaryDirectory() as temporary:
            conversation = "11111111-2222-3333-4444-555555555555"
            brain = Path(temporary)
            logs = brain / conversation / ".system_generated/logs"
            logs.mkdir(parents=True)
            transcript = logs / "transcript.jsonl"
            task = f"{conversation}/task-4"
            entries = [
                {"type": "USER_INPUT", "content": "运行验证"},
                {"type": "PLANNER_RESPONSE", "content": "- [/] A\n- [ ] B\n- [ ] C\n<!-- todo:2/4 -->\n<!-- todo:8/3 -->"},
                {"type": "GENERIC", "status": "RUNNING", "created_at": "2026-11-21T04:23:59Z",
                 "content": f"Tool is running as a background task with task id: {task}\n"},
            ]

            def snapshot():
                transcript.write_text("\n".join(map(json.dumps, entries)) + "\n", encoding="utf-8")
                return LAUNCHER["read_progress"](f"https://127.0.0.1:8000/c/{conversation}", brain)

            starting = snapshot()
            self.assertEqual(starting["background_count"], 1)
            self.assertEqual(starting["background_start"], 1795235039.0)
            self.assertEqual([item["state"] for item in starting["items"]], ["running", "pending", "pending"])

            entries.append({"type": "PLANNER_RESPONSE", "content": f'Task id "{task}" finished with result:'})
            self.assertEqual(snapshot()["background_count"], 1)

            entries.append({"type": "SYSTEM_MESSAGE",
                            "content": f'[Message] sender={task} priority=MESSAGE_PRIORITY_HIGH content=Task id "{task}" finished with result:'})
            finished = snapshot()
            self.assertEqual(finished["background_count"], 0)
            self.assertIsNone(finished["background_start"])


if __name__ == "__main__":
    unittest.main()
