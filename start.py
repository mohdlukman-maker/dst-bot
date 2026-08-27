#!/usr/bin/env python3
"""
start.py — Unified One-Click Launcher for DST Autonomous AI Bot.

Launches the complete 3-component self-improving system:
1. Reflex Layer (reflex.py) — 200ms emergency light/eating/kiting
2. Game Driver (local_agent.py) — 100-day roadmap execution
3. 2-Minute AI Auto-Tuner (auto_tuner.py) — analyzes telemetry every 120s & adjusts parameters

Press Ctrl+C to cleanly stop all processes.
"""
import subprocess
import sys
import time
import os
import signal
from pathlib import Path

HERE = Path(__file__).parent.resolve()
PYTHON_EXE = sys.executable

processes = []

def cleanup(sig=None, frame=None):
    print("\n🛑 Stopping all bot processes cleanly...")
    for p, name in processes:
        try:
            p.terminate()
            p.wait(timeout=2.0)
        except Exception:
            try:
                p.kill()
            except Exception:
                pass
    print("✅ All bot processes stopped. Goodbye!")
    sys.exit(0)

signal.signal(signal.SIGINT, cleanup)
signal.signal(signal.SIGTERM, cleanup)

def main():
    print("=" * 70)
    print("🚀 STARTING DON'T STARVE TOGETHER AUTONOMOUS BOT + AI AUTO-TUNER")
    print("=" * 70)
    print("• Fast Local Driver: 1 tick/sec real-time gameplay (0 latency)")
    print("• Emergency Reflexes: 200ms light/eating/dodge daemon")
    print("• 2-Minute AI Tuner: Analyzes telemetry & hot-adjusts code/parameters")
    print("=" * 70)

    # 1. Start Reflex Daemon
    print("[Launcher] 1/3 Starting Reflex Daemon (reflex.py)...")
    p_reflex = subprocess.Popen(
        [PYTHON_EXE, str(HERE / "reflex.py")],
        cwd=str(HERE),
    )
    processes.append((p_reflex, "reflex"))
    time.sleep(1.0)

    # 2. Start 2-Minute AI Auto-Tuner
    print("[Launcher] 2/3 Starting 2-Minute AI Auto-Tuner (auto_tuner.py)...")
    p_tuner = subprocess.Popen(
        [PYTHON_EXE, str(HERE / "auto_tuner.py")],
        cwd=str(HERE),
    )
    processes.append((p_tuner, "auto_tuner"))
    time.sleep(1.0)

    # 3. Start Local Agent Driver
    print("[Launcher] 3/3 Starting Local Agent Driver (local_agent.py)...")
    p_agent = subprocess.Popen(
        [PYTHON_EXE, str(HERE / "local_agent.py")],
        cwd=str(HERE),
    )
    processes.append((p_agent, "local_agent"))

    print("\n✅ All systems active! Wilson is playing in the background.")
    print("💡 Press Ctrl+C anytime to stop.\n")

    while True:
        try:
            time.sleep(1.0)
            for p, name in processes:
                if p.poll() is not None:
                    print(f"⚠️ Process {name} exited with code {p.returncode}")
        except KeyboardInterrupt:
            cleanup()
            break

if __name__ == "__main__":
    main()
