#!/usr/bin/env python3
"""Acceptance tests for lib/world_map.py (Task 4)."""
import os
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import world_map


def nb_entry(prefab, x, z, day=1):
    return {"n": prefab, "x": x, "z": z, "d": 5, "ok": True}


class TestWorldMap(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        # isolate maps dir
        p = mock.patch.object(world_map, "_maps_dir")
        # simpler: patch the path builder to use tmp
        self.patch = mock.patch("lib.world_map._path")
        self.mock_path = self.patch.start()
        self.addCleanup(self.patch.stop)
        self.mock_path.side_effect = lambda key: os.path.join(self._td.name, f"{key}.json")

    def test_observe_creates_entries(self):
        st = {"day": 1, "nearby": [nb_entry("grass", 10, 10), nb_entry("sapling", 20, 20),
                                    nb_entry("flint", 30, 30)]}
        n = world_map.observe("w", st)
        self.assertEqual(n, 3)
        self.assertEqual(world_map.stats("w")["total"], 3)

    def test_observe_twice_no_duplicates(self):
        st = {"day": 1, "nearby": [nb_entry("grass", 10, 10), nb_entry("grass", 20, 20)]}
        world_map.observe("w", st)
        n2 = world_map.observe("w", st)   # same positions
        self.assertEqual(n2, 0)
        self.assertEqual(world_map.stats("w")["total"], 2)

    def test_within_2_units_merge(self):
        st = {"day": 1, "nearby": [nb_entry("grass", 10.0, 10.0), nb_entry("grass", 11.0, 10.5)]}
        n = world_map.observe("w", st)
        self.assertEqual(n, 1)
        self.assertEqual(world_map.stats("w")["total"], 1)

    def test_far_apart_stay_separate(self):
        st = {"day": 1, "nearby": [nb_entry("grass", 10, 10), nb_entry("grass", 30, 30)]}
        n = world_map.observe("w", st)
        self.assertEqual(n, 2)

    def test_find_nearest_first(self):
        st = {"day": 1, "nearby": [nb_entry("grass", 10, 10), nb_entry("grass", 30, 30),
                                    nb_entry("grass", 20, 20)]}
        world_map.observe("w", st)
        res = world_map.find("w", "grass", near_xz=(19, 19))
        self.assertEqual(len(res), 3)
        self.assertEqual((res[0]["x"], res[0]["z"]), (20.0, 20.0))  # nearest first

    def test_find_unknown_prefab(self):
        st = {"day": 1, "nearby": [nb_entry("grass", 10, 10)]}
        world_map.observe("w", st)
        self.assertEqual(world_map.find("w", "merm"), [])

    def test_observe_empty_state(self):
        self.assertEqual(world_map.observe("w", {}), 0)

    def test_worlds_isolated(self):
        st = {"day": 1, "nearby": [nb_entry("grass", 10, 10)]}
        world_map.observe("w1", st)
        self.assertEqual(world_map.stats("w1")["total"], 1)
        self.assertEqual(world_map.stats("w2")["total"], 0)

    def test_set_get_base(self):
        self.assertTrue(world_map.set_base("w", 15, 25))
        b = world_map.get_base("w")
        self.assertEqual(b.get("x"), 15.0)
        self.assertEqual(b.get("z"), 25.0)
        self.assertEqual(world_map.get_base("w2"), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
