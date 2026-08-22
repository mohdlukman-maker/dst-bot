# PROJECT.md — DST Survival Bot (dst-bot)

## Purpose
An AI that plays Don't Starve Together (DST) in real time by controlling
Wilson, aiming to survive the maximum in-game day count. The AI must
genuinely decide and learn — not follow a script. The user watches this
live; anything that looks like cheating (teleporting items, skipping
animations, spawning structures) is out unless it's an explicit labelled
fallback after the legitimate path fails.

## Objective
Build and maintain a 3-tier agent that keeps Wilson alive indefinitely
through genuine perception → decision → action loops, with hard
survival fail-safes independent of the slow reasoning layer.

## Architecture (3 tiers, split by latency)
- **Lua mod** (`dst_ai_bot/modmain.lua`) — 0.25–1s. Sensing, action
  execution, job-runner. The ONLY channel to the game.
- **Reflex daemon** (`reflex.py`, Python) — 200ms. Survival fail-safes
  (eat/flee/fuel/light). Never blocks on the LLM.
- **Agent** (`local_agent.py` / `llm_agent.py` / `llm_brain.py` /
  `brain.py`) — 2–60s. Goals and planning, not keystrokes.

Channel: `claude_drive.py --tick` (read JSON state) / `--send`
(issue one command) / `--result` (read last outcome). The Lua mod and
agent talk via the game's persistent-string save API (SetPersistentString
/ GetPersistentString) — the DST mod sandbox blocks io.open.

## Technology Stack
- Language: Python 3 (agent/reflex), Lua (in-game mod).
- LLM: DeepSeek (key loaded at runtime from Hermes `.env` as
  DEEPSEEK_API_KEY — never hardcoded; see brain.py).
- Vision: vision.py (screenshot capture/analysis).
- No external services required for core play; LLM optional for planning.

## Important Constraints
- **Local-first / offline game host.** Offline solo host,
  `GLOBAL.TheWorld.ismastersim == true`. We are the server.
- Every mod edit requires a full game restart (~5 min). Syntax and
  nil-crash review before saving is worth the time.
- The `ref` symlink points at the base mod clone
  (DST-ArtificialWilson, hineios) used as a reference — git-ignored.
- No real secrets in the repo. `brain.py` reads DEEPSEEK_API_KEY from
  the Hermes `.env` at runtime.

## Source Layout
- `dst_ai_bot/` — Lua mod (modmain.lua, modinfo.lua). Deployed into the
  DST mods folder.
- `reflex.py` — 200ms emergency daemon.
- `local_agent.py` — main agent loop (largest module).
- `llm_agent.py` / `llm_brain.py` / `brain.py` — LLM-driven planning.
- `driver.py` / `claude_drive.py` — game-state I/O bridge.
- `lib/` — decision_log, explore, invariants, plan, run_log,
  state_reader, targets, world_map.
- `vision.py` — screenshot capture.
- `tests/` — pytest suite (test_decision_log, test_explore,
  test_invariants, test_lessons, test_plan, test_run_log,
  test_state_reader, test_targets, test_world_map).
- `data/`, `knowledge/` — run state and game knowledge JSON/MD.
- `CLAUDE.md` — operator manual (the agent's playbook). README.md,
  INTEGRATION.md, MOD_DESIGN_NOTES.md, NEXT_SESSION.md — project docs.

## Security / Privacy
- No API keys, passwords, or personal data committed.
- `*.env`, runtime logs, screenshots, and the `ref` symlink are
  git-ignored.
- DeepSeek key is loaded from the local Hermes `.env` at runtime only.

## Supported Platforms
- Windows 10/11 (dev host, git-bash/MSYS) driving a local DST install.
- Python 3.11+ with stdlib only for core agent (verify deps).

## Known External Dependencies
- Don't Starve Together (local install) with the Lua mod enabled.
- DeepSeek API access (key in Hermes `.env`) for LLM planning tier.
- Optional: ODA/DWG tooling only if CAD overlap is needed (not core).
