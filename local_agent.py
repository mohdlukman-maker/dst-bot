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
        "desc": "Day 1: craft axe + torch (survival basics)",
        "complete": lambda st: "axe" in (st.get("equipped") or []) and
                               any("torch" in i for i in (st.get("items") or [])),
        "asks_when_stuck": "I have an axe but still need a torch (2 cutgrass + 2 twigs). The area is picked clean. Where should I look?",
    },
    {
        "id": "campfire",
        "desc": "Build a campfire before night (3 cutgrass + 2 logs)",
        "complete": lambda st: len(st.get("fires") or []) > 0,
        "asks_when_stuck": "It's getting dark and I have no campfire. I need logs (chop trees) + cutgrass. What should I prioritize?",
    },
    {
        "id": "explore_base",
        "desc": "Explore to find a base-worthy spot (rocks/beefalo/grass)",
        "complete": lambda st: False,  # never 'complete' - asks when explored enough
        "asks_when_stuck": "I've explored several areas. Where should I set up base? (looking for: rocky biome, beefalo, grass, pig village)",
    },
    {
        "id": "armor",
        "desc": "Day 2-5: spear + log suit before hounds (day 6)",
        "complete": lambda st: "spear" in (st.get("items") or []) and "logarmor" in (st.get("items") or []),
        "asks_when_stuck": "Hounds may come on day 6 and I have no spear/armor. What should I craft first?",
    },
    {
        "id": "winter_prep",
        "desc": "Day 18 deadline: thermal stone + insulation + 40 logs",
        "complete": lambda st: False,
        "asks_when_stuck": "Winter is coming (day 21). I need thermal stone + warm clothes + log stockpile. What's the priority?",
    },
]

