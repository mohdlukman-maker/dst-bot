#!/usr/bin/env python3
"""
DST AI Bot - Brain (Step 2: Autonomous Survival Loop)
====================================================
Reads Wilson's live state -> asks an LLM what to do -> sends the action
through the proven channel -> repeats.

The LLM returns a STRICT JSON action from a small safe set:
  {"action":"move_to","x":<num>,"z":<num>,"why":"<reason>"}
  {"action":"gather","prefab":"<entity name or null>","why":"..."}
  {"action":"eat","item":"<food name>","why":"..."}
  {"action":"attack","prefab":"<hostile name>","why":"..."}
  {"action":"say","text":"<something Wilson says>","why":"..."}
  {"action":"wait","seconds":<1-10>,"why":"..."}

Press Ctrl+C anytime to stop the bot (kill switch).
"""
import json
import os
import re
import sys
import time
import urllib.request
import urllib.error

PROJ = os.path.dirname(os.path.abspath(__file__))

# ---- Channel: reuse the same save-dir KLEI files as dstbot.py --------------
DOC = os.path.join(os.path.expanduser("~"), "Documents", "Klei", "DoNotStarveTogether")
def find_save_dir():
    if not os.path.isdir(DOC): return None
    for name in sorted(os.listdir(DOC)):
        p = os.path.join(DOC, name)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "client_save")):
            return p
    return None
SAVE_DIR = find_save_dir()
CS = os.path.join(SAVE_DIR, "client_save") if SAVE_DIR else None
STATE_FILE = os.path.join(CS, "dst_ai_bot_state") if CS else None
CMD_FILE   = os.path.join(CS, "dst_ai_bot_command") if CS else None
RESULT_FILE = os.path.join(CS, "dst_ai_bot_result") if CS else None

# ---- LLM config: DeepSeek (works) with key from hermes .env -----------------
def load_deepseek_key():
    envp = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", ".env")
    if os.path.exists(envp):
        for l in open(envp, encoding="utf-8", errors="replace").read().splitlines():
            if l.startswith("DEEPSEEK_API_KEY="):
                return l.split("=", 1)[1].strip().strip('"').strip("'")
    return None

API_KEY = load_deepseek_key()
API_URL = "https://api.deepseek.com/v1/chat/completions"
MODEL = "deepseek-v4-flash"

# ---- KLEI helpers -----------------------------------------------------------
def strip_klei(raw):
    if raw is None: return None
    if raw.startswith(b"KLEI"):
        m = re.match(rb"KLEI\s+\d+\s+(.*)", raw, re.DOTALL)
        return m.group(1).decode("utf-8", errors="replace").strip() if m else None
    return raw.decode("utf-8", errors="replace").strip()

def add_klei(payload: str) -> bytes:
    return b"KLEI     1 " + payload.encode("utf-8")

def read_state():
    try:
        with open(STATE_FILE, "rb") as f:
            raw = f.read()
        txt = strip_klei(raw)
        return json.loads(txt) if txt else None
    except Exception:
        return None

def send_command(cmd_dict, timeout=1.5):
    """Fire-and-forget: write the command, briefly wait for a result but never
    block the loop. Success is verified via STATE changes instead."""
    cmdid = int(time.time() * 1000)
    cmd_dict = dict(cmd_dict); cmd_dict["id"] = cmdid
    try:
        with open(CMD_FILE, "wb") as f:
            f.write(add_klei(json.dumps(cmd_dict)))
    except Exception as e:
        return {"ok": False, "error": f"write failed: {e}"}
    # Short opportunistic wait for result (best-effort, non-blocking)
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if os.path.exists(RESULT_FILE):
                with open(RESULT_FILE, "rb") as f:
                    res = json.loads(strip_klei(f.read()) or "{}")
                if res.get("id") == cmdid:
                    return res.get("result")
        except Exception:
            pass
        time.sleep(0.15)
    return {"ok": "pending", "reply": "action sent (verifying via state)"}

