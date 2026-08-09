#!/usr/bin/env python3
"""
REFLEX DAEMON - Claude's reflex layer for Wilson.
Runs every 200ms, deterministic, NO LLM. Keeps Wilson alive:
- hunger < 30 + food in inventory -> eat best food
- health < 30 -> eat healing food
- night/dusk + no light -> (future: warn / move to campfire)
- owns command.json writes (single-writer guarantee via lock file)

The deliberative layer (Hermes LLM) makes goals; this daemon keeps
Wilson from dying while the LLM thinks. Run with: python reflex.py
"""
import os, time, re, json, sys, atexit

DOC = os.path.join(os.path.expanduser("~"), "Documents", "Klei", "DoNotStarveTogether")
CS = os.path.join(DOC, "40630831", "client_save")
FP = os.path.join(CS, "dst_ai_bot_state")
LOCK = os.path.join(CS, "dst_ai_bot_DAEMON.lock")

# Single-instance guard
if os.path.exists(LOCK):
    try:
        with open(LOCK) as f:
            pid = int(f.read().strip())
        import subprocess
        r = subprocess.run(["tasklist", "/FI", f"PID eq {pid}"], capture_output=True, text=True)
        if str(pid) in r.stdout:
            print(f"Reflex daemon already running (PID {pid}). Exiting.")
            sys.exit(0)
    except Exception:
        pass
with open(LOCK, "w") as f:
    f.write(str(os.getpid()))
atexit.register(lambda: os.path.exists(LOCK) and os.remove(LOCK))

def get_state():
    try:
        raw = open(FP, "rb").read()
        m = re.match(rb"KLEI\s+\d+\s+(.*)", raw, re.DOTALL)
        return json.loads(m.group(1).decode())
    except Exception:
        return None

def send(cmd):
    cmdid = int(time.time() * 1000)
    cmd = dict(cmd); cmd["id"] = cmdid
    with open(os.path.join(CS, "dst_ai_bot_command"), "wb") as f:
        f.write(b"KLEI     1 " + json.dumps(cmd).encode())

FOOD = ("berries", "carrot", "cookedmeat", "smallmeat", "cookedsmallmeat", "meat", "blue_mushroom", "green_mushroom")

# LIGHT REFLEX (Claude v4 Q4): at dusk-minus-90s, ensure a light source is equipped.
# Wilson should never ARRIVE at night without a plan. This fires pre-emptively.
LIGHT_ITEMS = ("torch", "lantern", "minerhat", "firepit", "campfire", "coldfire", "moggles")
light_reflex_armed = False  # armed when dusk approaches; disarmed once equipped/night over

def eat_best(items):
    # PREEMPT any running gather job first (Claude: don't starve while chopping)
    send({"action": "preempt_job"})
    for f in FOOD:
        if f in items:
            send({"action": "eat", "item": f})
            return f
    return None

# Hostiles to flee from (frogs killed Wilson - session 2 lesson)
HOSTILES = ("frog", "spider", "spider_warrior", "hound", "houndfire", "hound_wave",
            "tentacle", "snake", "mosquito", "killerbee", "walrus", "merm",
            "spider_dropper", "spider_hider", "spider_spitter", "treeguard", "bearger", "deerclops")

def ensure_light(st):
    """Dusk minus ~90s: if no light equipped, arm the reflex to prep a torch."""
    global light_reflex_armed
    equipped = st.get("equipped") or []
    items = st.get("items") or []
    secs_night = st.get("seconds_until_night") or 0
    isnight = st.get("isnight") or False

    # already have light equipped -> disarmed
    if any(l in equipped for l in LIGHT_ITEMS):
        light_reflex_armed = False
        return None
    # it's night and no light -> emergency: warn only (deliberative layer acts)
    if isnight:
        return None
    # day time, no night coming soon -> disarmed
    if secs_night > 90:
        light_reflex_armed = False
        return None

    # dusk approaching (<90s) and no light equipped
    if not light_reflex_armed:
        light_reflex_armed = True
        if "torch" in items:
            send({"action": "equip", "item": "torch"})
            return "equipped torch (dusk prep)"
        # no torch yet: preempt jobs and signal the deliberative layer
        send({"action": "preempt_job"})
        return "DUSK-WARNING: no torch, materials needed (twigs+cutgrass)"
    return None

_fire_emergency_last = 0.0  # cooldown for the dark+no-light emergency (30s)

