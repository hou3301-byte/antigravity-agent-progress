import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import usage


def returned_quotas(fraction):
    return {"response": {"groups": [
        {"displayName": "Gemini Models", "buckets": [
            {"bucketId": "gemini-5h", "remainingFraction": fraction, "resetTime": "2026-11-26T15:00:00Z"},
            {"bucketId": "argon-weekly", "resetTime": "2026-11-29T15:00:00.123456789Z"},
        ]},
        {"displayName": "Other Plan", "buckets": [
            {"bucketId": "custom", "displayName": "Monthly", "remainingFraction": 1},
            {"bucketId": "missing", "displayName": "Other"},
        ]},
    ]}}


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.now = usage.timestamp("2026-11-26T12:00:00Z")

    def test_normalizes_explicit_pools_and_preserves_missing_values(self):
        buckets = usage.quota_buckets(returned_quotas("0.75"), self.now)
        self.assertEqual([(b["label"], b["window"], b["remaining"]) for b in buckets], [
            ("Gemini", "5h", 75), ("Gemini 4 Argon", "weekly", 0), ("Other Plan", "Monthly", 100),
        ])
        self.assertTrue(buckets[1]["reset"] % 1)
        self.assertEqual(usage.quota_buckets(returned_quotas(1.1), self.now)[0]["pool"], "argon")

    def test_rates_and_estimates_follow_cycle_and_keep_official_percent(self):
        history = {}
        for index, fraction in enumerate((.9, .89, .88)):
            checked = self.now + index * 30
            buckets = usage.quota_buckets(returned_quotas(fraction), checked)
            history = usage.quota_rates(buckets, history, checked)
        self.assertAlmostEqual(buckets[0]["rate"], 120)
        session = {"generating_since": checked + 5, "pool": "gemini"}
        snap = usage.quota_snapshot(buckets, checked, False, session, checked + 20)
        self.assertAlmostEqual(snap[0]["estimate"], .5)
        self.assertEqual(snap[0]["remaining"], 88)
        self.assertNotIn("estimate", snap[1])
        for stale, at in ((True, checked + 20), (False, buckets[0]["reset"])):
            snap = usage.quota_snapshot(buckets, checked, stale, session, at)
            self.assertTrue(snap[0]["stale"])
            self.assertNotIn("rate", snap[0])
            self.assertNotIn("estimate", snap[0])
        reset = usage.quota_buckets(returned_quotas(1), checked + 60)
        self.assertEqual(len(usage.quota_rates(reset, history, checked + 60)[reset[0]["id"]]), 1)

    def test_stale_reads_and_account_changes_are_isolated(self):
        monitor = usage.UsageMonitor(Path("."), 100)
        monitor.endpoint = (22, 9999, "local-token")
        account_a = {"userStatus": {"email": "account-a"}}
        account_b = {"userStatus": {"email": "account-b"}}
        with patch.object(usage, "rpc", side_effect=[account_a, returned_quotas(.88), account_a]):
            monitor.refresh_quotas(self.now)
        self.assertTrue(monitor.buckets)
        with patch.object(usage, "rpc", side_effect=[account_a, OSError(), account_a]):
            monitor.refresh_quotas(self.now + 30)
        self.assertTrue(monitor.stale)
        self.assertTrue(monitor.buckets)
        with patch.object(usage, "rpc", side_effect=[account_a, returned_quotas(.11), account_b]):
            monitor.refresh_quotas(self.now + 60)
        self.assertEqual(monitor.buckets, [])
        self.assertEqual(monitor.history, {})

    def test_step_metrics_match_generator_metadata_by_absolute_index(self):
        planner = {"type": "CORTEX_STEP_TYPE_PLANNER_RESPONSE"}
        completed = {**planner, "status": "CORTEX_STEP_STATUS_DONE", "metadata": {
            "createdAt": "2026-11-26T12:00:00.123456000Z", "finishedGeneratingAt": "2026-11-26T12:00:02.123456789Z",
            "modelUsage": {"model": "MODEL_TEST", "outputTokens": "120"},
        }}
        running = {**planner, "status": "CORTEX_STEP_STATUS_GENERATING", "metadata": {
            "createdAt": "2026-11-26T12:00:05Z", "generatorModel": "MODEL_TEST",
        }}
        measured = usage.step_metrics([completed, running], 40, {40: {"timeToFirstToken": "0.75s"}},
                                      True, {"MODEL_TEST": "Gemini 4 Argon"}, {"gemini"})
        self.assertAlmostEqual(measured["speed"], 60)
        self.assertEqual(measured["ttft"], .75)
        self.assertEqual(measured["pool"], "gemini")
        self.assertEqual(measured["generating_since"], self.now + 5)
        self.assertIsNone(usage.step_metrics([running], 41, {}, False, {}, {})["generating_since"])

    def test_deduplicates_transcript_token_rows_and_persists_layout(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "transcript.jsonl"
            sample = {"type": "PLANNER_RESPONSE", "step_index": 2, "input_tokens": 7,
                      "cache_read_tokens": 4, "output_tokens": 3}
            path.write_text(f"{json.dumps(sample)}\n{json.dumps(sample)}\n{{partial\n", encoding="utf-8")
            cached = usage.transcript_totals(path, (None, {}))
            self.assertEqual(cached[1], {"input": 7, "cache_read": 4, "output": 3})
            self.assertIs(usage.transcript_totals(path, cached), cached)
            path = Path(tmp) / "usage-layout.json"
            self.assertEqual(usage.load_layout(path), usage.DEFAULT_LAYOUT)
            layout = {**usage.DEFAULT_LAYOUT, "hidden": ["ttft"], "sections": ["metrics", "quota"]}
            self.assertTrue(usage.valid_layout(layout))
            usage.save_layout(path, layout)
            self.assertEqual(usage.load_layout(path), layout)
            self.assertFalse(usage.valid_layout({**layout, "hidden": ["ttft", "ttft"]}))
            self.assertFalse(usage.valid_layout({**layout, "hidden": [{"unsafe": True}]}))