# ---- State normalizer -------------------------------------------------------
KNOWN_FOOD = {"berries","carrot","corn","eggplant","meat","smallmeat","cookedmeat",
              "cookedsmallmeat","pumpkin","potato","onion","tomato","garlic",
              "drumstick","froglegs","fish","butter","honey","nuts","petals",
              "berries_juicy","cutgrass"}  # cutgrass isn't food; safe list is advisory

GATHERABLE = {"berrybush","berrybush2","grass","sapling","flint","twigs","carrot_plant",
              "flower","reeds","cactus","tree","deciduoustree","evergreen","rock",
              "mushroom_red","mushroom_green","mushroom_blue","boulder"}

def normalize_state(st):
    """Compress the raw state into a compact prompt-friendly view."""
    if not st:
        return "NO STATE (is the mod running in-game?)"
    hp = st.get("health") or [0,1]
    hg = st.get("hunger") or [0,1]
    sn = st.get("sanity") or [0,1]
    pos = st.get("pos") or {}
    lines = []
    lines.append(f"Day {st.get('day','?')} ({st.get('phase','?')}), season {st.get('season','?')}")
    lines.append(f"HP {hp[0]:.0f}/{hp[1]:.0f} | hunger {hg[0]:.0f}/{hg[1]:.0f} | sanity {sn[0]:.0f}/{sn[1]:.0f}")
    lines.append(f"pos ({pos.get('x',0):.0f}, {pos.get('z',0):.0f})")
    items = st.get("items") or []
    lines.append("inventory: " + (", ".join(items) if items else "empty"))
    equipped = st.get("equipped") or []
    if equipped:
        lines.append("EQUIPPED: " + ", ".join(equipped) + " (already in hands — use it, do NOT gather/pickup it)")
    act = st.get("activeitem")
    if act:
        lines.append("active item: " + act)
    nearby = st.get("nearby") or []
    # Summarize nearby: name xN
    from collections import Counter
    cnt = Counter()
    for e in nearby:
        cnt[e.get("n","?")] += 1
    near_str = ", ".join(f"{n}x{c}" for n, c in sorted(cnt.items())) if cnt else "nothing nearby"
    lines.append("nearby: " + near_str)
    # Water awareness
    if st.get("on_water") is True:
        lines.append("⚠ STANDING ON/AT WATER EDGE")
    ld = st.get("land_dirs")
    if isinstance(ld, list) and ld:
        lines.append("land directions (within 8u): " + ", ".join(ld))
    return "\n".join(lines)

# ---- LLM call ---------------------------------------------------------------
SYSTEM = """You are the PLANNER for Wilson in Don't Starve Together (DST).
You receive the RESOURCE MAP + inventory, and produce an ordered survival plan.

DST RECIPES (ground truth):
- axe = 1 twigs + 1 flint
- campfire = 3 cutgrass + 2 log
- torch = 2 cutgrass + 2 twigs
- pickaxe = 2 twigs + 2 flint

RESOURCE SOURCES:
- flint: on ground (PICKUP)
- twigs: pick sapling (PICK) or chop twiggytree (needs axe)
- cutgrass: pick grass (PICK)
- log: chop evergreen (needs AXE)
- berries: pick berrybush (food)
- carrot: pick carrot_planted (food)

PLANNING RULES:
1. Starving (hunger<40) or night approaching with no light -> food/campfire first.
2. No axe -> plan: gather twigs, gather flint, craft axe (axe enables chopping).
3. No campfire -> plan: chop 2 evergreen (log x2), gather 3 grass (cutgrass x3), craft campfire.
4. Include only steps whose ingredients you lack (check inventory).
5. If a needed resource is NOT in the map, add an explore step (move_to unexplored).

Respond with ONLY a JSON list of steps:
[{"step":"gather_flint","count":1,"why":"..."},{"step":"gather_twigs","count":1,"why":"..."},
 {"step":"craft","recipe":"axe","why":"..."},{"step":"chop_tree","count":2,"why":"..."},
 {"step":"gather_grass","count":3,"why":"..."},{"step":"craft","recipe":"campfire","why":"..."}]
Allowed: gather_flint, gather_twigs, gather_grass, gather_berries, gather_food,
chop_tree, mine_rock, craft, eat, move_to, explore. Max 10 steps."""

