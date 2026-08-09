#!/usr/bin/env python3
"""
lib/invariants.py — the v8 survival invariants (Claude's #1 priority).

"Replace 'avoid known dangers' with two rules that don't care what the danger
is." These are gates, not preferences. Every public function returns a safe
default and never raises — this runs alongside the live game loop.

INVARIANT 1 (the leash):  never travel further out than you can get back from.
INVARIANT 2 (health-delta retreat): if health drops and you didn't choose a
    fight, disengage immediately.
INVARIANT 3 (emergency food reserve): N food items the normal eat rule is
    forbidden to touch. Only the emergency rule may spend them.
INVARIANT 4 (campfire kit): never travel without 3 cutgrass + 2 logs.

All are implementable purely in Python against the existing state dict —
none need a Lua change (that was the point of the v8 review).
"""
import math

WALK_SPEED = 6.0          # units/sec (to be measured; ~6 is the DST default)
RESERVE_SIZE = 2          # food items held in the emergency reserve
RESERVE_FOODS = ("berries", "carrot", "seeds", "cookedmeat", "cooked_smallmeat")
# v9 tiered kits: torch kit is the DAY-1 invariant (no tool), campfire kit is
# DAY-2+ (needs the axe). The v8 advice made the agent chop bare-handed.
TORCH_KIT = {"cutgrass": 2, "twigs": 2}
CAMPFIRE_KIT = {"cutgrass": 3, "log": 2}


def _num(v, default=0.0):
    return v if isinstance(v, (int, float)) else default


def _pos(state):
    p = state.get("pos") if isinstance(state, dict) else None
    if isinstance(p, dict):
        return _num(p.get("x")), _num(p.get("z"))
    return 0.0, 0.0


def distance(ax, az, bx, bz):
    return math.sqrt((ax - bx) ** 2 + (az - bz) ** 2)


# ---------------------------------------------------------------- INVARIANT 1
def can_get_home(state: dict, base_xz: tuple) -> bool:
    """
    The leash. True if Wilson can still return to base with the food and light
    he currently holds. If False, the only legal action is "head home".

    food_s:  hunger_seconds_remaining from state, or a conservative estimate
             (hunger_current / HUNGER_RATE ~= hunger / 0.3125)
    light_s: seconds_until_night, or 9999 if unknown
    """
    try:
        if not isinstance(state, dict) or base_xz is None:
            return True  # no leash anchor yet -> don't block everything
        p = state.get("pos")
        if not isinstance(p, dict) or "x" not in p or "z" not in p:
            return True  # no position -> can't judge the leash; fail open
        x, z = _pos(state)
        bx, bz = float(base_xz[0]), float(base_xz[1])
        dist = distance(x, z, bx, bz)
        travel_s = dist / WALK_SPEED

        hunger_s = state.get("hunger_seconds_remaining")
        if hunger_s is None:
            h = state.get("hunger")
            h_cur = _num(h[0] if isinstance(h, (list, tuple)) else h)
            if h_cur <= 0:
                return True  # no hunger data -> can't judge; fail open
            hunger_s = h_cur / 0.3125
        hunger_s = _num(hunger_s)

        light_s = _num(state.get("seconds_until_night"), 9999)
        if light_s <= 0:
            light_s = 9999  # unknown/not reported -> assume daylight is fine

        return (hunger_s > travel_s * 1.5 + 60 and
                light_s > travel_s * 1.5 + 60)
    except Exception:
        return True  # fail-open: never crash the caller, never trap the bot


# ---------------------------------------------------------------- INVARIANT 2
def unexplained_damage(state: dict, last_health: float, deliberately_fighting: bool = False) -> bool:
    """
    True if health dropped more than 2 points and we are NOT deliberately
    fighting. Caller should abort the current job and move away from the
    nearest _combat entity.
    """
    try:
        if deliberately_fighting:
            return False
        h = state.get("health")
        if isinstance(h, (list, tuple)) and h:
            cur = _num(h[0])
        else:
            cur = _num(h)
        return (last_health - cur) > 2.0
    except Exception:
        return False


# ---------------------------------------------------------------- INVARIANT 3
def reserve_food_ids(state: dict) -> set:
    """
    The item identities the normal eat rule is FORBIDDEN to touch.
    Identities are (prefab, slot) pairs so two berries are distinguishable.
    Returns a set; empty if state has no usable inventory structure.
    """
    try:
        items = state.get("items") or []
        counts = state.get("item_counts") or {}
        reserved = set()
        held = 0
        for prefab in RESERVE_FOODS:
            n = int(counts.get(prefab, 0) or 0)
            take = min(n, RESERVE_SIZE - held)
            for i in range(take):
                reserved.add((prefab, f"reserve-{i}"))
            held += take
            if held >= RESERVE_SIZE:
                break
        return reserved
    except Exception:
        return set()


def emergency_food_available(state: dict) -> list:
    """List of reserve food prefabs currently held (for the emergency rule only)."""
    try:
        counts = state.get("item_counts") or {}
        return [p for p in RESERVE_FOODS if int(counts.get(p, 0) or 0) > 0]
    except Exception:
        return []


# ---------------------------------------------------------------- INVARIANT 4
def has_campfire_kit(state: dict) -> bool:
    """True if Wilson carries the full campfire kit (3 cutgrass + 2 logs)."""
    try:
        counts = state.get("item_counts") or {}
        return all(int(counts.get(m, 0) or 0) >= c for m, c in CAMPFIRE_KIT.items())
    except Exception:
        return False


def missing_campfire_kit(state: dict) -> dict:
    """What's missing from the kit: {"cutgrass": 1, "log": 0} style."""
    try:
        counts = state.get("item_counts") or {}
        missing = {}
        for m, c in CAMPFIRE_KIT.items():
            have = int(counts.get(m, 0) or 0)
            if have < c:
                missing[m] = c - have
        return missing
    except Exception:
        return {}


def has_torch_kit(state: dict) -> bool:
    """Day-1 invariant: 2 cutgrass + 2 twigs (no tool needed)."""
    try:
        counts = state.get("item_counts") or {}
        return all(int(counts.get(m, 0) or 0) >= c for m, c in TORCH_KIT.items())
    except Exception:
        return False


def missing_torch_kit(state: dict) -> dict:
    try:
        counts = state.get("item_counts") or {}
        missing = {}
        for m, c in TORCH_KIT.items():
            have = int(counts.get(m, 0) or 0)
            if have < c:
                missing[m] = c - have
        return missing
    except Exception:
        return {}


def kit_priority_plan(state: dict) -> str:
    """Readable instruction when the kit is incomplete."""
    missing = missing_campfire_kit(state)
    if not missing:
        return "campfire kit complete"
    parts = [f"{c} {m}" for m, c in sorted(missing.items())]
    return "gather " + ", ".join(parts) + " (campfire kit)"
