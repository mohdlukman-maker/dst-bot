#!/usr/bin/env python3
"""
REFLEX DAEMON - Fast Reflex Layer for Wilson.
Runs every 200ms, deterministic, NO LLM. Keeps Wilson alive:
- hunger < 30 + food in inventory -> eat best food
- health < 50 -> eat healing food (Pierogi, Cooked Meat, Berries)
- night/dusk + no light -> light emergency torch or fuel campfire
- freezing / overheating -> temperature control reflexes
- rain / moisture -> auto-equip waterproofing
- threats / boss evasion -> rapid dodge and escape
"""
import os, time, re, json, sys, atexit, math

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

FOOD = ("pierogi", "meatballs", "baconeggs", "dragonpie",
        "cookedmeat", "cookedsmallmeat", "cooked_smallmeat", "cooked_drumstick",
        "carrot_cooked", "acorn_cooked", "berries", "carrot", "acorn",
        "smallmeat", "meat", "drumstick", "seeds",
        "blue_mushroom", "green_mushroom", "red_mushroom", "petals")

LIGHT_ITEMS = ("torch", "lantern", "minerhat", "firepit", "campfire", "coldfire", "moggles")
WATERPROOF_ITEMS = ("eyebrella", "umbrella", "strawhat", "footballhat", "raincoat")
light_reflex_armed = False

def eat_best(items):
    for f in FOOD:
        if f in items:
            send({"action": "preempt_job"})
            send({"action": "eat", "item": f})
            return f
    return None

HOSTILES = ("frog", "spider", "spider_warrior", "hound", "houndfire", "hound_wave",
            "tentacle", "snake", "mosquito", "killerbee", "walrus", "merm",
            "spider_dropper", "spider_hider", "spider_spitter", "treeguard", "bearger", "deerclops", "moose", "antlion")

def ensure_light(st):
    """Night-light reflex: Ensures Wilson never stands in the dark."""
    global light_reflex_armed
    equipped = st.get("equipped") or []
    items = st.get("items") or []
    isdusk = st.get("isdusk") or False
    isnight = st.get("isnight") or False
    phase = st.get("phase") or ""
    fires = st.get("fires") or []
    have_fire = any(f.get("d", 99) <= 25 for f in fires)

    if any(l in equipped for l in LIGHT_ITEMS):
        light_reflex_armed = False
        return None

    if have_fire and not isnight:
        light_reflex_armed = False
        return None

    if isnight or (isdusk and phase == "dusk"):
        if not have_fire and "torch" in items:
            send({"action": "equip", "item": "torch"})
            return "equipped torch (dark, no fire)"
        return None

    raw_secs = st.get("seconds_until_night")
    if isinstance(raw_secs, (int, float)) and raw_secs <= 90 and not have_fire:
        return "DUSK-WARNING: no fire for night - get a campfire"
    return None

_fire_emergency_last = 0.0

def ensure_fire_fuel(st):
    """Fuels low fire or crafts emergency torch if darkness falls without light."""
    global _fire_emergency_last
    fires = st.get("fires") or []
    items = st.get("items") or []
    equipped = st.get("equipped") or []
    isdusk = st.get("isdusk") or False
    isnight = st.get("isnight") or False

    if "log" in items or "twigs" in items:
        for f in fires:
            if f.get("d", 99) <= 15 and f.get("fuel_pct", 100) < 35:
                send({"action": "fuel", "item": "log" if "log" in items else "twigs"})
                return f"fuelled {f.get('n')} ({f.get('fuel_pct')}%)"

    if (isdusk or isnight) and not fires:
        has_light = any(l in equipped for l in LIGHT_ITEMS)
        if not has_light:
            if "torch" in items:
                send({"action": "preempt_job"})
                time.sleep(1.2)
                send({"action": "equip", "item": "torch"})
                return "EMERGENCY: dark + no fire - equipped torch"
            if time.time() - _fire_emergency_last < 10:
                return None
            counts = st.get("item_counts") or {}
            if counts.get("twigs", 0) >= 2 and counts.get("cutgrass", 0) >= 2:
                send({"action": "craft", "recipe": "torch"})
                time.sleep(1.5)
                send({"action": "equip", "item": "torch"})
                _fire_emergency_last = time.time()
                return "EMERGENCY: dark - CRAFTED+equipped torch from materials"
            if time.time() - _fire_emergency_last < 30:
                return None
            _fire_emergency_last = time.time()
            send({"action": "preempt_job"})
            return "EMERGENCY: dark + no fire + no light! Need torch materials"
    return None

