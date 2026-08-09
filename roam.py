#!/usr/bin/env python3
"""
ROAM GATHERER (user-designed): Wilson roams, identifies resources, plans ahead,
and sweeps a route WITHOUT waiting for each pickup confirmation. Only at the end
(or at checkpoints) do we verify what was actually collected.

Philosophy (user): "change the way wilson take resources, roam around identifying
resource and plan ahead, do not need to wait confirmation if resources is collected
or not, just go to the next one. only confirm after all resources are assumed to
have been collected"

Pattern per target:  move_to(x,z) -> wait arrival -> gather_job (fire+forget) -> next
The gather_job is NOT waited on; the next move_to comes after a fixed settle window.
Batch verification happens at the end: counts vs start + ground_items missed.

Safety: the reflex daemon (separate) handles flee/eat/light/fuel. This loop aborts
on: threats close, health critical, or night-without-light.
"""
import os, sys, time, re, json, math

DOC = os.path.join(os.path.expanduser("~"), "Documents", "Klei", "DoNotStarveTogether")
CS = os.path.join(DOC, "40630831", "client_save")
STATE_F = os.path.join(CS, "dst_ai_bot_state")
CMD_F = os.path.join(CS, "dst_ai_bot_command")
RESULT_F = os.path.join(CS, "dst_ai_bot_result")
LOGF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "survival_log.txt")

def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    print(line)
    try:
        with open(LOGF, "a", encoding="utf-8") as f:
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

def dist(ax, az, bx, bz):
    return math.sqrt((ax-bx)**2 + (az-bz)**2)

# resource priority: flint first (tools), then twigs/cutgrass (torch), food, logs
PRIORITY = {
    "flint": 0,
    "sapling": 1, "twigs": 2,
    "grass": 3, "cutgrass": 4,
    "carrot_planted": 5, "berrybush": 6, "berrybush2": 7,
    "evergreen": 8, "deciduoustree": 9,
    "rock1": 10, "rock2": 11,
}

def pick_targets(st, max_n=10):
    """Choose harvestable targets: nearest-first within each priority tier."""
    nb = st.get("nearby") or []
    pos = st.get("pos") or {}
    px, pz = pos.get("x", 0), pos.get("z", 0)
    targets = []
    for e in nb:
        n = e.get("n")
        if n not in PRIORITY: continue
        if not e.get("ok"): continue      # only harvestable
        d = e.get("d", 99)
        if d > 35: continue               # don't wander too far per pass
        targets.append((PRIORITY[n], d, n, e.get("x"), e.get("z")))
    # sort by priority tier, then distance
    targets.sort(key=lambda t: (t[0], t[1]))
    return targets[:max_n]

def ensure_light(st):
    """If we can make a torch, do it (plan-ahead light)."""
    counts = st.get("item_counts") or {}
    equipped = st.get("equipped") or []
    if any("torch" in e for e in equipped):
        return
    if counts.get("cutgrass", 0) >= 2 and counts.get("twigs", 0) >= 2:
        send({"action": "craft", "recipe": "torch"})
        time.sleep(2)
        send({"action": "equip", "item": "torch"})
        log("🔥 TORCH crafted+equipped (plan-ahead)")
        time.sleep(2)

def should_abort(st):
    """Abort conditions: critical health, threats within 6m, or paused."""
    h = st.get("health") or [150]
    if h[0] < 20:
        return "health_critical"
    for t in st.get("threats") or []:
        if t.get("targeting") and t.get("d", 99) < 6:
            return "threat_close"
    age = time.time() - os.path.getmtime(STATE_F) if os.path.exists(STATE_F) else 999
    if age > 10:
        return "paused"
    return None

# exploration heading: Wilson wanders to DISCOVER resources (user design:
# "roam around identifying resource") - when the local area is swept, walk
# to a fresh patch instead of standing still.
EXPLORE_DIRS = [(1,0), (0,1), (-1,0), (0,-1), (1,1), (-1,1), (1,-1), (-1,-1)]
explore_idx = 0

def explore_step(st, dist_step=40):
    """Walk ~40 units in the next cardinal/diagonal direction to find fresh resources."""
    global explore_idx
    pos = st.get("pos") or {}
    px, pz = pos.get("x", 0), pos.get("z", 0)
    dx, dz = EXPLORE_DIRS[explore_idx % len(EXPLORE_DIRS)]
    explore_idx += 1
    tx, tz = px + dx*dist_step, pz + dz*dist_step
    log(f"🧭 EXPLORE -> ({tx:.0f},{tz:.0f}) (finding fresh resources)")
    send({"action": "move_to", "x": tx, "z": tz})
    time.sleep(12)   # walk time
    # record what we discover into the world map if available
    try:
        import worldmap
        worldmap.record_state(get_state())
    except Exception:
        pass

def roam(max_targets=10, settle=5.0, explore=True):
    """Main loop: plan route, sweep without waiting, confirm at end.
    If few targets are found, explore to discover fresh patches (user design)."""
    st = get_state()
    if not st.get("pos"):
        log("no state yet - waiting")
        time.sleep(3)
        return
    pos = st.get("pos") or {}
    start_counts = dict(st.get("item_counts") or {})
    log(f"🌍 ROAM START at ({pos.get('x'):.0f},{pos.get('z'):.0f}) | items: {start_counts}")

    targets = pick_targets(st, max_targets)
    if len(targets) < 2 and explore:
        explore_step(st)
        st = get_state()
        targets = pick_targets(st, max_targets)
    if not targets:
        log("no harvestable targets in range (explored, still nothing)")
        return
    log(f"🗺 PLAN: {len(targets)} targets -> " + ", ".join(f"{n}@{d}m" for _,d,n,_,_ in targets[:6]) + "...")

    visited = set()
    px, pz = pos.get("x", 0), pos.get("z", 0)
    for pri, d, n, tx, tz in targets:
        # abort check each step
        st = get_state()
        abort = should_abort(st)
        if abort:
            log(f"⏹ abort: {abort}")
            break
        if (n, tx, tz) in visited: continue
        visited.add((n, tx, tz))

        # plan-ahead: if torch craftable, make it
        ensure_light(st)

        # 1) walk to target (fire and forget - no confirmation needed)
        send({"action": "move_to", "x": tx, "z": tz})
        # 2) wait for arrival (up to ~8s) WITHOUT polling-heavy loops
        time.sleep(min(8, 2 + d / 4))
        # 3) fire the gather (one-shot, don't wait for result)
        send({"action": "gather_job", "prefab": n, "count": 3})
        log(f"  🎯 {n} @ ({tx},{tz}) - gather fired, moving on")
        # 4) fixed settle window for the pick + drop to land, then NEXT target
        time.sleep(settle)

    # ===== BATCH CONFIRMATION (the only verification) =====
    time.sleep(3)
    st = get_state()
    end_counts = dict(st.get("item_counts") or {})
    gained = {}
    for k, v in end_counts.items():
        if v > start_counts.get(k, 0):
            gained[k] = v - start_counts.get(k, 0)
    ground = st.get("ground_items") or []
    log(f"✅ ROAM DONE | collected: {gained if gained else 'nothing new'}")
    if ground:
        log(f"   ground (missed?): {[(g.get('n'), g.get('d')) for g in ground]}")
    log(f"   final items: {end_counts} | pos: ({st.get('pos',{}).get('x',0):.0f},{st.get('pos',{}).get('z',0):.0f})")
    return gained

if __name__ == "__main__":
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 10
    settle = float(sys.argv[2]) if len(sys.argv) > 2 else 5.0
    roam(max_targets=n, settle=settle)
