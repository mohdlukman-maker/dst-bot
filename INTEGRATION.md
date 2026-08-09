# Integration Guide — measurement & memory layer

This document describes, in plain English, the three places the existing bot
code should call this library. **No code here is implemented yet** — these are
the wiring notes.

---

## What we built

| File | What it does |
|------|-------------|
| `lib/state_reader.py` | The ONE safe way to read game state. Strips the KLEI header, computes freshness (`age_s`), surfaces mod-reported section errors. Never raises. |
| `lib/run_log.py` | One JSONL line per play session ("run"). Answers: is the bot getting better? |
| `lib/decision_log.py` | Every action becomes a prediction recorded BEFORE acting, compared AFTER. Wrong predictions = training data. |
| `lib/world_map.py` | Permanent spatial memory: where grass/saplings/flint were seen, per world. Survives restarts. |
| `lib/lessons.py` | Turns refuted predictions into readable rules (High/Medium confidence). |

All data lives under `data/`: `runs.jsonl`, `decisions.jsonl`, `lessons.md`,
`maps/<world_key>.json`.

---

## Where to call what (4 integration points)

### 1. Bot startup → `start_run()`

When the bot session begins (game loaded, agent loop starting):

```python
from lib import run_log
RUN_ID = run_log.start_run(mod_version="v8", notes="new world")
```

The `run_id` is held in memory and used for every decision logged in this
session, so all decisions can be traced back to a run.

### 2. Every control-loop tick → `read_state()`, and SKIP if not fresh

This is the most important wiring change, and the only one that would have
prevented two past false bug reports (the bot acted on a corpse of a state
file after the mod crashed):

```python
from lib import state_reader

tick_state = state_reader.read_state(SAVE_DIR, max_age_s=5.0)
if not tick_state["fresh"]:
    # state is stale or missing: the mod may have crashed, or the game
    # is paused. DO NOT act on it. Log the reason, wait, try again.
    #   reason: "missing" / "unreadable" / "bad_json" / "stale" / "no_timestamp"
    continue
state = tick_state["state"]
# also available: tick_state["errors"] (mod-reported section failures)
#                 tick_state["age_s"]
```

Also use `read_heartbeat()` for pause/crash triage:

```python
hb = state_reader.read_heartbeat(SAVE_DIR)
# hb["verdict"]: "running" | "paused" | "dead" | "unknown"
```

### 3. Every action → `log_decision()` before, `log_outcome()` after

Before pushing a command to the game, record what you expect:

```python
from lib import decision_log

before = current_state
did = decision_log.log_decision(
    run_id=RUN_ID,
    state=before,
    goal="acquire_twigs",
    action={"action": "gather_job", "prefab": "sapling"},
    why="need 2 twigs for torch; nearest ready sapling at 4m",
    expected={"item_counts.twigs": "+1"},
)
# ... send the command, wait for it to complete ...
decision_log.log_outcome(did, state_after)
# verdict: "confirmed" | "refuted" | "inconclusive"
```

The expectation syntax is deliberately small: `"+N"`, `"-N"`, `"increases"`,
`"decreases"`, `"changes"`, `"unchanged"`, with dot-paths like
`"item_counts.log"`. Missing numeric fields count as 0.

### 4. Wilson dies / session ends → `end_run()`

```python
from lib import run_log

run_log.end_run(
    run_id=RUN_ID,
    final_state=last_state,     # day, health, hunger, season are extracted
    cause="mob",                # darkness / starvation / mob / freezing /
                                # crash / manual_stop / unknown
    extra={"structures": ["campfire", "axe"]},
)
```

---

## Closing the loop: lessons from decisions

Periodically (e.g. once per run end, or when the agent is idle):

```python
from lib import lessons
lessons_text = lessons.build_lessons()   # writes data/lessons.md
```

This reads every recorded decision, finds actions that were predicted and
refuted 3+ times, and writes plain-language rules. After a few runs you get
things like:

> `gather_job sapling` failed 7 times. Most common expectation:
> `item_counts.twigs +1`. Last seen day 4.

Which tells you the *sensing* is broken for that prefab — a real bug, found
automatically by comparing predictions to outcomes.

---

## Rules of the road

- **Never crash the caller.** Every public function in `lib/` catches its own
  exceptions and returns a safe default. The live game loop must never die
  because logging failed.
- **Append-only, always.** `runs.jsonl` and `decisions.jsonl` only grow.
  Corrupt lines are skipped, never fatal.
- **Never delete map entries.** A picked grass tuft still marks where grass
  grows. `world_map.observe()` only merges and updates.
- **Freshness is explicit.** If `fresh` is False, the caller must not act.
  Partial states (with `_errors`) are still usable — the caller decides.

---

## Suggested next steps (out of scope here)

1. Wire `state_reader` into `local_agent.py` and `reflex.py` (replaces the
   ad-hoc `get_state()` calls).
2. Add `log_decision`/`log_outcome` around `send()` in the agent loop.
3. Use `world_map` instead of the current `worldmap.py` (which has a similar
   purpose but no merge rules or base concept).
4. Add the v8 survival invariants (leash check, health-delta retreat,
   emergency food reserve) as a new `lib/invariants.py`.
