#!/usr/bin/env python3
"""
lib/cooking.py — Crock Pot recipe evaluator and food economy optimizer.

Wilson's survival past Day 20 depends on Crock Pot cooking:
- Raw berries / carrots rot quickly and provide low hunger.
- Meatballs (1 meat + 3 filler/ice/berries) = 62.5 hunger (+3 HP, +5 sanity).
- Pierogi (1 meat + 1 egg + 1 veggie + 1 filler) = 40 HP (+37.5 hunger, +5 sanity).
- Bacon and Eggs (2 eggs + 1.5 meat value) = 75 hunger (+20 HP).

This module determines what recipe can be cooked from current inventory items.
"""
from typing import Dict, List, Optional, Tuple

# Meat value table in DST
MEAT_VALUES: Dict[str, float] = {
    "meat": 1.0,
    "cookedmeat": 1.0,
    "meat_dried": 1.0,
    "monstermeat": 1.0,
    "cookedmonstermeat": 1.0,
    "monstermeat_dried": 1.0,
    "smallmeat": 0.5,
    "cookedsmallmeat": 0.5,
    "cooked_smallmeat": 0.5,
    "smallmeat_dried": 0.5,
    "drumstick": 0.5,
    "cooked_drumstick": 0.5,
    "froglegs": 0.5,
    "cookedfroglegs": 0.5,
    "fish": 1.0,
    "eel": 1.0,
}

# Fillers and vegetables
VEGGIE_ITEMS = {"carrot", "carrot_cooked", "corn", "pumpkin", "eggplant", "red_mushroom", "blue_mushroom", "green_mushroom"}
FRUIT_ITEMS = {"berries", "berries_cooked", "berries_juicy", "pomegranate", "dragonfruit"}
EGG_ITEMS = {"bird_egg", "bird_egg_cooked", "tallbirdegg", "rottenegg"}
ICE_ITEMS = {"ice"}
SWEETENER_ITEMS = {"honey", "honeycomb"}

# Invalids / non-food fillers in Crock Pot
TWIGS_ITEM = "twigs"


def get_meat_count(counts: Dict[str, int]) -> float:
    """Calculates total meat value in inventory."""
    total = 0.0
    for item, qty in (counts or {}).items():
        if item in MEAT_VALUES and qty > 0:
            total += MEAT_VALUES[item] * qty
    return total


def get_filler_count(counts: Dict[str, int]) -> int:
    """Calculates total generic safe filler items (berries, ice, carrots, mushrooms)."""
    total = 0
    safe_fillers = FRUIT_ITEMS | ICE_ITEMS | VEGGIE_ITEMS
    for item, qty in (counts or {}).items():
        if item in safe_fillers:
            total += int(qty)
    return total


def can_cook_meatballs(counts: Dict[str, int]) -> Optional[List[str]]:
    """
    Checks if Meatballs can be cooked (1 meat + 3 fillers).
    Formula: Meat value > 0, No twigs, Monster meat <= 1.
    Returns the list of 4 ingredient names to use, or None.
    """
    counts_copy = dict(counts or {})
    # Find 1 meat item
    chosen_meat = None
    # Prefer monster meat first (safe in meatballs with 1 max), then small meat, then big meat
    meat_priority = ["monstermeat", "cookedmonstermeat", "smallmeat", "cookedsmallmeat", "cooked_smallmeat", "drumstick", "meat", "cookedmeat", "froglegs"]
    for m in meat_priority:
        if counts_copy.get(m, 0) > 0:
            chosen_meat = m
            counts_copy[m] -= 1
            break

    if not chosen_meat:
        return None

    # Find 3 fillers (ice, berries, carrots, mushrooms)
    chosen_fillers = []
    filler_priority = ["ice", "berries", "berries_cooked", "carrot", "carrot_cooked", "red_mushroom", "blue_mushroom", "green_mushroom"]
    for f in filler_priority:
        while counts_copy.get(f, 0) > 0 and len(chosen_fillers) < 3:
            chosen_fillers.append(f)
            counts_copy[f] -= 1
        if len(chosen_fillers) == 3:
            break

    if len(chosen_fillers) == 3:
        return [chosen_meat] + chosen_fillers
    return None


def can_cook_pierogi(counts: Dict[str, int]) -> Optional[List[str]]:
    """
    Checks if Pierogi can be cooked (1 meat + 1 egg + 1 veggie + 1 filler).
    Heals 40 HP! Essential for surviving hounds and winter boss fights.
    """
    counts_copy = dict(counts or {})
    # 1. Meat
    chosen_meat = None
    for m in ["monstermeat", "smallmeat", "meat", "drumstick", "froglegs"]:
        if counts_copy.get(m, 0) > 0:
            chosen_meat = m
            counts_copy[m] -= 1
            break
    if not chosen_meat:
        return None

    # 2. Egg
    chosen_egg = None
    for e in EGG_ITEMS:
        if counts_copy.get(e, 0) > 0:
            chosen_egg = e
            counts_copy[e] -= 1
            break
    if not chosen_egg:
        return None

    # 3. Veggie
    chosen_veggie = None
    for v in VEGGIE_ITEMS:
        if counts_copy.get(v, 0) > 0:
            chosen_veggie = v
            counts_copy[v] -= 1
            break
    if not chosen_veggie:
        return None

    # 4. Any filler (ice, berries, veggie, twigs)
    chosen_filler = None
    for f in ["twigs", "ice", "berries", "carrot"]:
        if counts_copy.get(f, 0) > 0:
            chosen_filler = f
            counts_copy[f] -= 1
            break
    if not chosen_filler:
        return None

    return [chosen_meat, chosen_egg, chosen_veggie, chosen_filler]


def best_crockpot_recipe(counts: Dict[str, int], health_pct: float = 1.0) -> Optional[Tuple[str, List[str]]]:
    """
    Returns (recipe_name, [ingredients]) based on current health priority.
    If health is low (<50%), prioritizes Pierogi (40 HP).
    Otherwise, prioritizes Meatballs (62.5 Hunger).
    """
    if health_pct < 0.6:
        p_ing = can_cook_pierogi(counts)
        if p_ing:
            return ("pierogi", p_ing)

    mb_ing = can_cook_meatballs(counts)
    if mb_ing:
        return ("meatballs", mb_ing)

    p_ing = can_cook_pierogi(counts)
    if p_ing:
        return ("pierogi", p_ing)

    return None
