#!/usr/bin/env python3
"""Tests for lib/targets.py (v9 Bug C fix)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import targets


def E(guid, prefab, d, ok=True):
    return {"guid": guid, "n": prefab, "d": d, "ok": ok}


class TestTargetTracker(unittest.TestCase):
    def test_picks_nearest(self):
        t = targets.TargetTracker()
        st = {"nearby": [E(2, "sapling", 10), E(1, "sapling", 3), E(3, "sapling", 20)]}
        self.assertEqual(t.pick(st)["guid"], 1)

    def test_fired_then_not_repicked(self):
        t = targets.TargetTracker()
        st = {"nearby": [E(1, "sapling", 3)]}
        e = t.pick(st)
        t.fired(e)
        self.assertIsNone(t.pick(st))   # in flight -> not picked again

    def test_reported_done_not_retried(self):
        t = targets.TargetTracker()
        st = {"nearby": [E(1, "sapling", 3)]}
        t.fired(t.pick(st))
        t.reported({})                  # failed (gained nothing)
        self.assertIsNone(t.pick(st))   # absorbed into done

    def test_ok_false_skipped(self):
        t = targets.TargetTracker()
        st = {"nearby": [E(1, "sapling", 3, ok=False), E(2, "sapling", 5, ok=True)]}
        self.assertEqual(t.pick(st)["guid"], 2)

    def test_clear_reenables(self):
        t = targets.TargetTracker()
        st = {"nearby": [E(1, "sapling", 3)]}
        t.fired(t.pick(st))
        t.reported({})
        self.assertIsNone(t.pick(st))
        t.clear()
        self.assertEqual(t.pick(st)["guid"], 1)

    def test_no_guid_entries_skipped(self):
        t = targets.TargetTracker()
        st = {"nearby": [{"n": "sapling", "d": 3}]}   # no guid
        self.assertIsNone(t.pick(st))

    def test_empty_state(self):
        t = targets.TargetTracker()
        self.assertIsNone(t.pick({}))
        self.assertIsNone(t.pick(None))
        t.fired(None)
        t.reported(None)


if __name__ == "__main__":
    unittest.main(verbosity=2)
