#!/usr/bin/env python3
"""
LOCAL AGENT (user-designed architecture):
"all action need to be decided by ai, can it learn and save the action local?
 roam around should not be decided by ai after 1 roam, collecting and detecting
 the item has actually been collected should be local, it need to be teach like that"

PRINCIPLES:
1. LEARN LOCALLY  - every action result is saved to learnings.json (what worked,
   what failed, resource locations). No re-deciding what already works.
2. ROAM IS LOCAL  - the roam loop runs autonomously. AI decides strategy ONCE
   (craft priorities, base location), the agent executes the grind forever.
3. VERIFY LOCALLY - collection is confirmed by the agent (inventory delta +
   ground items) WITHOUT asking the AI "did it work?".
4. ESCALATE ONLY  - the AI hears about: new craft unlocked, base-worthy spot,
   near-death, night-without-light, or when asked. Everything else is silent.

The agent saves knowledge to:
  learnings.json  - what worked / what failed (per-prefab gather success)
  worldmap.json   - known resource locations (already exists)
  agent_state.json - current mode, target, last actions (resumable)
"""
import os, sys, time, re, json, math, random

# v8 invariants (Claude's #1 priority): leash, damage-retreat, campfire kit
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from lib import invariants
from lib import run_log as run_logger
from lib.targets import TargetTracker
from lib.explore import Explorer
from lib import decision_log as decision_logger

RUN_ID = run_logger.start_run(mod_version="v8-invariants", notes="autonomous agent run")
# Session A2 T4: publish the run id so the reflex's death records join this
# run instead of writing run_id="" (which also poisoned the _CLOSED guard).
# NOTE: HERE is defined BELOW this block - use __file__ directly.
try:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                           "data", "current_run.txt"), "w") as f:
        f.write(RUN_ID)
except Exception:
    pass
last_health = None   # module-level: shared by main() and _main_loop()
_damage_log_last = 0.0   # Session A2 T3: rate-limit the damage warning (2s)
_food_attempted = set()  # (guid, prefab) food spots tried this life (re-target guard)
_dusk_spots_attempted = set()  # v9.3:
_flee_biome_target = None  # v10: when fleeing a swamp, run toward base (120+ units) (x,z) world-map spots the dusk guard already tried (stale-spot loop guard)
_last_day_announced = 0   # milestone dialogue: highest day already spoken this life
DAY_MILESTONE_LINES = {   # the survival-curve markers from NEXT_SESSION.md's own goals
    5:  ["Day five. Statistically I should be dead by now."],
    10: ["Double digits. Nobody tell the last version of me."],
    15: ["Day fifteen. I'm starting to recognize the trees."],
    21: ["Winter, allegedly. Let's see if the prep was worth it."],
    35: ["Spring. I genuinely did not expect to see this."],
}
TRACKER = TargetTracker()  # Session A Task 3: GUID-based re-target guard (180s auto-clear)
EXPLORER = Explorer("default")  # Session A Task 4: visited-grid frontier explorer (persisted)

DOC = os.path.join(os.path.expanduser("~"), "Documents", "Klei", "DoNotStarveTogether")
CS = os.path.join(DOC, "40630831", "client_save")
STATE_F = os.path.join(CS, "dst_ai_bot_state")
CMD_F = os.path.join(CS, "dst_ai_bot_command")
RESULT_F = os.path.join(CS, "dst_ai_bot_result")
HERE = os.path.dirname(os.path.abspath(__file__))
LOG_F = os.path.join(HERE, "survival_log.txt")
LEARN_F = os.path.join(HERE, "learnings.json")
AGENT_F = os.path.join(HERE, "agent_state.json")

# ---------------- persistence: the "local learning" ----------------
def load_json(path, default):
    try:
        if os.path.exists(path):
            return json.load(open(path, encoding="utf-8"))
    except Exception:
        pass
    return default

