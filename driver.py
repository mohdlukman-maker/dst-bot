#!/usr/bin/env python3
"""
Wilson Survival Driver - direct control with robust action execution.
Handles: move -> settle -> gather (retry if busy) -> verify -> eat.
Logs everything to survival_log.txt for learning.
"""
import os, time, re, json, sys
from datetime import datetime

DOC = os.path.join(os.path.expanduser("~"), "Documents", "Klei", "DoNotStarveTogether")
CS = os.path.join(DOC, "40630831", "client_save")
FP = os.path.join(CS, "dst_ai_bot_state")
LOGF = os.path.join(os.path.dirname(os.path.abspath(__file__)), "survival_log.txt")

def log(msg):
    line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line)
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(line + "\n")

def get_heartbeat():
    """Read the heartbeat file (static scheduler - works even when paused).
    Returns dict or None. Use to distinguish paused vs crashed."""
    import os as _os
    hb_path = _os.path.join(CS, "dst_ai_bot_heartbeat")
    try:
        if _os.path.exists(hb_path):
            raw = open(hb_path, "rb").read()
            m = re.match(rb"KLEI\s+\d+\s+(.*)", raw, re.DOTALL)
            return json.loads(m.group(1).decode())
    except Exception:
        pass
    return None

def is_paused():
    """True if the sim is paused (heartbeat advancing, sim_ts frozen).
    Returns None if heartbeat missing (not loaded yet)."""
    hb = get_heartbeat()
    if not hb:
        return None
    return bool(hb.get("paused")) or (hb.get("sim_ts") is None)

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
    return cmdid

def read_result(cmdid, timeout=6):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if os.path.exists(os.path.join(CS, "dst_ai_bot_result")):
                with open(os.path.join(CS, "dst_ai_bot_result"), "rb") as f:
                    res = json.loads(re.sub(rb"^KLEI\s+\d+\s+", b"", f.read()) or b"{}")
                if res.get("id") == cmdid:
                    return res.get("result")
        except Exception:
            pass
        time.sleep(0.2)
    return {"ok": "timeout"}

def wait_idle(stable_seconds=2.0, max_wait=8.0):
    p0 = None
    start = time.time()
    stable = 0
    while time.time() - start < max_wait:
        st = get_state()
        if st:
            pos = st.get("pos", {})
            p = (pos.get("x", 0), pos.get("z", 0))
            if p0 and ((p[0]-p0[0])**2 + (p[1]-p0[1])**2) ** 0.5 < 0.5:
                stable += 0.5
                if stable >= stable_seconds:
                    return True
            else:
                stable = 0
            p0 = p
        time.sleep(0.5)
    return False

def goto(x, z, settle=3.0):
    send({"action": "move_to", "x": x, "z": z})
    time.sleep(3)
    wait_idle(settle)

def gather(prefab, retries=3):
    for attempt in range(retries):
        cmdid = send({"action": "gather", "prefab": prefab})
        res = read_result(cmdid, timeout=6)
        err = str(res.get("error") or res.get("err") or "")
        if "busy" in err or "no valid action" in err:
            log(f"  gather {prefab}: {err} -> waiting, retry {attempt+1}")
            time.sleep(2.5)
            continue
        return res
    return {"ok": False, "error": "busy after retries"}

def eat(item):
    cmdid = send({"action": "eat", "item": item})
    return read_result(cmdid, timeout=6)

def gather_job(prefab, count=20, timeout=40):
    """Burst gather: one command runs walk->work->sweep->verify server-side.
    Returns the structured job report."""
    cmdid = send({"action": "gather_job", "prefab": prefab, "count": count})
    return read_result(cmdid, timeout=timeout)

def summary():
    st = get_state()
    if not st: return None
    return {
        "day": st.get("day"), "phase": st.get("phase"),
        "hunger": st.get("hunger"), "health": st.get("health"),
        "sanity": st.get("sanity"), "items": st.get("items"),
        "equipped": st.get("equipped"), "counts": st.get("item_counts"),
        "pos": st.get("pos"),
    }

if __name__ == "__main__":
    print("Wilson Survival Driver")
    print("Logging to:", LOGF)
    print("Ctrl+C to stop\n")
    with open(LOGF, "a", encoding="utf-8") as f:
        f.write(f"\n===== DRIVER SESSION {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} =====\n")
    while True:
        try:
            time.sleep(1)
        except KeyboardInterrupt:
            print("\nstopped")
            break
