#!/usr/bin/env python3
"""Tests for 100-Day Roadmap multi-phase progression in lib/plan.py."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import plan


def S(day=1, season="autumn", counts=None, equipped=None, items=None, fires=None, nearby=None):
    return {
        "day": day,
        "season": season,
        "item_counts": counts or {},
        "equipped": equipped or [],
        "items": items or list((counts or {}).keys()),
        "fires": fires or [],
        "nearby": nearby or [],
        "allow_multiphase": True,
    }


class Test100DayRoadmap(unittest.TestCase):
    def test_day_1_satisfaction_transitions_to_science_machine_on_day_6(self):
        # Day 1 complete, on day 6 -> next goal is science_machine
        st = S(day=6, season="autumn",
               counts={"twigs": 10, "flint": 5, "cutgrass": 10, "log": 10, "rocks": 15, "berries": 2, "carrot": 2},
               equipped=["axe", "pickaxe", "spear"],
               fires=[{"d": 2, "fuel_pct": 80}])
        
        with mock.patch.object(plan, "get_base", return_value={"x": 0, "z": 0}):
            a = plan.next_action(st)
            # Missing gold for science machine -> gathers gold (rock2)
            self.assertEqual(a, {"action": "gather_job", "prefab": "rock2"})

    def test_winter_prep_phase_triggers_heatrock(self):
        # Day 18 (late Autumn) -> Thermal Stone (heatrock) is prioritized
        st = S(day=18, season="autumn",
               counts={"twigs": 10, "flint": 5, "cutgrass": 10, "log": 10, "rocks": 15, "berries": 2, "carrot": 2,
                       "goldnugget": 5, "backpack": 1, "armorwood": 1, "shovel": 1},
               equipped=["axe", "pickaxe", "spear"],
               fires=[{"d": 2, "fuel_pct": 80}],
               nearby=[
                   {"n": "researchlab", "d": 5},
                   {"n": "researchlab2", "d": 5},
                   {"n": "cookpot", "d": 5},
                   {"n": "treasurechest", "d": 5},
                   {"n": "lightning_rod", "d": 5},
               ])

        with mock.patch.object(plan, "get_base", return_value={"x": 0, "z": 0}):
            a = plan.next_action(st)
            # Rocks >= 10, pickaxe owned, flint >= 3 -> crafts heatrock
            self.assertEqual(a, {"action": "craft", "recipe": "heatrock"})

    def test_spring_prep_triggers_umbrella(self):
        # Spring -> Umbrella / Eyebrella waterproofing
        st = S(day=38, season="spring",
               counts={"twigs": 10, "flint": 5, "cutgrass": 10, "log": 10, "rocks": 15, "berries": 2, "carrot": 2,
                       "goldnugget": 5, "heatrock": 1, "winterhat": 1, "ice": 15, "log": 30,
                       "pigskin": 1, "silk": 2, "shovel": 1},
               equipped=["axe", "pickaxe", "spear", "backpack", "armorwood"],
               fires=[{"d": 2, "fuel_pct": 80}],
               nearby=[
                   {"n": "researchlab", "d": 5},
                   {"n": "researchlab2", "d": 5},
                   {"n": "cookpot", "d": 5},
                   {"n": "treasurechest", "d": 5},
                   {"n": "lightning_rod", "d": 5},
               ])

        with mock.patch.object(plan, "get_base", return_value={"x": 0, "z": 0}):
            a = plan.next_action(st)
            # Crafts umbrella for rain defense
            self.assertEqual(a, {"action": "craft", "recipe": "umbrella"})


if __name__ == "__main__":
    unittest.main()
