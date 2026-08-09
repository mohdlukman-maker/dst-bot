# DST AI Bot — Step 1 DONE ✅ (v0.5, persistent-string channel)

An in-game Don't Starve Together mod + terminal CLI. The bot channel works:
live state streams out, commands go in. **Verified working in-game.**

## How it works (the final architecture)

DST's mod sandbox is extremely locked down:
- Bare `os`, `pcall`, `io` are **NIL** — only a curated list exists
  (`pairs, ipairs, print, math, table, type, string, tostring, require, Class,
  GLOBAL, modname, MODROOT`, `Add*` hooks)
- `GLOBAL.io.open` is **completely blocked** — every path format throws
  "invalid filepath"
- `ThePlayer`/`TheWorld` globals are **nil** in the mod env — capture entities
  from `AddPlayerPostInit(inst)` / `AddPrefabPostInit("world", inst)`

The channel that works: **the game's own save API**
```
mod -> CLI : TheSim:SetPersistentString("dst_ai_bot_state", json, false, cb)
             -> file: <save>/client_save/dst_ai_bot_state  (KLEI header)
CLI -> mod : CLI writes <save>/client_save/dst_ai_bot_command (KLEI format)
             mod reads it via TheSim:GetPersistentString("dst_ai_bot_command")
```
KLEI file format: `KLEI     1 <payload>` (regex-strip the header to read).

## Files
- Mod: `...\Don't Starve Together\mods\dst_ai_bot\` (modinfo.lua + modmain.lua)
- CLI: `<workspace>\dst-bot\dstbot.py`
- Save-dir channel: `Documents\Klei\DoNotStarveTogether\<id>\client_save\dst_ai_bot_*`
- Dev force-enable: game `mods\modsettings.lua` has `ForceEnableMod("dst_ai_bot")`

## Use
1. Launch DST (mod auto-loads via ForceEnableMod)
2. Enter a world, wait ~15 s
3. `cd C:\Users\mohdl\AppData\Local\hermes\my-works\dst-bot && python dstbot.py`
4. `status` → live health/hunger/pos · `ping` → Pong! · `move_to <x> <z>` · `say <text>`

## CLI commands
- `status` — live player state (health/hunger/sanity/position/items)
- `ping` — round-trip test (pong = channel works both ways)
- `move_to <x> <z>` — make player walk/run to world coords
- `say <text>` — character speaks
- `exit` — quit

## Hard-won lessons (DST mod sandbox)
1. **No bare `os`, `pcall`, `io`, `tonumber`, `select`** in mods — use `GLOBAL.` prefix.
2. **`GLOBAL.io.open` blocked entirely** ("invalid filepath" for ALL paths).
3. **Use `TheSim:SetPersistentString`/`GetPersistentString`** for file channel.
4. **`ThePlayer`/`TheWorld` are nil in mod env** — capture from `Add*PostInit(inst)`.
5. **Timestamps**: `TheSim:GetRealTime()/1000` (no `os.time`).
6. **Do NOT poll file I/O fast** on the main thread (was hanging world load) — 1s cadence, only when player is stable.
7. Frequent crashes made DST disable mods / corrupt a save — `ForceEnableMod` + fresh worlds help.

---

# ✅ STEP 1 COMPLETE (verified in real game 2026-08-07)

The channel works end-to-end:
- `ping` → `Pong!` (commands in, responses out)
- `status` → live Day/Phase/Season + health/hunger/sanity/position/items
- `move_to <x> <z>` → Wilson walks to the point (verified on screen)
- `say <text>` → speech bubble appears (verified on screen)
- State streams every 1s; survives death/respawn (poll re-attaches to new player)

## Final fixes that made it work (the last three bugs)
1. **Player respawn kills the poll task** — entity-attached DoPeriodicTask dies on
   death; re-attach on EVERY AddPlayerPostInit (cancel old, start new).
2. **World-state scoping bug** — `local myworld` was declared AFTER `write_state`,
   so write_state read a nil global. Declared it before. (Same trap as `armed`.)
3. **Day = world.state.cycles + 1** — the clock has no GetDayNumber; use the
   worldstate component's `.state` table (phase/season/cycles/isday...).

## How to run (complete)
1. Launch DST (mod auto-loads via ForceEnableMod in modsettings.lua)
2. Enter a world (alive!), wait ~10s
3. `cd C:\Users\mohdl\AppData\Local\hermes\my-works\dst-bot && python dstbot.py`
4. `status` / `ping` / `move_to <x> <z>` / `say <text>`

## Next: Step 2 — the AI brain
The channel is the foundation. Step 2 wires an AI (LLM) into it: read state →
decide an action (gather, eat, craft, avoid night, explore) → send move_to/actions.

---

# ✅ STEP 2 DONE — Autonomous AI Survival Brain (verified live!)

Wilson is driven by an AI (DeepSeek via API, hybrid-ready for local later).

## How to run the brain
1. DST running, Wilson ALIVE in a world (mod auto-loads)
2. `cd C:\Users\mohdl\AppData\Local\hermes\my-works\dst-bot`
3. `python brain.py`   (press Ctrl+C anytime = kill switch)

## Brain components
- `brain.py` — the autonomous loop: read state.json → normalize → LLM decide → send action → repeat (~2s/turn)
- LLM: DeepSeek `deepseek-v4-flash` via API (key from hermes `.env` DEEPSEEK_API_KEY)
- The mod now also reports `nearby` entities (name+distance within 25m) so the AI can navigate
- Mod actions: `move_to`, `gather <prefab>`, `eat <item>`, `attack <prefab>`, `say`, `wait`, `ping`, `log`

## AI decision set (strict JSON, validated)
```
{"action":"move_to","x":..,"z":..,"why":".."}
{"action":"gather","prefab":"berrybush|..","why":".."}
{"action":"eat","item":"berries|..","why":".."}
{"action":"attack","prefab":"spider|..","why":".."}
{"action":"say","text":"..","why":".."}
{"action":"wait","seconds":1-10,"why":".."}
```

## Notes
- DeepSeek v4-flash is a REASONING model: max_tokens must be ~1000+ or it spends
  the whole budget on reasoning_content and returns empty content.
- If the brain says "LLM error" repeatedly: check DEEPSEEK_API_KEY in hermes .env.
- Near-empty spawns make the AI wander to explore — correct behavior.
- The AI only does safe actions; it cannot break the game or cheat.
