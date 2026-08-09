# 🎮 Migrating the DST Bot to Claude Code

## What's ready (all built and tested)

| File | Purpose |
|------|---------|
| `claude_drive.py` | The channel wrapper: `--tick` reads state, `--send` issues commands |
| `CLAUDE.md` | Claude Code's brain: protocol, playbook, personality, pitfalls |
| `reflex.py` | Safety net daemon (flee/eat/light/fuel) — runs independently |
| `vision.py` | Optional: screenshot analysis via OpenRouter |
| `worldmap.py` | Persistent resource memory |

## How to run Claude Code as the brain

1. **Start the game** → Host → new/current world → in game
2. **Start the reflex daemon** (safety net):
   ```
   python reflex.py
   ```
3. **Launch Claude Code in the project folder**:
   ```
   cd C:\Users\mohdl\AppData\Local\hermes\my-works\dst-bot
   claude
   ```
4. **Tell Claude Code to play** — e.g.:
   > "You are driving Wilson in Don't Starve Together. Read CLAUDE.md and the
   > claude_drive.py interface, then start the survival playbook. Tick the
   > state, decide, send ONE command at a time, verify, and keep going.
   > Survive as many days as possible. Talk as Wilson when appropriate."

Claude Code will auto-load CLAUDE.md and drive the game via:
```
python claude_drive.py --tick
python claude_drive.py --send '{"action":"gather_job","prefab":"grass","count":3}'
python claude_drive.py --result
```

## Verification checklist (do these once in Claude Code)

- [ ] `--tick` shows live state (health/day/pos) with `_state_age_s < 10`
- [ ] Send `{"action":"ping"}` → result comes back
- [ ] Craft axe → verify `equipped: ['axe']` on next tick
- [ ] When dusk approaches: torch crafted + equipped BEFORE night
- [ ] On ghost (health 50 + skeleton): solve the `respawnfromghost` issue
      (the current mod's revive reports "requested" but the rez doesn't complete
      — investigate `OnRespawnFromGhost`/`DoActualRez` in player_common_extensions.lua
      and fix the mod's revive handler)

## Known issues for Claude Code to solve

1. **Ghost revive incomplete** — `respawnfromghost` pushed but Wilson stays a ghost.
   Look at DoActualRez (needs the right state/timing) or use the game's
   `c_resurrect()` console path.
2. **item_counts lag** — counts update ~2s after actions; always re-tick before
   trusting a count.
3. **gather_job targets nearest, not harvestable** — a picked tuft shadows ready
   ones. A fix is staged in modmain.lua (harvestable-preference) but needs a
   game restart to load.

## Rules that keep Wilson alive

- One command → verify → next. Never queue bursts.
- Torch EQUIPPED (not just crafted) before dusk; carry spares (burn ~60s).
- Fire = presence check: dark + no fires[] + no light = EMERGENCY, fix NOW.
- Don't fight unless `combat_ready` (weapon+armor+health>60%). Flee frogs.
- Game paused (`_state_age_s > 10`) → do nothing, wait for unpause.