CRAFT_PRIORITIES = [
    ("axe", {"twigs": 1, "flint": 1}),
    ("pickaxe", {"twigs": 2, "flint": 2}),
    ("torch", {"cutgrass": 2, "twigs": 2}),
    ("spear", {"twigs": 2, "flint": 1, "rope": 1}),
    ("campfire", {"cutgrass": 3, "log": 2}),
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

RESOURCE_VALUE = {
    "flint": 5, "sapling": 4, "twigs": 4, "grass": 3, "cutgrass": 3,
    "carrot_planted": 4, "berrybush": 4, "berrybush2": 4,
    "evergreen": 3, "deciduoustree": 3, "rock1": 3, "rock2": 3,
}

# Session (2026-08-10) marsh-death postmortem: Wilson died 6x in one run,
# 4 of them to tentacles, because nothing in the agent knew threats existed
# when picking WHERE to gather - pick_target routed him straight into a
# tentacle nest chasing flint, and the flee distance (20) wasn't enough to
# clear a dense cluster. Shared list (was duplicated 3x with slightly
# different tuples in the threat-guard and retreat-invariant blocks below).
AGGRESSIVE_PREFABS = ("merm", "spider", "spider_warrior", "frog", "hound", "houndfire",
                      "tentacle", "snake", "mosquito", "bee", "killerbee",
                      "pigman", "werepig", "walrus", "depthworm",
                      "spiderqueen", "deerclops", "treeguard")
THREAT_AVOID_RADIUS = 12   # don't route a gather target within this many units of a live threat

# ---------------- the autonomous loop ----------------
def current_plan(st):
    """Stage-driven plan: gather ONLY what the current stage needs.
    If the stage is complete -> advance + ask. If needs are unmet -> roam for them.
    If nothing needed and stage incomplete -> ask the stage question (stuck)."""
    counts = st.get("item_counts") or {}
    stage = get_current_stage()
    # 1) stage complete?
    try:
        if stage["complete"](st):
            next_stage()
            stage = get_current_stage()
            ask_question_async(
                f"Stage '{stage['id']}' is next: {stage['desc']}",
                {"stage": stage["id"]})
    except Exception:
        pass
    # 2) what materials does this stage need? (derive from craft priorities)
    needs = {}
    for name, mats in CRAFT_PRIORITIES:
        if name in (st.get("equipped") or []): continue
        missing = [m for m, c in mats.items() if counts.get(m, 0) < c]
        if missing:
            for m in missing:
                needs[m] = needs.get(m, 0) + 1
    want_prefabs = set()
    if needs.get("twigs"): want_prefabs.add("sapling")
    if needs.get("cutgrass"): want_prefabs.add("grass")
    if needs.get("flint"): want_prefabs.add("flint")
    if needs.get("log"): want_prefabs.add("evergreen")
    if needs.get("rocks"): want_prefabs.add("rock1")
    if needs.get("berries"): want_prefabs.add("berrybush")
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
        if n not in RESOURCE_VALUE: continue
        # Session A Task 3: never re-target a GUID that's done or in flight
        g = e.get("guid")
        if g is not None and (g in TRACKER.done or g == TRACKER.in_flight):
            continue
        # axe-less: never target trees (need axe to chop efficiently)
        if not has_axe and n in ("evergreen", "deciduoustree"):
            continue
        if want_prefabs and n not in want_prefabs and n not in ("sapling","grass"): continue
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
    "rock2": "rocks", "berrybush": "berries", "carrot_planted": "carrot",
    "seeds": "seeds",
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

def try_craft(st):
    """Local craft: iterate taught priorities, craft what's possible."""
    counts = st.get("item_counts") or {}
    equipped = st.get("equipped") or []
    for name, mats in CRAFT_PRIORITIES:
        if name in equipped: continue
        if name in ("torch",) and any("torch" in e for e in equipped): continue
        if all(counts.get(m, 0) >= c for m, c in mats.items()):
            d_craft = log_before(st, f"craft {name} ({plan_digest(st)})",
                                 {"action": "craft", "recipe": name},
                                 f"materials present: {mats}",
                                 {f"owned.{name}": "+1"})
            send({"action": "craft", "recipe": name})
            time.sleep(2)
            if name in ("axe", "pickaxe", "spear"):
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
    start_counts = dict(st.get("item_counts") or {})
    pos = st.get("pos") or {}
    log(f"🌍 ROAM at ({pos.get('x'):.0f},{pos.get('z'):.0f}) | needs: {needs}")

    for _ in range(6):
        st = get_state()
        if st.get("isnight") and not (st.get("equipped") or []):
            log("🌙 night + no light - stop roaming")
            break
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


def _main_loop():
    global RUN_ID, last_health, _damage_log_last
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
                log(f"💀 WILSON DIED (ghost) - waiting for reflex revive, day {st.get('day')}")
                # record the run honestly
                try:
                    run_logger.end_run(RUN_ID, st, "death")
                except Exception:
                    pass
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
            # postmortem: 20 units wasn't enough to clear a dense tentacle
            # cluster - Wilson fled one tentacle's range straight into the
            # next one's. Widened to 35.
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
            continue
        # LOW HEALTH OR HUNGER -> food gathering outranks the plan. Wilson
        # nearly starved (hunger 5) because the override only checked health.
        h = st.get("health") or [150]
        hung = st.get("hunger") or [150]
        if h[0] < 50 or hung[0] < 60:
            # track attempted food spots so a dug-out carrot isn't re-targeted
            # forever (that loop had Wilson digging the same spent tuft every 8s)
            food_targets = [e for e in (st.get("nearby") or [])
                            if e.get("ok") and e.get("n") in
                            ("carrot_planted", "berrybush", "berrybush2", "seeds")
                            and e.get("d", 99) < 35
                            and (e.get("guid"), e.get("n")) not in _food_attempted]
            # prefer seeds (reliable PICKUP) over carrots/bushes (DIG/ripe-gated)
            food_targets.sort(key=lambda e: 0 if e.get("n") == "seeds" else 1)
            if food_targets:
                f = min(food_targets, key=lambda e: e.get("d", 99))
                _food_attempted.add((f.get("guid"), f.get("n")))
                d_fm = log_before(st, f"food move->({f.get('x')},{f.get('z')}) ({plan_digest(st)})",
                                  {"action": "move_to", "x": f.get("x"), "z": f.get("z")},
                                  f"food override hunger={hung[0]:.0f}", {"pos": "changes"})
                send({"action": "move_to", "x": f.get("x"), "z": f.get("z")})
                time.sleep(min(8, 2 + (f.get("d", 5))/4))
                st_f = get_state()
                log_after(d_fm, st_f)
                prod_f = PRODUCTS.get(f.get("n"), f.get("n"))
                d_fg = log_before(st_f, f"gather food {f.get('n')}->{prod_f} ({plan_digest(st_f)})",
                                  {"action": "gather_job", "prefab": f.get("n"), "count": 3},
                                  f"food override hunger={hung[0]:.0f}",
                                  {f"item_counts.{prod_f}": "+1"})
                send({"action": "gather_job", "prefab": f.get("n"), "count": 3})
                log(f"🍎 HUNGER {hung[0]:.0f}: gathering food {f.get('n')}@({f.get('x')},{f.get('z')})")
                time.sleep(8)
                st = get_state()
                log_after(d_fg, st)
                counts2 = st.get("item_counts") or {}
                for food in ("berries", "carrot", "seeds"):
                    if counts2.get(food, 0) > 0:
                        send({"action": "eat", "item": food})
                        log(f"🍽 EATING {food} (hunger {counts2.get(food)} left)")
                        time.sleep(4)
                        break
                continue
        # critical health -> EAT available food first, THEN wait
        if h[0] < 25 or (st.get("hunger") or [150])[0] < 40:
            counts = st.get("item_counts") or {}
            FOOD_ITEMS = ["berries", "carrot", "seeds", "cookedmeat", "cooked_smallmeat",
                          "smallmeat", "meat", "drumstick", "cooked_drumstick",
                          "robin", "crow", "butterfly", "mushroom", "red_mushroom",
                          "green_mushroom", "blue_mushroom", "petals"]
            ate = False
            for food in FOOD_ITEMS:
                if counts.get(food, 0) > 0:
                    send({"action": "eat", "item": food})
                    log(f"🍽 EATING {food} (low health/hunger)")
                    time.sleep(4)
                    ate = True
                    break
            if not ate:
                if h[0] < 15:
                    log("⚠️ health critical, no food available")
                    ask_question_async(
                        "I'm critically low on health/hunger with no food nearby. "
                        "Where is the nearest safe food source?",
                        {"health": h[0], "hunger": st.get("hunger"), "items": counts})
            time.sleep(3)   # Session A fix T1: don't hot-spin while critically low
            continue
        # INVARIANT 2: unexplained damage -> retreat (v8: health delta is ground truth)
        if invariants.unexplained_damage(st, last_health, deliberately_fighting=False):
            # Session A2 T3: log once per burst, not thousands per second
            now_dl = time.time()
            if now_dl - _damage_log_last >= 2:
                log("⚠️ INVARIANT: unexplained damage - retreating")
                _damage_log_last = now_dl
            send({"action": "preempt_job"})
            # move away from nearest AGGRESSIVE threat only (a robin peck or
            # darkness damage must not send Wilson fleeing into water - that's
            # how he got stuck at the shore)
            threats = [t for t in (st.get("threats") or [])
                       if t.get("hp", 0) > 0
                       and (t.get("targeting") or t.get("n") in AGGRESSIVE_PREFABS)]
            if threats:
                nearest = min(threats, key=lambda t: t.get("d", 99))
                px, pz = st.get("pos", {}).get("x", 0), st.get("pos", {}).get("z", 0)
                tx, tz = px + 40, pz + 40  # default: run NE (widened, see postmortem note above)
                for e in (st.get("nearby") or []):
                    if e.get("n") == nearest.get("n"):
                        dx, dz = px - e.get("x", px), pz - e.get("z", pz)
                        mag = math.sqrt(dx*dx + dz*dz) or 1
                        tx, tz = px + dx/mag*40, pz + dz/mag*40
                        break
                send({"action": "move_to", "x": tx, "z": tz})
                log(f"  fleeing from {nearest.get('n')} -> ({tx:.0f},{tz:.0f})")
                time.sleep(6)
            else:
                # Session A2 T3: no threat to flee (darkness/starvation/
                # freezing/sanity damage) - breathe instead of hot-spinning
                time.sleep(2)
            continue
        # INVARIANT 1: the leash - never travel further than you can get back
        base = worldmap.get_base("default") if 'worldmap' in sys.modules else {}
        if base:
            if not invariants.can_get_home(st, (base.get("x", 0), base.get("z", 0))):
                bx, bz = base.get("x"), base.get("z")
                log(f"🔗 LEASH: too far from base ({bx},{bz}) for food/light - heading home")
                d_ls = log_before(st, f"leash return ({plan_digest(st)})",
                                  {"action": "move_to", "x": bx, "z": bz},
                                  f"leash: home at ({bx},{bz})", {"pos": "changes"})
                send({"action": "preempt_job"})
                send({"action": "move_to", "x": bx, "z": bz})
                time.sleep(8)
                log_after(d_ls, get_state())
                continue
        # INVARIANT 4: campfire kit - never travel without 3 cutgrass + 2 logs.
        # TOOL-AWARE: logs need an axe (bare-hand chopping is 10x slower and
        # wastes the day). Only enforce the full kit once Wilson HAS an axe;
        # before that, gather the cutgrass half (grass needs no tool).
        counts = st.get("item_counts") or {}
        has_axe = "axe" in (st.get("items") or []) or "axe" in (st.get("equipped") or [])
        if has_axe and not invariants.has_campfire_kit(st):
            kit_msg = invariants.kit_priority_plan(st)
            log(f"🔥 KIT: {kit_msg} (priority job)")
            send({"action": "preempt_job"})
            # gather the missing items via a targeted roam
            for _ in range(2):
                st = get_state()
                for e in (st.get("nearby") or []):
                    if e.get("n") in ("grass", "evergreen", "deciduoustree") and e.get("ok"):
                        d_km = log_before(st, f"kit move->({e.get('x')},{e.get('z')}) ({plan_digest(st)})",
                                          {"action": "move_to", "x": e.get("x"), "z": e.get("z")},
                                          "campfire kit gather", {"pos": "changes"})
                        send({"action": "move_to", "x": e.get("x"), "z": e.get("z")})
                        time.sleep(5)
                        st_k = get_state()
                        log_after(d_km, st_k)
                        prod_k = PRODUCTS.get(e.get("n"), e.get("n"))
                        d_kg = log_before(st_k, f"kit gather {e.get('n')}->{prod_k} ({plan_digest(st_k)})",
                                          {"action": "gather_job", "prefab": e.get("n"), "count": 3},
                                          "campfire kit gather",
                                          {f"item_counts.{prod_k}": "+1"})
                        send({"action": "gather_job", "prefab": e.get("n"), "count": 3})
                        time.sleep(5)
                        log_after(d_kg, get_state())
                        break
        elif not has_axe:
            # no axe yet: gather only tool-less resources (grass/sapling/flint),
            # never trees. The tools stage handles axe crafting first.
            pass
        # craft what we can locally
        try_craft(st)
        # STAGE-DRIVEN roam: one pass, then evaluate
        needs, want_prefabs = current_plan(st)
        stage = get_current_stage()
        before = dict(st.get("item_counts") or {})
        end_counts, last_report = roam_once(st, want_prefabs=want_prefabs)
        # did this pass collect anything?
        gained = {k: v - before.get(k, 0) for k, v in end_counts.items() if v > before.get(k, 0)}
        if gained:
            stuck_streak = 0
        else:
            # Session A Task 2: branch on the job's own verdict, not just counts
            report = last_report or {}
            reason = report.get("reason")
            if reason == "stalled":
                # target unreachable -> pick a different one; NOT a stuck signal
                log("  ⚠️ job stalled - target unreachable, picking another (not stuck)")
            elif reason == "swing_cap":
                # tree/rock needs more swings -> re-issue with a higher count
                p = report.get("prefab")
                if p:
                    log(f"  ⚠️ swing_cap on {p} - re-issuing with count 60")
                    d_ri = log_before(st, f"re-issue gather {p}->{PRODUCTS.get(p, p)} ({plan_digest(st)})",
                                      {"action": "gather_job", "prefab": p, "count": 60},
                                      "swing_cap re-issue",
                                      {f"item_counts.{PRODUCTS.get(p, p)}": "+1"})
                    send({"action": "gather_job", "prefab": p, "count": 60})
                    time.sleep(5)
                    log_after(d_ri, get_state())
            elif report.get("ok") is False and "no matching entity" in str(report.get("error", "")):
                # target gone -> move on; NOT a stuck signal
                log("  ⚠️ target gone (no matching entity) - moving on (not stuck)")
            elif report.get("lost"):
                for k in (report.get("lost") or {}):
                    if k in ("axe", "pickaxe", "shovel", "hammer", "goldenaxe", "goldenshovel"):
                        log(f"  🔨 TOOL BROKE: {k} - needs recrafting")
            else:
                stuck_streak += 1
                if stuck_streak >= 2:
                    # STUCK: ask instead of wandering (user correction) - non-blocking
                    ask_question_async(stage["asks_when_stuck"], {
                        "stage": stage["id"], "items": end_counts,
                        "phase": st.get("phase"), "day": st.get("day")})
                    stuck_streak = 0
                    continue
        last_health = (st.get("health") or [150])[0]
        # checkpoint
        ag = load_json(AGENT_F, {})
        ag.update({"ts": time.time(), "items": end_counts, "mode": "stage", "stage": stage["id"]})
        save_json(AGENT_F, ag)
        time.sleep(2)

if __name__ == "__main__":
    main()
