# STATUS.md — Current Verified State

_Generated during the GitHub-migration pass (2026-08-22). Verified by
inspecting the actual working tree and git state, not prior chats._

## Completion State
- Project upgraded to a full 100-Day Survival Architecture (multi-phase roadmap, Crock Pot food economy, temperature and seasonal reflexes, and boss evasion).
- 102 pytest tests passing cleanly across 11 test modules.
- Repository hygiene intact: tracked files clean and ready for local play.

## Completed Components
- **Multi-Phase 100-Day Roadmap** (`lib/plan.py`):
  - Phase 1 (Days 1-5): Tools, light insurance, food reserve, spear.
  - Phase 2 (Days 6-15): Science Machine, Alchemy Engine, Backpack, Log Suit armor, Crock Pot, Base Chests, Lightning Rod.
  - Phase 3 (Days 16-20): Winter Prep (Thermal Stone `heatrock`, warm clothing, ice mining, log stockpiles).
  - Phase 4 (Days 21-35): Winter Survival (Crock Pot cooking, heated Thermal Stone, Deerclops evasion).
  - Phase 5 (Days 36-55): Spring Survival (Waterproofing with Umbrella/Eyebrella + Lightning Rod + Frog Rain Avoidance).
  - Phase 6 (Days 56-70): Summer Survival (Endothermic Fire Pit + Chilled Thermal Stone + Ice Flingomatic).
  - Phase 7 (Days 71-100): Long-term Sustain (Tooth Trap field + Autonomous farming loop).
- **Crock Pot Optimizer** (`lib/cooking.py`): Evaluates and prioritizes Meatballs (hunger) and Pierogi (40 HP healing).
- **Reflex Daemon** (`reflex.py`): Fast 200ms fail-safes upgraded for temperature control (freezing/overheating), weather/rain waterproofing, fire fuel, and boss evasion.
- **Local Agent** (`local_agent.py`): Upgraded `PLAN_STAGES` and `CRAFT_PRIORITIES` to include all 100-day tech tree items.
- **Tests**: 102 pytest test cases in `tests/` covering plan, roadmap, cooking, invariants, targets, and world map.

## In Development (as of last commit 4ce0025 / WIP e9dd921)
- Active WIP on reflex.py, local_agent.py, lib/explore.py, and the Lua
  mod (dst_ai_bot/modmain.lua) — captured but untested end-to-end in
  this pass.
- `llm_agent.py`, `llm_brain.py`, `start_bot.py` newly added,
  not yet exercised.

## Known Bugs
- Crash bugs and torch/chop interrupt + marsh death-loop were fixed in
  commit 4ce0025 (postmortem). Re-test after restart.
- WIP modifications not yet validated by a live run or full test pass.

## Known Limitations
- Every Lua mod change requires a full DST restart (~5 min) to verify.
- DeepSeek key is runtime-only; planning tier silently degrades without it.
- `data/` and `agent_state.json` are small runtime state, tracked for
  context but regenerated during play.

## Current Branch
- `master` (no remote yet at inspection; remote added during migration).

## Last Meaningful Commit
- `e9dd921` chore: repo hygiene + capture WIP before GitHub migration
- Prior: `4ce0025` Fix crash bugs, torch/chop interrupt, marsh death-loop

## Current Technical Problems
- None blocking. WIP code needs a live verification run.

## Current Risks
- `ref` symlink to DST-ArtificialWilson is git-ignored but still on
  disk — do not `git add ref`.
- Runtime logs regenerate; if `.gitignore` is bypassed (e.g. `git add -f`)
  they would bloat the repo.

## Last Completed
- Repo hygiene: de-tracked 14 MB survival_log.txt + PNGs + ref gitlink.
- Added `.ai/` state and `AGENTS.md`.
- Local commit e9dd921; pushed to private GitHub remote (migration).

## Remaining Unfinished
- Live end-to-end test of the WIP changes.
- Decide fate of stale `claude_*.md` working drafts (kept per user choice).
- Periodic: keep `.ai/STATUS.md` and `NEXT_TASK.md` current.
