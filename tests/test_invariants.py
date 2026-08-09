#!/usr/bin/env python3
"""Tests for lib/invariants.py (v8 invariants)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import invariants


def mk_state(pos=(0, 0), hunger=100, hunger_s=None, light_s=None, counts=None, health=150):
    s = {
        "pos": {"x": pos[0], "z": pos[1]},
        "hunger": [hunger, 150],
        "health": [health, 150],
        "item_counts": counts or {},
    }
    if hunger_s is not None:
        s["hunger_seconds_remaining"] = hunger_s
    if light_s is not None:
        s["seconds_until_night"] = light_s
    return s


class TestLeash(unittest.TestCase):
    def test_close_and_fed_passes(self):
        s = mk_state(pos=(10, 0), hunger_s=600, light_s=600)
        self.assertTrue(invariants.can_get_home(s, (0, 0)))

    def test_far_with_little_hunger_fails(self):
        # 200 units away needs ~110s (200/6*1.5+60); only 80s of food left
        s = mk_state(pos=(200, 0), hunger_s=80, light_s=9999)
        self.assertFalse(invariants.can_get_home(s, (0, 0)))

    def test_far_but_enough_food_passes(self):
        # 200 units needs ~110s; 300s of food is plenty
        s = mk_state(pos=(200, 0), hunger_s=300, light_s=9999)
        self.assertTrue(invariants.can_get_home(s, (0, 0)))

    def test_night_deadline_blocks(self):
        # far away AND night coming in 70s (< 110s needed) -> blocked
        s = mk_state(pos=(200, 0), hunger_s=600, light_s=70)
        self.assertFalse(invariants.can_get_home(s, (0, 0)))

    def test_no_base_returns_true(self):
        s = mk_state(pos=(500, 0), hunger_s=10, light_s=10)
        self.assertTrue(invariants.can_get_home(s, None))  # no anchor: fail-open

    def test_hunger_estimate_fallback(self):
        # hunger 30 -> ~96s remaining (30/0.3125); 200 units needs ~33s*1.5+60=110
        s = mk_state(pos=(200, 0), hunger=30)
        self.assertFalse(invariants.can_get_home(s, (0, 0)))

    def test_garbage_state_no_crash(self):
        self.assertTrue(invariants.can_get_home(None, (0, 0)))
        self.assertTrue(invariants.can_get_home({}, (0, 0)))


class TestDamageRetreat(unittest.TestCase):
    def test_small_drop_no_retreat(self):
        s = mk_state(health=148)
        self.assertFalse(invariants.unexplained_damage(s, 150))

    def test_big_drop_retreat(self):
        s = mk_state(health=140)
        self.assertTrue(invariants.unexplained_damage(s, 150))

    def test_fighting_suppresses(self):
        s = mk_state(health=100)
        self.assertFalse(invariants.unexplained_damage(s, 150, deliberately_fighting=True))

    def test_healed_no_retreat(self):
        s = mk_state(health=150)
        self.assertFalse(invariants.unexplained_damage(s, 140))


class TestFoodReserve(unittest.TestCase):
    def test_reserves_two(self):
        s = mk_state(counts={"berries": 3, "carrot": 1})
        r = invariants.reserve_food_ids(s)
        self.assertEqual(len(r), 2)  # two identities reserved

    def test_less_than_two_reserves_what_exists(self):
        s = mk_state(counts={"carrot": 1})
        self.assertEqual(len(invariants.reserve_food_ids(s)), 1)

    def test_none_reserves_nothing(self):
        self.assertEqual(invariants.reserve_food_ids(mk_state(counts={})), set())

    def test_emergency_available(self):
        s = mk_state(counts={"berries": 1, "log": 3})
        self.assertEqual(invariants.emergency_food_available(s), ["berries"])


class TestCampfireKit(unittest.TestCase):
    def test_full_kit(self):
        s = mk_state(counts={"cutgrass": 3, "log": 2})
        self.assertTrue(invariants.has_campfire_kit(s))
        self.assertEqual(invariants.missing_campfire_kit(s), {})

    def test_missing_log(self):
        s = mk_state(counts={"cutgrass": 3, "log": 1})
        self.assertFalse(invariants.has_campfire_kit(s))
        self.assertEqual(invariants.missing_campfire_kit(s), {"log": 1})

    def test_missing_both(self):
        s = mk_state(counts={"twigs": 5})
        self.assertEqual(invariants.missing_campfire_kit(s), {"cutgrass": 3, "log": 2})

    def test_plan_message(self):
        s = mk_state(counts={"cutgrass": 1})
        self.assertIn("2 cutgrass", invariants.kit_priority_plan(s))
        self.assertIn("2 log", invariants.kit_priority_plan(s))
        self.assertEqual(invariants.kit_priority_plan(mk_state(counts={"cutgrass": 3, "log": 2})),
                         "campfire kit complete")

    def test_garbage_no_crash(self):
        self.assertFalse(invariants.has_campfire_kit(None))
        self.assertEqual(invariants.missing_campfire_kit({}), {"cutgrass": 3, "log": 2})


if __name__ == "__main__":
    unittest.main(verbosity=2)
