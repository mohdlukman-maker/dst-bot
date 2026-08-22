# STATUS.md — Current Verified State

_Generated during the GitHub-migration pass (2026-08-22). Verified by
inspecting the actual working tree and git state, not prior chats._

## Completion State
- Project is a functioning 3-tier DST survival bot (Lua mod + Python
  reflex daemon + Python LLM agent). 6 commits on `master`.
- Repository hygiene established: runtime logs/screenshots and the
  `ref` symlink removed from tracking; `.gitignore` in place.
- `AGENTS.md` + `.ai/` state files written so a fresh clone reconstructs
  context.

## Completed Components
- **Lua mod** (`dst_ai_bot/modmain.lua`): sensing, action dispatch,
  job-runner (gather_job walk→work→sweep→verify), state schema.
- **Reflex daemon** (`reflex.py`): 200ms fail-safes (eat/flee/fuel/light),
  never blocks on LLM.
- **Agent** (`local_agent.py`, `llm_agent.py`, `llm_brain.py`,
  `brain.py`): planning tier, DeepSeek-backed.
- **Bridge** (`claude_drive.py`, `driver.py`): JSON state I/O.
- **Lib** (`lib/*.py`): decision_log, explore, invariants, plan, run_log,
  state_reader, targets, world_map.
- **Tests**: 9 pytest modules.
- **Docs**: CLAUDE.md (operator playbook), README, INTEGRATION,
  MOD_DESIGN_NOTES, NEXT_SESSION.

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
