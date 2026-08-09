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

    def test_empty_expected_inconclusive(self):
        # Session A fix T5: expectation-less decisions must NOT score confirmed
        did = decision_log.log_decision("r", {"a": 1}, "g", {"action": "x"}, "w", {})
        self.assertEqual(decision_log.log_outcome(did, {"a": 2}), "inconclusive")

    def test_mixed_confirmed_and_inconclusive_confirmed(self):
        # T5: mixed scores confirmed only when at least one expectation held.
        # NOTE: state must be NESTED - _get_path walks dicts by dot-path.
        before = {"item_counts": {"twigs": 0}}
        after = {"item_counts": {"twigs": 1}}
        did = decision_log.log_decision("r", before, "g", {"action": "x"}, "w",
                                        {"item_counts.twigs": "+1", "pos.x": "changes"})
        # twigs +1 held (confirmed); pos.x missing in BOTH -> inconclusive
        self.assertEqual(decision_log.log_outcome(did, after), "confirmed")

    def test_nearest_threat_distance_increases_confirmed(self):
        # Task 7 follow-up: a flee that opened the gap scores confirmed
        before = {"nearest_threat_d": 8}
        after = {"nearest_threat_d": 20}
        did = decision_log.log_decision("r", before, "g", {"action": "x"}, "w",
                                        {"nearest_threat_d": "increases"})
        self.assertEqual(decision_log.log_outcome(did, after), "confirmed")

    def test_craft_auto_equip_confirmed(self):
        # Session A2 T1: crafted tool auto-equips -> lives in equipped, not
        # item_counts. The owned map must make the expectation score confirmed.
        before = {"item_counts": {"twigs": 1, "flint": 1}, "equipped": []}
        after = {"item_counts": {"twigs": 1, "flint": 1}, "equipped": ["axe"]}
        # simulate what get_state() derives
        for st_ in (before, after):
            owned = dict(st_.get("item_counts") or {})
            for it in (st_.get("equipped") or []):
                owned[it] = owned.get(it, 0) + 1
            st_["owned"] = owned
        did = decision_log.log_decision("r", before, "g", {"action": "craft"}, "w",
                                        {"owned.axe": "+1"})
        self.assertEqual(decision_log.log_outcome(did, after), "confirmed")

    def test_craft_auto_equip_refuted(self):
        # craft did not land -> owned.axe stays 0 -> refuted
        before = {"item_counts": {"twigs": 1, "flint": 1}, "equipped": []}
        after = {"item_counts": {"twigs": 1, "flint": 1}, "equipped": []}
        for st_ in (before, after):
            owned = dict(st_.get("item_counts") or {})
            for it in (st_.get("equipped") or []):
                owned[it] = owned.get(it, 0) + 1
            st_["owned"] = owned
        did = decision_log.log_decision("r", before, "g", {"action": "craft"}, "w",
                                        {"owned.axe": "+1"})
        self.assertEqual(decision_log.log_outcome(did, after), "refuted")

    def test_nearest_threat_distance_decreases_refuted(self):
        # Task 7 follow-up: a flee that let the merm close 8->3 scores refuted
        before = {"nearest_threat_d": 8}
        after = {"nearest_threat_d": 3}
        did = decision_log.log_decision("r", before, "g", {"action": "x"}, "w",
                                        {"nearest_threat_d": "increases"})
        self.assertEqual(decision_log.log_outcome(did, after), "refuted")


if __name__ == "__main__":
    unittest.main(verbosity=2)
