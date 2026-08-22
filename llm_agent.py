#!/usr/bin/env python3
"""
LLM AGENT (Option A, user direction 2026-08-11): the queue executor.

The user's design: "give 5 instructions at once, and while waiting Wilson to
execute them, queue another set of instructions - this way no lagging in the
game." So:

- llm_brain.py (background thread) continuously proposes the next batch of
  <=5 commands from live state.
- This agent holds a QUEUE. It sends ONE command at a time over the channel,
  waits for the mod's result, then sends the next. The queue is refilled from
  the brain the moment it drops to <=2, so the LLM's think-time is hidden.
- SAFETY VALIDATOR: every proposed command is checked before sending -
  no gather/move at night without light, no walking toward hostiles, no
  crafting without materials, etc. Rejected commands are logged and skipped.
- reflex.py stays as the instant emergency layer (flee/eat/light).

The old rule-based local_agent.py remains as a fallback; this agent REPLACES
it as the primary driver.
"""

import os, re, json, time, threading, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import llm_brain

# ---- channel paths ----
DOC = os.path.join(os.path.expanduser("~"), "Documents", "Klei", "DoNotStarveTogether")
CS = os.path.join(DOC, "40630831", "client_save")
STATE_F = os.path.join(CS, "dst_ai_bot_state")
CMD_F = os.path.join(CS, "dst_ai_bot_command")
RES_F = os.path.join(CS, "dst_ai_bot_result")

LEARN_F = os.path.join(HERE, "learnings.json")
_last_say = 0.0          # say-cooldown tracker
_last_cmd_sig = None     # dedup: signature of the last executed command

# ---- logging (unbuffered, append) ----
LOG_F = os.path.join(HERE, "llm_agent_out.log")
# ---- run identity (shared with reflex + run_log) ----
RUN_ID = time.strftime("%H%M%S")
try:
    os.makedirs(os.path.join(HERE, "data"), exist_ok=True)
    with open(os.path.join(HERE, "data", "current_run.txt"), "w") as f:
        f.write(RUN_ID)
except Exception:
    pass