def save_json(path, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
    except Exception:
        pass

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOG_F, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass

def get_state():
    try:
        raw = open(STATE_F, "rb").read()
        m = re.match(rb"KLEI\s+\d+\s+(.*)", raw, re.DOTALL)
        st = json.loads(m.group(1).decode())
        # derived scalar: nearest threat distance (flat key, _get_path-safe).
        # Tests the thing that matters for a flee - did the gap open? -
        # without list-index paths (threats[0] is not stable identity).
        threats = st.get("threats") or []
        st["nearest_threat_d"] = min((t.get("d", 999) for t in threats), default=999)
        # Session A2 T1: combined ownership map (item_counts + equipped).
        # Crafted tools auto-equip -> they live in equipslots, not itemslots,
        # so item_counts misses them and craft expectations score refuted
        # even when the craft succeeded.
        owned = dict(st.get("item_counts") or {})
        for it in (st.get("equipped") or []):
            owned[it] = owned.get(it, 0) + 1
        st["owned"] = owned
        return st
    except Exception:
        return {}

def set_game_paused(paused, reason=""):
    """v9.2: freeze/resume the sim for analysis. The mod's static poll keeps
    reading commands while paused, so unpause always arrives. Returns True if
    the command was accepted (dispatched), False on any error."""
    try:
        action = "pause" if paused else "unpause"
        r = send({"action": action, "reason": reason})
        # send() writes a file; give the mod a beat to process it
        time.sleep(1.5)
        return True
    except Exception as e:
        log(f"\u26D4 pause cmd error: {e}")
        return False

def game_is_paused():
    """Check the heartbeat file (real-time clock, survives pause)."""
    try:
        hb = load_json(os.path.join(os.path.dirname(STATE_F), "dst_ai_bot_heartbeat"), {})
        return bool(hb.get("paused"))
    except Exception:
        return False

def send(cmd):
    cmdid = int(time.time() * 1000)
    cmd = dict(cmd); cmd["id"] = cmdid
    try:
        with open(CMD_F, "wb") as f:
            f.write(b"KLEI     1 " + json.dumps(cmd).encode())
    except Exception:
        pass
    return cmdid

# ---------------- learnings: what the agent has LEARNED ----------------
def load_learnings():
    return load_json(LEARN_F, {"gather_success": {}, "gather_fail": {}, "lessons": []})

def learn_gather(prefab, succeeded):
    """Teach the agent: did gathering this prefab work? (local learning)"""
    L = load_learnings()
    key = "gather_success" if succeeded else "gather_fail"
    L[key][prefab] = L[key].get(prefab, 0) + 1
    save_json(LEARN_F, L)

def gather_reliability(prefab):
    """What has the agent LEARNED about this prefab? higher = more reliable"""
    L = load_learnings()
    s = L["gather_success"].get(prefab, 0)
    f = L["gather_fail"].get(prefab, 0)
    if s + f == 0: return 0.5   # unknown - assume mediocre
    return s / (s + f)

# ---------------- STRATEGY: plan stages (AI teaches; agent executes + asks) ----------------
# The agent works through STAGES. Each stage has explicit completion criteria.
# When a stage is COMPLETE or the agent is STUCK, it ASKS A QUESTION instead
# of roaming aimlessly (user correction: "always ask questions instead of just
# roaming without a plan").

PLAN_STAGES = [
    {
        "id": "tools",
        "desc": "Day 1: craft axe and pickaxe (survival basics)",
        "complete": lambda st: ("axe" in (st.get("equipped") or []) or "axe" in (st.get("item_counts") or {})) and
                               ("pickaxe" in (st.get("equipped") or []) or "pickaxe" in (st.get("item_counts") or {})),
        "asks_when_stuck": "I'm stuck in the tools stage (need axe/pickaxe). I can't find materials nearby. Where should I look?",
    },
    {
        "id": "campfire",
        "desc": "Build a campfire before night (3 cutgrass + 2 logs)",
        "complete": lambda st: len(st.get("fires") or []) > 0 or "torch" in (st.get("equipped") or []),
        "asks_when_stuck": "It's getting dark and I have no light. I need logs + cutgrass. What should I prioritize?",
    },
    {
        "id": "explore_base",
        "desc": "Explore to find a base-worthy spot (rocks/beefalo/grass, AWAY from swamp)",
        "complete": lambda st: (st.get("day") or 0) >= 3 and get_base() is not None,
        "asks_when_stuck": "I've explored several areas. Where should I set up base? (looking for: rocky biome, beefalo, grass, pig village, NOT near swamp)",
    },
    {
        "id": "science",
        "desc": "Day 3-5: build science machine (4 logs + 4 rocks + 1 gold) to unlock T1 recipes",
        "complete": lambda st: any(e.get("n") == "researchlab" and e.get("d", 99) <= 15 for e in (st.get("nearby") or [])) or
                               "researchlab" in (st.get("can_build") or []) or "spear" in (st.get("can_build") or []),
        "asks_when_stuck": "I need a science machine (4 logs + 4 rocks + 1 gold). I can't craft a spear or armor without it. Where can I find gold?",
    },
    {
        "id": "armor",
        "desc": "Day 5-6: spear + armorwood BEFORE hounds (day 6 attack)",
        "complete": lambda st: ("spear" in (st.get("item_counts") or {}) or "spear" in (st.get("equipped") or [])) and
                               ("armorwood" in (st.get("item_counts") or {}) or any("armor" in e for e in (st.get("equipped") or []))),
        "asks_when_stuck": "Hounds attack on day 6! I need a spear + armorwood NOW. I have the science machine but need materials. What should I gather?",
    },
    {
        "id": "alchemy_crockpot",
        "desc": "Day 7-14: Alchemy Engine + Crock Pot + Base Chests + Lightning Rod",
        "complete": lambda st: any(e.get("n") == "cookpot" and e.get("d", 99) <= 20 for e in (st.get("nearby") or [])) and
                               any(e.get("n") == "researchlab2" and e.get("d", 99) <= 20 for e in (st.get("nearby") or [])),
        "asks_when_stuck": "I need an Alchemy Engine and Crock Pot to cook Meatballs/Pierogi. Where can I get charcoal (burnt trees) and gold?",
    },
    {
        "id": "winter_prep",
        "desc": "Day 15-20: Thermal stone + Warm clothing + 30+ logs before winter (day 21)",
        "complete": lambda st: "heatrock" in (st.get("item_counts") or {}) or "heatrock" in (st.get("equipped") or []),
        "asks_when_stuck": "Winter is coming (day 21). I need a Thermal Stone (10 rocks + 1 pickaxe + 3 flint) and warm clothes. What's the priority?",
    },
    {
        "id": "winter_survival",
        "desc": "Day 21-35: Winter survival (Crock Pot cooking + Deerclops evasion on Day 30)",
        "complete": lambda st: (st.get("day") or 0) >= 36 or (st.get("season") == "spring"),
        "asks_when_stuck": "It is Winter! Maintain heated Thermal Stone near fire, cook Meatballs, and avoid Deerclops on Day 30.",
    },
    {
        "id": "spring_survival",
        "desc": "Day 36-55: Spring survival (Waterproofing with Umbrella/Eyebrella + Lightning Rod + Frog Rain Avoidance)",
        "complete": lambda st: (st.get("day") or 0) >= 56 or (st.get("season") == "summer"),
        "asks_when_stuck": "Spring rain causes wetness and sanity drain. Equip Umbrella and stay near base Lightning Rod.",
    },
    {
        "id": "summer_survival",
        "desc": "Day 56-70: Summer survival (Endothermic Fire Pit + Chilled Thermal Stone + Ice Flingomatic)",
        "complete": lambda st: (st.get("day") or 0) >= 71 or (st.get("season") == "autumn"),
        "asks_when_stuck": "Summer heat causes wildfires and overheating. Stay near Endothermic Fire Pit with chilled Thermal Stone.",
    },
    {
        "id": "sustain_100",
        "desc": "Day 71-100: Long-term sustain (Tooth Trap defense + Autonomous farming loop)",
        "complete": lambda st: (st.get("day") or 0) >= 100,
        "asks_when_stuck": "Scale Tooth Trap defenses and food preserves to reach the 100-day milestone!",
    },
]

CRAFT_PRIORITIES = [
    # Phase 1: Day 1 survival
    ("axe", {"twigs": 1, "flint": 1}),
    ("pickaxe", {"twigs": 2, "flint": 2}),
    ("torch", {"cutgrass": 2, "twigs": 2}),
    ("campfire", {"cutgrass": 3, "log": 2}),
    ("rope", {"cutgrass": 3}),
    ("boards", {"log": 4}),
    ("cutstone", {"rocks": 3}),
    ("transistor", {"goldnugget": 2, "cutstone": 1}),
    # Phase 2: Science Machine & Armor
    ("researchlab", {"log": 4, "rocks": 4, "goldnugget": 1}),
    ("spear", {"twigs": 2, "flint": 1, "rope": 1}),
    ("shovel", {"twigs": 2, "flint": 1, "rope": 1}),
    ("armorwood", {"log": 8, "rope": 2}),
    ("backpack", {"cutgrass": 4, "twigs": 4}),
    ("firepit", {"log": 2, "rocks": 12}),
    ("trap", {"twigs": 2, "cutgrass": 6}),
    # Phase 3: Alchemy Engine & Crock Pot
    ("researchlab2", {"boards": 4, "cutstone": 2, "transistor": 2}),
    ("cookpot", {"cutstone": 3, "charcoal": 6, "twigs": 6}),
    ("treasurechest", {"boards": 3}),
    ("lightning_rod", {"goldnugget": 3, "cutstone": 1}),
    # Phase 4: Winter & Seasonal Gear
    ("heatrock", {"rocks": 10, "pickaxe": 1, "flint": 3}),
    ("winterhat", {"beefalowool": 4, "silk": 4}),
    ("umbrella", {"twigs": 6, "pigskin": 1, "silk": 2}),
    ("footballhat", {"pigskin": 1, "rope": 1}),
    ("coldfirepit", {"nitre": 2, "cutstone": 4, "transistor": 2}),
    ("trap_teeth", {"log": 1, "rope": 1, "houndstooth": 1}),
]

# ---------------- QUESTION ESCALATION (ask, don't wander) ----------------
# The agent writes a question to agent_question.json and STOPS roaming.
# The AI answers in agent_answer.json; the agent resumes.
QUESTION_F = os.path.join(HERE, "agent_question.json")
ANSWER_F = os.path.join(HERE, "agent_answer.json")

# Module-level: is a question outstanding? (non-blocking questions, Session A)
_question_outstanding = False
_question_asked_at = 0.0

def ask_question_async(question, context=None):
    """Write the question. Do NOT wait. Returns immediately.
    Skips if a question is already outstanding and under 120s old."""
    global _question_outstanding, _question_asked_at
    now = time.time()
    if _question_outstanding and (now - _question_asked_at) < 120:
        return
    save_json(QUESTION_F, {
        "ts": now,
        "question": question,
        "context": context or {},
        "stage": current_stage_id(),
    })
    log(f"❓ QUESTION ASKED: {question}")
    _question_outstanding = True
    _question_asked_at = now

def check_answer():
    """Non-blocking. Returns the answer string if one has arrived, else None.
    Clears the outstanding flag when an answer is consumed."""
    global _question_outstanding
    try:
        if os.path.exists(ANSWER_F):
            ans = json.load(open(ANSWER_F, encoding="utf-8"))
            os.remove(ANSWER_F)
            _question_outstanding = False
            return ans.get("answer")
    except Exception:
        pass
    return None

def current_stage_id():
    ag = load_json(AGENT_F, {})
    return ag.get("stage", "tools")

def get_current_stage():
    sid = current_stage_id()
    for s in PLAN_STAGES:
        if s["id"] == sid:
            return s
    return PLAN_STAGES[0]

def next_stage():
    ag = load_json(AGENT_F, {})
    stages = [s["id"] for s in PLAN_STAGES]
    cur = ag.get("stage", "tools")
    try:
        idx = stages.index(cur)
        nxt = stages[idx+1] if idx+1 < len(stages) else stages[-1]
    except ValueError:
        nxt = "tools"
    ag["stage"] = nxt
    save_json(AGENT_F, ag)
    log(f"🔄 STAGE ADVANCED: {cur} -> {nxt}")
    # milestone dialogue (user request): real progress, spoken - not guessed
    # from state like the mod's own idle chatter, this fires exactly once,
    # exactly when the stage the player can see in agent_state.json actually
    # advances.
    lines = STAGE_MILESTONE_LINES.get(cur)
    if lines:
        send({"action": "say", "text": random.choice(lines)})

# Milestone lines: keyed by the stage just COMPLETED (cur in next_stage()),
# spoken once when Wilson clears it. Written in his established voice (dry,
# theatrical, aware he's a bot in an experiment) rather than generic hype.
STAGE_MILESTONE_LINES = {
    "tools": [
        "Axe. Torch. I am now marginally less doomed.",
        "Tools acquired. On to the next way to almost die.",
    ],
    "campfire": [
        "Fire secured. I didn't think we'd get this far.",
        "A campfire exists. Historic. Truly historic.",
    ],
    "explore_base": [
        "I've scouted enough ground to have opinions about it.",
        "Base-hunting complete. Or I got bored. Hard to say.",
    ],
    "armor": [
        "Spear. Armor. I could almost survive a hound now.",
        "Armed and armored. This is new for me.",
    ],
    "winter_prep": [
        "Winter kit assembled. Ask me again once it's actually cold.",
    ],
}

RESOURCE_VALUE = {
    "flint": 5, "sapling": 4, "twigs": 4, "grass": 3, "cutgrass": 3,
    "carrot_planted": 4, "berrybush": 4, "berrybush2": 4,
    "evergreen": 3, "deciduoustree": 3, "rock1": 4, "rock2": 4,
    "seeds": 3, "berries": 3, "carrot": 3,  # loose/ripe food
    "goldnugget": 10, "nugget": 10,  # v10: gold for science machine — critical
    "flower": 2,  # v9.6: sanity! picking flowers restores sanity (petals also
                    # eatable in a pinch). The user's collect-everything strategy.
}

# Session (2026-08-10) marsh-death postmortem: Wilson died 6x in one run,
# 4 of them to tentacles, because nothing in the agent knew threats existed
# when picking WHERE to gather - pick_target routed him straight into a
# tentacle nest chasing flint, and the flee distance (20) wasn't enough to
# clear a dense cluster. Shared list (was duplicated 3x with slightly
# different tuples in the threat-guard and retreat-invariant blocks below).
AGGRESSIVE_PREFABS = ("merm", "spider", "spider_warrior", "frog", "hound", "houndfire",
                      "tentacle", "snake", "mosquito", "killerbee",
                      "pigman", "werepig", "walrus", "depthworm",
                      "spiderqueen", "deerclops", "treeguard")
THREAT_AVOID_RADIUS = 12   # don't route a gather target within this many units of a live threat

# Opportunistic foraging (user request): loose/ripe FOOD wasn't in
# RESOURCE_VALUE at all, so pick_target() only ever considered it during the
# reactive low-hunger override - Wilson walked right past food lying on the
# ground otherwise. These are considered on every roam pass regardless of
# what the current stage needs, but bounded so it stays "grab it in passing"
# rather than a special trip: short range (no detour) and a per-item cap
# (a target, per the ask) so he doesn't hoard seeds forever instead of
# finishing the stage's actual materials.
FOOD_FORAGE_CAP = {"seeds": 15, "berries": 10, "carrot": 10}
FOOD_FORAGE_RANGE = 15

# ---------------- the autonomous loop ----------------
# v10.4: FOCUSED stage needs — each stage collects ONLY what it needs,
# one phase at a time, never everything at once. Kills the "scatter" where
# the bot chased twigs+flint+cutgrass+log+rocks+gold+rope simultaneously.
STAGE_NEEDS = {
    # Day 1: tools + campfire — pure basics (user's insight: no torch, no scatter)
    "tools":       {"twigs": 2, "cutgrass": 2, "flint": 2},
    "campfire":    {"cutgrass": 3, "log": 2},          # rush the campfire
    "explore_base":{"twigs": 4, "cutgrass": 6, "log": 6},  # stock up while scouting
    "science":     {"log": 4, "rocks": 4, "goldnugget": 1},  # researchlab materials
    "armor":       {"twigs": 2, "flint": 2, "rope": 2, "log": 8},  # spear + armorwood
    "food_farm":   {"twigs": 2, "cutgrass": 6},         # 2 traps
    "winter_prep": {"log": 40, "beefalowool": 8, "silk": 4, "rocks": 12},  # thermal + winter hat
}

def current_plan(st):
    """Stage-aware collection plan (v10.4). Each stage collects ONLY its own
    focused needs — never the whole craft table. If the stage is complete,
    advance. If needs are unmet, roam for exactly those. No scatter."""
    counts = st.get("item_counts") or {}
    stage = get_current_stage()
    stage_id = stage.get("id", "tools")
    # 1) stage complete?
    try:
        if stage["complete"](st):
            next_stage()
            stage = get_current_stage()
            stage_id = stage.get("id", "tools")
            ask_question_async(
                f"Stage '{stage['id']}' is next: {stage['desc']}",
                {"stage": stage["id"]})
    except Exception:
        pass
    # 2) what does THIS stage need? (focused set — no scatter)
    needs = {}
    stage_targets = STAGE_NEEDS.get(stage_id, {})
    for mat, target in stage_targets.items():
        have = counts.get(mat, 0)
        # don't over-collect; if we're halfway there or more, skip
        if have < target:
            needs[mat] = max(1, target - have)
    # also: if no explicit needs but the stage needs a craft we own the mats
    # for, try_craft handles it. Keep needs focused.
    want_prefabs = set()
    if needs.get("twigs"): want_prefabs.add("sapling")
    if needs.get("cutgrass"): want_prefabs.add("grass")
    if needs.get("flint"): want_prefabs.add("flint")
    if needs.get("log"): want_prefabs.add("evergreen")
    if needs.get("rocks"): want_prefabs.add("rock1")
    if needs.get("goldnugget"): want_prefabs.add("goldnugget")
    if needs.get("beefalowool"): want_prefabs.add("beefalo")
    return needs, want_prefabs

def pick_target(st, want_prefabs, visited):
    """Nearest harvestable target weighted by learning + value.
    TOOL-AWARE: without an axe, trees are worthless (10x slower chopping) -
    gather grass/sapling/flint first so the axe gets crafted."""
    TRACKER.maybe_clear()   # Session A Task 3: after 180s, regrown resources are retryable
    nb = st.get("nearby") or []
    pos = st.get("pos") or {}
    px, pz = pos.get("x", 0), pos.get("z", 0)
    counts = st.get("item_counts") or {}
    has_axe = "axe" in (st.get("items") or []) or "axe" in (st.get("equipped") or [])
    has_pick = "pickaxe" in (st.get("items") or []) or "pickaxe" in (st.get("equipped") or [])
    # v10.2: rocks are valuable (firepit+science machine). Mine only with
    # a pickaxe; without one, rocks need bare hands (slow) so deprioritize.
    # rock1/rock2 need a pickaxe to be efficient.
    # threat-aware targeting (postmortem fix): a resource sitting inside a
    # tentacle nest is not worth walking into. Only threats with known coords.
    threats = [t for t in (st.get("threats") or [])
               if t.get("hp", 0) > 0
               and (t.get("targeting") or t.get("n") in AGGRESSIVE_PREFABS)
               and t.get("x") is not None and t.get("z") is not None]
    best, best_score = None, -1
    for e in nb:
        n = e.get("n")
        if not e.get("ok"): continue
        is_forage_food = n in FOOD_FORAGE_CAP
        if n not in RESOURCE_VALUE and not is_forage_food: continue
        # Session A Task 3: never re-target a GUID that's done or in flight
        g = e.get("guid")
        if g is not None and (g in TRACKER.done or g == TRACKER.in_flight):
            continue
        # axe-less: never target trees (need axe to chop efficiently)
        if not has_axe and n in ("evergreen", "deciduoustree"):
            continue
        # pick-less: don't target rocks (bare-hand mining is too slow)
        if not has_pick and n in ("rock1", "rock2"):
            continue
        if is_forage_food:
            if e.get("d", 99) > FOOD_FORAGE_RANGE: continue
            if counts.get(n, 0) >= FOOD_FORAGE_CAP[n]: continue
        elif want_prefabs and n not in want_prefabs and n not in ("sapling","grass"): continue
        if (n, e.get("x"), e.get("z")) in visited: continue
        d = e.get("d", 99)
        if d > 40: continue
        ex, ez = e.get("x"), e.get("z")
        if threats and ex is not None and ez is not None and any(
                math.hypot(ex - t["x"], ez - t["z"]) < THREAT_AVOID_RADIUS for t in threats):
            continue
        # score = value + learned reliability - distance
        score = RESOURCE_VALUE.get(n, 1) + gather_reliability(n)*2 - d*0.1
        if score > best_score:
            best_score = score
            best = (n, e.get("x"), e.get("z"), d, e.get("guid"))
    return best

# ---------------- Session A Task 5: falsifiable decisions ----------------
PRODUCTS = {   # prefab -> what it yields, for gather expectations
    "grass": "cutgrass", "sapling": "twigs", "flint": "flint",
    "evergreen": "log", "deciduoustree": "log", "rock1": "rocks",
    "rock2": "rocks", "rock1": "rocks", "berrybush": "berries", "carrot_planted": "carrot",
    "seeds": "seeds", "flower": "petals",
    "goldnugget": "goldnugget", "nugget": "goldnugget",  # v10: gold for science
}

def plan_digest(st):
    """The WHOLE current plan (stage + needs + wants) as a string - the
    field that makes planner bugs visible in the decision record."""
    try:
        needs, wp = current_plan(st)
        return f"stage={current_stage_id()} needs={dict(needs)} wants={sorted(wp)}"
    except Exception:
        return "stage=?"

def log_before(state, goal, action, why, expected):
    """Record a falsifiable decision BEFORE acting. Returns decision_id."""
    try:
        return decision_logger.log_decision(RUN_ID, state, goal, action, why, expected)
    except Exception:
        return ""

def log_after(decision_id, state_after):
    """Close the decision AFTER the result. Returns the verdict string."""
    if not decision_id:
        return ""
    try:
        return decision_logger.log_outcome(decision_id, state_after)
    except Exception:
        return ""

# v10.1: stage-gated crafting — only craft items the current stage allows.
# Prevents crafting trap before axe, firepit before campfire, etc.
STAGE_CRAFT_ALLOW = {
    "tools":       {"axe", "pickaxe", "campfire", "rope"},
    "campfire":    {"axe", "pickaxe", "campfire", "rope", "firepit"},
    "explore_base":{"axe", "pickaxe", "campfire", "rope", "firepit", "researchlab"},
    "science":     {"axe", "pickaxe", "torch", "campfire", "rope", "firepit", "researchlab", "trap", "shovel"},
    "armor":       {"axe", "pickaxe", "torch", "campfire", "rope", "firepit", "researchlab", "trap", "shovel", "spear", "armorwood", "backpack"},
    "food_farm":   {"axe", "pickaxe", "torch", "campfire", "rope", "firepit", "researchlab", "trap", "shovel", "spear", "armorwood", "backpack"},
    "winter_prep": {"axe", "pickaxe", "torch", "campfire", "rope", "firepit", "researchlab", "trap", "shovel", "spear", "armorwood", "backpack"},
}

def try_craft(st):
    """Local craft: iterate taught priorities, craft what's possible.
    v10.1: stage-gated — only craft items the current plan stage allows.
    v10: tech-gated items need can_build[]; no stacking structures."""
    counts = st.get("item_counts") or {}
    equipped = st.get("equipped") or []
    can_build = st.get("can_build") or []
    TECH_GATED = {"spear", "armorwood", "backpack", "researchlab", "shovel"}
    # get current stage
    stage_id = "tools"
    try:
        stage_id = get_current_stage().get("id", "tools")
    except Exception:
        pass
    allowed = STAGE_CRAFT_ALLOW.get(stage_id, STAGE_CRAFT_ALLOW["tools"])
    for name, mats in CRAFT_PRIORITIES:
        if name not in allowed: continue  # stage gate
        if name in equipped: continue
        if name in ("axe", "pickaxe", "spear", "shovel") and counts.get(name, 0) > 0:
            continue
        if name in ("torch",) and any("torch" in e for e in equipped): continue
        if name in ("firepit", "campfire") and len(st.get("fires") or []) > 0:
            continue
        if name == "trap" and counts.get("trap", 0) >= 2:
            continue
        if name == "armorwood" and any("armor" in e for e in equipped):
            continue
        if name in TECH_GATED and name not in can_build:
            continue
        if all(counts.get(m, 0) >= c for m, c in mats.items()):
            d_craft = log_before(st, f"craft {name} ({plan_digest(st)})",
                                 {"action": "craft", "recipe": name},
                                 f"materials present: {mats}",
                                 {f"owned.{name}": "+1"})
            send({"action": "craft", "recipe": name})
            time.sleep(2)
            if name in ("axe", "pickaxe", "spear"):
                # v9.4: NEVER equip a tool at dusk/night - it rips the torch
                # out of Wilson's hand (the 11:01:41 night crash: pickaxe
                # crafted at night -> equipped -> sanity 96->72). Tools can
                # wait for day; the torch cannot.
                phase_c = st.get("phase")
                if phase_c == "day":
                    send({"action": "equip", "item": name})
                    time.sleep(1)
            # torch is deliberately NOT auto-equipped here: equipping it
            # immediately on craft burns its fuel in broad daylight, and
            # doing so while a chop/mine job is mid-swing rips the axe out
            # of Wilson's hand (mod tool-recheck kills the job as
            # tool_broke). reflex.py's ensure_light() equips it at the
            # right time (dusk-90s / dark-emergency) instead.
            log_after(d_craft, get_state())
            log(f"🔨 LOCAL CRAFT: {name} (materials present)")
            learn_gather(name, True)
            return True
    return False

def verify_collection(start_counts, end_counts):
    """LOCAL verification: did we actually collect? (no AI involved)"""
    gained = {}
    for k, v in end_counts.items():
        if v > start_counts.get(k, 0):
            gained[k] = v - start_counts.get(k, 0)
    return gained

def find_job_result(st, cmdid):
    """Session A Task 2: find this command's result in the mod's ring buffer
    (st["results"], newest first, entries {id, result, t}).
    Returns the result dict (job report OR dispatch error) or None."""
    try:
        for entry in (st or {}).get("results") or []:
            if entry.get("id") == cmdid:
                return entry.get("result") or {}
    except Exception:
        pass
    return None

def roam_once(st, settle=5, want_prefabs=None):
    """One autonomous roam pass: pick target -> walk -> fire gather -> verify at end."""
    needs, wp = current_plan(st)
    if want_prefabs is None:
        want_prefabs = wp
    visited = set()
    last_report = None
    roam_once._ground_picks = 0  # v10.1: reset ground-item pickup counter
    start_counts = dict(st.get("item_counts") or {})
    pos = st.get("pos") or {}
    log(f"🌍 ROAM at ({pos.get('x'):.0f},{pos.get('z'):.0f}) | needs: {needs}")

    for _ in range(6):
        st = get_state()
        if st.get("isnight") and not (st.get("equipped") or []):
            log("🌙 night + no light - stop roaming")
            break
        # v9.6 (user strategy): collect EVERYTHING Wilson passes - loose
        # ground items are free (no walking cost). Sweep them before any
        # plant/flint target.
        # v10.1: limit to 3 ground-item pickups per roam pass (butterfly
        # loop: Wilson spent 5 min picking butterflies instead of exploring).
        if len(visited) > 0 and getattr(roam_once, '_ground_picks', 0) >= 3:
            gi_near = []
        else:
            gi = st.get("ground_items") or []
            # v10.2: skip butterflies/robins/crows — human player ignores them
            # (wastes time, fills inventory, ~1 hunger each)
            USELESS_GROUND = {"butterfly", "robin", "crow", "rabbit", "rabbithole"}
            gi_near = [g for g in gi if g.get("d", 99) < 20 and g.get("n")
                       and g.get("n") not in USELESS_GROUND]
        if gi_near:
            gi_near.sort(key=lambda g: g.get("d", 99))
            g = gi_near[0]
            prod_g = PRODUCTS.get(g.get("n"), g.get("n"))
            d_gi = log_before(st, f"ground pickup {g.get('n')}->{prod_g} ({plan_digest(st)})",
                              {"action": "gather_job", "prefab": g.get("n"), "count": 3},
                              f"ground item at d={g.get('d'):.0f}", {f"item_counts.{prod_g}": "+1"})
            cmdid_gi = send({"action": "gather_job", "prefab": g.get("n"), "count": 3})
            log(f"  🧺 ground pickup: {g.get('n')}@({g.get('x'):.0f},{g.get('z'):.0f}) d={g.get('d'):.0f}")
            roam_once._ground_picks = getattr(roam_once, '_ground_picks', 0) + 1
            deadline = time.time() + 20
            while time.time() < deadline:
                time.sleep(1.5)
                st2 = get_state()
                if find_job_result(st2, cmdid_gi) is not None:
                    break
            st = st2
            log_after(d_gi, st)
            continue
        t = pick_target(st, want_prefabs, visited)
        if not t:
            # EXPLORE TOWARD KNOWN RESOURCES (worldmap), max 2 steps per roam.
            # Random wandering was wasting the whole day.
            explored = 0
            while explored < 2:
                px2, pz2 = pos.get("x",0), pos.get("z",0)
                dest = None
                try:
                    from lib import world_map as wm
                    # any known resource prefab we still need, nearest first
                    for prefab in ("sapling", "grass", "flint", "berrybush", "carrot_planted"):
                        spots = wm.find("default", prefab, near_xz=(px2, pz2), limit=1)
                        if spots:
                            s = spots[0]
                            d2 = math.sqrt((s["x"]-px2)**2 + (s["z"]-pz2)**2)
                            if d2 > 5:   # not the spot we're standing on
                                dest = (s["x"], s["z"])
                                break
                except Exception:
                    dest = None
                if dest is None:
                    # Session A Task 4: visited-grid frontier (replaces cardinal cycling)
                    bx = bz = None
                    try:
                        from lib import world_map as wm3
                        b = wm3.get_base("default")
                        if b:
                            bx, bz = b.get("x"), b.get("z")
                    except Exception:
                        pass
                    base_xz = (bx, bz) if bx is not None and bz is not None else None
                    dest = EXPLORER.next_target(st, base_xz, 200)
                    if dest is None:
                        # everything in range explored -> head to base, give up this pass
                        log("🧭 all reachable cells explored - heading to base")
                        if base_xz is not None:
                            send({"action": "move_to", "x": bx, "z": bz})
                            time.sleep(8)
                        break
                d_exp = log_before(st, f"explore->({dest[0]:.0f},{dest[1]:.0f}) ({plan_digest(st)})",
                                   {"action": "move_to", "x": dest[0], "z": dest[1]},
                                   "frontier explore - no known resource", {"pos": "changes"})
                send({"action": "move_to", "x": dest[0], "z": dest[1]})
                log(f"🧭 explore -> ({dest[0]:.0f},{dest[1]:.0f})")
                time.sleep(10)
                st2 = get_state()
                log_after(d_exp, st2)
                pos = st2.get("pos") or pos
                # Session A Task 4: fill + persist the visited grid
                EXPLORER.observe(st2)
                EXPLORER.save()
                # record what we discovered
                try:
                    from lib import world_map as wm2
                    wm2.observe("default", st2)
                except Exception:
                    pass
                # found targets now?
                st = st2
                t = pick_target(st, want_prefabs, visited)
                if t:
                    break
                explored += 1
            if not t:
                # give up this pass: batch confirmation, return counts
                end_counts = dict(st.get("item_counts") or {})
                log(f"✅ ROAM pass done (no targets): {end_counts}")
                return end_counts, last_report
            continue
        n, tx, tz, d, guid = t
        visited.add((n, tx, tz))
        d_move = log_before(st, f"move->({tx},{tz}) for {n} ({plan_digest(st)})",
                            {"action": "move_to", "x": tx, "z": tz},
                            f"roam target {n} at d={d}", {"pos": "changes"})
        send({"action": "move_to", "x": tx, "z": tz})
        time.sleep(min(9, 2 + d/4))
        st_moved = get_state()
        log_after(d_move, st_moved)
        # count = max_swings in the mod. Trees need 10-20 swings (never cap at 3);
        # pick-mode targets (grass/sapling/flint) finish in 1 swing anyway.
        swing_cap = 25 if n in ("evergreen", "deciduoustree", "rock1", "rock2") else 3
        prod = PRODUCTS.get(n, n)
        d_job = log_before(st_moved, f"gather {n}->{prod} ({plan_digest(st_moved)})",
                           {"action": "gather_job", "prefab": n, "count": swing_cap},
                           f"roam target at ({tx},{tz})",
                           {f"item_counts.{prod}": "+1"})
        TRACKER.fired({"guid": guid})   # Session A Task 3: mark in flight BEFORE the job runs
        cmdid = send({"action": "gather_job", "prefab": n, "count": swing_cap})
        log(f"  🎯 {n}@({tx},{tz}) fired (max_swings={swing_cap})")
        # Session A2 T2: wait for the mod's own verdict as the completion
        # signal, not a fixed sleep. A tree chopped with a torch equipped
        # (light guard refuses the swap at night) takes longer than 23s;
        # reading state mid-job scored refuted on successful gathers.
        deadline = time.time() + 45          # hard cap; do not wait forever
        report = None
        st = None
        while time.time() < deadline:
            time.sleep(1.5)
            st = get_state()
            report = find_job_result(st, cmdid)
            if report is not None:
                break
        # deadline passed with no report: genuinely inconclusive - log it
        if report is None:
            log(f"  ⏳ no job report within 45s for {n} (cmdid {cmdid}) - outcome inconclusive")
        if report:
            report["prefab"] = n
            last_report = report
            log(f"  📋 job report: {json.dumps(report)}")
        TRACKER.reported(report)   # Session A Task 3: completes or fails -> done, no retry this roam
        log_after(d_job, st)
        # LOCAL verification after each gather
        gained = verify_collection(start_counts, dict(st.get("item_counts") or {}))
        if gained:
            for g, cnt in gained.items():
                learn_gather(g, True)
            start_counts = dict(st.get("item_counts") or {})
            log(f"  ✅ confirmed local: {gained}")

    # batch confirmation at end
    st = get_state()
    end_counts = dict(st.get("item_counts") or {})
    return end_counts, last_report

def main():
    global RUN_ID
    log("🤖 LOCAL AGENT ONLINE (plan-driven + v8 invariants)")
    # seed the world map with current surroundings
    try:
        from lib import world_map as wm
        wm.observe("default", get_state())
    except Exception:
        pass
    # Session A fix T2: the leash needs a denominator. Spawn is a mediocre
    # base; a mediocre anchor beats no anchor. (set_base was never called -
    # the leash + explorer leash were inert.)
    try:
        from lib import world_map as wm
        p = (get_state() or {}).get("pos") or {}
        if not wm.get_base("default") and p.get("x") is not None and p.get("z") is not None:
            wm.set_base("default", p["x"], p["z"])
            log(f"🏠 base set to spawn ({p['x']:.0f},{p['z']:.0f})")
    except Exception:
        pass
    cause = "unknown"
    notes = ""
    try:
        _main_loop()
        cause = "manual_stop"
    except KeyboardInterrupt:
        cause = "manual_stop"
        raise
    except Exception as e:
        cause = "crash"
        notes = f"{type(e).__name__}: {e}"
        raise
    finally:
        run_logger.end_run(RUN_ID, get_state(), cause,
                           {"notes": notes} if notes else None)



# ============================================================
# v10.5: GOAL-QUEUE PLANNER — multi-step lookahead
# ============================================================
# The GOAL_QUEUE holds 3-5 concrete steps. The main loop executes
# them ONE AT A TIME (not re-evaluating from scratch each tick).
# Re-plan happens: every 30s, OR when queue empties, OR on emergency.
GOAL_QUEUE = []
_last_plan_ts = 0
_last_plan_phase = "day"
_last_plan_health = 150
PLAN_INTERVAL = 30  # seconds between full re-plans

CRAFT_RECIPES = {
    "axe":        {"twigs": 1, "flint": 1},
    "pickaxe":    {"twigs": 2, "flint": 2},
    "torch":      {"cutgrass": 2, "twigs": 2},
    "campfire":   {"cutgrass": 3, "log": 2},
    "rope":       {"cutgrass": 3},
    "researchlab":{"log": 4, "rocks": 4, "goldnugget": 1},
    "spear":      {"twigs": 2, "flint": 1, "rope": 1},
    "armorwood":  {"log": 8, "rope": 2},
    "firepit":    {"log": 2, "rocks": 12},
    "trap":       {"twigs": 2, "cutgrass": 6},
    "shovel":     {"twigs": 2, "flint": 1, "rope": 1},
    "backpack":   {"cutgrass": 4, "twigs": 4},
}

# What to gather for each material
MATERIAL_SOURCES = {
    "twigs": "sapling", "cutgrass": "grass", "flint": "flint",
    "log": "evergreen", "rocks": "rock1", "goldnugget": "goldnugget",
    "rope": None,  # crafted, not gathered
}

def plan_goals(st):
    """Generate a prioritized GOAL_QUEUE based on current state.
    Each goal is a dict: {type, target, count, prefab, recipe, x, z}
    Types: gather, craft, equip, fuel, hold, explore, eat, flee
    """
    goals = []
    counts = st.get("item_counts") or {}
    equipped = st.get("equipped") or []
    items = st.get("items") or []
    phase = st.get("phase") or "day"
    day = st.get("day") or 1
    pos = st.get("pos") or {}
    px, pz = pos.get("x", 0), pos.get("z", 0)
    fires = st.get("fires") or []
    have_fire = len(fires) > 0
    has_axe = "axe" in items or "axe" in equipped
    has_pick = "pickaxe" in items or "pickaxe" in equipped
    has_spear = "spear" in items or "spear" in equipped
    has_armor = any("armor" in e for e in equipped) or "armorwood" in counts
    hunger = (st.get("hunger") or [150])[0]
    health = (st.get("health") or [150])[0]
    sanity = (st.get("sanity") or [200])[0]
    secs_night = st.get("seconds_until_night")
    if not isinstance(secs_night, (int, float)):
        secs_night = 999

    # ---- NIGHT/DARKNESS SURVIVAL (highest priority — darkness kills fast) ----
    # If it's night, STAY AT FIRE. Never leave the fire to gather in the dark.
    if phase in ("dusk", "night") or secs_night < 120:
        if have_fire:
            goals.append({"type": "fuel", "item": "log"})
            if hunger < 80:
                goals.append({"type": "eat"})
            goals.append({"type": "craft_idle"})
            goals.append({"type": "hold"})
            return goals
        else:
            # URGENT: build campfire before night
            need_cg = max(0, 3 - counts.get("cutgrass", 0))
            need_log = max(0, 2 - counts.get("log", 0))
            if need_cg > 0:
                goals.append({"type": "gather", "material": "cutgrass",
                              "prefab": "grass", "count": 3})
            if need_log > 0 and has_axe:
                goals.append({"type": "gather", "material": "log",
                              "prefab": "evergreen", "count": 2})
            if need_cg == 0 and need_log == 0:
                goals.append({"type": "craft", "recipe": "campfire"})
            if not has_axe and need_log > 0:
                if counts.get("flint", 0) >= 1 and counts.get("twigs", 0) >= 1:
                    goals.insert(0, {"type": "craft", "recipe": "axe"})
                    goals.append({"type": "equip", "item": "axe"})
            return goals

    # ---- HUNGER EMERGENCY (eat before anything else) ----
    if hunger < 50:
        # try to eat from inventory first
        ate = False
        for food in ("cookedmeat", "cookedsmallmeat", "cooked_smallmeat",
                     "cooked_drumstick", "acorn_cooked", "carrot_cooked",
                     "berries", "carrot", "acorn", "drumstick",
                     "smallmeat", "meat", "mushroom", "seeds"):
            if counts.get(food, 0) > 0:
                goals.append({"type": "eat"})
                ate = True
                break
        if not ate:
            # no food in inventory — gather nearby food
            nb = st.get("nearby") or []
            food_target = None
            for e in nb:
                if e.get("ok") and e.get("n") in ("carrot_planted", "berrybush", "berrybush2", "seeds") and e.get("d", 99) < 40:
                    food_target = e; break
            if food_target:
                goals.append({"type": "gather", "material": "food",
                              "prefab": food_target.get("n"), "count": 1,
                              "x": food_target.get("x"), "z": food_target.get("z")})
                goals.append({"type": "eat"})
            else:
                # no food nearby — explore to find some
                goals.append({"type": "explore", "reason": "find food (starving!)"})
        return goals


    
    # ---- DAYTIME ----
    # 1. Survival basics: axe + campfire kit
    if not has_axe:
        need_twigs = max(0, 1 - counts.get("twigs", 0))
        need_flint = max(0, 1 - counts.get("flint", 0))
        if need_twigs > 0:
            goals.append({"type": "gather", "material": "twigs",
                          "prefab": "sapling", "count": 1})
        if need_flint > 0:
            goals.append({"type": "gather", "material": "flint",
                          "prefab": "flint", "count": 1})
        if need_twigs == 0 and need_flint == 0:
            goals.append({"type": "craft", "recipe": "axe"})
            goals.append({"type": "equip", "item": "axe"})
        # also gather cutgrass for campfire (no tool needed)
        need_cg = max(0, 3 - counts.get("cutgrass", 0))
        if need_cg > 0:
            goals.append({"type": "gather", "material": "cutgrass",
                          "prefab": "grass", "count": 3})
        return goals
    
    # 2. Campfire kit (need 3 cutgrass + 2 log)
    if not have_fire and day == 1:
        need_cg = max(0, 3 - counts.get("cutgrass", 0))
        need_log = max(0, 2 - counts.get("log", 0))
        if need_cg > 0:
            goals.append({"type": "gather", "material": "cutgrass",
                          "prefab": "grass", "count": 3})
        if need_log > 0:
            goals.append({"type": "gather", "material": "log",
                          "prefab": "evergreen", "count": 2})
        if need_cg == 0 and need_log == 0:
            goals.append({"type": "craft", "recipe": "campfire"})
        return goals
    
    # 3. Pickaxe (need 2 twigs + 2 flint)
    if not has_pick:
        need_twigs = max(0, 2 - counts.get("twigs", 0))
        need_flint = max(0, 2 - counts.get("flint", 0))
        if need_twigs > 0:
            goals.append({"type": "gather", "material": "twigs",
                          "prefab": "sapling", "count": 2})
        if need_flint > 0:
            goals.append({"type": "gather", "material": "flint",
                          "prefab": "flint", "count": 2})
        if need_twigs == 0 and need_flint == 0:
            goals.append({"type": "craft", "recipe": "pickaxe"})
            goals.append({"type": "equip", "item": "pickaxe"})
        return goals
    
    # 4. Mine rocks (need 4 for science machine + 12 for firepit)
    need_rocks = max(0, 16 - counts.get("rocks", 0))
    if need_rocks > 0 and has_pick:
        goals.append({"type": "gather", "material": "rocks",
                      "prefab": "rock1", "count": 16})
        return goals
    
    # 5. Find gold (explore toward rocky biome)
    if counts.get("goldnugget", 0) < 1:
        goals.append({"type": "explore", "reason": "find gold"})
        return goals
    
    # 6. Science machine
    can_researchlab = (counts.get("log", 0) >= 4 and 
                       counts.get("rocks", 0) >= 4 and
                       counts.get("goldnugget", 0) >= 1)
    if can_researchlab and "researchlab" not in (st.get("can_build") or []):
        goals.append({"type": "craft", "recipe": "researchlab"})
        return goals
    
    # 7. Spear + armor (before hounds day 6)
    if day >= 3 and not has_spear:
        need_rope = max(0, 1 - counts.get("rope", 0))
        if need_rope > 0:
            need_cg = max(0, 3 - counts.get("cutgrass", 0))
            if need_cg > 0:
                goals.append({"type": "gather", "material": "cutgrass",
                              "prefab": "grass", "count": 3})
                return goals
            goals.append({"type": "craft", "recipe": "rope"})
            return goals
        need_twigs = max(0, 2 - counts.get("twigs", 0))
        need_flint = max(0, 1 - counts.get("flint", 0))
        if need_twigs > 0 or need_flint > 0:
            if need_twigs > 0:
                goals.append({"type": "gather", "material": "twigs",
                              "prefab": "sapling", "count": 2})
            if need_flint > 0:
                goals.append({"type": "gather", "material": "flint",
                              "prefab": "flint", "count": 1})
            return goals
        goals.append({"type": "craft", "recipe": "spear"})
        return goals
    
    # 8. Armorwood
    if day >= 4 and not has_armor:
        need_rope = max(0, 2 - counts.get("rope", 0))
        need_log = max(0, 8 - counts.get("log", 0))
        if need_rope > 0:
            need_cg = max(0, 3 * need_rope - counts.get("cutgrass", 0))
            if need_cg > 0:
                goals.append({"type": "gather", "material": "cutgrass",
                              "prefab": "grass", "count": 6})
                return goals
            goals.append({"type": "craft", "recipe": "rope"})
            return goals
        if need_log > 0:
            goals.append({"type": "gather", "material": "log",
                          "prefab": "evergreen", "count": 8})
            return goals
        goals.append({"type": "craft", "recipe": "armorwood"})
        return goals
    
    # 9. Firepit (upgrade from campfire)
    if not have_fire and counts.get("rocks", 0) >= 12 and counts.get("log", 0) >= 2:
        goals.append({"type": "craft", "recipe": "firepit"})
        return goals
    
    # 10. Explore / stockpile
    goals.append({"type": "explore", "reason": "stockpile resources"})
    return goals


def execute_goal(goal, st):
    """Execute ONE goal from the queue. Returns True if goal is DONE."""
    gtype = goal.get("type")
    counts = st.get("item_counts") or {}
    equipped = st.get("equipped") or []
    pos = st.get("pos") or {}
    
    if gtype == "gather":
        material = goal.get("material")
        prefab = goal.get("prefab")
        need = goal.get("count", 1)
        have = counts.get(material, 0)
        if have >= need:
            log(f"  ✅ goal done: have {material}={have} (target {need})")
            return True

        # v10.7: GRAB WHATEVER'S CLOSEST — don't walk past useful resources
        # to reach a specific one. Check ALL nearby useful resources and pick
        # the nearest, regardless of which material the plan asked for.
        nb = st.get("nearby") or []
        USEFUL = {"grass": "cutgrass", "sapling": "twigs", "flint": "flint",
                  "evergreen": "log", "rock1": "rocks", "rock2": "rocks",
                  "seeds": "seeds", "carrot_planted": "carrot", "berrybush": "berries",
                  "deciduoustree": "log"}
        # Build list of all useful nearby resources we still need
        candidates = []
        # Check the full GOAL_QUEUE for what's needed (not just this one goal)
        all_needed = set()
        for g in GOAL_QUEUE:
            if g.get("type") == "gather":
                all_needed.add(g.get("material"))
        for e in nb:
            if not e.get("ok"): continue
            n = e.get("n")
            d = e.get("d", 99)
            if d > 50: continue
            mat = USEFUL.get(n)
            if mat and (mat == material or mat in all_needed):
                candidates.append((d, e, mat))
        # Also include ANY useful resource even if not in the plan (opportunistic)
        for e in nb:
            if not e.get("ok"): continue
            n = e.get("n")
            d = e.get("d", 99)
            if d > 30: continue
            mat = USEFUL.get(n)
            if mat and not any(c[2] == mat for c in candidates):
                candidates.append((d, e, mat))

        if candidates:
            # Pick the CLOSEST useful resource
            candidates.sort(key=lambda c: c[0])
            d, target, target_mat = candidates[0]
            target_prefab = target.get("n")

            send({"action": "move_to", "x": target.get("x"), "z": target.get("z")})
            time.sleep(3)
            # RE-READ state after move
            st_fresh = get_state()
            # Check what's now nearby after the move
            nb_fresh = st_fresh.get("nearby") or []
            # Grab anything within 5 units at the new position
            for e in nb_fresh:
                if (e.get("ok") and e.get("d", 99) < 5 and
                    e.get("n") in USEFUL):
                    quick_mat = USEFUL[e.get("n")]
                    quick_prefab = e.get("n")
                    SWING_COUNT = {"evergreen": 25, "deciduoustree": 25, "rock1": 25, "rock2": 25,
                                   "grass": 3, "sapling": 3, "flint": 3, "seeds": 3,
                                   "carrot_planted": 3, "berrybush": 3}
                    swing = SWING_COUNT.get(quick_prefab, 3)
                    wait_time = max(5, int(swing * 1.2))
                    send({"action": "gather_job", "prefab": quick_prefab, "count": swing})
                    log(f"  🎯 grabbing {quick_prefab} ({quick_mat}) d={e.get('d'):.0f} [{swing} swings]")
                    time.sleep(wait_time)
                    break
            # Check if the original goal is now satisfied
            st_after = get_state()
            counts_after = st_after.get("item_counts") or {}
            if isinstance(counts_after, list):
                counts_after = {i.get('prefab',''):i.get('count',0) for i in counts_after if isinstance(i,dict)}
            have_after = counts_after.get(material, 0)
            if have_after >= need:
                log(f"  ✅ goal done: {material} {have_after}/{need}")
                return True
            return False
        else:
            # nothing useful nearby — explore toward known resources or outward
            log(f"  🧭 nothing useful nearby, exploring")
            pos = st.get("pos") or {}
            # Try world map first
            try:
                from lib import world_map as wm
                hits = wm.find("default", prefab, near_xz=(pos.get("x",0), pos.get("z",0)), limit=3) or []
                if hits:
                    hx, hz = hits[0].get("x",0), hits[0].get("z",0)
                    send({"action": "move_to", "x": hx, "z": hz})
                    log(f"  🧭 heading to known {prefab} at ({hx:.0f},{hz:.0f})")
                    time.sleep(8)
                    return False
            except Exception:
                pass
            # fallback: explore outward
            ex = EXPLORER.next_target(st, base_xz=(pos.get("x",0), pos.get("z",0)),
                                       target_prefab=prefab)
            if ex:
                tx, tz = ex
                send({"action": "move_to", "x": tx, "z": tz})
                log(f"  🧭 exploring -> ({tx:.0f},{tz:.0f})")
                time.sleep(8)
            else:
                send({"action": "move_to", "x": pos.get("x",0)+random.randint(-60,60),
                      "z": pos.get("z",0)+random.randint(-60,60)})
                time.sleep(8)
            return False
    
    elif gtype == "craft":
        recipe = goal.get("recipe")
        mats = CRAFT_RECIPES.get(recipe, {})
        if all(counts.get(m, 0) >= c for m, c in mats.items()):
            # v10.5: For campfire, move to a spot away from trees/saplings
            # to avoid burning them (and Wilson)
            if recipe in ("campfire", "firepit"):
                pos = st.get("pos") or {}
                px, pz = pos.get("x", 0), pos.get("z", 0)
                nb = st.get("nearby") or []
                # check if any tree/sapling is within 6 units
                too_close = [e for e in nb if e.get("n") in ("evergreen", "deciduoustree", "sapling") and e.get("d", 99) < 6]
                if too_close:
                    # move 8 units in a random direction to get clear
                    import random as _r
                    tx = px + _r.randint(-10, 10)
                    tz = pz + _r.randint(-10, 10)
                    send({"action": "move_to", "x": tx, "z": tz})
                    log(f"  🚶 moving away from trees before crafting {recipe}")
                    time.sleep(4)
                    # don't return True — re-execute the craft next tick
                    return False
            send({"action": "craft", "recipe": recipe})
            log(f"  🔨 crafting {recipe}")
            time.sleep(2)
            return True
        else:
            log(f"  ⏳ can't craft {recipe} yet — missing materials")
            return True  # re-plan will gather the missing mats
    
    elif gtype == "equip":
        item = goal.get("item")
        if item in equipped:
            return True
        if item in (st.get("items") or []):
            send({"action": "equip", "item": item})
            log(f"  🔧 equipping {item}")
            time.sleep(1)
            return True
        return True
    
    elif gtype == "fuel":
        fires = st.get("fires") or []
        if not fires:
            return True
        f = fires[0]
        fp = f.get("fuel_pct", 100) or 100
        # v10.6: fuel_pct is 0-100 (percentage), not 0-1
        if fp < 35 and counts.get("log", 0) > 0:
            send({"action": "fuel", "item": "log"})
            log(f"  🔥 fueling campfire (fuel_pct={fp:.0%})")
            time.sleep(2)
        return True
    
    elif gtype == "eat":
        for food in ("cookedmeat", "cookedsmallmeat", "cooked_smallmeat",
                     "cooked_drumstick", "acorn_cooked", "carrot_cooked",
                     "berries", "carrot", "acorn", "drumstick",
                     "smallmeat", "meat", "mushroom", "seeds"):
            if counts.get(food, 0) > 0:
                send({"action": "eat", "item": food})
                log(f"  🍽 eating {food}")
                time.sleep(3)
                break
        return True
    
    elif gtype == "craft_idle":
        # try to craft something useful while at campfire
        if try_craft(st):
            return False
        return True
    
    elif gtype == "hold":
        # v10.5: DON'T stand on top of the campfire — it causes burn damage.
        # Stand 5-6 units away (close enough for light, far enough to not burn)
        fires = st.get("fires") or []
        pos = st.get("pos") or {}
        px, pz = pos.get("x", 0), pos.get("z", 0)
        if fires:
            f = fires[0]
            fx, fz = f.get("x", 0), f.get("z", 0)
            d_to_fire = f.get("d", 99)
            if d_to_fire < 6:
                # too close — step away from the fire (8 units = safe from burn)
                dx = px - fx; dz = pz - fz
                dist = max(0.1, (dx*dx + dz*dz) ** 0.5)
                tx = fx + (dx / dist) * 8
                tz = fz + (dz / dist) * 8
                send({"action": "move_to", "x": tx, "z": tz})
                log(f"  🌙 stepping away from campfire (d={d_to_fire:.0f}m -> 6m)")
                time.sleep(3)
                return False
        log("  🌙 holding at campfire — waiting for day")
        time.sleep(5)
        return False
    
    elif gtype == "explore":
        reason = goal.get("reason", "scouting")
        # straight-line explore outward from current pos
        # pass the goal's material->prefab mapping so explorer can use world map
        explore_prefab = {"rocks": "rock1", "goldnugget": "goldnugget", "flint": "flint",
                          "twigs": "sapling", "cutgrass": "grass", "log": "evergreen"}.get(reason, None) if reason else None
        ex = EXPLORER.next_target(st, base_xz=(pos.get("x",0), pos.get("z",0)),
                                   target_prefab=explore_prefab if reason and reason.startswith("find ") else None)
        if ex:
            tx, tz = ex
            send({"action": "move_to", "x": tx, "z": tz})
            log(f"  🧭 exploring: {reason} -> ({tx:.0f},{tz:.0f})")
            # wait for move, then check if we found what we're looking for
            time.sleep(6)
            # re-read state and observe world map
            try:
                from lib import world_map as wm
                st_explore = get_state()
                wm.observe("default", st_explore)
            except Exception:
                pass
            return True
        else:
            send({"action": "move_to", "x": pos.get("x",0)+random.randint(-100,100),
                  "z": pos.get("z",0)+random.randint(-100,100)})
            time.sleep(8)
        return True
    
    return True  # unknown goal type — skip


def _main_loop():
    global RUN_ID, last_health, _damage_log_last, _last_day_announced
    global GOAL_QUEUE, _last_plan_ts, _last_plan_phase, _last_plan_health
    stuck_streak = 0
    while True:
        st = get_state()
        if not st.get("pos"):
            time.sleep(3); continue
        # paused?
        age = time.time() - os.path.getmtime(STATE_F) if os.path.exists(STATE_F) else 999
        if age > 10:
            time.sleep(5); continue
        # non-blocking question answers (Session A)
        ans = check_answer()
        if ans is not None:
            log(f"💬 ANSWER: {ans}")
        # DEATH: Wilson is a ghost (health 50, playerghost tag). The reflex
        # auto-revives. While a ghost, DO NOT gather (ghosts can't hold items).
        # Wait for the revive, then reset the plan state for the respawn.
        if st.get("is_ghost"):
            if not getattr(_main_loop, "was_ghost", False):
                _main_loop.was_ghost = True
                log(f"💀 WILSON DIED at day {st.get('day')} - ending run and quitting (user strategy)")
                # record the run honestly
                try:
                    run_logger.end_run(RUN_ID, st, "death")
                except Exception:
                    pass
                # v9.6: "repeat until he dies and quit the game" - send quit,
                # give the mod a moment to land it, then exit the daemon.
                try:
                    send({"action": "quit"})
                except Exception:
                    pass
                time.sleep(3)
                log("🛑 run over - exiting agent (game quitting)")
                os._exit(0)
            time.sleep(3)
            continue
        if getattr(_main_loop, "was_ghost", False):
            _main_loop.was_ghost = False
            log("🔄 RESPAWNED - resetting plan state for the new life")
            try:
                ag = {"ts": time.time(), "stage": "tools", "items": {}}
                save_json(AGENT_F, ag)
            except Exception:
                pass
            _last_day_announced = 0   # new life, milestones start over
        # milestone dialogue: day-count markers, spoken once per life (not
        # guessed - keyed off the same st.day the agent itself is acting on)
        day = st.get("day") or 0
        if day > _last_day_announced and day in DAY_MILESTONE_LINES:
            send({"action": "say", "text": random.choice(DAY_MILESTONE_LINES[day])})
            _last_day_announced = day
        # THREAT GUARD (v8 Q1 discriminator): NOT every _combat entity is a threat.
        # crows/robins/rabbits/butterflies/beefalo carry _combat but are passive.
        # Real threats: (a) targeting us, (b) hostile/monster tagged, (c) known
        # aggressive prefabs. Passive _combat = neutral, ignore.
        close_threats = [t for t in (st.get("threats") or [])
                         if t.get("hp", 0) > 0 and t.get("d", 99) < 10
                         and (t.get("targeting") or t.get("n") in AGGRESSIVE_PREFABS)]
        if close_threats:
            # FLEE FIRST, ask later (the spider-kill: waiting for an answer while
            # under attack is how Wilson died). The reflex also flees, but the
            # agent must not sit still asking questions mid-combat.
            names = {t.get("n") for t in close_threats}
            nearest_t = min(close_threats, key=lambda t: t.get("d", 99))
            px2, pz2 = st.get("pos", {}).get("x", 0), st.get("pos", {}).get("z", 0)
            tx2 = nearest_t.get("x")
            tz2 = nearest_t.get("z")
            if tx2 is None or tz2 is None:
                tx2, tz2 = px2 + 1, pz2 + 1
            dx2, dz2 = px2 - tx2, pz2 - tz2
            mag2 = math.sqrt(dx2*dx2 + dz2*dz2) or 1
            # v10 SWAMP AVOIDANCE: if on a swamp tile OR 2+ tentacles near,
            # flee the ENTIRE BIOME (120 units toward base) not just 35 from
            # one tentacle. 49% of all bot deaths were to tentacles in swamps.
            tentacle_count = sum(1 for t in close_threats if t.get("n") == "tentacle")
            on_swamp = st.get("on_swamp") or False
            if on_swamp or tentacle_count >= 2:
                flee_dist = 120
                base = None
                try:
                    base = worldmap.get_base("default")
                except Exception:
                    pass
                if base and base.get("x") is not None:
                    bdx, bdz = base["x"] - px2, base["z"] - pz2
                    bmag = math.sqrt(bdx*bdx + bdz*bdz) or 1
                    nx2, nz2 = px2 + bdx/bmag*flee_dist, pz2 + bdz/bmag*flee_dist
                else:
                    nx2, nz2 = px2 + dx2/mag2*flee_dist, pz2 + dz2/mag2*flee_dist
                log(f"SWAMP FLEE: {tentacle_count} tentacles, on_swamp={on_swamp} -> ({nx2:.0f},{nz2:.0f})")
            else:
                # normal flee: 35 units away from the nearest threat
                nx2, nz2 = px2 + dx2/mag2*35, pz2 + dz2/mag2*35
            # NOTE: _get_path only walks dicts (no list indices), so a per-threat
            # path like threats.0.d would always resolve None -> permanent
            # refuted. "threats": "changes" compares the list by value: a flee
            # that changed nothing scores refuted, a disengage scores confirmed.
            d_fl = log_before(st, f"FLEE {names} ({plan_digest(st)})",
                              {"action": "move_to", "x": nx2, "z": nz2},
                              f"threat {names} at {nearest_t.get('d')}m",
                              {"nearest_threat_d": "increases"})
            send({"action": "preempt_job"})
            send({"action": "move_to", "x": nx2, "z": nz2})
            log(f"🏃 FLEEING from {names} -> ({nx2:.0f},{nz2:.0f})")
            time.sleep(6)
            log_after(d_fl, get_state())
            # only AFTER disengaging, ask what to do about the area
            st_after = get_state()
            if not [t for t in (st_after.get("threats") or []) if t.get("d", 99) < 12]:
                log("   disengaged - noting danger spot, continuing")
            else:
                log("   STILL threatened - keeping distance")
            time.sleep(2)  # FIX: no-sleep continue would hot-spin the loop
            continue
        # v10 HOUND PREP: day 6 is the first hound attack. From day 5 onward,
        # if Wilson has no weapon+armor, crafting them is URGENT — a single
        # hound does 20 damage and they come in packs. This check runs BEFORE
        # the dusk guard because being unarmed at dusk on day 5+ is a death
        # sentence even if you survive the night.
        day_now = st.get("day") or 0
        if day_now >= 5:
            equipped_now = st.get("equipped") or []
            counts_now = st.get("item_counts") or {}
            can_build_now = st.get("can_build") or []
            has_weapon = "spear" in equipped_now or counts_now.get("spear", 0) > 0
            has_armor = any("armor" in e for e in equipped_now) or counts_now.get("armorwood", 0) > 0
            if not has_weapon and "spear" in can_build_now:
                log("⚔️ HOUND PREP: crafting spear (day 6 attack approaching)")
                send({"action": "craft", "recipe": "spear"})
                time.sleep(2)
                if st.get("phase") == "day":
                    send({"action": "equip", "item": "spear"})
                    time.sleep(1)
                continue
            if not has_armor and "armorwood" in can_build_now:
                log("🛡️ HOUND PREP: crafting armorwood (day 6 attack approaching)")
                send({"action": "craft", "recipe": "armorwood"})
                time.sleep(2)
                send({"action": "equip", "item": "armorwood"})
                time.sleep(1)
                continue
        # ===================================================
        # v10.5: GOAL-QUEUE PLANNER (replaces reactive loop)
        # ===================================================
        # Emergency guards (pause, death, threat, swamp, hound prep)
        # have already fired above. Everything below is PLANNED.
        
        # Re-plan if: queue empty, 30s elapsed, or phase changed
        global GOAL_QUEUE, _last_plan_ts
        now = time.time()
        phase_now = st.get("phase") or "day"
        
        # Don't re-plan if we're mid-gather on a tree/rock (needs many swings)
        mid_chop = False
        if GOAL_QUEUE and GOAL_QUEUE[0].get("type") == "gather":
            gp = GOAL_QUEUE[0].get("prefab", "")
            if gp in ("evergreen", "deciduoustree", "rock1", "rock2"):
                mid_chop = True
        # Force re-plan if critical conditions changed:
        # - hunger dropped below 50 (starving)
        # - phase changed (day->dusk->night)
        # - queue empty
        # - 30s elapsed (and not mid-chop)
        hunger_now = (st.get("hunger") or [150])[0]
        health_now = (st.get("health") or [150])[0]
        phase_now = st.get("phase") or "day"
        force_replan = (hunger_now < 50 or 
                        phase_now != _last_plan_phase or
                        (health_now < 50 and health_now < (_last_plan_health or 150)))
        need_replan = (len(GOAL_QUEUE) == 0 or 
                       force_replan or
                       (now - _last_plan_ts > PLAN_INTERVAL and not mid_chop))
        
        if need_replan:
            GOAL_QUEUE = plan_goals(st)
            _last_plan_ts = now
            _last_plan_phase = phase_now
            _last_plan_health = (st.get("health") or [150])[0]
            log(f"📋 PLAN ({phase_now} D{st.get('day',0)}): {len(GOAL_QUEUE)} goals: " +
                " -> ".join(g.get("type","?") + (":"+g.get("recipe","") if g.get("recipe") else 
                          ":"+g.get("material","") if g.get("material") else 
                          ":"+g.get("item","") if g.get("item") else "") 
                          for g in GOAL_QUEUE[:5]))
        
        # Execute the FIRST goal in the queue
        if GOAL_QUEUE:
            goal = GOAL_QUEUE[0]
            done = execute_goal(goal, st)
            if done:
                GOAL_QUEUE.pop(0)
                log(f"  ⏭ next goal ({len(GOAL_QUEUE)} remaining)")
        else:
            # no goals — explore
            log("  🧭 no goals — exploring")
            pos = st.get("pos") or {}
            send({"action": "move_to",
                  "x": pos.get("x",0) + random.randint(-80,80),
                  "z": pos.get("z",0) + random.randint(-80,80)})
            time.sleep(8)
        
        # v10.6: Re-equip ONLY when the current goal needs a tool and it's wrong
        # This prevents the axe/pickaxe flip-flop that happened every tick
        phase_re = st.get("phase")
        equipped_re = st.get("equipped") or []
        items_re = st.get("items") or []
        fires_re = st.get("fires") or []
        if GOAL_QUEUE and GOAL_QUEUE[0].get("type") == "gather":
            gp = GOAL_QUEUE[0].get("prefab", "")
            wanted_tool = None
            if gp in ("evergreen", "deciduoustree"):
                wanted_tool = "axe"
            elif gp in ("rock1", "rock2"):
                wanted_tool = "pickaxe"
            if wanted_tool and wanted_tool in items_re and wanted_tool not in equipped_re:
                # only re-equip during day or dusk-with-fire
                if phase_re == "day" or (phase_re == "dusk" and any(f.get("d", 99) <= 20 for f in fires_re)):
                    send({"action": "equip", "item": wanted_tool})
                    log(f"🔧 RE-EQUIP: {wanted_tool} (for {gp})")
                    time.sleep(1)
        
        # try_craft (opportunistic — doesn't conflict with plan)
        if not GOAL_QUEUE or GOAL_QUEUE[0].get("type") not in ("gather", "fuel", "hold"):
            try_craft(st)
        
        time.sleep(3)
        

if __name__ == "__main__":
    main()
