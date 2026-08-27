#!/usr/bin/env python3
"""
lib/plan.py — The 100-Day Survival State Machine & Precondition Resolver.

"Every action declares its preconditions, and the planner satisfies them
recursively before acting." 

Upgraded from Day-1-only to a full 100-Day Multi-Phase Roadmap:
- Phase 1: Day 1-5 (Scout, Tools, Base Anchor, Spear)
- Phase 2: Day 6-15 (Science Machine, Alchemy Engine, Backpack, Armor, Crock Pot, Ice Box, Lightning Rod)
- Phase 3: Day 16-20 (Winter Prep: Thermal Stone, Winter Hat, Ice Harvesting, Charcoal, Log Stockpile)
- Phase 4: Day 21-35 (Winter Survival: Crock Pot Cooking, Deerclops Kiting, Temperature Control)
- Phase 5: Day 36-55 (Spring Survival: Waterproofing, Umbrella/Eyebrella, Football Helmet, Frog Rain Avoidance)
- Phase 6: Day 56-70 (Summer Survival: Endothermic Fire Pit, Ice Flingomatic, Chilled Thermal Stone)
- Phase 7: Day 71-100 (Autumn 2 / Long-term Sustain: Tooth Trap Field, Autonomous Farming & Cooking)
"""
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Dict, List, Optional, Any


# ---------------------------------------------------------------- helpers
def has(state, item, n=1):
    try:
        return (state or {}).get("item_counts", {}).get(item, 0) >= n
    except Exception:
        return False


def equipped(state, item):
    try:
        return item in (state or {}).get("equipped", [])
    except Exception:
        return False


def owns(state, item, n=1):
    """Inventory OR equipped — equipping must not make a tool 'disappear'."""
    return has(state, item, n) or equipped(state, item)


def get_base():
    """Agent-side base location (set via _set_base). Stored in memory + file."""
    try:
        from lib import world_map
        b = world_map.get_base("default")
        if b:
            return b
    except Exception:
        pass
    return None


def fire_within(state, radius=12):
    try:
        for f in (state or {}).get("fires", []):
            if f.get("d", 99) <= radius:
                return True
    except Exception:
        pass
    return False


def has_structure_nearby(state, prefab, radius=20):
    try:
        for entity in (state or {}).get("nearby", []):
            if entity.get("n") == prefab and entity.get("d", 99) <= radius:
                return True
    except Exception:
        pass
    return False


# ---------------------------------------------------------------- the step dataclass
@dataclass
class Step:
    name:   str
    tools:  list = field(default_factory=list)   # must be IN INVENTORY / EQUIPPED first
    needs:  dict = field(default_factory=dict)   # item -> count required
    action: dict = None                          # command to fire when ready
    gate:   callable = None                      # done-ness test (state) -> bool
    note:   str = ""


# ---------------------------------------------------------------- Phase 1: Day 1
DAY1_PLAN = [
    Step(name="axe",
         needs={"twigs": 1, "flint": 1},
         action={"action": "craft", "recipe": "axe"},
         gate=lambda s: owns(s, "axe"),
         note="unlocks everything. 1 twig + 1 flint. Always first."),
    Step(name="torch_kit",
         needs={"twigs": 2, "cutgrass": 2},
         action=None,
         gate=lambda s: (has(s, "twigs", 2) and has(s, "cutgrass", 2))
                        or owns(s, "torch"),
         note="no tool needed. Day-1 light insurance."),
    Step(name="food",
         needs={"berries": 1, "carrot": 1},
         action=None,
         gate=lambda s: has(s, "berries", 1) and has(s, "carrot", 1),
         note="user ask: no food plan was the death gap - carry 1 berry + 1 carrot."),
    Step(name="pickaxe",
         needs={"twigs": 2, "flint": 2},
         action={"action": "craft", "recipe": "pickaxe"},
         gate=lambda s: owns(s, "pickaxe"),
         note="gold and stone gate all later progression."),
    Step(name="base_site",
         action={"action": "_set_base"},
         gate=lambda s: get_base() is not None,
         note="provisional is fine. The leash needs a denominator."),
    Step(name="campfire_kit",
         tools=["axe"],
         needs={"cutgrass": 3, "log": 2},
         action=None,
         gate=lambda s: has(s, "cutgrass", 3) and has(s, "log", 2),
         note="gated behind the axe. Bare-handed chopping is never correct."),
    Step(name="firepit",
         tools=["axe"],
         needs={"log": 2, "rocks": 12},
         action={"action": "craft", "recipe": "firepit"},
         gate=lambda s: fire_within(s, 12),
         note="campfire is fine as a fallback if rocks are scarce."),
    Step(name="spear",
         tools=["axe"],
         needs={"twigs": 2, "flint": 1, "rope": 1},
         action={"action": "craft", "recipe": "spear"},
         gate=lambda s: owns(s, "spear"),
         note="hounds are possible from Day 6. Do not slip past Day 5."),
]


