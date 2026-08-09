# DST AI Bot — Question for Claude Opus (PART 1: questions)

## Current state (working, verified in-game)
A Lua mod runs inside Don't Starve Together (offline solo host, TheWorld.ismastersim=true, we ARE the server). It writes state.json (health/hunger/sanity/pos/nearby-with-coords/ground_items/item_counts/equipped) via the game's save API and reads command.json. A Python agent (me, an LLM) reads state, decides, writes commands. Verified working: move_to, say, eat, gather (PICK/PICKUP/CHOP via ClearBufferedAction + locomotor:PushAction(ba, true)), craft (Builder:DoBuild), a job-runner (DoPeriodicTask loop that chops a whole tree in one command, sweep-picks drops, reports gained-delta vs snapshot).

## What keeps killing Wilson
1. Starvation (hunger hits 0 fast; reflex daemon now eats at hunger<30)
2. Mobs (frogs dropped him 150->50->dead in seconds; reflex now flees hostiles within 8m)
3. My knowledge of DST mechanics is INCOMPLETE — I don't reliably know: day length (how many real seconds), dusk/night timing, food values, frog aggression radius, seasonal threats (deerclops/hounds day ranges), sanity mechanics, kiting behavior, etc.

## Knowledge sources — which should I build on?
1. **Game scripts** (`<game>/data/databundles/scripts.zip`, ~4000 .lua files) — ground truth for APIs/mechanics. Read directly via Python zipfile. Always available offline. BUT: reverse-engineering facts from Lua is slow and error-prone.
2. **dontstarve.wiki.gg** — MediaWiki API (`/api.php?action=parse&page=X&format=json&prop=wikitext`) returns structured facts (Axe = 1 Twigs + 1 Flint, damage 27.2, 100 uses). Works but rate-limits (403 after 1-2 requests).
3. **dontstarve.fandom.com** — blocks direct requests (403 always).
4. **Klei official site** — works but mostly marketing/overview.

## Questions
1. **Which source should be the PRIMARY knowledge base for a bot like this?** My lean: scripts.zip for API mechanics + wiki.gg (throttled) for game facts. Is there a better source I'm missing (machine-readable data dump, JSON extraction)?
2. **How should the bot STORE and USE this knowledge?** (a) static facts file I hand-build, (b) on-demand wiki lookups, (c) teach the mod to EXPOSE more game state so I don't need wiki knowledge. My instinct: (c) first, (a) second, wiki only for what the game can't expose.
3. **Top ~20 facts a survival AI needs that I should hard-code?** (day length in seconds, dusk/night durations, hunger drain rate, food values, frog/spider/hound aggression, deerclops arrival day, winter timing, sanity drain at night). List with approximate values to verify.
4. **Architecture**: reflex daemon (flee/eat, 200ms) + me (deliberative, ~1-5s) + job runner. Any refinement? What state would make my decisions 10x better (time-until-dusk, nearest-threat distance, biome, available recipes, workleft of target)?

## NEW FEATURE: Wilson personality/dialogue (user's request)
User wants Wilson FUN to watch, especially when idle waiting for commands:
- Contextual lines: hungry ("I could really use some berries..."), idle ("So... what's the plan, boss?"), gathering ("These twigs will make a fine axe!"), scared ("THOSE FROGS! WHY!?"), dusk ("Getting dark...").
- Mod has a working `say` command (talker:Say -> speech bubble).
- Question: cleanest way to add? (a) mod-side idle dialogue timers with a state-based line table, (b) brain-side: send say alongside actions, (c) both. How often should Wilson talk (fun but not spammy)? Any talker:Say gotchas (cooldown, length, client/server)?

## Constraints
Windows, DST via Steam, offline solo host, user watches real-time. No computer vision. Token economy matters (state read every ~2s). Must work offline.
