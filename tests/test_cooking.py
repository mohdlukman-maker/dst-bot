#!/usr/bin/env python3
"""Tests for lib/cooking.py (Crock Pot evaluator)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from lib import cooking


class TestCooking(unittest.TestCase):
    def test_meatballs_monstermeat_and_berries(self):
        counts = {"monstermeat": 1, "berries": 3}
        ing = cooking.can_cook_meatballs(counts)
        self.assertIsNotNone(ing)
        self.assertEqual(len(ing), 4)
        self.assertEqual(ing[0], "monstermeat")
        self.assertEqual(ing[1:], ["berries", "berries", "berries"])

    def test_meatballs_with_ice(self):
        counts = {"smallmeat": 1, "ice": 3}
        ing = cooking.can_cook_meatballs(counts)
        self.assertIsNotNone(ing)
        self.assertEqual(ing[0], "smallmeat")
        self.assertEqual(ing[1:], ["ice", "ice", "ice"])

    def test_meatballs_missing_filler(self):
        counts = {"meat": 1, "berries": 2}
        ing = cooking.can_cook_meatballs(counts)
        self.assertIsNone(ing)

    def test_meatballs_missing_meat(self):
        counts = {"berries": 10, "ice": 10}
        ing = cooking.can_cook_meatballs(counts)
        self.assertIsNone(ing)

    def test_pierogi_complete(self):
        counts = {"monstermeat": 1, "bird_egg": 1, "carrot": 1, "twigs": 1}
        ing = cooking.can_cook_pierogi(counts)
        self.assertIsNotNone(ing)
        self.assertEqual(ing, ["monstermeat", "bird_egg", "carrot", "twigs"])

    def test_best_crockpot_prioritizes_pierogi_when_low_health(self):
        counts = {"monstermeat": 2, "bird_egg": 1, "carrot": 1, "twigs": 1, "berries": 5}
        # Low health (<60%) -> Pierogi
        recipe, ings = cooking.best_crockpot_recipe(counts, health_pct=0.4)
        self.assertEqual(recipe, "pierogi")

        # Healthy (>60%) -> Meatballs
        recipe, ings = cooking.best_crockpot_recipe(counts, health_pct=0.9)
        self.assertEqual(recipe, "meatballs")


if __name__ == "__main__":
    unittest.main()