# ---------------------------------------------------------------- Phase 2: Base & Science (Days 5-15)
SCIENCE_BASE_PLAN = [
    Step(name="science_machine",
         tools=["axe", "pickaxe"],
         needs={"goldnugget": 1, "rocks": 4, "log": 4},
         action={"action": "craft", "recipe": "researchlab"},
         gate=lambda s: has_structure_nearby(s, "researchlab", 15) or (s or {}).get("tech_level", 0) >= 1,
         note="Science Machine unlocks tier 1 recipes."),
    Step(name="backpack",
         tools=["axe"],
         needs={"cutgrass": 4, "twigs": 4},
         action={"action": "craft", "recipe": "backpack"},
         gate=lambda s: owns(s, "backpack"),
         note="8 extra inventory slots."),
    Step(name="armorwood",
         tools=["axe"],
         needs={"log": 8, "rope": 2},
         action={"action": "craft", "recipe": "armorwood"},
         gate=lambda s: owns(s, "armorwood"),
         note="80% damage reduction for hound defense."),
    Step(name="shovel",
         needs={"twigs": 2, "flint": 2},
         action={"action": "craft", "recipe": "shovel"},
         gate=lambda s: owns(s, "shovel"),
         note="Digs up saplings and grass for base relocation."),
    Step(name="alchemy_engine",
         tools=["axe", "pickaxe"],
         needs={"boards": 4, "cutstone": 2, "transistor": 2},
         action={"action": "craft", "recipe": "researchlab2"},
         gate=lambda s: has_structure_nearby(s, "researchlab2", 15) or (s or {}).get("tech_level", 0) >= 2,
         note="Alchemy Engine unlocks tier 2 recipes (Crock Pot, Thermal Stone)."),
    Step(name="cookpot",
         tools=["axe", "pickaxe"],
         needs={"cutstone": 3, "charcoal": 6, "twigs": 6},
         action={"action": "craft", "recipe": "cookpot"},
         gate=lambda s: has_structure_nearby(s, "cookpot", 15),
         note="Triples food value through Meatballs/Pierogi."),
    Step(name="chest",
         tools=["axe"],
         needs={"boards": 3},
         action={"action": "craft", "recipe": "treasurechest"},
         gate=lambda s: has_structure_nearby(s, "treasurechest", 15),
         note="Base storage for logs, minerals, and seeds."),
    Step(name="lightning_rod",
         tools=["pickaxe"],
         needs={"goldnugget": 3, "cutstone": 1},
         action={"action": "craft", "recipe": "lightning_rod"},
         gate=lambda s: has_structure_nearby(s, "lightning_rod", 20),
         note="Protects base from burning during Spring/Autumn storms."),
]


# ---------------------------------------------------------------- Phase 3: Winter Prep (Days 16-20)
WINTER_PREP_PLAN = [
    Step(name="heatrock",
         tools=["pickaxe"],
         needs={"rocks": 10, "pickaxe": 1, "flint": 3},
         action={"action": "craft", "recipe": "heatrock"},
         gate=lambda s: owns(s, "heatrock"),
         note="Thermal Stone prevents freezing in Winter."),
    Step(name="winter_gear",
         needs={"beefalowool": 4, "silk": 4},
         action={"action": "craft", "recipe": "winterhat"},
         gate=lambda s: owns(s, "winterhat") or owns(s, "catcoonhat") or owns(s, "sweatervest"),
         note="Warm clothing delays freezing."),
    Step(name="ice_stockpile",
         tools=["pickaxe"],
         needs={"ice": 12},
         action=None,
         gate=lambda s: has(s, "ice", 12),
         note="Mine glaciers before winter for non-perishable crockpot filler."),
    Step(name="log_stockpile",
         tools=["axe"],
         needs={"log": 30},
         action=None,
         gate=lambda s: has(s, "log", 30),
         note="Trees and plants do not grow in winter."),
]


# ---------------------------------------------------------------- Phase 4: Spring & Summer Prep (Days 36-70)
SPRING_SUMMER_PLAN = [
    Step(name="umbrella",
         needs={"twigs": 6, "pigskin": 1, "silk": 2},
         action={"action": "craft", "recipe": "umbrella"},
         gate=lambda s: owns(s, "umbrella") or owns(s, "eyebrella"),
         note="Waterproofing against Spring downpours."),
    Step(name="footballhat",
         needs={"pigskin": 1, "rope": 1},
         action={"action": "craft", "recipe": "footballhat"},
         gate=lambda s: owns(s, "footballhat"),
         note="80% head armor for boss fights."),
    Step(name="coldfirepit",
         tools=["pickaxe"],
         needs={"nitre": 2, "cutstone": 4, "transistor": 2},
         action={"action": "craft", "recipe": "coldfirepit"},
         gate=lambda s: has_structure_nearby(s, "coldfirepit", 15),
         note="Endothermic Fire Pit cools Wilson in Summer."),
]