def ensure_fire_fuel(st):
    """Claude P0.1 + v6: fuel a low fire (threshold), AND detect fire ABSENCE.
    A burned-out campfire becomes ash (no fueled entity) - so also invert:
    (dusk or night) with no fire within 20 and no light equipped = EMERGENCY."""
    fires = st.get("fires") or []
    items = st.get("items") or []
    equipped = st.get("equipped") or []
    isdusk = st.get("isdusk") or False
    isnight = st.get("isnight") or False

    # 1) threshold: fuel a low fire if we have fuel
    if "log" in items or "twigs" in items:
        for f in fires:
            if f.get("d", 99) <= 15 and f.get("fuel_pct", 100) < 35:
                send({"action": "fuel", "item": "log" if "log" in items else "twigs"})
                return f"fuelled {f.get('n')} ({f.get('fuel_pct')}%)"

    # 2) ABSENCE check (Claude v6): dark + no fire nearby + no light = emergency
    if (isdusk or isnight) and not fires:
        has_light = any(l in equipped for l in LIGHT_ITEMS)
        if not has_light:
            # if we have a torch in inventory, equip it NOW
            if "torch" in items:
                send({"action": "equip", "item": "torch"})
                return "EMERGENCY: dark + no fire - equipped torch"
            # no torch held: if we carry the materials, CRAFT one immediately
            # (this is the night-death gap: warn-only left Wilson in the dark
            # with 4 twigs + 14 cutgrass in his pocket)
            counts = st.get("item_counts") or {}
            if counts.get("twigs", 0) >= 2 and counts.get("cutgrass", 0) >= 2:
                send({"action": "craft", "recipe": "torch"})
                time.sleep(1.5)
                send({"action": "equip", "item": "torch"})
                _fire_emergency_last = time.time()
                return "EMERGENCY: dark - CRAFTED+equipped torch from materials"
            # COOLDOWN: don't spam preempt_job every 0.2s (that froze Wilson -
            # the agent's commands were preempted before they could move him).
            if time.time() - _fire_emergency_last < 30:
                return None
            _fire_emergency_last = time.time()
            send({"action": "preempt_job"})
            return "EMERGENCY: dark + no fire + no light! Need torch materials"
    return None

def ensure_temperature(st):
    """Claude P0.3: if freezing and a fire is within 20m, move toward it."""
    if st.get("is_freezing"):
        fires = st.get("fires") or []
        if fires:
            f = min(fires, key=lambda x: x.get("d", 99))
            if f.get("d", 99) <= 20:
                send({"action": "move_to", "x": f.get("x"), "z": f.get("z")})
                return f"freezing -> moving to fire ({f.get('d')}m)"
    return None

def flee_threats(st):
    """If a hostile is within ~10m, move away. Uses state's threats[] (the mod
    tags ALL _combat entities - merm, pigman, spider, frog...) instead of a
    hardcoded prefab list that missed merm and let Wilson die."""
    nb = st.get("nearby") or []
    threats = st.get("threats") or []
    pos = st.get("pos") or {}
    px, pz = pos.get("x", 0), pos.get("z", 0)
    nearest = None
    nd = 99
    # v8 discriminator: NOT every _combat entity is a threat (crows/robins/
    # rabbits are passive). Flee: targeting us, OR known aggressive prefabs.
    AGGRESSIVE = ("merm", "spider", "spider_warrior", "frog", "hound", "houndfire",
                  "tentacle", "snake", "mosquito", "bee", "killerbee",
                  "pigman", "werepig", "walrus", "depthworm",
                  "spiderqueen", "deerclops", "treeguard")
    for t in threats:
        if t.get("hp", 0) <= 0: continue       # dead/ghost
        if not (t.get("targeting") or t.get("n") in AGGRESSIVE):
            continue                            # passive _combat: not a threat
        d = t.get("d", 99)
        if d < nd:
            nd = d
            nearest = t
    # fall back to nearby if threats[] is missing the mob (belt and braces)
    if nearest is None or nd > 12:
        for e in nb:
            if e.get("n") in HOSTILES and e.get("d", 99) < nd:
                nd = e.get("d")
                nearest = e
    if nearest and nd < 10:
        import math
        # escape direction: away from the threat's x/z (mod now provides them)
        tx = nearest.get("x")
        tz = nearest.get("z")
        if tx is None or tz is None:
            # fallback: nearest nearby entity that HAS coords
            for e in nb:
                if e.get("x") is not None and e.get("z") is not None:
                    tx, tz = e.get("x"), e.get("z")
                    break
        if tx is None or tz is None:
            tx, tz = px + 1, pz + 1   # unknown direction: nudge NE
        dx, dz = px - tx, pz - tz     # opposite of threat direction
        mag = math.sqrt(dx*dx + dz*dz) or 1
        step = 15
        nx, nz = px + dx/mag*step, pz + dz/mag*step
        send({"action": "preempt_job"})
        send({"action": "move_to", "x": nx, "z": nz})
        print(f"[reflex] FLEEING {nearest.get('n')} at {nd:.0f}m -> ({nx:.0f},{nz:.0f})")
        return True
    return False

last_eat = 0
_revive_sent_at = 0.0  # only revive once per death (avoid spam)

