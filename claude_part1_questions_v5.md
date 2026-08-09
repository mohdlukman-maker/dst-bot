# DST AI Bot — Question for Claude (v5): Research the BEST survival strategy, then propose mod upgrades

## Your task (two parts)
**PART A:** Search the web for the best Don't Starve Together SOLO survival strategies (day-by-day plan for the first 10-20 days, priority order, base location tips, what kills beginners, autumn/winter prep). Sources: the DST wiki (dontstarve.wiki.gg), community guides, Reddit r/dontstarve, Steam guides. Synthesize into a concise day-by-day survival playbook a bot should follow.

**PART B:** Based on that playbook + the current mod capabilities below, propose SPECIFIC mod modifications (Lua commands, state fields, reflex rules) that would let the bot execute the playbook.

## Current bot architecture (all working, verified in-game)
- **Lua mod** on the server (offline solo host, ismastersim=true) — the bot's HANDS. Writes state.json via persistent strings, reads command.json.
- **Agent (me, an LLM)** — the BRAIN. Reads state, decides, writes commands.
- **Reflex daemon (Python)** — 200ms fail-safes: flee hostiles <8m, eat when hungry<30, dusk-minus-90s light prep, preempts jobs.

### Mod commands (all work)
move_to, say, eat, equip, craft (auto-routes placer recipes to DoBuild with pt), gather_job (job-runner: busy-gated swings, workfinished listener, settle delay, GiveItem fallback, gained-delta verification), gather (delegates to job), attack, revive (fixed: checks playerghost tag), set_dialogue, dump_knowledge, preempt_job, ping, get_state, log

### State.json fields
health, hunger, sanity, day, phase, season, pos (x/z), nearby[] (prefab, coords, distance, ok=harvestable), ground_items[] (loose items), item_counts (stack-accurate), equipped, threats[] (prefab, dist, is_targeting_me, hp), can_build[] (via builder:CanBuild), is_busy, seconds_until_dusk, seconds_until_night (via clock:GetTimeUntilPhase), sim_ts, results (last 5 job reports)

### Job report format (ground truth)
{"status":"ok","phase":"collected","reason":"worked_down","gained":{"log":2},"lost":[],"swings":19,"elapsed":8}

### Heartbeat (pause detection)
dst_ai_bot_heartbeat: {heartbeat_ts, sim_ts, paused} — static scheduler ticks even when paused. Running=paused=crashed triage.

## Known lessons (from deaths)
1. Campfire burns out — needs fuel management (log re-add)
2. Torch must be crafted BEFORE dusk, not during the night emergency
3. Grass/sapling take ~1-2 min to regrow — gather during day
4. Ghost state: health reads 50/150, IsDead()=false — revive needs playerghost TAG check (fixed)
5. Frog swarms kill fast — flee reflex needed (added)

## What the playbook needs to cover (be specific)
1. **Day 1 priority order** — what to build first? (axe → campfire → torch? science machine day 1 or 2?)
2. **Base location** — what to look for? (pig king? beefalo? swamp? rock biome proximity?)
3. **Food strategy** — berries/carrots early, then farms? crock pot? what's the minimum viable food chain?
4. **Autumn (first 20 days)** — what must be DONE before winter?
5. **Winter prep checklist** — thermal stone, winter clothes (which?), food stockpile size?
6. **Common beginner killers** — darkness, starvation, hounds, deerclops, freezing — when do they come, how to prep?
7. **What can a bot do that a human can't?** — e.g. exact-coordinate gathering at night, inventory-count precision. Lean into these.

## Then propose mod modifications (PART B)
For each playbook item, what mod command/state/reflex would execute it? Examples of what I'm thinking:
- `build` command that places structures at chosen coordinates (already have craft-with-pt)
- `plant` command (plant dug_grass/saplings for farms) — uses ACTIONS.PLANT?
- `fuel` command (add logs to campfire) — uses ACTIONS.ADDFUEL?
- `attack` improvements (kite logic? auto-attack nearest threat?)
- `equip` with thermal stone management (swap when frozen/overheating)
- Season-aware reflexes (winter = gather wood NOW, prep thermal)
- A "farm" workflow: dig → plant → wait → harvest

## Constraints
- Windows, DST via Steam, offline solo host, user watches in real-time
- No computer vision in the mod (vision is external, anomaly-triggered only)
- Token economy matters — the agent reads state every ~2s
- Everything must be executable via the Lua mod (no UI clicks)
- The user wants to SURVIVE THE MAX DAY POSSIBLE — this is a long-horizon goal, not just Day 1

## Deliverable format
1. The day-by-day playbook (concise, bot-actionable)
2. Ranked list of mod modifications (P0/P1/P2) with exact Lua snippets where useful
3. The top 3 things that would most increase survival days
