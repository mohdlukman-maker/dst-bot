#!/usr/bin/env python3
"""
auto_tuner.py — 2-Minute AI Self-Improving & Auto-Tuning Engine.

Runs every 120 seconds in the background:
1. Gathers the last 2-minute telemetry window (health drops, deaths, food levels, stuck events).
2. Sends summary to AI (Gemini / DeepSeek / OpenRouter) for optimization analysis.
3. Automatically adjusts tuning_config.json with minimal parameters.
4. Bot hot-reloads the new tuning parameters live without restarting the game.
"""
import os
import sys
import time
import json
import re
import logging
from pathlib import Path
from dotenv import dotenv_values

logging.basicConfig(
    format="%(asctime)s - [AutoTuner] %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("auto_tuner")

HERE = Path(__file__).parent.resolve()
CONFIG_FILE = HERE / "tuning_config.json"
LEARN_FILE = HERE / "learnings.json"
LOG_FILE = HERE / "survival_log.txt"
HERMES_ENV = Path.home() / ".hermes" / ".env"
LOCAL_ENV = HERE / ".env"

# Save state paths
DOC = Path.home() / "Documents" / "Klei" / "DoNotStarveTogether"
CS = DOC / "40630831" / "client_save"
STATE_FILE = CS / "dst_ai_bot_state"

# Load API keys
env_vars = {}
if HERMES_ENV.exists():
    env_vars.update(dotenv_values(HERMES_ENV))
if LOCAL_ENV.exists():
    env_vars.update(dotenv_values(LOCAL_ENV))
for k, v in os.environ.items():
    env_vars[k] = v

GEMINI_KEY = env_vars.get("GEMINI_API_KEY") or env_vars.get("GOOGLE_API_KEY")
DEEPSEEK_KEY = env_vars.get("DEEPSEEK_API_KEY")
OPENROUTER_KEY = env_vars.get("OPENROUTER_API_KEY")


def load_tuning_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "hunger_eat_threshold": 35,
        "health_heal_threshold": 50,
        "flee_threat_radius": 12,
        "flee_boss_radius": 35,
        "light_emergency_seconds": 90,
        "target_timeout_s": 15,
    }


def save_tuning_config(config: dict):
    config["last_updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    logger.info("💾 Saved updated tuning_config.json successfully!")


def read_live_state() -> dict:
    if STATE_FILE.exists():
        try:
            raw = open(STATE_FILE, "rb").read()
            m = re.match(rb"KLEI\s+\d+\s+(.*)", raw, re.DOTALL)
            if m:
                return json.loads(m.group(1).decode())
        except Exception:
            pass
    return {}


def collect_recent_telemetry(lines_count: int = 40) -> dict:
    """Collects the last 2 minutes of gameplay telemetry."""
    st = read_live_state()
    recent_logs = []
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                all_lines = f.readlines()
                recent_logs = [l.strip() for l in all_lines[-lines_count:]]
        except Exception:
            pass

    learnings = {}
    if LEARN_FILE.exists():
        try:
            with open(LEARN_FILE, "r", encoding="utf-8") as f:
                learnings = json.load(f)
        except Exception:
            pass

    return {
        "current_state": {
            "day": st.get("day", 1),
            "season": st.get("season", "autumn"),
            "health": st.get("health", [150, 150]),
            "hunger": st.get("hunger", [150, 150]),
            "sanity": st.get("sanity", [200, 200]),
            "equipped": st.get("equipped", []),
            "items": st.get("item_counts", {}),
            "threats": len(st.get("threats", [])),
        },
        "learnings": {
            "gather_success": learnings.get("gather_success", {}),
            "gather_fail": learnings.get("gather_fail", {}),
        },
        "recent_logs": recent_logs,
        "active_config": load_tuning_config(),
    }


def call_ai_tuner(telemetry: dict) -> dict:
    """Queries Gemini / DeepSeek / OpenRouter with the 2-minute performance window."""
    prompt = (
        "You are Wilson's AI Optimizer in Don't Starve Together.\n"
        "Here is the last 2 minutes of Wilson's gameplay telemetry and current tuning parameters:\n\n"
        f"{json.dumps(telemetry, indent=2)}\n\n"
        "TASK:\n"
        "Analyze whether Wilson is taking unnecessary damage, starving, failing gather jobs, or running out of light.\n"
        "Return a minimal JSON dictionary containing ONLY the tuning parameters that should be adjusted in tuning_config.json.\n"
        "Available parameters:\n"
        "- hunger_eat_threshold (int 20..75)\n"
        "- health_heal_threshold (int 30..80)\n"
        "- flee_threat_radius (int 8..30)\n"
        "- flee_boss_radius (int 25..55)\n"
        "- light_emergency_seconds (int 60..150)\n"
        "- target_timeout_s (int 8..30)\n"
        "- notes (brief 1-sentence reason for change)\n\n"
        "Return STRICT JSON only, nothing else."
    )

    try:
        import httpx

        # 1. Try Gemini API
        if GEMINI_KEY:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent?key={GEMINI_KEY}"
            resp = httpx.post(url, json={"contents": [{"parts": [{"text": prompt}]}]}, timeout=15.0)
            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"]
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))

        # 2. Try OpenRouter
        elif OPENROUTER_KEY:
            url = "https://openrouter.ai/api/v1/chat/completions"
            headers = {"Authorization": f"Bearer {OPENROUTER_KEY}"}
            body = {
                "model": "google/gemini-2.5-flash",
                "messages": [{"role": "user", "content": prompt}],
            }
            resp = httpx.post(url, json=body, headers=headers, timeout=15.0)
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))

        # 3. Try DeepSeek API
        elif DEEPSEEK_KEY:
            url = "https://api.deepseek.com/chat/completions"
            headers = {"Authorization": f"Bearer {DEEPSEEK_KEY}"}
            body = {
                "model": "deepseek-chat",
                "messages": [{"role": "user", "content": prompt}],
            }
            resp = httpx.post(url, json=body, headers=headers, timeout=15.0)
            if resp.status_code == 200:
                text = resp.json()["choices"][0]["message"]["content"]
                match = re.search(r"\{.*\}", text, re.DOTALL)
                if match:
                    return json.loads(match.group(0))

    except Exception as e:
        logger.warning(f"AI API query encountered error: {e}")

    # Fallback: Heuristic Auto-Tuner (Zero API cost)
    return heuristic_tuner(telemetry)