def ensure_temperature(st):
    """Manages freezing (winter) and overheating (summer)."""
    # 1. Freezing check
    is_freezing = st.get("is_freezing") or (st.get("temperature", 30) < 10)
    if is_freezing:
        fires = st.get("fires") or []
        if fires:
            f = min(fires, key=lambda x: x.get("d", 99))
            if f.get("d", 99) <= 25:
                send({"action": "move_to", "x": f.get("x"), "z": f.get("z")})
                return f"freezing -> moving to warm fire ({f.get('d'):.1f}m)"
        # Emergency heat: equip thermal stone if in inventory
        items = st.get("items") or []
        if "heatrock" in items and "heatrock" not in (st.get("equipped") or []):
            send({"action": "equip", "item": "heatrock"})
            return "freezing -> equipped thermal stone"

    # 2. Overheating check (Summer)
    is_overheating = st.get("is_overheating") or (st.get("temperature", 30) > 65)
    if is_overheating:
        cold_fires = [f for f in (st.get("fires") or []) if "cold" in (f.get("n") or "")]
        if cold_fires:
            cf = min(cold_fires, key=lambda x: x.get("d", 99))
            if cf.get("d", 99) <= 25:
                send({"action": "move_to", "x": cf.get("x"), "z": cf.get("z")})
                return f"overheating -> moving to endothermic fire ({cf.get('d'):.1f}m)"
        items = st.get("items") or []
        for gear in ("eyebrella", "umbrella", "strawhat"):
            if gear in items and gear not in (st.get("equipped") or []):
                send({"action": "equip", "item": gear})
                return f"overheating -> equipped {gear}"

    return None

def ensure_weather(st):
    """Manages rain / moisture in Spring."""
    moisture = st.get("moisture", 0)
    is_raining = st.get("is_raining", False)
    if is_raining or moisture > 30:
        items = st.get("items") or []
        equipped = st.get("equipped") or []
        for wp in WATERPROOF_ITEMS:
            if wp in items and wp not in equipped:
                send({"action": "equip", "item": wp})
                return f"rain/wetness ({moisture:.0f}%) -> equipped {wp}"
    return None

def flee_threats(st):
    """If a hostile is within ~10m (or boss within ~25m), move away."""
    nb = st.get("nearby") or []
    threats = st.get("threats") or []
    pos = st.get("pos") or {}
    px, pz = pos.get("x", 0), pos.get("z", 0)
    nearest = None
    nd = 99
    
    AGGRESSIVE = ("merm", "spider", "spider_warrior", "frog", "hound", "houndfire",
                  "tentacle", "snake", "mosquito", "killerbee",
                  "pigman", "werepig", "walrus", "depthworm",
                  "spiderqueen", "deerclops", "treeguard", "bearger", "moose")
    
    for t in threats:
        if t.get("hp", 0) <= 0: continue
        if not (t.get("targeting") or t.get("n") in AGGRESSIVE):
            continue
        d = t.get("d", 99)
        if d < nd:
            nd = d
            nearest = t
            
    if nearest is None or nd > 12:
        for e in nb:
            if e.get("n") in HOSTILES and e.get("d", 99) < nd:
                nd = e.get("d")
                nearest = e

    # Bosses have larger danger radius
    is_boss = nearest and nearest.get("n") in ("deerclops", "bearger", "moose", "spiderqueen")
    threat_radius = 25 if is_boss else 10

    if nearest and nd < threat_radius:
        tx = nearest.get("x")
        tz = nearest.get("z")
        if tx is None or tz is None:
            for e in nb:
                if e.get("x") is not None and e.get("z") is not None:
                    tx, tz = e.get("x"), e.get("z")
                    break
        if tx is None or tz is None:
            tx, tz = px + 1, pz + 1
        dx, dz = px - tx, pz - tz
        mag = math.sqrt(dx*dx + dz*dz) or 1
        step = 45 if is_boss else 30
        nx, nz = px + dx/mag*step, pz + dz/mag*step
        send({"action": "preempt_job"})
        send({"action": "move_to", "x": nx, "z": nz})
        print(f"[reflex] FLEEING {nearest.get('n')} at {nd:.0f}m -> ({nx:.0f},{nz:.0f})")
        return True
    return False