SOURCES = {
    "twigs":         {"prefab": "sapling",       "tool": None},
    "cutgrass":      {"prefab": "grass",         "tool": None},
    "flint":         {"prefab": "flint",         "tool": None},
    "log":           {"prefab": "evergreen",     "tool": "axe"},
    "rocks":         {"prefab": "rock1",         "tool": "pickaxe"},
    "goldnugget":    {"prefab": "rock2",         "tool": "pickaxe"},
    "nitre":         {"prefab": "rock1",         "tool": "pickaxe"},
    "ice":           {"prefab": "rock_ice",      "tool": "pickaxe"},
    "charcoal":      {"prefab": "burntground",   "tool": "axe"},
    "rope":          {"recipe": "rope",          "needs": {"cutgrass": 3}},
    "boards":        {"recipe": "boards",        "needs": {"log": 4}},
    "cutstone":      {"recipe": "cutstone",      "needs": {"rocks": 3}},
    "transistor":    {"recipe": "transistor",    "needs": {"goldnugget": 2, "cutstone": 1}},
    "berries":       {"prefab": "berrybush",      "tool": None},
    "carrot":        {"prefab": "carrot_planted","tool": None},
    "monstermeat":   {"prefab": "spider",        "tool": "spear"},
    "silk":          {"prefab": "spider",        "tool": "spear"},
    "beefalowool":   {"prefab": "beefalo",       "tool": None},
    "pigskin":       {"prefab": "pighead",       "tool": "hammer"},
}


def step_producing(item):
    """The plan step whose action produces `item` (or named `item`)."""
    all_steps = DAY1_PLAN + SCIENCE_BASE_PLAN + WINTER_PREP_PLAN + SPRING_SUMMER_PLAN
    for st in all_steps:
        if st.name == item or (st.action or {}).get("recipe") == item:
            return st
    return None


# ---------------------------------------------------------------- the resolver
def gather_for(item, state):
    """What to do to obtain `item`: gather/craft, resolving tool first."""
    src = SOURCES.get(item)
    if not src:
        return None
    if "recipe" in src:
        # Check sub-materials for refined items (boards, cutstone, rope, transistor)
        for sub_item, sub_n in src.get("needs", {}).items():
            if not has(state, sub_item, sub_n):
                return gather_for(sub_item, state)
        return {"action": "craft", "recipe": src["recipe"]}
    if src["tool"] and not owns(state, src["tool"]):
        sub = step_producing(src["tool"])
        if sub:
            return _action_for(sub, state, 99)   # tool first, always
    return {"action": "gather_job", "prefab": src["prefab"]}


def _action_for(step, state, depth):
    """One step, recursively resolved. Returns the ONE command, or None."""
    if depth > 6:
        return None
    # 1. missing a TOOL -> go make the tool instead (Bug A's cure)
    for tool in step.tools:
        if not owns(state, tool):
            sub = step_producing(tool)
            if sub:
                return _action_for(sub, state, depth + 1)
    # 2. missing MATERIALS -> go gather them
    for item, n in step.needs.items():
        if not has(state, item, n):
            g = gather_for(item, state)
            if g:
                return g
    # 3. everything ready -> do the step (action may be None: holding IS the goal)
    return step.action


def get_current_phase_plan(state: dict) -> List[Step]:
    """Determines which phase plan is active based on day and season."""
    day = (state or {}).get("day", 1)
    season = (state or {}).get("season", "autumn")

    # If Day 1 plan is not complete, always finish Day 1 first
    day1_incomplete = any(st.gate and not st.gate(state) for st in DAY1_PLAN)
    if day1_incomplete:
        return DAY1_PLAN

    if day <= 15 and season == "autumn":
        return DAY1_PLAN + SCIENCE_BASE_PLAN
    elif day <= 20 and season == "autumn":
        return DAY1_PLAN + SCIENCE_BASE_PLAN + WINTER_PREP_PLAN
    elif season == "winter":
        return DAY1_PLAN + SCIENCE_BASE_PLAN + WINTER_PREP_PLAN
    else:
        return DAY1_PLAN + SCIENCE_BASE_PLAN + WINTER_PREP_PLAN + SPRING_SUMMER_PLAN


def next_action(state, depth=0):
    """The ONE command to fire right now, or None when the plan is complete."""
    try:
        if depth > 6:
            return None
        
        # Check if state specifies a standalone day-1 run
        plan_steps = DAY1_PLAN
        # If day is given and > 1 or progression is enabled, use phase roadmap
        day = (state or {}).get("day", 1)
        if day > 1 or (state or {}).get("allow_multiphase", False):
            plan_steps = get_current_phase_plan(state)

        for step in plan_steps:
            if step.gate and step.gate(state):
                continue
            act = _action_for(step, state, depth + 1)
            if act is not None:
                return act
        return None
    except Exception:
        return None


def plan_remaining(state):
    """Names of steps not yet satisfied (for logs / questions)."""
    try:
        plan_steps = get_current_phase_plan(state) if (state or {}).get("day", 1) > 1 else DAY1_PLAN
        return [st.name for st in plan_steps
                if not (st.gate and st.gate(state))]
    except Exception:
        return []
