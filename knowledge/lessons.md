# DST AI Bot - Lessons Learned

## Session 2 (2026-08-08) - Wilson died to frogs, Day 1
- **Death cause**: frogs (3 at 3-10m), health dropped 150 -> 50 -> dead. Reflex layer
  only watched hunger/health thresholds, NOT threats.
- **Lesson (confidence: high)**: Wilson's health can drop fast to mobs. The reflex
  layer MUST flee from hostiles (frog/spider/hound/tentacle within ~8m), not just
  eat when hungry.
- **Lesson (high)**: gather WORKS via `ClearBufferedAction()` + 
  `locomotor:PushAction(BufferedAction(player, target, action), true)` — the `true`
  walk-into-range flag is the fix. Manual GoToPoint BEFORE PushAction CONFLICTS.
- **Lesson (high)**: `GLOBAL.TheWorld` IS available; `TheWorld.ismastersim=true` on
  offline host. We ARE the server. Client-RPC theories were moot.
- **Lesson (high)**: job runner works — DoPeriodicTask(0.25) + stall watchdog +
  swing cap. Chopped tree to workleft 1 in one job (needed swing-cap tolerance:
  don't stop when workleft <= 3).
- **Lesson (medium)**: `item_counts` via `Inventory:Has(prefab, 1, true)` counts
  stacks correctly; itemslots iteration under-counts.
- **Lesson (medium)**: chopped logs/picked grass drop to GROUND first; need
  separate PICKUP (ground_items via FindEntities INLIMBO filter).
- **Lesson (high)**: verify mod syntax before reload — a stray `end` crashes the
  mod load ("Disable mod or exit"). Always block-balance check + read client_log.txt.
- **Lesson (medium)**: two brain.py processes running = command file fights =
  Wilson freezes. Single-instance guard required.

## Session 1 (2026-08-07) - Wilson starved (twice), Day 3-4
- **Lesson (high)**: hunger hits 0 fast (day 1-2 if not eating); food FIRST when
  hunger < 40. Starvation kills faster than expected.
- **Lesson (medium)**: `gather` needs the target in range; "no valid action" can
  mean busy, missing tool, OR already-picked. Log which.
- **Lesson (medium)**: game autopauses when window unfocused -> bot freezes.
  Keep DST focused.

## 2026-08-08 — STARVATION DEATH (Day 4, hunger 0)

### What happened
Wilson died of starvation with cutgrass 19 + twigs 6 in inventory, next to
food he couldn't gather. The food override looped on a dug-out carrot
(carrot_planted@-335,-7) every 8s, then finally moved to a berrybush — too late.

### Root causes (ALL the food gaps)
1. **Food override targeted spent spots** — a dug carrot stayed "nearest" and
   got re-targeted forever. FIXED: _food_attempted set skips tried (guid,prefab).
2. **carrot_planted is DIG, not PICK** — gather_job digs it; if already dug,
   nothing yields. The override didn't verify collection and move on.
3. **berrybush picked only when RIPE** — an unripe bush falls through to
   workable -> DIG (uproots, no berries). Need to check pickable readiness.
4. **No inventory food reserve** — the plan's food step (1 berry + 1 carrot)
   existed but the gather loop couldn't complete it near spawn.
5. **Ground seeds are PICKUP but gather_job(prefab="seeds") found nothing** —
   seeds may be ground_items, not the entity scan list.
6. **Hunger threshold too late** — override fired at hunger<60 but food was
   10-25m away; travel + gather > remaining hunger time at low hunger.

### Fixes applied
- food override: hunger<60 OR hp<50 triggers; _food_attempted re-target guard
- plan: food step (1 berry + 1 carrot) added after torch_kit
- chop: trees now get max_swings=25 + 18s settle (was 3 + 5s = "2 tries and gone")

### The REAL lesson (meta)
Food must be treated like the axe: **a standing Day-1 priority with a verified
mechanism**, not an override that fires when already starving. The eat reflex
checks inventory; the agent must check the WORLD MAP for food spots EARLY and
gather a reserve BEFORE hunger drops. Next: seed the map, verify food yield
per prefab, and treat berries/carrots as a plan step with collection verification.


## Session 2026-08-11 — Death #2: "Picked-clean spawn" (day 5 night, hp 150→0 in dark)

### What happened (verified from unbuffered agent log)
1. Spawn area had NO grass/saplings within 35m (picked clean / rocky biome).
2. Agent's food override (hunger<60) trapped it in a SEED loop: seeds = +1 hunger
   each, hunger hovered 36-48 all day -> plan (axe→torch→campfire) NEVER ran.
3. Day 1-2: dusk guard didn't exist yet -> night hit with no light -> sanity
   crash 164→0 in ~50s -> crawlinghorror/terrorbeak spawn at sanity 0.
4. Flee reflex worked (fled horrors) but dragged Wilson through a TENTACLE
   swamp (he was near swamp biome) -> repeated tentacle damage + endless
   flee-chains every night (09:39-09:47, 09:54).
5. Day 5: finally found twigs/flint (rocky area), crafted axe+pickaxe, but
   cutgrass stuck at 1 (needs 2 for torch, 3 for campfire) - the ONE missing
   resource. Dusk guard fired but 35m scan found nothing.
6. Died in the dark at day 5 night, hp 30. Revived via reflex (day 6, hp 50).

### Root causes
- **A. No torch-material contingency**: dusk guard scanned nearby ONLY; no
  world-map fallback. FIXED in v9.1 (walks to known grass/sapling spot).
  VERIFIED WORKING: 10:06:58 "walking to known sapling@(241,-267)".
- **B. Seed trap**: food override with seeds only = infinite loop, plan starved.
  FIXED in v9 (seeds last priority, fall-through to plan when hunger ≥55).
- **C. Sanity-0 horror spiral**: once sanity hits 0 at night, horrors spawn and
  flee-chains waste the whole night + drag into danger. The REAL fix is never
  reaching night without light (A+B). Secondary: flee should avoid water/swamp
  if possible (hard - keep as known limitation).
- **D. State lag during analysis**: agent read state while game ran; by the time
  a decision was made, health had moved. FIXED in v9.2: pause() before analysis.

### New capabilities added (v9.2)
- Mod: pause/unpause commands (TheNet:SetServerPaused, static poll survives
  pause - verified APIs from mainfunctions.lua).
- Agent: set_game_paused() around critical-health and stuck analyses.
- Logs now unbuffered (-u) so decisions are visible live.

### Next game strategy (differs from last)
1. DUSK-GUARD-FIRST: at phase==dusk (or ≤120s to night) with no light, STOP
   everything and gather torch kit, using WORLD MAP if nearby is empty.
2. TORCH KIT = 2 cutgrass + 2 twigs kept ALWAYS (not just at dusk) once axe
   exists - the campfire-kit invariant now includes the torch half.
3. Never eat seeds above hunger 40 (they're +1 hunger; berries/carrot first).
4. World-map-aware travel: when a roam pass yields nothing, go to the NEAREST
   known resource cluster (map has 367 known spots) instead of random explore.
5. Pause before analysis (v9.2) so health can't drift during diagnosis.


## 2026-08-11 — Architecture change: Option A (LLM decides everything)

User decision: "this project doesn't feel like AI decides anything autonomously"
-> the rules agent was the architect/mechanic, never the driver. Direction chosen:
LLM decides EVERY action, batched 5-at-a-time (user's design) so there's no
game lag while the brain thinks.

### New architecture (live)
- llm_brain.py  - DeepSeek (deepseek-chat, 1.1s) reads summarized state
  (+ world-map known spots beyond sensor + lessons.md memory), returns JSON
  array of <=5 commands. response_format=json_object.
- llm_agent.py  - queue executor: sends 1 cmd at a time, waits for the mod's
  result, prefetches the next batch when queue <=2 (demand-driven Event, NOT
  a 3s timer - the first version spammed ~20 API calls/min). Publishes
  current_run.txt so the reflex attributes deaths. On death: logs run + quit.
- validator     - thin guardrails: no gather/move at night without light,
  no walking toward hostiles, no gather next to hostiles, craft/eat/equip
  material checks, move_to cap 250m, say cooldown 12s, command dedup.
- reflex.py     - unchanged instant emergencies (flee/eat/light) - no revive
  (user: repeat until death, then quit).
- local_agent.py - RETIRED as driver (kept as fallback).

### BUGS FOUND IN THE AUDIT ('check again, push it further')
1. SYSTEM_PROMPT.format(lessons=...) -> KeyError: '"action"' - the prompt
   contains literal JSON braces {"action":...} that .format() reads as
   placeholders. FIX: .replace({lessons}, ...). The brain thread logged the
   error every 3s and proposed nothing - would have silently paralyzed the bot.
2. Refill timer every 3s = API spam. FIX: demand-driven Event (queue <=2).
3. llm_agent didn't publish current_run.txt - reflex deaths unattributed. FIXED.
4. summarize_state crashed on malformed health/hunger (IndexError) - hardened
   with _pair().

### Tests (all pass)
- validator: 13/13 (night-light, tentacle-adjacent gather, distance cap, cooldown)
- robustness: markdown-wrapped JSON, {"commands":[...]} wrapper, garbage -> [],
  network failure -> [] (queue keeps executing)
- full loop sim: brain propose -> validate -> send -> result -> verdict -> refill
