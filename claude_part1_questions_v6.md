# DST AI Bot — Refinement Question for Claude (v6): Two bugs the USER caught (not me)

## The pattern to notice
Both bugs were caught by the USER watching the screen — not by the mod's sensing or my logic. The bot was blind to things the human could see plainly:
1. "there's plenty of grass around wilson" — while my nearby[] showed none
2. "you used the torch/bare hand to chop the tree even though the axe is in the inventory" — while the bot chopped 18+ swings/tree

## Bug 1: Grass blindness (FIXED, needs your review)
**Root cause (source-verified):** grass.lua has tags `plant, renewable, silviculture, lunarplant_target, witherable, FX, NOCLICK`. My resource FindEntities used `cant_tags={"INLIMBO","FX","NOCLICK"}` — so **every grass tuft was excluded**. Saplings don't have NOCLICK (verified), which is why saplings showed but grass never did.

**My fix (nearby sensing):**
```lua
local _, ents = PCALL(function()
    return TheSimRef:FindEntities(px, py, pz, 30, nil, {"INLIMBO", "playerghost"},
        {"pickable", "CHOP_workable", "MINE_workable", "DIG_workable", "HAMMER_workable", "_inventoryitem"})
end)
-- keep nearest 3 PER PREFAB (list structure), each with x/z/d/ok
-- harvestable check matches each entry to its real entity by prefab+min-distance
```

**Questions:**
1. Is there a *canonical* way to distinguish "plant/tree resource" from "decorative" (flower, butterfly) without tag-guessing? The game's own `GetActionButtonAction`-style component checks? I now include flowers/butterflies in nearby[] (they have pickable/_inventoryitem) — is that noise I should filter, and if so how?
2. `oneof` with 6 tags — does every pickable plant have at least one of those? (grass=pickable, tree=CHOP_workable, rock=MINE_workable, flint=_inventoryitem...) What am I missing? (reeds? cactus? mushroom?)
3. The nearest-3-per-prefab dedup: is 3 the right number, or should I cap by total entity count instead (e.g. max 30 total)?

## Bug 2: Wrong tool equipped (FIXED, needs your review)
**Root cause (source-verified):** axe.lua does `tool:SetAction(ACTIONS.CHOP, 1)` — the axe must be EQUIPPED for its effectiveness. Worker:GetEffectiveness returns `self.actions[action] or 0` — with a torch equipped (no CHOP action) it's 0/slow. The job-runner never checked what was in hand.

**My fix:**
```lua
local function equip_best_tool(workaction)
    -- CHOP->axe, MINE->pickaxe, DIG->shovel, HAMMER->hammer
    -- if equipped item's tool:CanDoAction(workaction) -> keep it
    -- else find the wanted prefab in itemslots and inv:Equip(it)
end
-- called in job_start BEFORE starting any work-mode (non-PICK/PICKUP) job
```

**Questions:**
1. Is `tool:CanDoAction(action)` the right check for "is this tool good for this job"? Or should I compare `tool:GetEffectiveness(action) > 0`? (golden axe? moonglass axe? — tools with DIFFERENT prefabs but same action)
2. My tool_map is prefab-name based ("axe"). Better: iterate inventory for ANY item whose tool component can do the action, and equip the highest-effectiveness one? (e.g. if both axe and goldenaxe exist, equip golden)
3. **The deeper issue:** after chopping, should the bot RE-EQUIP the torch at dusk even if a job is running? The user's scenario was: torch equipped (dusk prep) -> gather_job chop -> chop with torch. How should light-source and work-tool priorities interact? (My reflex equips torch at dusk-90s; the job now steals the hand for the axe. Who wins?)

## The meta-question
Both bugs = the mod's *perception* diverged from the *screen reality* the user could see. What single change would catch this class of bug EARLIER next time?
- (a) periodic vision spot-check comparing nearby[] to what's on screen?
- (b) a "self-audit" mod command that dumps raw FindEntities (no filters) so I can diff against the filtered view?
- (c) something else?

## Current context
- Offline solo host, ismastersim=true, mod on server
- state.json: nearby[] (now: up to 3/prefab, 30m, ok flag), ground_items, item_counts, equipped, threats, fires (fuel%), temperature, combat_ready, can_build, seconds_until_dusk/night, heartbeat
- Commands: move_to, gather_job (job-runner w/ busy-gate, workfinished listener, settle delay, GiveItem fallback, gained-delta verify), craft (DoBuild with pt for placers), fuel (ADDFUEL), deploy (DEPLOY), equip, eat, attack, revive (playerghost), say/set_dialogue
- Reflex daemon (Python): flee hostiles, eat<30 hunger, dusk-90s light, fire-fuel<35%, freezing->fire
- Vision: qwen3-vl via OpenRouter, anomaly-triggered only (~8s/call)
- Wilson: Day 3, died to darkness night 2 (campfire burned out, fuel reflex didn't fire — ALSO under investigation)

## What I want from you
1. Review both fixes for correctness + edge cases
2. Answer the specific questions above
3. The "self-audit" pattern: is it worth a `debug_scan` command that returns UNFILTERED nearby (prefab, dist, tags) so I can catch perception bugs in 1 command instead of a restart cycle?
