#!/usr/bin/env python3
"""Tests for lib/plan.py (v9 state machine)."""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import plan


def S(counts=None, equipped=None, items=None, fires=None):
    return {"item_counts": counts or {}, "equipped": equipped or [],
            "items": items or list((counts or {}).keys()), "fires": fires or []}


class TestPlanResolver(unittest.TestCase):
    def test_empty_state_first_action_gathers_axe_material(self):
        # axe step needs twigs+flint -> resolver returns the first missing
        # material (twigs from sapling). It NEVER crafts before materials exist.
        a = plan.next_action(S())
        self.assertIn(a, [
            {"action": "gather_job", "prefab": "sapling"},
            {"action": "gather_job", "prefab": "flint"},
        ])

    def test_ready_materials_craft_axe(self):
        a = plan.next_action(S(counts={"twigs": 1, "flint": 1}))
        self.assertEqual(a, {"action": "craft", "recipe": "axe"})

    def test_has_twigs_gathers_flint_for_axe(self):
        a = plan.next_action(S(counts={"twigs": 1}))
        self.assertEqual(a, {"action": "gather_job", "prefab": "flint"})

    def test_has_flint_gathers_twigs_for_axe(self):
        a = plan.next_action(S(counts={"flint": 1}))
        self.assertEqual(a, {"action": "gather_job", "prefab": "sapling"})

    def test_axe_done_then_torch_kit_materials(self):
        a = plan.next_action(S(counts={"twigs": 1, "flint": 1}, items=["twigs","flint"],
                               equipped=["axe"]))
        # axe crafted+equipped -> next is torch_kit: needs 2 twigs + 2 cutgrass
        # already has 1 twig -> gather 1 more twig (sapling) or cutgrass
        self.assertIn(a, [
            {"action": "gather_job", "prefab": "sapling"},
            {"action": "gather_job", "prefab": "grass"},
        ])

    def test_no_axe_first_action_never_trees(self):
        # even with kit materials, without an axe the resolver never returns
        # a tree-chop; it resolves torch_kit (needs twigs) or the axe first
        a = plan.next_action(S(counts={"cutgrass": 3, "log": 2}))
        self.assertNotEqual(a, {"action": "gather_job", "prefab": "evergreen"})
        self.assertIn(a, [
            {"action": "gather_job", "prefab": "sapling"},   # torch_kit twigs
            {"action": "gather_job", "prefab": "grass"},     # torch_kit cutgrass
            {"action": "craft", "recipe": "axe"},
        ])

    def test_torch_then_food_flow(self):
        # torch_kit satisfied -> food step comes next (1 berry + 1 carrot)
        a = plan.next_action(S(counts={"cutgrass": 3, "twigs": 2},
                               equipped=["axe"]))
        self.assertIn(a, [
            {"action": "gather_job", "prefab": "berrybush"},
            {"action": "gather_job", "prefab": "carrot_planted"},
        ])

    def test_axe_then_torch_kit_materials(self):
        # axe crafted but torch_kit incomplete -> gather its materials
        a = plan.next_action(S(counts={"twigs": 1, "flint": 1},
                               equipped=["axe"]))
        self.assertIn(a, [
            {"action": "gather_job", "prefab": "sapling"},
            {"action": "gather_job", "prefab": "grass"},
        ])

    def test_full_plan_returns_none(self):
        st = S(counts={"twigs": 5, "flint": 3, "cutgrass": 5, "log": 3, "rocks": 12,
                       "rope": 1, "berries": 1, "carrot": 1},
               equipped=["axe", "pickaxe", "spear"],
               fires=[{"d": 2, "fuel_pct": 80}])
        # base_site gate needs get_base() - mock it
        with mock.patch.object(plan, "get_base", return_value={"x": 0, "z": 0}):
            a = plan.next_action(st)
        self.assertIsNone(a)

    def test_rope_recurses_to_cutgrass(self):
        # spear needs rope; rope needs 3 cutgrass; no cutgrass -> gather grass
        a = plan.next_action(S(counts={"twigs": 2, "flint": 1}, equipped=["axe"]))
        # axe done -> torch kit (needs cutgrass/twigs) comes first in the plan,
        # so this should be grass or sapling
        self.assertIn(a, [
            {"action": "gather_job", "prefab": "grass"},
            {"action": "gather_job", "prefab": "sapling"},
        ])

    def test_garbage_state_no_crash(self):
        # garbage inputs never raise; they resolve to a safe gather action
        for bad in (None, "x", 42, {}):
            a = plan.next_action(bad)
            self.assertTrue(isinstance(a, dict) or a is None)
        # empty inventory -> gather twigs for the axe (plan unfinished)
        self.assertEqual(plan.next_action({}), {"action": "gather_job", "prefab": "sapling"})

    def test_plan_remaining_lists_unsatisfied(self):
        rem = plan.plan_remaining(S())
        self.assertIn("axe", rem)
        self.assertIn("torch_kit", rem)


if __name__ == "__main__":
    unittest.main(verbosity=2)
