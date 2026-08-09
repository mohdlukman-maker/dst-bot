#!/usr/bin/env python3
"""Acceptance tests for lib/decision_log.py (Task 3)."""
import os
import sys
import json
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import decision_log


class TestDecisionLog(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        decision_log._DATA_DIR = self._td.name
        decision_log._DECISIONS = {}
        decision_log._COUNTER = 0

    def test_plus1_confirmed(self):
        before = {"item_counts": {"twigs": 0}}
        after = {"item_counts": {"twigs": 1}}
        did = decision_log.log_decision("r", before, "g", {"action": "x"}, "why",
                                        {"item_counts.twigs": "+1"})
        self.assertEqual(decision_log.log_outcome(did, after), "confirmed")

    def test_plus1_no_change_refuted(self):
        before = {"item_counts": {"twigs": 1}}
        after = {"item_counts": {"twigs": 1}}
        did = decision_log.log_decision("r", before, "g", {"action": "x"}, "why",
                                        {"item_counts.twigs": "+1"})
        self.assertEqual(decision_log.log_outcome(did, after), "refuted")

    def test_plus2_requires_at_least_2(self):
        before = {"item_counts": {"twigs": 0}}
        after = {"item_counts": {"twigs": 1}}
        did = decision_log.log_decision("r", before, "g", {"action": "x"}, "why",
                                        {"item_counts.twigs": "+2"})
        self.assertEqual(decision_log.log_outcome(did, after), "refuted")

    def test_changes_on_moved_position(self):
        before = {"pos": {"x": 1, "z": 1}}
        after = {"pos": {"x": 5, "z": 5}}
        did = decision_log.log_decision("r", before, "g", {"action": "x"}, "why",
                                        {"pos": "changes"})
        self.assertEqual(decision_log.log_outcome(did, after), "confirmed")

    def test_unchanged_on_moved_value_refuted(self):
        before = {"pos": {"x": 1}}
        after = {"pos": {"x": 5}}
        did = decision_log.log_decision("r", before, "g", {"action": "x"}, "why",
                                        {"pos": "unchanged"})
        self.assertEqual(decision_log.log_outcome(did, after), "refuted")

    def test_missing_path_both_inconclusive(self):
        before = {"a": 1}
        after = {"a": 1}
        did = decision_log.log_decision("r", before, "g", {"action": "x"}, "why",
                                        {"nope.deep": "changes"})
        self.assertEqual(decision_log.log_outcome(did, after), "inconclusive")

    def test_missing_numeric_is_zero(self):
        before = {}  # no item_counts at all
        after = {"item_counts": {"twigs": 1}}
        did = decision_log.log_decision("r", before, "g", {"action": "x"}, "why",
                                        {"item_counts.twigs": "+1"})
        self.assertEqual(decision_log.log_outcome(did, after), "confirmed")

    def test_unknown_decision_id_empty(self):
        self.assertEqual(decision_log.log_outcome("d-999999", {}), "")

    def test_refuted_newest_first(self):
        for i in range(3):
            did = decision_log.log_decision("r", {"n": i}, "g", {"action": "x"}, "w",
                                            {"n": "+1"})
            decision_log.log_outcome(did, {"n": i})  # no change -> refuted
            import time as _t
            _t.sleep(0.01)
        refs = decision_log.refuted(limit=10)
        self.assertEqual(len(refs), 3)
        ts = [r["ts"] for r in refs]
        self.assertEqual(ts, sorted(ts, reverse=True))  # newest first

    def test_refuted_filters_by_run(self):
        for run in ("A", "B", "A"):
            did = decision_log.log_decision(run, {"n": 1}, "g", {"action": "x"}, "w",
                                            {"n": "+1"})
            decision_log.log_outcome(did, {"n": 1})
        refs_a = decision_log.refuted(run_id="A", limit=10)
        self.assertEqual(len(refs_a), 2)
        self.assertTrue(all(r["run_id"] == "A" for r in refs_a))


if __name__ == "__main__":
    unittest.main(verbosity=2)
