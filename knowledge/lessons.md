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
