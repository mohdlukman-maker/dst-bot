# NEXT SESSION HANDOFF

## Session state (2026-08-10 evening)
- World ended deliberately (game closed) after Day 9, 6 deaths in ~50 min, for
  postmortem analysis. Daemons stopped cleanly, lock file removed.
- Next launch is a FRESH world (no continuation needed).

## What's DONE this session (all fixed live, some not yet loaded - see below)
1. **Crash #1**: `_damage_log_last` UnboundLocalError in `_main_loop()` - it's
   a module-level var but the function's `global` declaration didn't list it,
   so the reassignment later in the function made Python treat every
   reference as local. Fixed: added to the `global` line.
2. **Crash #2**: same species of bug, `stuck_streak` - initialized in `main()`
   but read/written in the sibling function `_main_loop()` (different scope
   entirely, no `global`, no local init there). Fixed: now initialized once
   at the top of `_main_loop()`, removed the dead init in `main()`.
3. **Torch-too-early / chopping interrupted (one root cause, two symptoms)**:
   `try_craft()` force-equipped whatever it just crafted, including torch,
   regardless of time of day or an in-flight chop job. Log proof: craft-torch
   events were immediately followed by `evergreen` jobs failing `tool_broke`.
   Fixed: torch no longer auto-equips on craft; only `reflex.py`'s
   dusk-timer/dark-emergency logic equips it now, and those two paths now
   `preempt_job` first instead of yanking the tool mid-swing.
4. **PICK-mode stall watchdog (modmain.lua)** — NOT YET LIVE, needs a game
   restart to load the mod. WORK-mode jobs (chop/mine) had a 10s stall
   watchdog; PICK-mode (grass/sapling/flint/berries) had none at all - if the
   engine silently rejects a pickup (out of range / target invalidated -
   likely the "I can't do that" voice line the user observed), the job hung
   forever waiting for a `picksomething` event that never fires. Added a 5s
   timeout mirroring the work-mode watchdog.
5. **Marsh death-loop postmortem fix**: this session's world spawned Wilson
   in/near a tentacle-thick marsh. 4 of 5 real deaths were `mob:tentacle`
   (+1 `mob:merm`, +1 darkness). Ghost-revive always returns to the ORIGINAL
   spawn point, so every death dropped him right back in danger, and two
   things made it worse:
   - `pick_target()` had **zero threat awareness** - it would route Wilson
     straight at a flint/grass sitting inside the tentacle nest, scoring
     purely on value/reliability/distance.
   - flee distance was only 15-20 units - not enough to clear a dense
     cluster (7 tentacles were within 20m of him on the last live tick).
   Fixed: added `AGGRESSIVE_PREFABS`/`THREAT_AVOID_RADIUS` (12 units) -
   `pick_target()` now excludes any candidate within that radius of a live
   aggressive/targeting threat. Flee distances widened: reflex.py 15->30,
   local_agent.py's threat-guard 20->35, retreat-invariant 30->40. (Also
   deduped 3 near-identical inline copies of the aggressive-prefab tuple
   into one `AGGRESSIVE_PREFABS` constant.)
   **Not done**: nothing yet detects "I keep dying in the same area" and
   relocates the base/leash away from it - the fix above avoids *walking
   into* threats but doesn't actively flee the whole biome. If the marsh
   death loop recurs even with threat-aware targeting, that's the next
   layer to build.

## Things to verify next session
- [ ] Confirm the PICK-mode stall watchdog fires correctly on a real stuck
      pickup (needs mod reload - first game launch after this handoff will
      pick it up automatically)
- [ ] Confirm torch no longer gets equipped mid-chop (watch a full craft-torch
      -> keep-chopping sequence)
- [ ] Confirm `pick_target()`'s threat exclusion actually keeps Wilson out of
      tentacle/spider nests - watch behavior in a hazardous biome
- [ ] If deaths still cluster in one spot even with threat-aware targeting,
      build the "relocate base after N deaths in the same area" logic
      (`worldmap.set_base` currently only ever sets it once, at spawn)

## Carried over from 2026-08-08 (not re-verified this session)
- SANITY care: petals (+5), cooked green mushrooms (+15) - still not wired
  into any priority/gather logic
- ADVANCED TOOLS: shovel, hammer, science machine, backpack, log suit/football
  helmet armor before hounds - stage progression never got far enough this
  session (kept resetting to "tools" on death) to reach these
- Winter prep (thermal stone, 40+ logs, fire pit) - not reached
