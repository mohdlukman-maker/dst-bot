# DST AI Bot — Question for Claude (v8): Architecture review + the run that died

## Current status (be honest: Wilson died at Day 2)

The bot has THREE layers now:
1. **Lua mod** (73KB, 1593 lines) — Wilson's hands: moves, picks, chops, crafts, state reporting
2. **reflex.py** — emergency safety net: flee hostiles, eat when starving, light prep, fire fuel
3. **local_agent.py** (17KB) — the autonomous brain (user's design): roams, gathers, verifies locally, learns locally, ASKS questions when stuck

## What the user designed (and it works!)

The user's architecture request was:
- "roam around should not be decided by AI after 1 roam, collecting and detecting the item has actually been collected should be local, it need to be teach like that"
- "always ask questions instead of just roaming without a plan"

So the agent:
- LEARNS locally (learnings.json: gather_success/fail per prefab)
- ROAMS autonomously (plan stages: tools -> campfire -> explore_base -> armor -> winter_prep)
- VERIFIES collection locally (inventory delta, no AI)
- ASKS when stuck or threatened (writes agent_question.json, waits for agent_answer.json)

## What happened — Wilson died Day 2

The agent roamed into a **merm village**. The flee reflex has a HARDCODED HOSTILES list (frog/spider/hound/tentacle/snake...) that did NOT include merm. So Wilson stood in a merm spawn at 7 HP while nothing fled. Cascade:
1. Health dropped to 7 (merm attacks)
2. Hunger hit 0, no food nearby
3. Agent entered "health critical - waiting" deadlock (didn't eat the crow it had)
4. Crows in inventory are NOT edible (live birds)
5. By the time I intervened, unsalvageable -> world deleted

## What I already fixed (before this question)

1. **flee_threats now uses threats[]** (the mod's _combat-tagged list) instead of hardcoded HOSTILES — catches merm/pigman/spider/frog. Fallback to HOSTILES list retained.
2. **Agent eats available food** before entering health-critical wait (seeds/berries/carrot/meat list)
3. **Agent asks questions** when: stage complete, stuck 2 passes, threats < 10m, health critical with no food
4. **Learnings saved** with lessons (never roam into merm clusters, keep hunger > 30, crows not food)

## The questions I need answered

### Q1: The merm death — is threats[] the right flee source?
The mod's threats[] uses FindEntities with `_combat` tag. Is `_combat` reliable for ALL hostiles (merm, pigman at dusk, frogs, spiders, hounds, depth worms)? What about WARG/WAVES that don't have the tag? Should flee radius be 10m or bigger? And critically: should the AGENT also check threats BEFORE choosing a roam direction (route-planning avoidance) instead of only reacting?

### Q2: Hunger management — what's the actual starvation math?
At hunger 0, DST drains ~1.25 HP/s? My agent keeps hunger > 40 as threshold. But the real issue was: agent had NO food and was far from known food. Should the agent:
- (a) ALWAYS keep N seeds/berries as emergency rations (never eat below a reserve)?
- (b) Track hunger_seconds_remaining and turn back toward food early?
- (c) Prioritize food sources (carrot/berry) in the roam target scoring when hunger < 60?

### Q3: The deadlock — agent at low health waits forever
Fixed (eats first). But the deeper issue: the agent had a CROW (not edible) and no real food. Should the agent carry a CAMPFIRE + COOK pot? Cooked food restores more. At what point should the agent learn "crow = kill for morsel, cook it" vs "crow not food"?

### Q4: The local_agent architecture — is it sound?
The agent loops: check threats -> eat if low -> craft what's possible -> roam_once (pick target by value+reliability-distance -> move -> fire gather -> verify delta) -> if nothing gained 2x, ask. Questions:
- Is the roam target scoring right? (RESOURCE_VALUE + gather_reliability*2 - distance*0.1)
- Should the agent have a "home base" concept (return to a safe spot at night) instead of roaming into danger?
- The plan stages: tools -> campfire -> explore_base -> armor -> winter_prep. Are these the right priorities/order for solo Wilson? What's MISSING (e.g. food stage)?

### Q5: The mod's eat command
`eat` pushes an EAT BufferedAction on the food item from itemslots. It returned ok but hunger stayed 0 for the crow. Is the issue that live crows have no edible component, or is the EAT action failing silently? Should eat VERIFY (check hunger delta) and report failure?

### Q6: Command pacing — the agent spammed move_to
I saw 3-4 "moving" results back-to-back — the agent (or reflex) was sending commands faster than the game processes them. What's the right pacing (1 command / N ms)? Should the mod queue commands or should the Python side rate-limit?

## The mod (73KB) — key architecture for context
- State: nearby[] (unfiltered scan + component classify: yields/work/n_seen/ok), scan audit (dropped/capped), threats[], fires[] (fuel%), item_counts, equipped, can_build, heartbeat (DoStaticPeriodicTask + IsServerPaused), temperature, combat_ready
- Commands: move_to, gather_job (job-runner: walk->work->sweep->verify gained/lost), craft (DoBuild w/ pt for placers), fuel, deploy, equip, eat, attack, revive (playerghost), say, set_dialogue, debug_inventory, preempt_job, dump_knowledge
- write_state has section() isolation (per-field errors in st._errors) + error logging
- pick mode listens for 'picksomething' on the PLAYER (picker), work mode on target 'workfinished'
- harvestable-preference: gather targets nearest HARVESTABLE (ok=True) not nearest

## The Python stack
- local_agent.py (17KB): plan stages, learnings, question escalation, threat guard, eat-first
- reflex.py (9.5KB): flee via threats[], eat<30, dusk light, fire fuel, freezing->fire
- roam.py: the sweep routine
- worldmap.py: persistent resource atlas
- learnings.json: gather_success/fail + lessons

**The meta-question: I keep dying to a NEW unexpected thing each run (darkness, fire burn-out, ghost-revive, merm village, starvation). What ONE system would prevent the most deaths? Is it: (a) better threat avoidance, (b) a food/fuel reserve discipline, (c) a "danger map" (remember where merm villages are and never go back), (d) something else?**
