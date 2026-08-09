#!/usr/bin/env python3
"""
lib/plan.py — the Day-1 plan as data + recursive precondition resolver (v9).

"Every action declares its preconditions, and the planner satisfies them
recursively before acting." Bug A (chop without axe), B (explore without
memory) and C (retry picked targets) are all the same bug: goals with no
preconditions. This module makes preconditions structural.

next_action(state) returns the ONE command to fire right now, or None when the
plan is complete (the only legal time to roam). Never raises.
"""
from dataclasses import dataclass, field
from functools import lru_cache


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


# ---------------------------------------------------------------- the plan
@dataclass
class Step:
    name:   str
    tools:  list = field(default_factory=list)   # must be IN INVENTORY first
    needs:  dict = field(default_factory=dict)   # item -> count required
    action: dict = None                          # command to fire when ready
    gate:   callable = None                      # done-ness test (state) -> bool
    note:   str = ""


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


SOURCES = {
    "twigs":    {"prefab": "sapling",   "tool": None},
    "cutgrass": {"prefab": "grass",     "tool": None},
    "flint":    {"prefab": "flint",     "tool": None},
    "log":      {"prefab": "evergreen", "tool": "axe"},
    "rocks":    {"prefab": "rock1",     "tool": "pickaxe"},
    "rope":     {"recipe": "rope",      "needs": {"cutgrass": 3}},
    "berries":  {"prefab": "berrybush",  "tool": None},
    "carrot":   {"prefab": "carrot_planted", "tool": None},
}


def step_producing(item):
    """The plan step whose action produces `item` (or named `item`)."""
    for st in DAY1_PLAN:
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


def next_action(state, depth=0):
    """The ONE command to fire right now, or None when the plan is complete."""
    try:
        if depth > 6:
            return None
        for step in DAY1_PLAN:
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
        return [st.name for st in DAY1_PLAN
                if not (st.gate and st.gate(state))]
    except Exception:
        return []
