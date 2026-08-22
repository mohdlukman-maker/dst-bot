#!/usr/bin/env python3
"""
LAUNCHER: reflex (instant safety) + llm_agent (Option A AI brain + queue).
The old rule-based local_agent.py is NOT started - the LLM brain replaces it.

Usage:  python start_bot.py
Starts both daemons with unbuffered output. Their logs:
  reflex_out.log    - emergency actions (flee/eat/light)
  llm_agent_out.log - every AI decision, command sent, result
"""
import os, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
PY = sys.executable

def run(name, script):
    out = open(os.path.join(HERE, f"{name}_out.log"), "a", encoding="utf-8")
    p = subprocess.Popen([PY, "-u", os.path.join(HERE, script)],
                         cwd=HERE, stdout=out, stderr=subprocess.STDOUT)
    print(f"{name}: pid {p.pid}")
    return p

if __name__ == "__main__":
    print(f"[{time.strftime('%H:%M:%S')}] starting DST bot (reflex + llm brain)")
    r = run("reflex", "reflex.py")
    time.sleep(2)
    a = run("llm_agent", "llm_agent.py")
    print("both daemons up. ctrl-c does NOT stop them - kill by pid or 'Stop' in Hermes.")
