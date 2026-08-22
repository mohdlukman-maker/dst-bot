# NEXT_TASK.md — Next Development Task

## Objective
Verify the active WIP changes with a live or simulated run, then
continue hardening the survival loop.

## Why It Is Needed
Commits 4ce0025 (crash/torch/marsh fixes) and e9dd921 (WIP capture)
modified reflex.py, local_agent.py, lib/explore.py, and the Lua mod,
but these were not validated by a full play session. The bot's value
is real survival, so changes must be exercised, not assumed correct.

## Relevant Files
- `reflex.py`, `local_agent.py`, `lib/explore.py`, `dst_ai_bot/modmain.lua`
- `tests/` (run full suite first)
- `claude_drive.py` (state I/O for manual tick tests)

## Requirements
1. Run `pytest` (or `python -m pytest tests/`) — all 9 suites green.
2. Start a local offline DST host with the Lua mod enabled.
3. Exercise a short session: gather_job, craft torch+equip before dusk,
   fuel fire, eat when hunger low. Confirm commands verify via `--tick`.
4. Watch reflex.py for false negatives (e.g. burnt-out fire no longer in
   fires[] — must still trigger emergency light).
5. Update `.ai/STATUS.md` with results.

## Acceptance Criteria
- `pytest` passes with no regressions.
- A live session shows: torch equipped before dusk, fire fueled, agent
  eats before hunger <30, no ghost-from-starvation.
- `.ai/STATUS.md` updated with the verification outcome.

## Constraints
- Every Lua mod edit needs a full DST restart (~5 min) before trusting it.
- Do not bypass `.gitignore` (`git add -f` on logs/PNGs/ref).
- Keep `CLAUDE.md` playbook in sync if survival rules change.

## Things That Must NOT Be Changed Unnecessarily
- `dst_ai_bot/modmain.lua` channel contract (SetPersistentString keys).
- The reflex daemon's 200ms independence from the LLM.
- Secret handling (key only via Hermes `.env` at runtime).
