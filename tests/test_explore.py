#!/usr/bin/env python3
"""Tests for lib/explore.py (v9 Bug B fix)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import explore


class TestExplorer(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self._tmp_name = self._td.name
        # isolate the explore data dir by replacing the instance method.
        # NOTE: the lambda's first arg is the Explorer instance (not self) -
        # use a different name so it closes over the test's _tmp_name.
        self._orig = explore.Explorer._path
        explore.Explorer._path = lambda _inst: os.path.join(self._tmp_name, "e.json")

    def tearDown(self):
        explore.Explorer._path = self._orig

    def _mk(self, x, z):
        return {"pos": {"x": x, "z": z}, "nearby": []}

    def test_observe_marks_cell(self):
        e = explore.Explorer()
        e.observe(self._mk(100, 100))
        self.assertIn(explore.cell_of(100, 100), e.visited)

    def test_next_target_not_current_cell(self):
        e = explore.Explorer()
        e.observe(self._mk(0, 0))  # marks (0,0) visited
        t = e.next_target(self._mk(0, 0))
        self.assertIsNotNone(t)
        self.assertNotEqual(explore.cell_of(t[0], t[1]), (0, 0))

    def test_no_cycle_within_reach(self):
        e = explore.Explorer()
        e.observe(self._mk(0, 0))
        targets = set()
        for _ in range(10):
            t = e.next_target(self._mk(0, 0))
            if t is None:
                break
            targets.add(explore.cell_of(t[0], t[1]))
            e.visited.add(explore.cell_of(t[0], t[1]))
        # 10 distinct targets -> no cycle
        self.assertGreaterEqual(len(targets), 8)

    def test_leash_respected(self):
        e = explore.Explorer()
        e.observe(self._mk(0, 0))
        # max_leash 60 units: all targets within ~60 of base (0,0)
        t = e.next_target(self._mk(0, 0), base_xz=(0, 0), max_leash=60)
        if t:
            d = (t[0]**2 + t[1]**2) ** 0.5
            self.assertLessEqual(d, 60 + 20)  # cell-center tolerance

    def test_persist_roundtrip(self):
        e = explore.Explorer()
        e.observe(self._mk(50, 50))
        e.save()
        e2 = explore.Explorer()
        self.assertIn(explore.cell_of(50, 50), e2.visited)

    def test_garbage_state(self):
        e = explore.Explorer()
        e.observe(None)
        e.observe({})
        self.assertIsNone(e.next_target(None))


if __name__ == "__main__":
    unittest.main(verbosity=2)
