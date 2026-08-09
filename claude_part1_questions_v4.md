# DST AI Bot — Question for Claude (v4, REVISED): Vision integration + pause handling

## New capability: VISION (just added, working)
The agent (me) can now SEE the screen. Pipeline (vision.py, session-proven):
1. **capture()** — PowerShell screenshot of primary screen → PNG (~1s)
2. **analyze()** — OpenRouter vision LLM `qwen/qwen3-vl-8b-instruct` → text description (~4-8s, accurate, reads on-screen text)
3. **see(question)** — capture + analyze in one call

Provider facts (tested this session):
- DeepSeek (deepseek-v4-flash): **text-only, rejects images** (HTTP 400)
- NVIDIA NIM API: unreachable from this network (90s timeout)
- OpenRouter: **works** (new key supplied by user; old key was dead/401)
- Local Ollama: installed; gemma4:12b + hermes-qwen3.6 CLAIM vision but returned **empty content** on real screenshots (38-93s, no output); qwen2.5:7b has no vision. Local vision unreliable here — OpenRouter is the vision path.

## IMPORTANT correction from the user
The "stale state / autopause" I attributed to window-focus loss was actually **the user manually pausing the game** (pause menu) while I diagnosed issues. The game was paused ON PURPOSE. This is expected behavior: paused → state.json stops updating → commands queue but nothing moves.

So the REAL problems are:
1. **Paused-game handling**: when the user pauses (or the game autopauses on focus loss — still a real DST behavior), the bot should DETECT it and WAIT gracefully, not keep firing commands into a frozen world.
2. **Vision is still valuable** — it can distinguish "paused menu" from "game running" from "window not focused" at a glance.

## Current architecture (all working)
- Lua mod on server (ismastersim=true): state.json (health/hunger/pos/nearby+coords/ground_items/item_counts/equipped/threats/can_build/is_busy/seconds_until_phase_change), command.json, job-runner (busy-gated swings, workfinished listener, settle delay, GiveItem fallback, gained-delta verification) — **the full gather arc works: tree fell, logs collected, reported gained:{log:2}**
- gather collapsed into gather_job (returns ground truth, not dispatch receipts)
- Reflex daemon (flee hostiles <8m, eat when hungry<30, preempts jobs)
- build_at command added (spawns structure prefab directly, bypasses placer UI — NOT yet tested, needs restart)
- Vision: see(question) → text description of screen

## Questions for you

1. **Paused-game detection & handling.** When the game is paused (user pause menu OR focus-loss autopause), state.json stops updating. How should the bot react?
   - Detect staleness in the mod itself? (e.g. is there a server-side "paused" flag the mod can read and report in state.json — `TheWorld.state.ispaused`? or a `SimIsPaused()`-style API?)
   - If the mod can report `paused: true`, the bot loop can: stop issuing commands, report "waiting for unpause", and resume automatically when state streams again. Clean.
   - If not, Python-side staleness detection (state age > N seconds → enter wait mode). What's the right threshold given the 2s poll?
   - Does DST have a console/launch option to disable pause entirely (so the world runs even when menu is open / window unfocused)? I recall dedicated servers never pause — is `dontstarve_dedicated_server_nullrenderer.exe` on the same machine (client → 127.0.0.1) the structural fix, and worth the setup complexity for a single-player bot session?

2. **How should vision slot into the control loop?** My plan:
   - Fast loop (every 2s): state.json — movement, gathering, stats
   - Vision (every ~30-60s OR on anomaly): see() for "what do I see?" — catches paused menu, night darkness, on-screen warnings, visual threats, UI dialogs
   - On state-stale: vision once to diagnose (paused menu? unfocused? crash?)
   Is that the right cadence? What are the highest-value vision checks for a survival bot?

3. **The build_at command** (spawn campfire directly at player position, bypassing the placer ghost that DoBuild creates for placer recipes like campfire/firepit): I wrote `GLOBAL.SpawnPrefab(prefab); p2.Transform:SetPosition(px,py,pz); deployable:SetBuilder(myplayer)`. Is spawning the structure prefab directly safe/correct server-side, or should I replicate what the placer does on confirm (e.g. `placer:LinkEntity()`, deploy action)? Also: campfire materials were CONSUMED by DoBuild but no campfire appeared (the placer ghost de-spawned) — should craft detect placer recipes and route to build_at automatically?

4. **Night survival with health 57** — Wilson ended an autopaused night with 57 health, axe + carrot only. When resumed: reflex eats carrot only if hunger<30 (it was 82 — won't trigger). What's the minimal Day-1-night rescue sequence with axe + 1 carrot and no fire materials? (e.g. eat carrot anyway for a bit of health? find/place campfire? torch?)

5. **Vision model tiering**: qwen3-vl-8b-instruct is fast+good. For HIGH-stakes calls (e.g. "is that a hound about to attack?") should I switch to a bigger model (qwen3-vl-32b or 235b) only when needed, keeping 8b for routine? Or is 8b enough everywhere?

## Constraints
- Windows, DST via Steam, offline solo host, user watches in real-time
- No computer vision in the mod — vision is external (screenshot + LLM)
- Token economy + latency matter (vision ~8s/call, ~400 tokens/call)
- Must work offline-ish: OpenRouter is cloud; if it dies, fall back to JSON-only mode
