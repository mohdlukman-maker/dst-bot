# DST AI Bot — Question for Claude (v9): the autonomous agent keeps dying to BORING bugs, not threats

## The situation (be honest, it's frustrating)

I built the local agent per your v8 design (plan stages, learn locally, ask when stuck)
and wired your 4 invariants (leash, health-delta retreat, campfire kit, food reserve).
Wilson STILL died — but not to merms or darkness. He died to *the agent failing to do
anything useful*:

```
15:34:41 ROAM at (-110,14) | needs: {'twigs': 4, 'flint': 3, 'cutgrass': 2, 'rope': 1, 'log': 1}
15:34:43   🎯 evergreen@(-110,14) fired      <- CHOPPING TREES WITH NO AXE (bare hands, 10x slow)
15:35:15   🎯 sapling@(-71,18) fired
15:35:20   ✅ confirmed local: {'twigs': 1}
15:35:36   🎯 sapling@(-62,-7) fired
15:36:01   🧭 one explore step -> (-27,-7)   <- EXPLORE LOOP STARTS
15:36:11   🧭 one explore step -> (-67,-47)  <- same 4 directions cycling
15:36:21   🧭 one explore step -> (-67,33)
15:36:31   🧭 one explore step -> (-27,-7)   <- BACK TO THE SAME SPOT
15:38:51 🌙 night + no light - stop roaming  <- died in the dark with 1 twig
```

## The THREE boring bugs that killed him

### BUG A: No axe → chopping trees bare-handed (FIXED by me, needs review)
The campfire-kit invariant (your v8 #4) demanded logs BEFORE Wilson had an axe. So the
agent fired gather_job at evergreens with bare hands (77 swings/tree vs ~9 with axe).
My fix: kit invariant + pick_target are now tool-aware — no trees targeted until
"axe" is in inventory. Is that the right gate, or should the agent craft the axe
FIRST as a hard pre-step of every loop iteration?

### BUG B: The explore loop cycles 4 spots forever
When pick_target finds nothing nearby, my agent does "one explore step" = walk 40 units
in a random cardinal direction, then re-scan. With nothing found, it does this up to 6x
per roam — and the 4 directions cycle, so it walks back and forth across the SAME area
forever, never branching. The world map (lib/world_map.py) now exists but the explore
doesn't use it well.

How should exploration actually work? My candidates:
(a) SPIRAL: increasing radius, 8 compass points, visit each once — guarantees new ground
(b) WORLD-MAP-DRIVEN: walk to the nearest KNOWN-but-unvisited resource spot
(c) FRONTIER: pick the direction that's been least explored (track a visited-grid)

### BUG C: The same sapling targeted repeatedly ("sapling@(-67,-7) fired" x3)
pick_target reads nearby[] and picks the nearest harvestable. But the SAME sapling at
distance 0 kept getting targeted even after being picked. The mod's nearby[] should show
ok=False for a picked sapling... unless the ok flag is stale (the state read happens
before the pick completes). The agent also re-fires gather_job on it. Should the agent:
(a) mark targets visited and never retry them in the same roam, or
(b) trust ok=False more aggressively?

## What I need from you

1. **Review my tool-aware fix** (BUG A): is "no trees without axe" the right rule?
2. **Design the explore** (BUG B): which of (a)/(b)/(c) — or a combination — and give me
   the actual algorithm (it's Python, ~30 lines)
3. **The retry problem** (BUG C): visited-set vs ok-flag trust
4. **The meta-question AGAIN**: I keep dying to *agent-mechanics* bugs (not exploring
   right, targeting wrong, not crafting). The threats are handled (leash + health-delta
   retreat + discriminator). What's the ONE thing that prevents agent-mechanics deaths —
   is it a "day 1 checklist" the agent must complete in order (axe -> torch -> campfire
   -> explore) with hard gates between steps, like a state machine?

## Context for your answer

- Mod (73KB, Claude-v7-audited) has: gather_job (walk->work->sweep->verify gained/lost),
  craft (DoBuild w/ pt), equip, fuel, deploy, eat, move_to, heartbeat, scan audit,
  harvestable-preference, picksomething-on-player fix, section() isolation in write_state
- Python: local_agent.py (plan stages tools->campfire->explore->armor->winter, learnings.json,
  question escalation, threat guard w/ discriminator, leash, health-delta retreat, campfire
  kit, eat-first), reflex.py (flee via threats[] discriminator, eat<30, dusk light, fire fuel),
  lib/ (state_reader, run_log, decision_log, world_map, lessons, invariants - all unit-tested 58/58)
- The measurement layer works: run_log recorded the agent crashes (cause=crash). But runs.jsonl
  shows "crash" then "unknown" double-entries - my end_run is being called twice (try/except/finally
  all call it). Fix that too?

## The REAL ask

Give me the **Day-1 state machine**: exact ordered checklist (craft axe -> torch -> campfire
kit -> explore spokes), with hard gates, that the agent must complete before ANY roaming.
I've spent three runs dying to "the agent did the steps in the wrong order."
