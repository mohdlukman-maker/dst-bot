#!/usr/bin/env python3
"""Acceptance tests for lib/lessons.py (Task 5)."""
import os
import sys
import json
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import lessons


def write_decisions(records):
    path = os.path.join(lessons._data_dir(), "decisions.jsonl")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


def refuted_record(action_name, prefab, day, exp=None):
    return {
        "verdict": "refuted",
        "day": day,
        "action": {"action": action_name, "prefab": prefab},
        "expected": exp if exp is not None else {"item_counts.twigs": "+1"},
    }


class TestLessons(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        lessons._DATA_DIR = self._td.name

    def test_high_confidence_line(self):
        recs = [refuted_record("gather_job", "sapling", day=4) for _ in range(7)]
        write_decisions(recs)
        out = lessons.build_lessons()
        self.assertIn("## High confidence (5+ refutations)", out)
        self.assertIn("`gather_job sapling` failed 7 times", out)
        self.assertIn("Last seen day 4", out)

    def test_medium_confidence_line(self):
        recs = [refuted_record("craft", "campfire", day=2) for _ in range(3)]
        write_decisions(recs)
        out = lessons.build_lessons()
        self.assertIn("## Medium confidence (3-4 refutations)", out)
        self.assertIn("`craft campfire` failed 3 times", out)
        self.assertNotIn("## High confidence", out)

    def test_two_refutations_nothing(self):
        recs = [refuted_record("gather_job", "grass", day=1) for _ in range(2)]
        write_decisions(recs)
        out = lessons.build_lessons()
        self.assertIn("_no patterns yet_", out)
        self.assertNotIn("## ", out)

    def test_confirmed_ignored(self):
        recs = [refuted_record("gather_job", "sapling", day=3) for _ in range(5)]
        recs += [{"verdict": "confirmed", "day": 2,
                  "action": {"action": "gather_job", "prefab": "sapling"},
                  "expected": {"x": "+1"}} for _ in range(20)]
        write_decisions(recs)
        out = lessons.build_lessons()
        self.assertIn("`gather_job sapling` failed 5 times", out)

    def test_missing_file_no_raise(self):
        out = lessons.build_lessons()
        self.assertIn("_no patterns yet_", out)


if __name__ == "__main__":
    unittest.main(verbosity=2)
