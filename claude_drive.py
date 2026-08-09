#!/usr/bin/env python3
"""
CLAUDE_DRIVE - lets Claude Code drive Wilson in DST.

How it works:
  Claude Code runs:  python claude_drive.py --tick
  This prints the CURRENT game state as JSON. Claude reads it, decides,
  and writes a command file:  python claude_drive.py --send <json>
  The Lua mod executes it and the result appears in the next --tick output.

Usage from Claude Code:
  # 1) See the world (run this, read the JSON, decide)
  python claude_drive.py --tick

  # 2) Issue a command (craft, gather, move, etc.)
  python claude_drive.py --send '{"action":"craft","recipe":"axe"}'

  # 3) Check your last command's result
  python claude_drive.py --result

Recommended rhythm (use sparingly, ~1 command per minute):
  --tick -> decide -> --send -> sleep 5-10s -> --tick (verify outcome) -> repeat

IMPORTANT RULES (learned the hard way):
  - Never spam commands. One action, verify it landed, then the next.
  - A job result reports "gained" - that is GROUND TRUTH. ok:true alone is NOT.
  - If state.json is stale (>10s), the game is PAUSED - do nothing, wait.
  - Wilson has a reflex daemon running separately: it flees hostiles, eats when
    starving, fuels fires, and equips torches before dusk. Do not fight it.
"""
import os, sys, json, re, time

DOC = os.path.join(os.path.expanduser("~"), "Documents", "Klei", "DoNotStarveTogether")
CS = os.path.join(DOC, "40630831", "client_save")
STATE_F = os.path.join(CS, "dst_ai_bot_state")
CMD_F = os.path.join(CS, "dst_ai_bot_command")
RESULT_F = os.path.join(CS, "dst_ai_bot_result")

def read_state():
    try:
        raw = open(STATE_F, "rb").read()
        m = re.match(rb"KLEI\s+\d+\s+(.*)", raw, re.DOTALL)
        return json.loads(m.group(1).decode())
    except Exception as e:
        return {"error": str(e)}

def send_command(cmd):
    cmdid = int(time.time() * 1000)
    if isinstance(cmd, str):
        cmd = json.loads(cmd)
    cmd["id"] = cmdid
    with open(CMD_F, "wb") as f:
        f.write(b"KLEI     1 " + json.dumps(cmd).encode())
    return {"sent": cmd, "id": cmdid}

def read_result():
    try:
        if os.path.exists(RESULT_F):
            raw = open(RESULT_F, "rb").read()
            m = re.match(rb"KLEI\s+\d+\s+(.*)", raw, re.DOTALL)
            return json.loads(m.group(1).decode())
    except Exception as e:
        return {"error": str(e)}
    return {"error": "no result yet"}

def tick():
    st = read_state()
    # annotate with staleness + key derived facts for the LLM
    if "pos" in st:
        age = time.time() - os.path.getmtime(STATE_F)
        st["_state_age_s"] = round(age, 1)
        st["_paused"] = age > 10
    # compact view: keep the fields that matter, drop noise
    keys = ["day", "phase", "season", "health", "hunger", "sanity", "pos",
            "nearby", "ground_items", "item_counts", "equipped", "threats",
            "fires", "can_build", "is_busy", "temperature", "is_freezing",
            "seconds_until_dusk", "seconds_until_night", "hunger_seconds_remaining",
            "scan", "results", "_errors", "_state_age_s", "_paused"]
    compact = {k: st.get(k) for k in keys if k in st}
    # Claude v7 D1/D2: timestamp freshness is the discriminator. If the file
    # mtime is fresh but timestamp is old, the mod is crashing before encode.
    ts = st.get("timestamp") or 0
    compact["_ts_age_s"] = max(0, time.time() - ts) if ts else None
    print(json.dumps(compact, indent=1))

def main():
    if len(sys.argv) < 2:
        print(__doc__)
        return
    mode = sys.argv[1]
    if mode == "--tick":
        tick()
    elif mode == "--send":
        send_command(sys.argv[2])
        print("sent")
    elif mode == "--result":
        print(json.dumps(read_result(), indent=1))
    elif mode == "--state":
        print(json.dumps(read_state(), indent=1))
    else:
        print(__doc__)

if __name__ == "__main__":
    main()
