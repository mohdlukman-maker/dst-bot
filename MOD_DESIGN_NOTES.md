# MOD_DESIGN_NOTES — DST Survival Bot (Wilson)

_Design document generated from code inspection. Describes only what exists today — no fixes, no suggestions. File/line references are exact._

---

## SECTION 1 — Decision order

**Where:** `local_agent.py` `_main_loop()` (line 413), a `while True` loop. The Lua mod only executes commands; the Python agent decides.

**Loop cadence:** each iteration reads `dst_ai_bot_state` (`get_state()`, line 69), then evaluates rules top-to-bottom. The loop **re-checks from the top every iteration** — there is no state machine that continues where it left off (the only persistent state is `agent_state.json`'s `stage`, read by `current_stage_id()` line 184).

**Interruption:** YES — later rules can interrupt earlier ones. The loop body is a sequence of `if ... continue` blocks; whichever condition fires first wins that iteration, and `continue` restarts from the top. A rule that fired last iteration has no priority this iteration.


**The rules, in order (each is a `continue` block):**

| # | Rule | Condition (line) | Action |
|---|------|------------------|--------|
| 1 | No position | `not st.get("pos")` (417) | sleep 3s |
| 2 | State stale | state file mtime > 10s (420) | sleep 5s (paused/crashed) |
| 3 | DEATH | `st.is_ghost` (426) | log death, `run_logger.end_run(...,"death")`, sleep 3s, wait for reflex revive |
| 4 | Respawn | `was_ghost` was True, now alive (437) | reset `agent_state.json` to `{stage:"tools"}` |
| 5 | LOW HP/HUNGER | `health < 50` OR `hunger < 60` (449) | move to food target (seeds preferred) → `gather_job` → `eat`; skip attempted spots via `_food_attempted` |
| 6 | CRITICAL | `health < 25` OR `hunger < 40` (477) | `eat` first FOOD_ITEMS match; if nothing and hp<15 → `ask_question()` (blocks up to 300s) |
| 7 | THREAT | close_threats (targeting OR aggressive prefab, d<10) (504) | `preempt_job` + `move_to` 20 units away (flee); asks only AFTER disengage |
| 8 | UNEXPLAINED DAMAGE | `invariants.unexplained_damage(st, last_health)` (538) | `preempt_job` + flee 30 units from nearest aggressive threat |
| 9 | LEASH | `not invariants.can_get_home(st, base)` (568) | `preempt_job` + `move_to` base |
| 10 | CAMPFIRE KIT | has_axe AND `not invariants.has_campfire_kit(st)` (581) | gather missing cutgrass/log via targeted roam |
| 11 | CRAFT | `try_craft(st)` (600) | craft+equip anything in CRAFT_PRIORITIES with materials present |
| 12 | PLAN ROAM | `current_plan(st)` → `roam_once(st, want_prefabs)` (602-605) | one roam pass; if nothing gained 2 passes → `ask_question(stage["asks_when_stuck"])` |
| 13 | CHECKPOINT | always (620) | write `agent_state.json` |

**The main loop (verbatim, lines 413-623):**

```python

def _main_loop():
    global RUN_ID, last_health
    while True:
        st = get_state()
        if not st.get("pos"):
            time.sleep(3); continue
        # paused?
        age = time.time() - os.path.getmtime(STATE_F) if os.path.exists(STATE_F) else 999
        if age > 10:
            time.sleep(5); continue
        # DEATH: Wilson is a ghost (health 50, playerghost tag). The reflex
        # auto-revives. While a ghost, DO NOT gather (ghosts can't hold items).
        # Wait for the revive, then reset the plan state for the respawn.
        if st.get("is_ghost"):
            if not getattr(_main_loop, "was_ghost", False):
                _main_loop.was_ghost = True
                log(f"💀 WILSON DIED (ghost) - waiting for reflex revive, day {st.get('day')}")
                # record the run honestly
                try:
                    run_logger.end_run(RUN_ID, st, "death")
                except Exception:
                    pass
            time.sleep(3)
            continue
        if getattr(_main_loop, "was_ghost", False):
            _main_loop.was_ghost = False
            log("🔄 RESPAWNED - resetting plan state for the new life")
            try:
                ag = {"ts": time.time(), "stage": "tools", "items": {}}
                save_json(AGENT_F, ag)
            except Exception:
                pass
        # LOW HEALTH OR HUNGER -> food gathering outranks the plan. Wilson
        # nearly starved (hunger 5) because the override only checked health.
        h = st.get("health") or [150]
        hung = st.get("hunger") or [150]
        if h[0] < 50 or hung[0] < 60:
            # track attempted food spots so a dug-out carrot isn't re-targeted
            # forever (that loop had Wilson digging the same spent tuft every 8s)
            food_targets = [e for e in (st.get("nearby") or [])
                            if e.get("ok") and e.get("n") in
                            ("carrot_planted", "berrybush", "berrybush2", "seeds")
                            and e.get("d", 99) < 35
                            and (e.get("guid"), e.get("n")) not in _food_attempted]
            # prefer seeds (reliable PICKUP) over carrots/bushes (DIG/ripe-gated)
            food_targets.sort(key=lambda e: 0 if e.get("n") == "seeds" else 1)
            if food_targets:
                f = min(food_targets, key=lambda e: e.get("d", 99))
                _food_attempted.add((f.get("guid"), f.get("n")))
                send({"action": "move_to", "x": f.get("x"), "z": f.get("z")})
                time.sleep(min(8, 2 + (f.get("d", 5))/4))
                send({"action": "gather_job", "prefab": f.get("n"), "count": 3})
                log(f"🍎 HUNGER {hung[0]:.0f}: gathering food {f.get('n')}@({f.get('x')},{f.get('z')})")
                time.sleep(8)
                st = get_state()
                counts2 = st.get("item_counts") or {}
                for food in ("berries", "carrot", "seeds"):
                    if counts2.get(food, 0) > 0:
                        send({"action": "eat", "item": food})
                        log(f"🍽 EATING {food} (hunger {counts2.get(food)} left)")
                        time.sleep(4)
                        break
                continue
        # critical health -> EAT available food first, THEN wait
        if h[0] < 25 or (st.get("hunger") or [150])[0] < 40:
            counts = st.get("item_counts") or {}
            FOOD_ITEMS = ["berries", "carrot", "seeds", "cookedmeat", "cooked_smallmeat",
                          "smallmeat", "meat", "drumstick", "cooked_drumstick",
                          "robin", "crow", "butterfly", "mushroom", "red_mushroom",
                          "green_mushroom", "blue_mushroom", "petals"]
            ate = False
            for food in FOOD_ITEMS:
                if counts.get(food, 0) > 0:
                    send({"action": "eat", "item": food})
                    log(f"🍽 EATING {food} (low health/hunger)")
                    time.sleep(4)
                    ate = True
                    break
            if not ate:
                if h[0] < 15:
                    log("⚠️ health critical, no food available")
                    ask_question(
                        "I'm critically low on health/hunger with no food nearby. "
                        "Where is the nearest safe food source?",
                        {"health": h[0], "hunger": st.get("hunger"), "items": counts})
                    time.sleep(5)
            continue
        # THREAT GUARD (v8 Q1 discriminator): NOT every _combat entity is a threat.
        # crows/robins/rabbits/butterflies/beefalo carry _combat but are passive.
        # Real threats: (a) targeting us, (b) hostile/monster tagged, (c) known
        # aggressive prefabs. Passive _combat = neutral, ignore.
        close_threats = [t for t in (st.get("threats") or [])
                         if t.get("hp", 0) > 0 and t.get("d", 99) < 10
                         and (t.get("targeting")
                              or t.get("n") in ("merm", "spider", "spider_warrior",
                                                "frog", "hound", "houndfire", "tentacle",
                                                "snake", "mosquito", "bee", "killerbee",
                                                "pigman", "werepig", "walrus", "depthworm",
                                                "spiderqueen", "deerclops", "treeguard"))]
        if close_threats:
            # FLEE FIRST, ask later (the spider-kill: waiting for an answer while
            # under attack is how Wilson died). The reflex also flees, but the
            # agent must not sit still asking questions mid-combat.
            names = {t.get("n") for t in close_threats}
            nearest_t = min(close_threats, key=lambda t: t.get("d", 99))
            px2, pz2 = st.get("pos", {}).get("x", 0), st.get("pos", {}).get("z", 0)
            tx2 = nearest_t.get("x")
            tz2 = nearest_t.get("z")
            if tx2 is None or tz2 is None:
                tx2, tz2 = px2 + 1, pz2 + 1
            dx2, dz2 = px2 - tx2, pz2 - tz2
            mag2 = math.sqrt(dx2*dx2 + dz2*dz2) or 1
            nx2, nz2 = px2 + dx2/mag2*20, pz2 + dz2/mag2*20
            send({"action": "preempt_job"})
            send({"action": "move_to", "x": nx2, "z": nz2})
            log(f"🏃 FLEEING from {names} -> ({nx2:.0f},{nz2:.0f})")
            time.sleep(6)
            # only AFTER disengaging, ask what to do about the area
            st_after = get_state()
            if not [t for t in (st_after.get("threats") or []) if t.get("d", 99) < 12]:
                log("   disengaged - noting danger spot, continuing")
            else:
                log("   STILL threatened - keeping distance")
            continue
        # INVARIANT 2: unexplained damage -> retreat (v8: health delta is ground truth)
        if invariants.unexplained_damage(st, last_health, deliberately_fighting=False):
            log("⚠️ INVARIANT: unexplained damage - retreating")
            send({"action": "preempt_job"})
            # move away from nearest AGGRESSIVE threat only (a robin peck or
            # darkness damage must not send Wilson fleeing into water - that's
            # how he got stuck at the shore)
            _AGGR = ("merm", "spider", "spider_warrior", "frog", "hound", "houndfire",
                     "tentacle", "snake", "mosquito", "bee", "killerbee",
                     "pigman", "werepig", "walrus", "depthworm",
                     "spiderqueen", "deerclops", "treeguard")
            threats = [t for t in (st.get("threats") or [])
                       if t.get("hp", 0) > 0
                       and (t.get("targeting") or t.get("n") in _AGGR)]
            if threats:
                nearest = min(threats, key=lambda t: t.get("d", 99))
                px, pz = st.get("pos", {}).get("x", 0), st.get("pos", {}).get("z", 0)
                tx, tz = px + 30, pz + 30  # default: run NE
                for e in (st.get("nearby") or []):
                    if e.get("n") == nearest.get("n"):
                        dx, dz = px - e.get("x", px), pz - e.get("z", pz)
                        mag = math.sqrt(dx*dx + dz*dz) or 1
                        tx, tz = px + dx/mag*30, pz + dz/mag*30
                        break
                send({"action": "move_to", "x": tx, "z": tz})
                log(f"  fleeing from {nearest.get('n')} -> ({tx:.0f},{tz:.0f})")
                time.sleep(6)
            continue
        # INVARIANT 1: the leash - never travel further than you can get back
        base = worldmap.get_base("default") if 'worldmap' in sys.modules else {}
        if base:
            if not invariants.can_get_home(st, (base.get("x", 0), base.get("z", 0))):
                bx, bz = base.get("x"), base.get("z")
                log(f"🔗 LEASH: too far from base ({bx},{bz}) for food/light - heading home")
                send({"action": "preempt_job"})
                send({"action": "move_to", "x": bx, "z": bz})
                time.sleep(8)
                continue
        # INVARIANT 4: campfire kit - never travel without 3 cutgrass + 2 logs.
        # TOOL-AWARE: logs need an axe (bare-hand chopping is 10x slower and
        # wastes the day). Only enforce the full kit once Wilson HAS an axe;
        # before that, gather the cutgrass half (grass needs no tool).
        counts = st.get("item_counts") or {}
        has_axe = "axe" in (st.get("items") or []) or "axe" in (st.get("equipped") or [])
        if has_axe and not invariants.has_campfire_kit(st):
            kit_msg = invariants.kit_priority_plan(st)
            log(f"🔥 KIT: {kit_msg} (priority job)")
            send({"action": "preempt_job"})
            # gather the missing items via a targeted roam
            for _ in range(2):
                st = get_state()
                for e in (st.get("nearby") or []):
                    if e.get("n") in ("grass", "evergreen", "deciduoustree") and e.get("ok"):
                        send({"action": "move_to", "x": e.get("x"), "z": e.get("z")})
                        time.sleep(5)
                        send({"action": "gather_job", "prefab": e.get("n"), "count": 3})
                        time.sleep(5)
                        break
        elif not has_axe:
            # no axe yet: gather only tool-less resources (grass/sapling/flint),
            # never trees. The tools stage handles axe crafting first.
            pass
        # craft what we can locally
        try_craft(st)
        # STAGE-DRIVEN roam: one pass, then evaluate
        needs, want_prefabs = current_plan(st)
        stage = get_current_stage()
        before = dict(st.get("item_counts") or {})
        end_counts = roam_once(st, want_prefabs=want_prefabs)
        # did this pass collect anything?
        gained = {k: v - before.get(k, 0) for k, v in end_counts.items() if v > before.get(k, 0)}
        if gained:
            stuck_streak = 0
        else:
            stuck_streak += 1
            if stuck_streak >= 2:
                # STUCK: ask instead of wandering (user correction)
                ask_question(stage["asks_when_stuck"], {
                    "stage": stage["id"], "items": end_counts,
                    "phase": st.get("phase"), "day": st.get("day")})
                stuck_streak = 0
        last_health = (st.get("health") or [150])[0]
        # checkpoint
        ag = load_json(AGENT_F, {})
        ag.update({"ts": time.time(), "items": end_counts, "mode": "stage", "stage": stage["id"]})
        save_json(AGENT_F, ag)
        time.sleep(2)

```

---

## SECTION 2 — Reflexes

**Where:** `reflex.py` — a separate daemon process (`while True`, line 266), runs every **200ms** (`time.sleep(0.2)`, line 323). No LLM, deterministic. Single-instance guarded by `dst_ai_bot_DAEMON.lock` (lines 21-34).

**Reflex rules, in priority order (checked each 200ms tick):**

| Priority | Rule | Exact threshold (line) | Action fired |
|----------|------|------------------------|--------------|
| -1 | DEATH | `st.is_ghost` true; fallback: health in (45,55) AND `skeleton_player` in nearby (220-228) | `revive` (once per 20s cooldown, line 232) + `run_logger.end_run(..., guess_death_cause(st))` |
| 0 | FLEE | threats: targeting OR aggressive prefab, `d < 10` (189) | `preempt_job` + `move_to` 15 units away from threat |
| 0.4 | FIRE FUEL | fire within 15m AND `fuel_pct < 35` AND (log or twigs in inventory) (117) | `fuel` (log preferred) |
| 0.4b | FIRE EMERGENCY | (dusk or night) AND no fires AND no light equipped (122) | if torch in inventory → `equip torch`; elif twigs>=2 and cutgrass>=2 → `craft torch` + `equip`; else `preempt_job` (30s cooldown, line 141) |
| 0.45 | FREEZING | `is_freezing` AND fire within 20m (150) | `move_to` fire |
| 0.5 | LIGHT PREP | `seconds_until_night <= 90` AND no light equipped AND not armed (92) | if torch in items → `equip torch`; else `preempt_job` + DUSK-WARNING |
| 1 | STARVING EAT | `hunger < 30` AND (inventory or ground food) AND 5s cooldown (305) | `preempt_job` + `eat` best FOOD; if no food but seeds on ground → `gather_job seeds` + `eat` |
| 2 | CRITICAL HP EAT | `health < 25` AND food AND 8s cooldown (318) | `preempt_job` + `eat` best FOOD |

**Latency trace (state change → action executes):**

| Hop | Mechanism | Interval |
|-----|-----------|----------|
| 1. Mod writes state | `poll_task = inst:DoPeriodicTask(1.0, poll)` → `write_state()` | 1.0s (modmain.lua:1600) |
| 2. Python reads state | `reflex.py` `get_state()` (line 36) | 0.2s loop |
| 3. Python writes command | `send()` writes `dst_ai_bot_command` (KLEI header) | immediate |
| 4. Mod reads command | `poll()` → `GetPersistentString(CMD_NAME)` (modmain.lua:1524) | 1.0s poll |
| 5. Mod executes | `execute_command(cmd)` | immediate |

**Worst-case total: ~1.0 + 0.2 + 1.0 = ~2.2s** (plus any KLEI file round-trip delay; observed 1-6s in practice). The reflex's own 200ms loop is NOT the bottleneck — the mod's 1s poll is.

---

## SECTION 3 — Tool checking

**Where:** Lua (the mod), not Python.

**Equip before work:** `equip_best_tool(workaction)` (modmain.lua:955) is called from `job_start()` (modmain.lua:1021) for any action that is not PICK/PICKUP. It scans equipslots + itemslots via `best_tool_for(action)` (line 932) using `components.tool:GetEffectiveness(action)`, skipping tools at ≤5% durability (line 944).

**When the tool is missing:** `best_tool_for` returns `nil` → `equip_best_tool` returns `(nil, "no_tool")` → `job_start` IGNORES this (only checks `eq_err == "refuse_unequip_light"`, line 1022). The job proceeds with whatever is in hand (bare hands). Tool CRAFTING is the Python side's job (`try_craft` in local_agent.py:276; `CRAFT_PRIORITIES` list).

**Refusal case:** `refuse_unequip_light` — at dusk/night with no fire within 15 units, `equip_best_tool` refuses to swap the held light source (lines 967-998). `job_start` then returns `{ok=false, error="refused: refuse_unequip_light (dusk/night, no fire)"}` (line 1024) — propagated to the agent (Claude v7 fix).

**Re-check while running:** NO. The tool is checked once at `job_start`. The job loop (`job_tick`, line 823) never re-evaluates the equipped tool — if the axe breaks mid-job, the job keeps swinging with bare hands until `swing_cap` or `stalled`.

---

## SECTION 4 — Target selection

**Lua side (`gather_job` command, modmain.lua:1115):** finds the nearest entity with `cmd.prefab`, preferring a HARVESTABLE one (`pickable:CanBePicked()` or `workable.workleft > 0` or `inventoryitem`), radius 40 (line 1127). Falls back to nearest any. Returns `{ok:false, "no matching entity nearby"}` if none. Identified by **entity object** (the job holds `job.target`).

**Python side (`pick_target`, local_agent.py:248):** scores `nearby[]` entries: `RESOURCE_VALUE[n] + gather_reliability(n)*2 - d*0.1`. Skips: `ok == False`, non-resource prefabs, trees when axe-less, targets not in `want_prefabs`, distance > 40, and entries in the per-roam `visited` set `(n, x, z)`.

**Avoid re-selecting just-harvested:**
- Python: per-roam `visited = set()` of `(n, x, z)` (roam_once line 307) — cleared each `roam_once` call (6 iterations).
- Python: `_food_attempted` set of `(guid, n)` (line 32, 461) — **never cleared** (persists for the life of the process).
- Python: `TargetTracker` (lib/targets.py) — GUID-based `done` set with 180s auto-clear. **DEAD CODE — never instantiated by any caller** (verified: no `TargetTracker(` calls in local_agent.py, reflex.py, roam.py, brain.py, driver.py).

**Ignore list / blacklist:** There is no persistent blacklist. The threat-guard uses a hardcoded `AGGRESSIVE` prefab tuple (lines 507-511, 544-547) — a filter, not a blacklist. `_food_attempted` is the only permanent re-target guard.

**Identity:** nearby[] entries carry `guid` (modmain.lua:594, `e.GUID`) and `x`/`z` coords. Python uses **coordinates** for `visited` and **guid for food** (`_food_attempted`). `TargetTracker` (the GUID-based design) is unused.

---

## SECTION 5 — Exploration

**What happens when nothing useful is nearby:** `roam_once` (local_agent.py:302) — `pick_target` returns None → the explorer branch (lines 319-363):

1. Up to 2 explore steps per roam pass.
2. First tries **known resource locations** from `world_map.find()` (per-prefab, nearest first): `sapling`, `grass`, `flint`, `berrybush`, `carrot_planted` (line 328).
3. If no known spot: **fallback to cardinal directions** `[(1,0),(0,1),(-1,0),(0,-1)]` at 40-unit steps, indexed by `explored % 4` (lines 340-341) — cycles N→E→S→W.
4. After each move: `world_map.observe("default", st2)` records discovered entities (line 350).
5. If still no target after 2 steps: roam pass ends, returns counts (line 359-363).

**Next direction/destination choice:** nearest known resource of a needed prefab (via `world_map.find`), else cardinal cycling. `lib/explore.py` (`Explorer` class with visited-grid frontier + `next_target()`) exists but is **DEAD CODE — never imported or instantiated** (verified: no `Explorer(` or `explore` import in local_agent.py, reflex.py, roam.py, brain.py, driver.py). The fallback cardinal cycle is the ONLY non-map exploration.

**Record of where it has been:**
- `world_map.json` — entries with prefab/x/z/last_seen_day/last_seen_ts (via `lib/world_map.py` `observe()`), written during exploration (local_agent.py:350). Read via `find()` in roam_once (line 329).
- `data/explore/<world>.json` — visited cells; **only written by the unused `Explorer` class. The main agent does not persist visited cells** — `visited` is per-`roam_once` only.
- `agent_state.json` — current stage + items checkpoint (local_agent.py:620-622).

---

## SECTION 6 — Stuck recovery

**Job hangs (Lua, modmain.lua):**
- **Stall watchdog:** in `job_tick` (lines 904-918): if `workleft` is unchanged for 40 ticks (0.25s each = **10s**), `job_report("failed", phase, "stalled")`.
- **Swing cap:** `job.swings >= job.max_swings` (line 920) → `job_report("failed", "swing_cap")` at line 921. `max_swings = cmd.count or 150` (line 1031). The Python agent sends count=25 for trees/rocks, 3 for pick-mode (local_agent.py:371).
- **Pickup retry:** sweep phase, per-entity GUID, 3 attempts → `GiveItem` fallback (lines 860-867).
- **Event-driven completion:** `workfinished` (work mode, target-hosted, line 1060) or `picksomething` (pick mode, player-hosted, line 1058) listener sets phase=settling → 1500ms settle → sweep. No timeout on the listener itself — if the event never fires, the stall watchdog (10s) catches it.

**Python agent stuck (local_agent.py):**
- **Stuck streak:** `stuck_streak` increments when a roam pass gains nothing; at >= 2 → `ask_question(stage["asks_when_stuck"])` (lines 611-617) — blocks up to 300s waiting for `agent_answer.json`.
- **Stale state:** mtime > 10s → sleep 5s (line 420) — treats pause/crash as "wait".
- **Deadlock risk (observation):** `ask_question` blocks the WHOLE loop up to 300s; the threat guard comes AFTER it in priority order (rule 6 before rule 7), so a question asked while a threat approaches delays the flee by up to 5 minutes. The reflex's `flee_threats` (200ms loop, separate process) is the backstop — but it also `preempt_job`s while the agent is blocked.

---

## SECTION 7 — Light and hunger

**Eating thresholds:**
| Layer | Threshold | Action |
|-------|-----------|--------|
| Reflex | `hunger < 30` + food available (5s cd) | eat best FOOD |
| Reflex | `health < 25` + food (8s cd) | eat best FOOD |
| Reflex | `hunger < 30`, no food, `seeds` on ground | gather_job seeds → eat |
| Agent | `hunger < 60` OR `health < 50` | gather food (food override) |
| Agent | `hunger < 40` OR `health < 25` | eat from inventory (FOOD_ITEMS list) |
| Agent | `health < 15`, no food | ask_question (blocks) |

**Food priority (reflex `FOOD`, line 50):** `berries, carrot, cookedmeat, smallmeat, cookedsmallmeat, meat, blue_mushroom, green_mushroom`. Agent `FOOD_ITEMS` (line 479): `berries, carrot, seeds, cookedmeat, cooked_smallmeat, smallmeat, meat, drumstick, cooked_drumstick, robin, crow, butterfly, mushroom, red_mushroom, green_mushroom, blue_mushroom, petals` — note **seeds are first-class food for the agent but NOT in the reflex FOOD list** (reflex handles seeds only via the ground-seed special case, line 310-315).

**Light thresholds:**
- Reflex light prep: `seconds_until_night <= 90` (dusk-minus-90s, line 87) — fixed, does not vary by phase/season.
- Reflex fire emergency: `(isdusk or isnight) and not fires and no light` (line 122).
- Reflex fuel: fire `fuel_pct < 35` within 15m.
- Lua light guard: refuses to unequip a light at dusk/night without fire within 15 (modmain.lua:967-998).
- Agent: `roam_once` stops roaming at night with no light (line 314: `isnight and not equipped`).

**Are thresholds time-of-day aware?** The `90s` light prep is fixed. The fuel threshold `35%` is fixed. Nothing scales with season or phase length. The agent's hunger thresholds are fixed numbers (60/40/25/15/50).

**What food does it eat first:** the first match in the FOOD / FOOD_ITEMS tuples (in list order). Berries first, then carrot, then cookedmeat... seeds are eaten only by the agent (4th) or by the reflex's ground-seed special case.

---

## SECTION 8 — State schema

**Source:** `write_state()` in modmain.lua (lines 309-690), written every **1.0s** via `DoPeriodicTask(1.0, poll)` (line 1600), delivered as KLEI-header JSON to `dst_ai_bot_state`.

| Field | Contents (mod line) | Read by |
|-------|--------------------|---------|
| `timestamp` | real-time ms/1000 (313) | dstbot.py, lib/state_reader.py |
| `in_world` | always true (315) | **WRITTEN BUT NEVER READ** |
| `phase` | worldstate phase (321) | local_agent, reflex, driver, brain, decision_log |
| `isday` / `isdusk` / `isnight` | booleans (322-324) | isday: **NEVER READ**; isdusk: reflex; isnight: local_agent, reflex |
| `day` | cycles+1 (326) | local_agent, driver, brain, run_log, world_map, lessons, decision_log |
| `season` | worldstate season (328) | brain, dstbot, run_log |
| `prefab` | player prefab (333) | local_agent, reflex, driver, roam, brain, world_map, plan, lessons |
| `pos` | {x,y,z} (334) | local_agent, reflex, driver, roam, brain, invariants, explore |
| `health` | [current,max] (336) | local_agent, reflex, driver, roam, brain, invariants, run_log |
| `is_ghost` | playerghost tag (341) | local_agent, reflex |
| `hunger` | [current,max] (342) | local_agent, reflex, driver, brain, invariants, run_log |
| `sanity` | [current,max] (343) | driver, brain, dstbot — **no survival decision uses it** |
| `items` | unique prefab list (369) | local_agent, reflex, driver, brain, invariants |
| `item_counts` | stack-accurate counts (370) | local_agent, reflex, driver, roam, invariants, plan, decision_log |
| `equipped` | equipslot prefabs (379) | local_agent, reflex, driver, roam, brain, plan |
| `activeitem` | active item prefab (380) | brain only |
| `ground_items` | loose items, {n,d,count,x,z} (405) | reflex, roam |
| `hunger_seconds_remaining` | current/rate (416) | lib/invariants only |
| `seconds_until_dusk` | clock API (428) | **WRITTEN BUT NEVER READ** |
| `seconds_until_night` | clock API (431) | reflex, lib/invariants |
| `can_build` | shortlist CanBuild (449) | **WRITTEN BUT NEVER READ** |
| `is_busy` | stategraph busy tag (451) | **WRITTEN BUT NEVER READ** |
| `threats` | {n,d,targeting,hp,x,z} (476) | local_agent, reflex, roam |
| `combat_ready` | weapon+armor+hp>60% (494) | **WRITTEN BUT NEVER READ** |
| `temperature` | current temp (503) | brain only |
| `is_freezing` / `is_overheating` | (504-505) | is_freezing: reflex; is_overheating: **NEVER READ** |
| `fires` | {n,d,fuel_pct,secs_left} (538) | local_agent, reflex, plan |
| `on_water` / `land_dirs` | ocean probe (553-561) | brain only |
| `nearby` | {n,d,x,z,ok,yields,work,guid,n_seen} (659) | local_agent, reflex, roam, brain, world_map, explore, targets |
| `scan` | {total,kept,dropped,capped} (660) | **WRITTEN BUT NEVER READ** |
| `results` | ring buffer, 5 newest (682) | **WRITTEN BUT NEVER READ** (the agent reads `dst_ai_bot_result` file? — NO: agent never reads the result file; job results are only consumed via item_counts deltas) |
| `sim_ts` | real-time ms (685) | driver, lib/state_reader |
| `_errors` | per-section failures (304) | lib/state_reader |

**Notable: 8 fields are written but never read** (`in_world`, `isday`, `seconds_until_dusk`, `can_build`, `is_busy`, `combat_ready`, `is_overheating`, `scan`, `results`). `results[]` (the job outcome ring buffer, the "ground truth" channel) is **never read by any Python file** — verification happens exclusively via `item_counts` deltas (local_agent.py:294 `verify_collection`).

---

## SECTION 9 — Memory and learning

**Persisted files (project root `dst-bot/` unless noted):**

| File | Written by (when) | Read by (when) | Acted on? |
|------|-------------------|----------------|-----------|
| `learnings.json` | `learn_gather(prefab, succeeded)` (local_agent.py:91-96) after each confirmed/denied gather | `gather_reliability(prefab)` (98-104) — read in `pick_target` scoring (270) | YES — adds up to +2 score to reliable prefabs |
| `agent_state.json` | checkpoint each loop (620-622); reset on respawn (441) | `current_stage_id()` (184), `get_current_stage()` (188), `next_stage()` (195) | YES — drives stage progression |
| `world_map.json` | `observe()` during exploration (350) | `find()` in roam explore branch (329) | YES — explore destination |
| `data/runs.jsonl` | `run_log.end_run()` — on death (432), crash/manual_stop (404-407), `finally` (410) | `run_log.summarize()` — **NOT IMPLEMENTED in the agent loop** (no caller found) | NO runtime effect — measurement only |
| `data/decisions.jsonl` | `log_decision()` / `log_outcome()` — **DEAD CODE** (no callers) | `build_lessons()` — also dead | NO |
| `data/lessons.md` | `build_lessons()` — **DEAD CODE** (no callers) | no reader | NO |
| `agent_question.json` / `agent_answer.json` | `ask_question()` (161) / external AI answer | `ask_question()` polls (171-180) | YES — blocking question loop |
| `survival_log.txt` | `log()` every agent message (64) | no reader | NO — human log only |

**What is read at the START of a run:** `RUN_ID = run_logger.start_run(...)` (line 30), `learnings.json` (via gather_reliability lazily), `agent_state.json` (stage), `world_map.json` (base + resources). The measurement layers (`decisions.jsonl`, `lessons.md`, `read_heartbeat`) exist but have **zero callers** — the learning loop as designed (predict → verify → learn) is only partially wired: `learn_gather` works, the decision/lesson pipeline does not.

---

## SECTION 10 — Known open bugs

The brief asks to copy the open-items list from `CLAUDE.md`. CLAUDE.md is an operator playbook (not a bug list); its "PITFALLS" section documents 8 learned hazards. Verifying each against the current code:

| Pitfall (CLAUDE.md) | Status in code |
|---------------------|----------------|
| Torch in inventory emits NO light — must `equip` | **FIXED** — `equip` command (modmain.lua:1307); reflex equips torch at dusk/emergency (reflex.py:95, 127) |
| A burned-out campfire is GONE (no fires[] entry) | **FIXED** — fire-absence emergency (reflex.py:122-145) |
| Grass/saplings regrow ~1-2 min — don't stand and wait | **FIXED** — per-roam visited set + worldmap navigation |
| `gather_job` picks the nearest entity — walk close to harvestable | **FIXED** — Lua prefers harvestable (modmain.lua:1136-1156); Python walks first (local_agent.py:367-368) |
| Wilson has an axe — equip before chopping | **FIXED** — `equip_best_tool` (modmain.lua:955, called at 1021) |
| Don't trust item_counts on first read after a command — re-tick | **FIXED** — settle sleeps + verify_collection (local_agent.py:375-383) |
| Ghost state = health 50 + items empty + skeleton → `revive` | **FIXED** — `is_ghost` field (modmain.lua:340-341) + death_reflex (reflex.py:215-245) |
| Never queue commands while paused — they fire on unpause | **FIXED** — stale-state gate (local_agent.py:420-422) + heartbeat (modmain.lua:1495-1513, read_heartbeat unused) |

**Additional observations from inspection (NOT in CLAUDE.md):**

1. **DEAD CODE — `lib/explore.py` (`Explorer`)**: no callers. The main agent uses cardinal-cycling fallback instead.
2. **DEAD CODE — `lib/targets.py` (`TargetTracker`)**: no callers. GUID-based retry protection designed but unused.
3. **DEAD CODE — `lib/lessons.py` (`build_lessons`) + `lib/decision_log.py` (`log_decision`/`log_outcome`)**: no callers. The predict→verify→learn pipeline is unwired.
4. **DEAD CODE — `lib/invariants.py` `reserve_food_ids` / `emergency_food_available`** (Invariant 3, emergency food reserve): no callers. The normal eat rule has no reserve protection.
5. **DEAD CODE — `lib/state_reader.py` `read_heartbeat`**: no callers. Pause detection in the agent uses file mtime instead (local_agent.py:420).
6. **`lib/plan.py` `next_action()`** — defined and tested but **not used by the main loop**; the loop uses `current_plan()` (local_agent.py:215) + `roam_once()`. The plan-as-data resolver (Day-1 sequence, recursive tool resolution) is unused at runtime.
7. **`_set_base` action sent by plan** (plan.py `base_site` step action `{"action":"_set_base"}`) — the mod has NO `_set_base` branch in `execute_command` → returns `{ok:false, "unknown: _set_base"}`. (However, `next_action` is itself unused, so this never fires at runtime.)
8. **`gather` command (modmain.lua:1333)** duplicates `gather_job` logic; called only by `brain.py`/`driver.py` (retired brain path). The active agent uses `gather_job` only.
9. **`results[]` ring buffer never read** by any Python file — job verification happens via `item_counts` deltas only.
10. **`st.sim_ts`** stamped with `TheSimRef:GetRealTime()` (modmain.lua:684) — the same clock as `heartbeat_ts`; the heartbeat pair can never diverge (both advance together), making the "sim frozen vs heartbeat advancing = paused" discrimination ineffective. (Observation only.)
11. **`seconds_until_dusk`** written but never read; the reflex's light prep uses `seconds_until_night` only.
12. **`isday` written but never read** — day detection uses `phase`/`isnight` instead.

---
