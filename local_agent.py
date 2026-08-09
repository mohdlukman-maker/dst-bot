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

RUN_ID = run_logger.start_run(mod_version="v8-invariants", notes="autonomous agent run")
last_health = None   # module-level: shared by main() and _main_loop()
_food_attempted = set()  # (guid, prefab) food spots tried this life (re-target guard)

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
        return json.loads(m.group(1).decode())
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

def ask_question(question, context=None):
    save_json(QUESTION_F, {
        "ts": time.time(),
        "question": question,
        "context": context or {},
        "stage": current_stage_id(),
    })
    log(f"❓ QUESTION ASKED: {question}")
    # wait for the AI to answer (poll agent_answer.json)
    waited = 0
    while waited < 300:   # up to 5 min
        if os.path.exists(ANSWER_F):
            try:
                ans = json.load(open(ANSWER_F, encoding="utf-8"))
                os.remove(ANSWER_F)
                log(f"💬 ANSWER: {ans.get('answer')}")
                return ans
            except Exception:
                pass
        time.sleep(5)
        waited += 5
    log("⏰ no answer in 5 min - resuming cautiously")
    return {"answer": "none"}

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
            ask_question(
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
    nb = st.get("nearby") or []
    pos = st.get("pos") or {}
    px, pz = pos.get("x", 0), pos.get("z", 0)
    counts = st.get("item_counts") or {}
    has_axe = "axe" in (st.get("items") or []) or "axe" in (st.get("equipped") or [])
    best, best_score = None, -1
    for e in nb:
        n = e.get("n")
        if not e.get("ok"): continue
        if n not in RESOURCE_VALUE: continue
        # axe-less: never target trees (need axe to chop efficiently)
        if not has_axe and n in ("evergreen", "deciduoustree"):
            continue
        if want_prefabs and n not in want_prefabs and n not in ("sapling","grass"): continue
        if (n, e.get("x"), e.get("z")) in visited: continue
        d = e.get("d", 99)
        if d > 40: continue
        # score = value + learned reliability - distance
        score = RESOURCE_VALUE.get(n, 1) + gather_reliability(n)*2 - d*0.1
        if score > best_score:
            best_score = score
            best = (n, e.get("x"), e.get("z"), d)
    return best

def try_craft(st):
    """Local craft: iterate taught priorities, craft what's possible."""
    counts = st.get("item_counts") or {}
    equipped = st.get("equipped") or []
    for name, mats in CRAFT_PRIORITIES:
        if name in equipped: continue
        if name in ("torch",) and any("torch" in e for e in equipped): continue
        if all(counts.get(m, 0) >= c for m, c in mats.items()):
            send({"action": "craft", "recipe": name})
            time.sleep(2)
            if name in ("torch", "axe", "pickaxe", "spear"):
                send({"action": "equip", "item": name})
                time.sleep(1)
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

def roam_once(st, settle=5, want_prefabs=None):
    """One autonomous roam pass: pick target -> walk -> fire gather -> verify at end."""
    needs, wp = current_plan(st)
    if want_prefabs is None:
        want_prefabs = wp
    visited = set()
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
                    # fallback: next cardinal direction (avoid immediate repeat)
                    dx, dz = [(1,0),(0,1),(-1,0),(0,-1)][explored % 4]
                    dest = (px2 + dx*40, pz2 + dz*40)
                send({"action": "move_to", "x": dest[0], "z": dest[1]})
                log(f"🧭 explore -> ({dest[0]:.0f},{dest[1]:.0f})")
                time.sleep(10)
                st2 = get_state()
                pos = st2.get("pos") or pos
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
                return end_counts
            continue
        n, tx, tz, d = t
        visited.add((n, tx, tz))
        send({"action": "move_to", "x": tx, "z": tz})
        time.sleep(min(9, 2 + d/4))
        # count = max_swings in the mod. Trees need 10-20 swings (never cap at 3);
        # pick-mode targets (grass/sapling/flint) finish in 1 swing anyway.
        swing_cap = 25 if n in ("evergreen", "deciduoustree", "rock1", "rock2") else 3
        send({"action": "gather_job", "prefab": n, "count": swing_cap})
        log(f"  🎯 {n}@({tx},{tz}) fired (max_swings={swing_cap})")
        # settle must cover the work time: trees need ~20s, picks ~5s
        time.sleep(settle + (18 if n in ("evergreen", "deciduoustree", "rock1", "rock2") else 0))
        # LOCAL verification after each gather
        st = get_state()
        gained = verify_collection(start_counts, dict(st.get("item_counts") or {}))
        if gained:
            for g, cnt in gained.items():
                learn_gather(g, True)
            start_counts = dict(st.get("item_counts") or {})
            log(f"  ✅ confirmed local: {gained}")

    # batch confirmation at end
    st = get_state()
    end_counts = dict(st.get("item_counts") or {})
    gained = verify_collection(dict(st.get("item_counts") or {}), end_counts)  # no-op; use saved start
    return end_counts

def main():
    global RUN_ID
    log("🤖 LOCAL AGENT ONLINE (plan-driven + v8 invariants)")
    stuck_streak = 0
    # seed the world map with current surroundings
    try:
        from lib import world_map as wm
        wm.observe("default", get_state())
    except Exception:
        pass
    try:
        _main_loop()
    except KeyboardInterrupt:
        run_logger.end_run(RUN_ID, get_state(), "manual_stop")
        raise
    except Exception as e:
        run_logger.end_run(RUN_ID, get_state(), "crash", {"notes": str(e)})
        raise
    finally:
        run_logger.end_run(RUN_ID, get_state(), "unknown")


def _main_loop():
    global RUN_ID, last_health
    while True:
        st = get_state()
        if not st.get("pos"):
            time.sleep(3); continue
        # paused?
        age = time.time() - os.path.getmtime(STATE_F) if os.path.exists(STATE_F) else 999
        if age > 10:
            time.sleep(5); continue
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
                send({"action": "move_to", "x": f.get("x"), "z": f.get("z")})
                time.sleep(min(8, 2 + (f.get("d", 5))/4))
                send({"action": "gather_job", "prefab": f.get("n"), "count": 3})
                log(f"🍎 HUNGER {hung[0]:.0f}: gathering food {f.get('n')}@({f.get('x')},{f.get('z')})")
                time.sleep(8)
                st = get_state()
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
                    ask_question(
                        "I'm critically low on health/hunger with no food nearby. "
                        "Where is the nearest safe food source?",
                        {"health": h[0], "hunger": st.get("hunger"), "items": counts})
                    time.sleep(5)
            continue
        # THREAT GUARD (v8 Q1 discriminator): NOT every _combat entity is a threat.
        # crows/robins/rabbits/butterflies/beefalo carry _combat but are passive.
        # Real threats: (a) targeting us, (b) hostile/monster tagged, (c) known
        # aggressive prefabs. Passive _combat = neutral, ignore.
        close_threats = [t for t in (st.get("threats") or [])
                         if t.get("hp", 0) > 0 and t.get("d", 99) < 10
                         and (t.get("targeting")
                              or t.get("n") in ("merm", "spider", "spider_warrior",
                                                "frog", "hound", "houndfire", "tentacle",
                                                "snake", "mosquito", "bee", "killerbee",
                                                "pigman", "werepig", "walrus", "depthworm",
                                                "spiderqueen", "deerclops", "treeguard"))]
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
            nx2, nz2 = px2 + dx2/mag2*20, pz2 + dz2/mag2*20
            send({"action": "preempt_job"})
            send({"action": "move_to", "x": nx2, "z": nz2})
            log(f"🏃 FLEEING from {names} -> ({nx2:.0f},{nz2:.0f})")
            time.sleep(6)
            # only AFTER disengaging, ask what to do about the area
            st_after = get_state()
            if not [t for t in (st_after.get("threats") or []) if t.get("d", 99) < 12]:
                log("   disengaged - noting danger spot, continuing")
            else:
                log("   STILL threatened - keeping distance")
            continue
        # INVARIANT 2: unexplained damage -> retreat (v8: health delta is ground truth)
        if invariants.unexplained_damage(st, last_health, deliberately_fighting=False):
            log("⚠️ INVARIANT: unexplained damage - retreating")
            send({"action": "preempt_job"})
            # move away from nearest AGGRESSIVE threat only (a robin peck or
            # darkness damage must not send Wilson fleeing into water - that's
            # how he got stuck at the shore)
            _AGGR = ("merm", "spider", "spider_warrior", "frog", "hound", "houndfire",
                     "tentacle", "snake", "mosquito", "bee", "killerbee",
                     "pigman", "werepig", "walrus", "depthworm",
                     "spiderqueen", "deerclops", "treeguard")
            threats = [t for t in (st.get("threats") or [])
                       if t.get("hp", 0) > 0
                       and (t.get("targeting") or t.get("n") in _AGGR)]
            if threats:
                nearest = min(threats, key=lambda t: t.get("d", 99))
                px, pz = st.get("pos", {}).get("x", 0), st.get("pos", {}).get("z", 0)
                tx, tz = px + 30, pz + 30  # default: run NE
                for e in (st.get("nearby") or []):
                    if e.get("n") == nearest.get("n"):
                        dx, dz = px - e.get("x", px), pz - e.get("z", pz)
                        mag = math.sqrt(dx*dx + dz*dz) or 1
                        tx, tz = px + dx/mag*30, pz + dz/mag*30
                        break
                send({"action": "move_to", "x": tx, "z": tz})
                log(f"  fleeing from {nearest.get('n')} -> ({tx:.0f},{tz:.0f})")
                time.sleep(6)
            continue
        # INVARIANT 1: the leash - never travel further than you can get back
        base = worldmap.get_base("default") if 'worldmap' in sys.modules else {}
        if base:
            if not invariants.can_get_home(st, (base.get("x", 0), base.get("z", 0))):
                bx, bz = base.get("x"), base.get("z")
                log(f"🔗 LEASH: too far from base ({bx},{bz}) for food/light - heading home")
                send({"action": "preempt_job"})
                send({"action": "move_to", "x": bx, "z": bz})
                time.sleep(8)
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
                        send({"action": "move_to", "x": e.get("x"), "z": e.get("z")})
                        time.sleep(5)
                        send({"action": "gather_job", "prefab": e.get("n"), "count": 3})
                        time.sleep(5)
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
        end_counts = roam_once(st, want_prefabs=want_prefabs)
        # did this pass collect anything?
        gained = {k: v - before.get(k, 0) for k, v in end_counts.items() if v > before.get(k, 0)}
        if gained:
            stuck_streak = 0
        else:
            stuck_streak += 1
            if stuck_streak >= 2:
                # STUCK: ask instead of wandering (user correction)
                ask_question(stage["asks_when_stuck"], {
                    "stage": stage["id"], "items": end_counts,
                    "phase": st.get("phase"), "day": st.get("day")})
                stuck_streak = 0
        last_health = (st.get("health") or [150])[0]
        # checkpoint
        ag = load_json(AGENT_F, {})
        ag.update({"ts": time.time(), "items": end_counts, "mode": "stage", "stage": stage["id"]})
        save_json(AGENT_F, ag)
        time.sleep(2)

if __name__ == "__main__":
    main()