def log(msg):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        with open(LOG_F, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def load_json(path, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def get_state():
    try:
        raw = open(STATE_F, "rb").read()
        m = re.search(rb"KLEI\s+\d+\s+(.*)", raw, re.DOTALL)
        if not m:
            return {}
        return json.loads(m.group(1).decode())
    except Exception:
        return {}


def send(cmd):
    cmdid = int(time.time() * 1000)
    cmd = dict(cmd)
    cmd["id"] = cmdid
    try:
        with open(CMD_F, "wb") as f:
            f.write(b"KLEI     1 " + json.dumps(cmd).encode())
    except Exception:
        pass
    return cmdid


def read_result(cmdid, timeout=25):
    """Wait for the mod's result for this command id."""
    t0 = time.time()
    while time.time() - t0 < timeout:
        st = get_state()
        for r in (st.get("results") or []):
            if r.get("id") == cmdid:
                return r.get("result")
        time.sleep(1.0)
    return None


# ---------------- SAFETY VALIDATOR ----------------
SAFE_GATHER = {"grass", "sapling", "flint", "evergreen", "deciduoustree",
               "berrybush", "berrybush2", "carrot_planted", "seeds", "flower",
               "rock1", "rock2"}
SAFE_CRAFT = {"axe", "pickaxe", "torch", "spear", "campfire"}
SAFE_EQUIP = {"torch", "axe", "pickaxe", "spear"}
SAFE_EAT = {"berries", "carrot", "seeds", "cookedmeat", "cooked_smallmeat",
            "smallmeat", "meat", "drumstick", "cooked_drumstick",
            "mushroom", "red_mushroom", "green_mushroom", "blue_mushroom"}
LIGHT_ITEMS = ("torch", "lantern", "minerhat", "moggles")

CRAFT_MATS = {
    "axe": {"twigs": 1, "flint": 1},
    "pickaxe": {"twigs": 2, "flint": 2},
    "torch": {"cutgrass": 2, "twigs": 2},
    "spear": {"twigs": 2, "flint": 1, "rope": 1},
    "campfire": {"cutgrass": 3, "log": 2},
}


def validate(cmd, st):
    """Return (ok, reason). Rules are thin guardrails - the LLM decides
    strategy, this only stops suicidal or impossible commands."""
    act = cmd.get("action")
    phase = st.get("phase")
    equipped = st.get("equipped") or []
    counts = st.get("item_counts") or {}
    pos = st.get("pos") or {}
    has_light = any(l in equipped for l in LIGHT_ITEMS)
    fires = st.get("fires") or []
    has_fire = any(f.get("d", 99) <= 25 and (f.get("fuel_pct") or 0) > 0 for f in fires)
    is_dark = phase in ("night",) or (phase == "dusk" and not has_light and not has_fire)

    # 1) never gather/move at night without any light source
    if act in ("gather_job", "move_to") and phase == "night" and not has_light and not has_fire:
        return False, "night without light"
    # 2) move_to near a hostile threat (<12m)
    if act == "move_to":
        threats = [t for t in (st.get("threats") or [])
                   if t.get("d", 99) < 12 and (t.get("targeting") or t.get("n") in
                   ("spider", "hound", "frog", "mosquito", "tentacle", "crawlinghorror",
                    "terrorbeak", "spider_warrior", "spider_hider", "spider_spitter"))]
        if threats:
            tx, tz = cmd.get("x"), cmd.get("z")
            if tx is not None and tz is not None:
                for t in threats:
                    if t.get("x") is not None and t.get("z") is not None and \
                       math_hypot(tx - t["x"], tz - t["z"]) < 12:
                        return False, f"walking toward {t.get('n')}"
    # 3) gather: prefab must be known + present nearby (or ground item)
    if act == "gather_job":
        n = cmd.get("prefab")
        if n not in SAFE_GATHER:
            return False, f"unknown gather prefab {n}"
        present = any(e.get("n") == n for e in (st.get("nearby") or [])) or \
                  any(g.get("n") == n for g in (st.get("ground_items") or []))
        if not present:
            return False, f"no {n} nearby"
    # 4) craft: recipe known + materials present
    if act == "craft":
        r = cmd.get("recipe")
        if r not in SAFE_CRAFT:
            return False, f"unknown recipe {r}"
        mats = CRAFT_MATS.get(r, {})
        for m, c in mats.items():
            if counts.get(m, 0) < c:
                return False, f"missing {m} for {r}"
    # 5) equip: item must be in inventory
    if act == "equip":
        it = cmd.get("item")
        if it not in SAFE_EQUIP:
            return False, f"unknown equip {it}"
        if counts.get(it, 0) <= 0 and it not in equipped:
            return False, f"no {it} in inventory"
    # 6) eat: item must be in inventory
    if act == "eat":
        it = cmd.get("item")
        if it not in SAFE_EAT:
            return False, f"unknown food {it}"
        if counts.get(it, 0) <= 0:
            return False, f"no {it} to eat"
    # 7) say: keep it short + cooldown (brain likes to chatter)
    if act == "say":
        if len(str(cmd.get("text", ""))) > 60:
            cmd["text"] = str(cmd.get("text"))[:57] + "..."
        if time.time() - _last_say < 12:
            return False, "say cooldown"
    # 8) gather: reject if the TARGET is near a hostile threat (walking into
    #    a tentacle nest for flint was a death cause - the old rules knew this)
    if act == "gather_job":
        n = cmd.get("prefab")
        threats2 = [t for t in (st.get("threats") or [])
                    if t.get("d", 99) < 15 and (t.get("targeting") or t.get("n") in
                    ("spider", "hound", "frog", "mosquito", "tentacle", "crawlinghorror",
                     "terrorbeak", "spider_warrior", "spider_hider", "spider_spitter"))]
        if threats2:
            for e in (st.get("nearby") or []):
                if e.get("n") == n and e.get("x") is not None and e.get("z") is not None:
                    for t in threats2:
                        if t.get("x") is not None and t.get("z") is not None and                            math_hypot(e["x"] - t["x"], e["z"] - t["z"]) < 12:
                            return False, f"{n} next to {t.get('n')}"
    # 9) move_to: distance cap - the LLM sometimes hallucinates far coords;
    #    walking 300+ units wastes the day. Cap at 250.
    if act == "move_to":
        px, pz = pos.get("x", 0), pos.get("z", 0)
        tx, tz = cmd.get("x"), cmd.get("z")
        if tx is not None and tz is not None and math_hypot(tx - px, tz - pz) > 250:
            return False, f"move_to too far ({math_hypot(tx - px, tz - pz):.0f}m)"
    return True, "ok"


def math_hypot(a, b):
    return (a*a + b*b) ** 0.5


# ---------------- BRAIN REFILL THREAD ----------------
class BrainRefiller(threading.Thread):
    """Prefetch batches in the background so the queue never waits.
    v2 (2026-08-11, 'push it further' audit): DEMAND-DRIVEN refill - the main
    loop signals when the queue drops to <=2; only then does the brain call
    the API. The old version asked every 3s unconditionally (~20 calls/min
    wasted). Also carries the last batch verdict back to the brain."""

    def __init__(self):
        super().__init__(daemon=True)
        self.lock = threading.Lock()
        self.need = threading.Event()   # set when a refill is wanted
        self.next_batch = []            # ready to use
        self.last_verdict = ""
        self._stop = False
        self.need.set()                 # first refill immediately

    def run(self):
        while not self._stop:
            if not self.need.wait(timeout=0.5):
                continue
            self.need.clear()
            try:
                st = get_state()
                if not st.get("pos"):
                    time.sleep(1)
                    self.need.set()
                    continue
                results = (st.get("results") or [])[-4:]
                cmds = llm_brain.ask_for_commands(st, results, self.last_verdict)
                with self.lock:
                    if cmds:
                        self.next_batch = cmds
                        log(f"🧠 BRAIN proposed {len(cmds)} cmds: {json.dumps(cmds)[:170]}")
                    else:
                        log("🧠 BRAIN returned nothing - will retry")
                        self.need.set()   # retry soon (network hiccup etc.)
            except Exception as e:
                log(f"🧠 BRAIN thread error: {e}")
                self.need.set()

    def take(self):
        with self.lock:
            b = self.next_batch
            self.next_batch = []
            return b

    def want_refill(self):
        """Called by main loop when the queue is low."""
        self.need.set()


# ---------------- MAIN LOOP ----------------
def main():
    log("🤖 LLM AGENT ONLINE (Option A: DeepSeek brain + 5-cmd queue)")
    refiller = BrainRefiller()
    refiller.start()

    queue = []           # commands ready to execute
    last_health = None
    _death_logged = False
    _outcomes = []       # batch outcome strings for the brain's verdict

    while True:
        st = get_state()
        if not st.get("pos"):
            time.sleep(2)
            continue
        # paused? state stops updating
        try:
            if os.path.exists(STATE_F) and time.time() - os.path.getmtime(STATE_F) > 10:
                time.sleep(3)
                continue
        except Exception:
            pass

        # DEATH -> quit the game (user strategy: repeat until he dies, then quit)
        if st.get("is_ghost"):
            if not _death_logged:
                _death_logged = True
                log(f"💀 WILSON DIED at day {st.get('day')} (run {RUN_ID}) - sending quit (user strategy)")
                try:
                    from lib import run_log as run_logger
                    run_logger.end_run(RUN_ID, st, "death")
                except Exception:
                    pass
                try:
                    send({"action": "quit"})
                except Exception:
                    pass
            time.sleep(3)
            continue

        # drain the queue: one command at a time, verify each
        if queue:
            cmd = queue.pop(0)
            global _last_say, _last_cmd_sig
            sig = json.dumps(cmd, sort_keys=True)
            if sig == _last_cmd_sig:
                log(f"🔁 dedup: {sig[:80]} (already executed)")
                continue
            ok, reason = validate(cmd, st)
            if not ok:
                log(f"🛡 rejected: {json.dumps(cmd)} ({reason})")
                continue
            _last_cmd_sig = sig
            if cmd.get("action") == "say":
                _last_say = time.time()
            cmdid = send(cmd)
            log(f"📤 {json.dumps(cmd)} (id={cmdid})")
            res = read_result(cmdid)
            if res is None:
                log(f"  ⏳ no result for {cmd.get('action')} - moving on")
                _outcomes.append(f"{cmd.get('action')}: no-result")
            else:
                ok_res = res.get("ok") if isinstance(res, dict) else False
                log(f"  📥 result: {json.dumps(res)[:140]}")
                _outcomes.append(f"{cmd.get('action')}: {'ok' if ok_res else 'fail'}")
            # health watch
            h = (st.get("health") or [150])[0]
            if last_health is not None and h < last_health - 10:
                log(f"⚠️ health dropped {last_health:.0f}->{h:.0f}")
            last_health = h
            time.sleep(0.5)
            continue

        # queue empty (or drained): grab the brain's prefetched batch
        batch = refiller.take()
        if batch:
            # send the last batch's outcomes back to the brain as context
            if _outcomes:
                refiller.last_verdict = "; ".join(_outcomes[-8:])
                log(f"🧠 verdict to brain: {refiller.last_verdict}")
                _outcomes = []
            queue = list(batch)
            log(f"📦 queued {len(queue)} commands from brain")
            continue

        # queue low -> signal refill (demand-driven, no API spam)
        refiller.want_refill()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
