#!/usr/bin/env python3
"""
DST AI Bot - CLI (v0.5, persistent-string channel)

The in-game mod writes state to the game's save dir as a KLEI persistent
string (client_save/dst_ai_bot_state), and reads commands from
client_save/dst_ai_bot_command. This CLI bridges those files.

KLEI format: "KLEI     1 " prefix + payload (10-byte header).
"""
import argparse
import re
import json
import os
import sys
import time

# The game's save dir (Documents\\Klei\\DoNotStarveTogether\\<steamid>)
DOC = os.path.join(os.path.expanduser("~"), "Documents", "Klei", "DoNotStarveTogether")

def find_save_dir():
    """Locate the 40630831 (or similar) save dir under the Klei folder."""
    if not os.path.isdir(DOC):
        return None
    for name in sorted(os.listdir(DOC)):
        p = os.path.join(DOC, name)
        if os.path.isdir(p) and os.path.exists(os.path.join(p, "client_save")):
            return p
    return None

SAVE_DIR = find_save_dir()
CLIENT_SAVE = os.path.join(SAVE_DIR, "client_save") if SAVE_DIR else None
STATE_FILE   = os.path.join(CLIENT_SAVE, "dst_ai_bot_state") if CLIENT_SAVE else None
CMD_FILE     = os.path.join(CLIENT_SAVE, "dst_ai_bot_command") if CLIENT_SAVE else None
RESULT_FILE  = os.path.join(CLIENT_SAVE, "dst_ai_bot_result") if CLIENT_SAVE else None

KLEI_HEADER = b"KLEI     1 "

def strip_klei(raw):
    """Remove the KLEI header ('KLEI' + spaces + version + space); return payload."""
    if raw is None:
        return None
    if raw.startswith(b"KLEI"):
        m = re.match(rb"KLEI\s+\d+\s+(.*)", raw, re.DOTALL)
        if m:
            return m.group(1).decode("utf-8", errors="replace").strip()
        return None
    return raw.decode("utf-8", errors="replace").strip()

def add_klei(payload: str) -> bytes:
    return KLEI_HEADER + payload.encode("utf-8")

def read_state():
    if not STATE_FILE or not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "rb") as f:
            raw = f.read()
        txt = strip_klei(raw)
        return json.loads(txt) if txt else None
    except Exception:
        return None

def send_command(cmd_dict, timeout=15.0):
    cmdid = int(time.time() * 1000)
    cmd_dict = dict(cmd_dict)
    cmd_dict["id"] = cmdid
    payload = json.dumps(cmd_dict)
    if CMD_FILE:
        with open(CMD_FILE, "wb") as f:
            f.write(add_klei(payload))
    # Wait for result file with matching id
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            if RESULT_FILE and os.path.exists(RESULT_FILE):
                with open(RESULT_FILE, "rb") as f:
                    raw = f.read()
                res = json.loads(strip_klei(raw) or "{}")
                if res.get("id") == cmdid:
                    return res.get("result")
        except Exception:
            pass
        time.sleep(0.2)
    return None

def fmt_pct(a, b):
    if a is None or not b:
        return "?"
    return f"{a:.0f}/{b:.0f}"

def cmd_status():
    state = read_state()
    if not state:
        print("  No state from game yet.")
        print("  Save dir:", SAVE_DIR or "NOT FOUND")
        print("  (Mod must be running in-game; it writes state to client_save/dst_ai_bot_state)")
        return
    print("  === LIVE GAME STATE ===")
    print(f"  Day    : {state.get('day','?')}   Phase: {state.get('phase','?')}   Season: {state.get('season','?')}")
    p = state.get("pos")
    if p:
        print(f"  Pos    : x={p.get('x','?')} y={p.get('y','?')} z={p.get('z','?')}")
    hp = state.get("health") or [None, None]
    hg = state.get("hunger") or [None, None]
    sn = state.get("sanity") or [None, None]
    print(f"  Health : {fmt_pct(hp[0], hp[1])}")
    print(f"  Hunger : {fmt_pct(hg[0], hg[1])}")
    print(f"  Sanity : {fmt_pct(sn[0], sn[1])}")
    items = state.get("items") or []
    print(f"  Inv    : {', '.join(items) if items else 'empty'}")
    print(f"  Timestamp: {state.get('timestamp')}")

def cmd_ping():
    res = send_command({"action": "ping"})
    if res is None:
        print("  No response (mod not running in-game, or command file not picked up).")
    else:
        print(f"  Pong! reply: {res.get('reply')}")

def cmd_move(x, z):
    res = send_command({"action": "move_to", "x": float(x), "z": float(z)})
    if res is None:
        print("  No response (mod not running in-game?).")
    else:
        print(f"  {res.get('reply') or res.get('error')}")

def cmd_say(text):
    res = send_command({"action": "say", "text": text})
    if res is None:
        print("  No response (mod not running in-game?).")
    else:
        print(f"  {res.get('reply') or res.get('error')}")

def REPL():
    print("DST AI Bot CLI  (v0.5 - persistent-string channel)")
    print("Commands: status | ping | move_to <x> <z> | say <text> | exit")
    while True:
        try:
            line = input("dstbot> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        parts = line.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ""
        if cmd in ("exit", "quit"):
            print("Bye!")
            break
        elif cmd == "status":
            cmd_status()
        elif cmd == "ping":
            cmd_ping()
        elif cmd == "move_to":
            try:
                x, z = arg.split()
                cmd_move(float(x), float(z))
            except ValueError:
                print("  usage: move_to <x> <z> (numbers)")
        elif cmd == "say":
            cmd_say(arg)
        else:
            print("  Unknown command. Try: status | ping | move_to x z | say text | exit")

def main():
    ap = argparse.ArgumentParser(description="DST AI Bot CLI")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--ping", action="store_true")
    args = ap.parse_args()
    if args.ping:
        cmd_ping()
        return
    if args.once:
        cmd_status()
        return
    REPL()

if __name__ == "__main__":
    main()
