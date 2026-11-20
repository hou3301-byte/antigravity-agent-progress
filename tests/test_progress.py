import json
import runpy
import tempfile
import unittest
from pathlib import Path


PROGRESS = runpy.run_path(str(Path(__file__).resolve().parents[1] / "agent-progress/scripts/progress.py"))


class ProgressTest(unittest.TestCase):
    def test_tools_before_card_and_new_turn(self):
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary).resolve()
            state_file = directory / ".system_generated/agent-progress/state.json"
            card = directory / "agent-progress-test01.html"

            def send(kind, **payload):
                PROGRESS["process_event"](kind, {"artifactDirectoryPath": str(directory), **payload})

            def read():
                return json.loads(state_file.read_text())

            send("invoke", invocationNum=0)
            turn = read()["started_at"]
            tool = {"name": "run_command", "args": {"CommandLine": "private"}}
            send("pre", stepIdx=4, toolCall=tool)
            self.assertEqual(read()["tools"][0]["state"], "running")
            self.assertIsNone(read()["run"])
            send("post", stepIdx=4, toolCall=tool)
            send("invoke", invocationNum=1)
            self.assertEqual(read()["started_at"], turn)
            self.assertEqual(read()["tools"][0]["state"], "done")
            self.assertNotIn("private", state_file.read_text())

            card.write_text('<script type="application/json" id="agent-progress-input">'
                            '{"items":[{"label":"核对","state":"done"}]}</script>')
            send("post", toolCall={"name": "write_file", "args": {"Path": str(card)}})
            self.assertEqual(read()["tools"][0]["name"], "run_command")
            self.assertIn('data-agent-progress-card="test01"', card.read_text())
            self.assertIn("1 / 1 项完成", card.read_text())
            send("stop", fullyIdle=True)
            self.assertFalse(read()["active"])
            send("invoke", invocationNum=0)
            self.assertGreater(read()["started_at"], turn)
            self.assertEqual(read()["tools"], [])
            self.assertEqual(read()["items"], [])

    def test_card_keeps_long_tool_history_outside_tasks(self):
        tools = [{"name": f"operation_{i}", "state": "done", "start": 1, "end": 2} for i in range(24)]
        tools[0].update(state="running", end=None)
        markup = PROGRESS["render"]({"run": "test01", "active": True, "tools": tools,
                                     "items": [{"label": "核对", "state": "done"}]})
        card, rest = markup.split("</section>", 1)
        summary, recent = rest.split("</summary>", 1)
        self.assertNotIn("operation_", card)
        self.assertIn('<details class="agy-tool-activity"><summary>', summary)
        self.assertIn("operation_0", summary)
        self.assertIn('data-since="1"', summary)
        self.assertEqual(recent.count("<li>"), 8)


if __name__ == "__main__":
    unittest.main()