def heuristic_tuner(telemetry: dict) -> dict:
    """Smart heuristic auto-tuning when offline or without API keys."""
    curr = telemetry.get("active_config", load_tuning_config())
    state = telemetry.get("current_state", {})
    health = state.get("health", [150, 150])[0]
    hunger = state.get("hunger", [150, 150])[0]

    updated = dict(curr)
    reasons = []

    if health < 60:
        updated["health_heal_threshold"] = min(75, updated.get("health_heal_threshold", 50) + 10)
        updated["flee_threat_radius"] = min(25, updated.get("flee_threat_radius", 12) + 4)
        reasons.append("Raised heal threshold and flee radius due to low HP")

    if hunger < 40:
        updated["hunger_eat_threshold"] = min(60, updated.get("hunger_eat_threshold", 35) + 10)
        reasons.append("Raised hunger eat threshold due to low hunger")

    if reasons:
        updated["notes"] = "; ".join(reasons)
        return updated
    return curr


def run_tuner_cycle():
    """Executes a single 2-minute auto-tuning evaluation."""
    logger.info("🔍 Collecting 2-minute gameplay telemetry window...")
    telemetry = collect_recent_telemetry()
    
    logger.info("🧠 Sending telemetry to AI Optimizer for analysis...")
    adjustments = call_ai_tuner(telemetry)

    if adjustments:
        current_config = load_tuning_config()
        changed = False
        for k, v in adjustments.items():
            if k in current_config and current_config[k] != v:
                logger.info(f"✨ Param change: {k}: {current_config[k]} ➔ {v}")
                current_config[k] = v
                changed = True

        if "notes" in adjustments:
            current_config["notes"] = adjustments["notes"]
            logger.info(f"📝 AI Diagnostic Note: {adjustments['notes']}")

        if changed or "notes" in adjustments:
            save_tuning_config(current_config)
        else:
            logger.info("✅ Bot performance stable. No parameter adjustments required this cycle.")


def main():
    logger.info("🚀 2-Minute AI Auto-Tuner is active! Evaluating gameplay every 120 seconds...")
    while True:
        try:
            time.sleep(120)  # 2 minutes
            run_tuner_cycle()
        except KeyboardInterrupt:
            logger.info("Auto-Tuner stopped.")
            break
        except Exception as e:
            logger.error(f"Error during auto-tuning cycle: {e}", exc_info=True)
            time.sleep(10)


if __name__ == "__main__":
    main()
