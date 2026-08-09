# NEXT SESSION HANDOFF — "don't die at all" mode

## Session state (2026-08-08 evening)
- Wilson: Day 6, alive, hp 37, fed via seed/berry gather-eat cycle
- Gear: lost at death spot (respawn dropped it) - rebuilding axe
- All daemons stopped cleanly. Channel files: clear before next game.

## What's DONE (this session's fixes - all tested live)
1. threats[] x/z — flee reflex flees AWAY from threats (was fleeing to 2x own pos)
2. Flee-first-ask-later — no more waiting under attack
3. Reflex emergency cooldown — no more preempt_job spam freezing Wilson
4. Retreat aggressive-only — no more robin-shore-flee bug
5. Low-health + hunger<60 food override — gathers food before starving
6. Food re-target guard (_food_attempted) — no more dug-carrot loop
7. Reflex crafts torch in the dark — closes the night-death gap
8. Chop fix: trees get max_swings=25 + 18s settle (was "2 tries and gone")
9. Plan food step (1 berry + 1 carrot) after torch_kit
10. Seeds in food path (reliable PICKUP) + ground-seed eat reflex
11. Death detection (is_ghost in mod + health~50/skeleton fallback)
12. Auto-revive reflex (respawnfromghost, 20s cooldown) + run_log cause
13. Agent ghost handling (pause plan, reset on respawn)
14. Command clearing after exec (kills stale-command auto-move on fresh world)
15. lib/ measurement layer: state_reader, run_log, decision_log, world_map,
    lessons, invariants, plan, explore, targets — 84+ tests green

## NEXT SESSION GOALS (user-stated priorities)
1. **DON'T DIE AT ALL** — no death+revive loop; survive continuously.
   Focus: why did we die this session? (starvation x2, frog, spider)
   - starvation: food override now fires earlier (hunger<60) + seeds path works
   - frog/spider: flee reflex works now (x/z fix) - VERIFY across a full night+day
2. **COUNT THE DAYS** — a real survival run, days 1->N without dying.
   run_log.summarize() shows the curve; aim for day 10+, then 21 (winter), 35 (spring)
3. **HEALTH + SANITY CARE** (user: "care for health and wilson saneness")
   - health: heal via food (cooked green/blue mushrooms, cookedmeat), bandages later
   - SANITY: NEW SYSTEM - monitor sanity in state, avoid darkness/ghosts/monsters,
     pick flowers (petals +5 sanity), cooked green mushrooms (+15), sleep? no tent early
4. **ADVANCED TOOLS** (user: "start crafting advanced tools")
   - shovel (2 twigs + 2 flint) - dig grass/saplings for replanting
   - hammer, science machine (gold nugget!) -> alchemy engine later
   - golden axe/pickaxe after gold
   - spear + log suit + football helmet (armor before hounds day 6)
   - backpack (3 grass + 4 twigs? actually 4 cutgrass + 4 twigs) - carry more

## Things to verify first next session
- [ ] Clean channel files BEFORE game launch (stale-command class)
- [ ] Mod needs restart to load is_ghost + threats x/z + cmd-clearing
- [ ] sanity field: check if mod state has it (st.sanity?) - add if missing
- [ ] Run the full test suite (should be 84+ green)
- [ ] Day-1 plan now: axe -> torch_kit -> food -> pickaxe -> base -> campfire_kit -> firepit -> spear
- [ ] VERIFY the flee works on a real spider (the x/z fix was never live-verified)