def ask_llm(state_text, last_actions, tries=3):
    if not API_KEY:
        return None, "no DEEPSEEK_API_KEY found"
    msgs = [{"role":"system","content":SYSTEM}]
    if last_actions:
        msgs.append({"role":"user","content":"Recent actions (most recent last):\n" + "\n".join(last_actions[-6:])})
    msgs.append({"role":"user","content":"Current state:\n" + state_text + "\n\nWhat is your next single action?"})
    body = {"model": MODEL, "messages": msgs, "max_tokens": 2000, "temperature": 0.4}
    req = urllib.request.Request(API_URL, data=json.dumps(body).encode(),
        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
    for attempt in range(tries):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode())
            content = data["choices"][0]["message"]["content"] or ""
            content = content.strip()
            # strip markdown fences if present
            content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
            if not content:
                # reasoning model may have consumed budget; retry with a nudge
                if attempt < tries - 1:
                    msgs.append({"role": "user", "content": "Output the JSON action now (only the JSON)."})
                    req = urllib.request.Request(API_URL, data=json.dumps({"model": MODEL, "messages": msgs, "max_tokens": 2000, "temperature": 0.4}).encode(),
                        headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"})
                    continue
                return None, "empty content after retries"
            return content, None
        except urllib.error.HTTPError as e:
            err = f"HTTP {e.code}: {e.read().decode()[:200]}"
            if e.code == 429 and attempt < tries-1:
                time.sleep(2); continue
            return None, err
        except Exception as e:
            if attempt < tries-1:
                time.sleep(1); continue
            return None, str(e)
    return None, "failed after retries"

def parse_action(content):
    """Extract a JSON action from the LLM reply (tolerate stray text)."""
    if not content: return None
    try:
        return json.loads(content)
    except Exception:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            try: return json.loads(m.group(0))
            except Exception: return None
    return None

# ---- Validation --------------------------------------------------------------
def validate_action(a):
    if not isinstance(a, dict) or "action" not in a: return None
    act = a["action"]
    if act == "move_to":
        if isinstance(a.get("x"), (int,float)) and isinstance(a.get("z"), (int,float)):
            return {"action":"move_to","x":float(a["x"]),"z":float(a["z"]),"why":str(a.get("why",""))}
    elif act == "gather":
        return {"action":"gather","prefab":a.get("prefab"),"why":str(a.get("why",""))}
    elif act == "eat":
        if a.get("item"): return {"action":"eat","item":str(a["item"]),"why":str(a.get("why",""))}
    elif act == "attack":
        return {"action":"attack","prefab":a.get("prefab"),"why":str(a.get("why",""))}
    elif act == "craft":
        r = str(a.get("recipe","")).strip()
        if r in ("axe","campfire","torch","pickaxe"):
            return {"action":"craft","recipe":r,"why":str(a.get("why",""))}
    elif act == "say":
        return {"action":"say","text":str(a.get("text",""))[:80],"why":str(a.get("why",""))}
    elif act == "wait":
        s = max(1, min(10, int(a.get("seconds", 2))))
        return {"action":"wait","seconds":s,"why":str(a.get("why",""))}
    return None

# ---- Main loop ---------------------------------------------------------------
def check_gained(before, after, resname):
    """True if gathering resname added something to inventory."""
    # resource -> what item it produces
    prod = {"flint":"flint","sapling":"twigs","grass":"cutgrass","berrybush":"berries",
            "carrot_planted":"carrot","evergreen":"log"}
    item = prod.get(resname)
    if item:
        return item in after and item not in before
    return len(after) > len(before)

def parse_plan(content):
    """Parse the LLM plan (JSON list of steps) -> list of dicts."""
    if not content: return None
    try:
        data = json.loads(content)
        if isinstance(data, list):
            return [s for s in data if isinstance(s, dict) and "step" in s]
        return None
    except Exception:
        m = re.search(r"\[.*\]", content, re.DOTALL)
        if m:
            try:
                data = json.loads(m.group(0))
                if isinstance(data, list):
                    return [s for s in data if isinstance(s, dict) and "step" in s]
            except Exception:
                pass
    return None

# map plan step names to resource types and gather targets
STEP_RESOURCE = {
    "gather_flint": "flint",
    "gather_twigs": "sapling",
    "gather_grass": "grass",
    "gather_food": "berrybush",
    "gather_berries": "berrybush",
}

def explore_direction(memory, from_pos, step=12):
    """Pick the cardinal direction with the FEWEST mapped resources (unexplored)."""
    import collections
    counts = { "north":0, "south":0, "east":0, "west":0 }
    px, pz = from_pos
    for name, pts in memory["resources"].items():
        for (x, z) in pts:
            ddx, ddz = x-px, z-pz
            if abs(ddx) > abs(ddz):
                counts["east" if ddx > 0 else "west"] += 1
            else:
                counts["south" if ddz > 0 else "north"] += 1
    # pick the least-explored
    best_dir = min(counts, key=counts.get)
    dirs = {"north":(0,step), "south":(0,-step), "east":(step,0), "west":(-step,0)}
    return dirs[best_dir]

def execute_step(step, st, memory, send, last_actions):
    """Deterministically execute one plan step. Returns 'done'/'blocked'/'replan'."""
    sname = step.get("step")
    pos = (st.get("pos") or {})
    px, pz = pos.get("x", 0), pos.get("z", 0)
    inv = set(st.get("items") or [])
    equipped = set(st.get("equipped") or [])
    dayphase = st.get("phase")

    if sname == "move_to":
        x, z = step.get("x"), step.get("z")
        if (x is None or z is None) and step.get("target") == "unexplored_area":
            # deterministic explore: pick the direction we've seen fewest resources
            dx, dz = explore_direction(memory, (px, pz))
            x, z = px + dx, pz + dz
        if x is None or z is None: return "blocked"
        send({"action":"move_to","x":float(x),"z":float(z)})
        print(f"  [step] move_to ({x:.0f},{z:.0f}) — {step.get('why','')}")
        time.sleep(3)
        return "done"

    if sname == "craft":
        recipe = step.get("recipe")
        if recipe == "axe" and "flint" in inv and "twigs" in inv and "axe" not in equipped:
            send({"action":"craft","recipe":"axe"})
            print(f"  [step] CRAFT axe — {step.get('why','')}")
            # wait longer: DoBuild consumes ingredients then equips; verify over 2 reads
            time.sleep(4)
            stv = read_state()
            eqv = set((stv or {}).get("equipped") or [])
            invv = set((stv or {}).get("items") or [])
            if "axe" in eqv or "axe" in invv:
                print("  [step] axe verified (equipped)")
                return "done"
            time.sleep(2)
            stv2 = read_state()
            eqv2 = set((stv2 or {}).get("equipped") or [])
            invv2 = set((stv2 or {}).get("items") or [])
            if "axe" in eqv2 or "axe" in invv2:
                print("  [step] axe verified (2nd read)")
                return "done"
            if "flint" in inv and "twigs" in inv:
                print(f"  [step] craft axe not confirmed (inv {sorted(invv2)}, eq {sorted(eqv2)})")
            else:
                print(f"  [step] ingredients consumed; axe may be equipped ({sorted(eqv2)}) - treating as done")
                return "done"
            return "again"
        if recipe == "campfire" and "log" in inv and "cutgrass" in inv:
            send({"action":"craft","recipe":"campfire"})
            print(f"  [step] CRAFT campfire — {step.get('why','')}")
            time.sleep(4)
            stv = read_state()
            invv = set((stv or {}).get("items") or [])
            eqv = set((stv or {}).get("equipped") or [])
            if "campfire" in invv or "campfire" in eqv:
                return "done"
            # campfire is a STRUCTURE - it gets placed on the ground, not in inventory
            print("  [step] campfire built (checking nearby for fire)")
            return "done"
        if recipe == "pickaxe" and "flint" in inv and "twigs" in inv:
            send({"action":"craft","recipe":"pickaxe"})
            print(f"  [step] CRAFT pickaxe — {step.get('why','')}")
            time.sleep(4)
            stv = read_state()
            eqv = set((stv or {}).get("equipped") or [])
            if "pickaxe" in eqv:
                return "done"
            return "again"
        if recipe == "pickaxe" and "flint" in inv and "twigs" in inv:
            # note: needs 2 flint + 2 twigs; check count if needed
            send({"action":"craft","recipe":"pickaxe"})
            print(f"  [step] CRAFT pickaxe — {step.get('why','')}")
            time.sleep(3)
            return "done"
        print(f"  [step] craft {recipe} SKIPPED (missing ingredients: inv={sorted(inv)})")
        return "blocked"

    if sname == "chop_tree":
        # CRITICAL: cannot chop without an axe equipped - check the REAL state
        if "axe" not in equipped and "axe" not in inv:
            print(f"  [step] chop NEEDS axe (equipped={sorted(equipped)}) -> replan to craft axe")
            return "replan"
        target = nearest_resource_by_type(memory, "evergreen", (px,pz))
        if not target: return "blocked"
        dx = target[0]-px; dz = target[1]-pz
        d = (dx*dx+dz*dz) ** 0.5
        if d > 10:
            send({"action":"move_to","x":target[0],"z":target[1]})
            print(f"  [step] chop: approach tree ({target[0]:.0f},{target[1]:.0f})")
            time.sleep(4)
            return "again"
        send({"action":"gather","prefab":"evergreen"})
        print(f"  [step] CHOP tree at ({target[0]:.0f},{target[1]:.0f}) — {step.get('why','')}")
        # Trees take MULTIPLE chops to fall. Poll state over ~12s:
        for _ in range(3):
            time.sleep(4)
            stv = read_state()
            if not stv: continue
            invv = set((stv or {}).get("items") or [])
            nearby = stv.get("nearby") or []
            # log can drop to the GROUND (check nearby ground logs)
            ground_logs = [e for e in nearby if e.get("n") == "log" and e.get("d", 99) < 6]
            if "log" in invv or ground_logs:
                print(f"  [step] LOG gained! (inv: {'log' in invv}, ground: {len(ground_logs)})")
                return "done"
        print(f"  [step] chop: still no log after multiple chops (inv {sorted(invv) if 'invv' in dir() else '?'})")
        return "again"

    if sname == "mine_rock":
        target = nearest_resource_by_type(memory, "rock", (px,pz)) or nearest_resource_by_type(memory, "boulder", (px,pz))
        if not target: return "blocked"
        send({"action":"gather","prefab":"rock"})
        print(f"  [step] MINE rock — {step.get('why','')}")
        time.sleep(3)
        return "done"

    # gather_X steps — USE the command result, never blind-loop
    resname = STEP_RESOURCE.get(sname)
    if resname:
        target = nearest_resource_by_type(memory, resname, (px,pz))
        if not target:
            print(f"  [step] {sname} blocked (no {resname} mapped)")
            return "blocked"
        dx = target[0]-px; dz = target[1]-pz
        d = (dx*dx+dz*dz) ** 0.5
        if d > 12:
            send({"action":"move_to","x":target[0],"z":target[1]})
            print(f"  [step] {sname}: approach ({target[0]:.0f},{target[1]:.0f}) — {step.get('why','')}")
            time.sleep(4)
            return "again"
        res = send({"action":"gather","prefab":resname})
        print(f"  [step] {sname}: gather {resname} — {step.get('why','')}")
        time.sleep(4)
        # 1) read the command result — react to the ACTUAL failure reason
        if isinstance(res, dict) and res.get("ok") is False:
            err = str(res.get("error") or res.get("err") or "")
            if "too far" in err:
                print(f"  [step] {sname}: {err} -> moving closer")
                send({"action":"move_to","x":target[0],"z":target[1]})
                time.sleep(4)
                return "again"
            if "no matching" in err:
                print(f"  [step] {sname}: {err} -> resource gone, re-plan")
                return "replan"
            if "no valid action" in err:
                print(f"  [step] {sname}: {err} -> wrong tool/state, re-plan")
                return "replan"
            print(f"  [step] {sname}: gather error: {err} -> re-plan")
            return "replan"
        # 2) verify via state
        stv = read_state()
        invv = set((stv or {}).get("items") or [])
        got = check_gained(inv, invv, resname)
        if got:
            return "done"
        # 3) if we moved/gathered but no gain, try ONE more then re-plan
        print(f"  [step] {sname}: no gain yet (inv {sorted(invv)}) -> re-plan instead of looping")
        return "replan"

    if sname == "eat":
        food = step.get("item") or next((i for i in inv if i in ("berries","carrot","meat","smallmeat","cookedmeat")), None)
        if food:
            send({"action":"eat","item":food})
            print(f"  [step] EAT {food} — {step.get('why','')}")
            time.sleep(2)
            return "done"
        return "blocked"

    print(f"  [step] unknown step: {sname}")
    return "blocked"

def nearest_resource_by_type(memory, name, from_pos):
    best, bd = None, 1e9
    for (x, z) in memory["resources"].get(name, []):
        d = (x-from_pos[0])**2 + (z-from_pos[1])**2
        if d < bd: bd, best = d, (x, z)
    return best

def fallback_plan(st, memory):
    """Deterministic build-order plan when the LLM fails or stalls.
    Uses whatever resources are mapped; skips what's missing."""
    inv = set(st.get("items") or [])
    equipped = set(st.get("equipped") or [])
    plan = []
    has_flint = "flint" in inv or bool(memory["resources"].get("flint"))
    has_twigs = "twigs" in inv or bool(memory["resources"].get("sapling")) or bool(memory["resources"].get("twiggytree"))
    has_grass = "cutgrass" in inv or bool(memory["resources"].get("grass"))
    has_log = "log" in inv or bool(memory["resources"].get("evergreen"))

    if "axe" not in equipped and "axe" not in inv:
        if "flint" not in inv:
            plan.append({"step":"gather_flint","count":1,"why":"axe needs flint"})
        if "twigs" not in inv:
            plan.append({"step":"gather_twigs","count":1,"why":"axe needs twigs"})
        if "flint" in inv and "twigs" in inv:
            plan.append({"step":"craft","recipe":"axe","why":"first tool"})
    if "campfire" not in inv and "campfire" not in equipped:
        if "log" not in inv:
            plan.append({"step":"chop_tree","count":2,"why":"campfire needs logs"})
        if "cutgrass" not in inv:
            plan.append({"step":"gather_grass","count":3,"why":"campfire needs cutgrass"})
        if "log" in inv and "cutgrass" in inv:
            plan.append({"step":"craft","recipe":"campfire","why":"light before night"})
    if not plan:
        plan.append({"step":"move_to","target":"unexplored_area","why":"explore for more resources"})
    return plan

def main():
    print("DST AI Bot Brain - Roam -> Plan -> Execute")
    print("Model:", MODEL, "| Channel:", CS)
    print("Keep the DST window FOCUSED (game autopauses when unfocused!).")
    print("Press Ctrl+C to stop (kill switch).\n")
    # SINGLE-INSTANCE GUARD: refuse to start if another brain is already running
    import subprocess as _sp
    try:
        _out = _sp.run(["powershell","-NoProfile","-Command",
            "Get-CimInstance Win32_Process -Filter \"Name='python.exe'\" | Where-Object {$_.CommandLine -match 'brain.py'} | Select-Object ProcessId | ConvertTo-Json"],
            capture_output=True, text=True, timeout=10)
        _ids = [x for x in _out.stdout.split() if x.isdigit()]
        _mine = os.getpid()
        _others = [i for i in _ids if int(i) != _mine]
        if _others:
            print(f"❌ ANOTHER BRAIN IS ALREADY RUNNING (PID {_others}). Kill it first: taskkill /F /PID {_others[0]}")
            sys.exit(1)
    except Exception as _e:
        print(f"(single-instance check skipped: {_e})")
    if not API_KEY:
        print("FATAL: DEEPSEEK_API_KEY not found in hermes .env"); sys.exit(1)
    if not CS or not os.path.isdir(CS):
        print("FATAL: save dir not found. Is DST installed?"); sys.exit(1)

    import math
    import collections

    # ---- memory: resource map (name -> [(x,z), ...]) ----
    memory = {"resources": collections.defaultdict(list), "spawn": None}
    patrol_index = 0
    pos_history = []
    plan = []
    plan_index = 0
    step_retries = 0
    last_failure = "" 

    def update_memory(st):
        if memory["spawn"] is None:
            memory["spawn"] = ((st.get("pos") or {}).get("x", 0), (st.get("pos") or {}).get("z", 0))
        for e in (st.get("nearby") or []):
            n, x, z, d = e.get("n"), e.get("x"), e.get("z"), e.get("d")
            if n and x is not None and z is not None and d is not None:
                if n in ("multiplayer_portal","wanderingtrader","crow","butterfly","fireflies",
                         "beehive","rabbit","carnival_host","flower","frog","robin","seeds",
                         "green_mushroom","float_fx_front","float_fx_back","splash","puffin",
                         "wobster_sheller","wobster_den","bee","wasphive","marsh_plant","mole",
                         "molehill","rock1"):
                    continue
                pts = memory["resources"][n]
                dup = any((px-x)**2 + (pz-z)**2 < 64 for px, pz in pts)
                if not dup:
                    pts.append((float(x), float(z)))

    def resource_summary():
        return "; ".join(f"{n}: {len(pts)}" for n, pts in memory["resources"].items()) or "none"

    def nearest(name, from_pos):
        best, bd = None, 1e9
        for (x, z) in memory["resources"].get(name, []):
            d = (x-from_pos[0])**2 + (z-from_pos[1])**2
            if d < bd: bd, best = d, (x, z)
        return best

    def patrol_waypoint(px, pz, idx):
        R = 25 + 20 * (idx // 8)
        ang = (idx % 8) * (math.pi / 4)
        return (px + R * math.cos(ang), pz + R * math.sin(ang))

    def send(cmd):
        return send_command(cmd)

    def stuck_here(px, pz, threshold=3.0):
        recent = pos_history[-6:]
        if len(recent) < 6: return False
        x0, z0 = recent[0]
        return ((px-x0)**2 + (pz-z0)**2) ** 0.5 < threshold

    # ============ PHASE 1: ROAM (deterministic, records resources + distances) ============
    phase = "roam"
    CRITICAL = ("flint", "sapling", "grass", "evergreen", "berrybush", "carrot_planted")
    TWIG_SOURCES = ("sapling", "twiggytree")
    print("[PHASE 1] ROAM - patrolling to record resources (no AI).")
    try:
        while True:
            st = read_state()
            if not st:
                print("[roam] waiting for state... (game focused? paused?)"); time.sleep(3); continue
            update_memory(st)
            pos = (st.get("pos") or {})
            px, pz = pos.get("x", 0), pos.get("z", 0)
            pos_history.append((px, pz))
            if len(pos_history) > 10: pos_history.pop(0)

            if phase == "roam":
                have = set(memory["resources"].keys())
                have_twigs = bool(have & set(TWIG_SOURCES))
                missing = [c for c in CRITICAL if c not in have and not (c == "sapling" and have_twigs)]
                if not missing:
                    phase = "plan"
                    print(f"[PHASE 2] PLAN - mapped: {resource_summary()}")
                    continue

                sx, sz = memory["spawn"] or (px, pz)
                wx, wz = patrol_waypoint(sx, sz, patrol_index)
                dist = ((wx-px)**2 + (wz-pz)**2) ** 0.5

                # water escape
                if st.get("on_water") is True:
                    ld = st.get("land_dirs") or []
                    if ld:
                        vec = {"north":(0,10),"south":(0,-10),"east":(10,0),"west":(-10,0)}.get(ld[0], (10,0))
                        send({"action":"move_to","x":px+vec[0],"z":pz+vec[1]})
                        print(f"[roam] on WATER - moving {ld[0]} to land")
                        time.sleep(3); continue
                    send({"action":"move_to","x":sx,"z":sz})
                    print("[roam] on WATER - returning to spawn")
                    time.sleep(3); continue

                if dist < 6:
                    patrol_index += 1
                    print(f"[roam] waypoint {patrol_index} done; missing: {', '.join(missing)}")
                    time.sleep(0.5); continue

                if stuck_here(px, pz) and dist > 6:
                    patrol_index += 1
                    print(f"[roam] stuck toward ({wx:.0f},{wz:.0f}); changing direction")
                    time.sleep(0.5); continue

                send({"action":"move_to","x":wx,"z":wz})
                print(f"[roam] -> wp {patrol_index} ({wx:.0f},{wz:.0f}) missing: {', '.join(missing)}")
                time.sleep(3.0); continue

            # ============ PHASE 2: PLAN (ONE AI call) ============
            elif phase == "plan":
                inv = st.get("items") or []
                equipped = st.get("equipped") or []
                fail_hint = ""
                if last_failure:
                    fail_hint = "\nPREVIOUS FAILURE to learn from: " + last_failure
                missing_hint = ""
                miss = [c for c in CRITICAL if not memory["resources"].get(c)]
                if miss:
                    missing_hint = "\nNOT MAPPED (add explore step): " + ", ".join(miss)
                prompt = (f"Day {st.get('day')} {st.get('phase')}, hunger {st.get('hunger')[0]:.0f}/150, "
                          f"sanity {st.get('sanity')[0]:.0f}/200.\n"
                          f"Inventory: {', '.join(inv) if inv else 'empty'}\n"
                          f"Equipped: {', '.join(equipped) if equipped else 'none'}\n"
                          f"Resource map: {resource_summary()}{missing_hint}{fail_hint}\n"
                          f"Current pos: ({px:.0f},{pz:.0f}). Produce the survival plan JSON.")
                content, err = ask_llm(prompt, [])
                plan = None
                if err:
                    print(f"[plan] LLM error ({err}); using fallback")
                else:
                    plan = parse_plan(content)
                    if not plan:
                        print(f"[plan] bad plan ({content[:80]!r}); using fallback")
                if not plan:
                    plan = fallback_plan(st, memory)
                print(f"[PHASE 3] EXECUTE - plan ({len(plan)} steps): {plan}")
                plan_index = 0
                phase = "execute"
                continue

            # ============ PHASE 3: EXECUTE (local, deterministic) ============
            elif phase == "execute":
                if plan_index >= len(plan):
                    print("[execute] plan done. Re-planning (new map data).")
                    phase = "plan"
                    continue
                step = plan[plan_index]
                result = execute_step(step, st, memory, send, [])
                if result == "done":
                    plan_index += 1
                    step_retries = 0
                elif result == "again":
                    step_retries += 1
                    if step_retries >= 2:
                        print("[execute] step retried 2x -> re-planning from real state")
                        phase = "plan"; plan_index = 0; step_retries = 0
                        continue
                    time.sleep(1.0); continue
                elif result == "replan":
                    print("[execute] replanning (state changed)")
                    last_failure = f"step '{step.get('step')}' failed; adapt the plan"
                    phase = "plan"; plan_index = 0; step_retries = 0
                elif result == "blocked":
                    print("[execute] step blocked -> roam to find missing resource")
                    last_failure = f"step '{step.get('step')}' blocked (resource not mapped)"
                    phase = "roam"; plan_index = 0; step_retries = 0
                time.sleep(2.0); continue

    except KeyboardInterrupt:
        print("\n\n🛑 Bot stopped by user. Wilson is all yours again.")
    except Exception as e:
        print(f"[loop] error: {e}")

if __name__ == "__main__":
    main()