def death_reflex(st):
    """USER ASK: detect Wilson dead (ghost) -> auto-revive + log the death.
    A ghost reads health 50 and carries the playerghost TAG (st.is_ghost from
    the mod). Log the death with cause so we LEARN from every run."""
    global _revive_sent_at
    is_ghost = (st or {}).get("is_ghost")
    if is_ghost is None:
        # fallback for old mod: ghost health reads exactly ~50 (half of max)
        # AND a skeleton_player marks the death spot nearby
        h = (st or {}).get("health") or [150]
        hcur = h[0] if isinstance(h, (list, tuple)) else h
        skel = [g for g in ((st or {}).get("nearby") or [])
                if g.get("n") == "skeleton_player"]
        is_ghost = (hcur and hcur > 45 and hcur < 55) and bool(skel)
    if not is_ghost:
        return None
    now = time.time()
    if now - _revive_sent_at > 20:   # allow one revive per death, retry after 20s
        _revive_sent_at = now
        send({"action": "revive"})
        cause = guess_death_cause(st)
        # record the death in the run log (measurement layer)
        try:
            from lib import run_log as rl
            # Session A2 T4: the agent owns run lifecycle; read its run_id
            # from the file it publishes. Fallback: distinct reflex id.
            rid = ""
            try:
                with open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                       "data", "current_run.txt")) as f:
                    rid = f.read().strip()
            except Exception:
                pass
            if not rid:
                rid = f"reflex-{int(time.time())}"
            rl.end_run(rid, st, cause)
        except Exception:
            pass
        print(f"[reflex] 💀 WILSON DIED ({cause}) - revive sent")
        return f"💀 DEAD ({cause}) - revive sent"
    return "ghost (revive cooldown)"

def guess_death_cause(st):
    """Best-effort cause from the state at death: threats, darkness, hunger."""
    threats = [t for t in (st or {}).get("threats", []) if t.get("hp", 0) > 0
               and (t.get("targeting") or t.get("n") in (
                   "merm", "spider", "spider_warrior", "frog", "hound", "houndfire",
                   "tentacle", "snake", "mosquito", "bee", "killerbee",
                   "pigman", "werepig", "walrus", "depthworm",
                   "spiderqueen", "deerclops", "treeguard"))]
    if threats:
        return f"mob:{threats[0].get('n')}"
    h = (st or {}).get("hunger") or [150]
    if h[0] < 10:
        return "starvation"
    ph = (st or {}).get("phase")
    if ph in ("dusk", "night"):
        return "darkness"
    return "unknown"

print("Reflex daemon running. Watching hunger/health...")
while True:
    try:
        st = get_state()
        if st:
            # REFLEX -1: DEATH (user ask): ghost -> auto-revive + log (top priority)
            dr = death_reflex(st)
            if dr:
                print(f"[reflex] {dr}")
                time.sleep(1)
                continue
            hunger = (st.get("hunger") or [150, 150])[0]
            health = (st.get("health") or [150, 150])[0]
            items = st.get("items") or []
            ground = [g.get("n") for g in (st.get("ground_items") or [])]
            now = time.time()
            # REFLEX 0: flee hostiles (highest priority - frogs killed Wilson)
            if flee_threats(st):
                last_eat = now
                time.sleep(0.5)
                continue
            # REFLEX 0.4: fire fuel check (Claude P0.1 - campfire burns out!)
            fr = ensure_fire_fuel(st)
            if fr:
                print(f"[reflex] {fr}")
                time.sleep(0.5)
                continue
            # REFLEX 0.45: freezing -> fire (Claude P0.3)
            tr = ensure_temperature(st)
            if tr:
                print(f"[reflex] {tr}")
                time.sleep(0.5)
                continue
            # REFLEX 0.5: dusk-minus-90s light prep (Claude v4: never ARRIVE at night)
            lr = ensure_light(st)
            if lr:
                print(f"[reflex] {lr}")
                time.sleep(0.5)
                continue
            # REFLEX 1: starving + food -> eat (cooldown 5s)
            if hunger < 30 and (items or ground) and now - last_eat > 5:
                ate = eat_best(items + ground)
                if ate:
                    print(f"[reflex] hunger {hunger:.0f} -> eating {ate}")
                    last_eat = now
                elif "seeds" in ground:
                    # ground seeds are reliable food: gather one, then eat
                    send({"action": "gather_job", "prefab": "seeds", "count": 1})
                    time.sleep(6)
                    send({"action": "eat", "item": "seeds"})
                    last_eat = now
                    print(f"[reflex] hunger {hunger:.0f} -> gathered+eaten ground seed")
            # REFLEX 2: critical health + food -> eat (cooldown 8s)
            elif health < 25 and (items or ground) and now - last_eat > 8:
                ate = eat_best(items + ground)
                if ate:
                    print(f"[reflex] health {health:.0f} -> eating {ate}")
                    last_eat = now
        time.sleep(0.2)
    except KeyboardInterrupt:
        print("Reflex daemon stopped.")
        break
    except Exception as e:
        time.sleep(1)
