#!/usr/bin/env python3
"""Acceptance tests for lib/run_log.py (Task 2)."""
import os
import sys
import json
import time
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import run_log


class TestRunLog(unittest.TestCase):
    def setUp(self):
        # isolate the data dir per test
        self.tmp = mock.patch.object(run_log, "_DATA_DIR", None)
        self.tmp.start()
        self.addCleanup(self.tmp.stop)
        # point data dir into a temp folder
        import tempfile
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        run_log._DATA_DIR = self._td.name

    def _runs(self):
        path = os.path.join(run_log._DATA_DIR, "runs.jsonl")
        if not os.path.exists(path):
            return []
        with open(path, encoding="utf-8") as f:
            return [json.loads(l) for l in f if l.strip()]

    def test_start_end_writes_one_line(self):
        rid = run_log.start_run("v7", "first run")
        self.assertTrue(run_log.end_run(rid, {"day": 4}, "darkness"))
        runs = self._runs()
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["run_id"], rid)
        self.assertEqual(runs[0]["day_reached"], 4)
        self.assertEqual(runs[0]["cause"], "darkness")
        self.assertEqual(runs[0]["mod_version"], "v7")

    def test_two_runs_append_only(self):
        r1 = run_log.start_run("v7")
        run_log.end_run(r1, {"day": 1}, "starvation")
        first_line = open(os.path.join(run_log._DATA_DIR, "runs.jsonl"), encoding="utf-8").readline()
        r2 = run_log.start_run("v7")
        run_log.end_run(r2, {"day": 3}, "mob")
        lines = open(os.path.join(run_log._DATA_DIR, "runs.jsonl"), encoding="utf-8").readlines()
        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0], first_line)  # first line unchanged

    def test_empty_state_writes_day0(self):
        rid = run_log.start_run("v7")
        self.assertTrue(run_log.end_run(rid, {}, "crash"))
        runs = self._runs()
        self.assertEqual(runs[0]["day_reached"], 0)

    def test_missing_hunger_no_raise(self):
        rid = run_log.start_run("v7")
        self.assertTrue(run_log.end_run(rid, {"day": 2, "health": [50, 150]}, "unknown"))
        runs = self._runs()
        self.assertEqual(runs[0]["final_health"], 50)
        self.assertIsNone(runs[0]["final_hunger"])

    def test_summarize_empty(self):
        self.assertEqual(run_log.summarize(), "no runs recorded")

    def test_summarize_stats(self):
        for day, cause in [(1, "darkness"), (3, "mob"), (6, "darkness"),
                           (2, "starvation"), (4, "darkness")]:
            rid = run_log.start_run("v7")
            run_log.end_run(rid, {"day": day}, cause)
        s = run_log.summarize(20)
        self.assertIn("Runs: 5", s)
        self.assertIn("Best day: 6", s)
        self.assertIn("Median day: 3", s)
        self.assertIn("Most common cause: darkness", s)

    def test_end_run_idempotent(self):
        # v9 fix: try/except/finally triple-call must write ONE line
        rid = run_log.start_run("v7")
        self.assertTrue(run_log.end_run(rid, {"day": 2}, "mob"))
        self.assertFalse(run_log.end_run(rid, {"day": 9}, "starvation"))  # 2nd call refused
        self.assertEqual(len(self._runs()), 1)

    def test_corrupt_line_skipped(self):
        path = os.path.join(run_log._DATA_DIR, "runs.jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write("{not json\n")
        rid = run_log.start_run("v7")
        run_log.end_run(rid, {"day": 5}, "freezing")
        s = run_log.summarize(20)
        self.assertIn("Runs: 1", s)
        self.assertIn("Best day: 5", s)


if __name__ == "__main__":
    unittest.main(verbosity=2)