last_eat = 0
_revive_sent_at = 0.0

def death_reflex(st):
    """Detects Wilson dead (ghost) -> logs death."""
    global _revive_sent_at
    is_ghost = (st or {}).get("is_ghost")
    if is_ghost is None:
        h = (st or {}).get("health") or [150]
        hcur = h[0] if isinstance(h, (list, tuple)) else h
        skel = [g for g in ((st or {}).get("nearby") or [])
                if g.get("n") == "skeleton_player"]
        is_ghost = (hcur and hcur > 45 and hcur < 55) and bool(skel)
    if not is_ghost:
        return None
    now = time.time()
    if now - _revive_sent_at > 20:
        _revive_sent_at = now
        cause = guess_death_cause(st)
        try:
            from lib import run_log as rl
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
        print(f"[reflex] 💀 WILSON DIED ({cause}) - death logged")
        return f"💀 DEAD ({cause}) - death logged"
    return "ghost"

def guess_death_cause(st):
    threats = [t for t in (st or {}).get("threats", []) if t.get("hp", 0) > 0
               and (t.get("targeting") or t.get("n") in HOSTILES)]
    if threats:
        return f"mob:{threats[0].get('n')}"
    h = (st or {}).get("hunger") or [150]
    if h[0] < 10:
        return "starvation"
    if (st or {}).get("is_freezing"):
        return "freezing"
    if (st or {}).get("is_overheating"):
        return "overheating"
    ph = (st or {}).get("phase")
    if ph in ("dusk", "night"):
        return "darkness"
    return "unknown"

if __name__ == "__main__":
    print("Reflex daemon running (100-Day survival mode). Watching hunger/health/weather/threats...")
    while True:
        try:
            st = get_state()
            if st:
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

                if flee_threats(st):
                    last_eat = now
                    time.sleep(0.5)
                    continue

                fr = ensure_fire_fuel(st)
                if fr:
                    print(f"[reflex] {fr}")
                    time.sleep(0.5)
                    continue

                tr = ensure_temperature(st)
                if tr:
                    print(f"[reflex] {tr}")
                    time.sleep(0.5)
                    continue

                wr = ensure_weather(st)
                if wr:
                    print(f"[reflex] {wr}")
                    time.sleep(0.5)
                    continue

                lr = ensure_light(st)
                if lr:
                    print(f"[reflex] {lr}")
                    time.sleep(0.5)
                    continue

                if hunger < 30 and (items or ground) and now - last_eat > 5:
                    ate = eat_best(items + ground)
                    if ate:
                        print(f"[reflex] hunger {hunger:.0f} -> eating {ate}")
                    elif "seeds" in ground:
                        send({"action": "gather_job", "prefab": "seeds", "count": 1})
                        time.sleep(6)
                        send({"action": "eat", "item": "seeds"})
                        print(f"[reflex] hunger {hunger:.0f} -> gathered+eaten ground seed")
                    last_eat = now

                elif health < 50 and now - last_eat > 15:
                    HEALING_FOOD = ("pierogi", "meatballs", "baconeggs", "dragonpie",
                                   "berries", "carrot", "cookedmeat", "smallmeat",
                                   "cookedsmallmeat", "cooked_smallmeat", "meat",
                                   "drumstick", "cooked_drumstick", "blue_mushroom",
                                   "green_mushroom", "red_mushroom")
                    healing_items = [f for f in (items + ground) if f in HEALING_FOOD]
                    if healing_items:
                        ate = eat_best(healing_items)
                        if ate:
                            print(f"[reflex] health {health:.0f} -> eating {ate} (healing)")
                            last_eat = now
            time.sleep(0.2)
        except KeyboardInterrupt:
            print("Reflex daemon stopped.")
            break
        except Exception as e:
            time.sleep(1)
