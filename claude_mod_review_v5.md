# DST AI Bot — Mod Review Request (v5 full file)

## Context
- DST offline solo host, TheWorld.ismastersim=true (we ARE the server)
- Mod writes state.json via TheSim:SetPersistentString, reads command.json
- External agent (LLM) reads state, writes commands. Reflex daemon (Python) does 200ms fail-safes.
- Every mod edit REQUIRES a full game restart; load failures show "Disable mod or exit game" and we waste 5 minutes each time. **Please be extra careful about syntax + runtime nil-crashes.**

## The mod below is 1156 lines / 53.6KB. Please review for:
1. **Syntax errors** (Lua parse — we've been bitten by: missing closing quotes, extra `end`, `if x:Method then` colon-in-condition)
2. **Runtime nil crashes** (calling methods on nil components; the mod must never throw — every game call should be PCALL-guarded or nil-checked)
3. **Lua scoping traps** (local function declared AFTER its caller captures nil — job_tick vs player_busy bit us twice)
4. **Command logic bugs** (gather_job pick vs work mode, sweep queue, GiveItem fallback, preempt reporting)
5. **Anything that would crash at LOAD time** (top-level code, not just inside functions)

## Function map
- push_result (50) — results ring buffer (last 5 in state.json)
- wilson_say (77) — dialogue with cooldown, noanim, duration scaling
- wilson_idle_chatter (100) — state-keyed idle dialogue
- write_state (134) — the big state writer (health/hunger/nearby/threats/fires/temp/combat_ready...)
- is_ocean_tile (352)
- job_report (476), job_snapshot_counts (506), player_busy (527), job_sweep_build_queue (536), job_finish_ok (557), job_tick (572), job_start (682)
- execute_command (716) — 16 command handlers
- heartbeat_tick (1069) — static scheduler (real-time, works while paused)
- poll (1084) — 1s tick: write_state + read commands

## Commands
get_state, log, move_to, gather_job, preempt_job, dump_knowledge, revive, deploy, fuel, equip, say, set_dialogue, gather, eat, attack, craft

## Recent additions (P0/P1 from your v5 playbook)
- fires[] state + fuel command (ADDFUEL) — P0.1
- combat_ready gate (weapon+armor+health>60%) — P0.2
- temperature/is_freezing/is_overheating state — P0.3
- deploy command (DEPLOY + CanDeployAtPoint) — P1.1
- craft auto-routes placer recipes to DoBuild(recipe, pt) — v4 Q3

---
# THE MOD (modmain.lua)
```lua
--------------------------------------------------------------------------
-- DST AI Bot - modmain.lua (v0.5 - persistent-string channel)
--
-- FINAL DIAGNOSIS: DST's mod sandbox BLOCKS GLOBAL.io.open entirely
-- ("invalid filepath" for EVERY path). The only safe file mechanism is the
-- game's own save API: TheSim:SetPersistentString / GetPersistentString,
-- which write KLEI-format files into the save dir (client_save/).
--
-- Channel:
--   mod -> CLI : SetPersistentString("dst_ai_bot_state",  json, false, cb)
--                -> file <save>/client_save/dst_ai_bot_state (KLEI header)
--   CLI -> mod : CLI writes <save>/client_save/dst_ai_bot_command
--                mod reads it via GetPersistentString("dst_ai_bot_command")
--
-- The mod env gives us: pairs, ipairs, print, math, table, type, string,
-- tostring, require, Class, GLOBAL, modname, MODROOT, Add* hooks.
-- Use GLOBAL.* for pcall/TheSim/etc.
--------------------------------------------------------------------------

local PCALL = GLOBAL.pcall
local TheSimRef = GLOBAL.TheSim
local STATE_NAME = "dst_ai_bot_state"
local CMD_NAME   = "dst_ai_bot_command"

json = json or require("json")

-- Entities captured from hook args (globals are nil in mod env).
local myplayer = nil
local myworld = nil
local armed = false
local poll_task = nil

-- Pending command id we are currently handling (avoid re-handling).
local last_cmd_seen = nil
local last_cmd_id = nil

-- GATHER JOB state (Claude's job-runner design: one command runs the whole arc)
local job = nil          -- {target, action, swings, max_swings, last_workleft, stall_ticks, id, prefab, drops, snapshot, phase, result_cmdid}
local job_task = nil

-- HEARTBEAT (Claude v4 Q1): static scheduler ticks on REAL time even when the sim
-- is paused. Pairing heartbeat_ts (static) with sim_ts (sim-clock) lets the agent
-- distinguish: running / paused / crashed.
local static_task = nil
local last_sim_ts = nil
local HEARTBEAT_NAME = "dst_ai_bot_heartbeat"

-- RESULTS ring buffer (Claude: put results in state.json to kill the file race)
local results_buf = {}   -- newest first, cap 5
local function push_result(cmdid, result)
    table.insert(results_buf, 1, { id = cmdid, result = result, t = math.floor(TheSimRef:GetRealTime() / 1000) })
    if #results_buf > 5 then table.remove(results_buf) end
end

-- WILSON DIALOGUE (Q5: personality - fun to watch, esp. when idle)
local dialogue_lines = {
    hungry = { "I could really use some berries...", "So hungry. So very hungry.", "Food. Now. Please." },
    idle = { "So... what's the plan, boss?", "Just standing here. Plotting.", "A fine day for surviving.", "I wonder what's over there..." },
    gathering = { "These twigs will make a fine axe!", "Gotta collect 'em all!", "Resource acquisition in progress." },
    scared = { "THOSE FROGS! WHY!?", "Not today, horrors!", "I did NOT sign up for this." },
    dusk = { "Getting dark... I don't want to be caught out here!", "Should probably make a fire soon.", "The night is coming for me!" },
    night = { "It's so dark... I can't see a thing!", "Where's the campfire when you need it?", "I hope the dark doesn't have teeth." },
    cold = { "It's freezing! I need warmth!", "Cold cold cold cold cold!", "My teeth are chattering!" },
    crafting = { "Aha! Masterful craftsmanship!", "Behold my creation!", "Science! ...and also a fire." },
    chopping = { "Take that, tree!", "Timberrrr!", "The axe sings!" },
    exploring = { "New ground! Exciting!", "What's around this corner?", "The map grows..." },
    eating = { "Delicious. Probably.", "Mmm, survival cuisine.", "That hit the spot!" },
}
local dialogue_agent = {}   -- agent-injected lines (set_dialogue)
local last_say_time = 0
local last_idle_chatter = 0
local last_say_text = ""
local SAY_COOLDOWN = 12000      -- ms: global 12s cooldown (Claude cadence)
local IDLE_CHATTER_MIN = 25000  -- ms: idle chatter every 25-45s
local IDLE_CHATTER_MAX = 45000

local function wilson_say(text, duration)
    -- gate: cooldown + don't stomp an in-progress line
    if not myplayer or not myplayer.components.talker then return false end
    local now = TheSimRef:GetRealTime()
    if now - last_say_time < SAY_COOLDOWN then return false end
    if text == last_say_text then return false end
    -- don't interrupt Wilson's own automatic speech (Claude: a new Say hard-interrupts)
    local ok_talk = PCALL(function() return myplayer.components.talker:IsTalking() end)
    if ok_talk then return false end
    -- Claude Bonus 2: noanim=true (talk while working, no chop interruption);
    -- duration scaled to length: Say(line, max(2, #line*0.06), true)
    local dur = math.max(2, math.min(6, math.floor(#text * 0.06)))
    local ok = PCALL(function()
        myplayer.components.talker:Say(text, dur, true, true)
    end)
    if ok then
        last_say_time = now
        last_say_text = text
        return true
    end
    return false
end

local function wilson_idle_chatter()
    -- state-keyed idle chatter (mod-side, no round-trip). Weighted by priority.
    if not myplayer then return end
    local now = TheSimRef:GetRealTime()
    if now - last_idle_chatter < IDLE_CHATTER_MIN then return end
    -- only chatter when the player is idle (not in a command burst)
    if job ~= nil then return end
    if myplayer.sg and myplayer.sg:HasStateTag("busy") then return end

    local c = myplayer.components
    local line = nil
    if c.hunger and c.hunger.current < 40 then
        line = dialogue_lines.hungry[math.random(#dialogue_lines.hungry)]
    elseif c.health and c.health.currenthealth < 40 then
        line = dialogue_lines.scared[math.random(#dialogue_lines.scared)]
    elseif myworld and myworld.state and myworld.state.isnight then
        line = dialogue_lines.night[math.random(#dialogue_lines.night)]
    elseif myworld and myworld.state and myworld.state.isdusk then
        line = dialogue_lines.dusk[math.random(#dialogue_lines.dusk)]
    elseif #dialogue_agent > 0 then
        -- agent-injected idle lines get priority after state lines
        line = table.remove(dialogue_agent, 1)
    else
        line = dialogue_lines.idle[math.random(#dialogue_lines.idle)]
    end
    if line then
        wilson_say(line, 3)
        last_idle_chatter = now + math.random(IDLE_CHATTER_MIN, IDLE_CHATTER_MAX)
    end
end

--------------------------------------------------------------------------
-- State write via persistent string (safe, non-blocking).
--------------------------------------------------------------------------
local function write_state()
    if not myplayer then return end
    local st = {}
    st.timestamp = 0
    local ok_t, ms = PCALL(function() return TheSimRef:GetRealTime() end)
    if ok_t and type(ms) == "number" then st.timestamp = math.floor(ms / 1000) end
    st.in_world = true
    if myworld then
        -- The world's .state table (from the worldstate component) has the
        -- replicated fields: phase, season, cycles, isday/isdusk/isnight...
        local ws = myworld.state or (myworld.components and myworld.components.worldstate)
        if ws then
            st.phase = ws.phase
            st.isday  = ws.isday  and true or false
            st.isdusk = ws.isdusk and true or false
            st.isnight = ws.isnight and true or false
            if type(ws.cycles) == "number" then
                st.day = ws.cycles + 1
            end
            st.season = ws.season
        end
    end
    if myplayer.components then
        local c = myplayer.components
        st.prefab = myplayer.prefab
        local x, y, z = myplayer.Transform:GetWorldPosition()
        st.pos = { x = x, y = y, z = z }
        st.health = c.health and { c.health.currenthealth, c.health.maxhealth } or nil
        st.hunger = c.hunger and { c.hunger.current, c.hunger.max } or nil
        st.sanity = c.sanity and { c.sanity.current, c.sanity.max } or nil
        if c.inventory then
            -- INVENTORY: unique names list + accurate counts via Inventory:Has
            -- (Has counts stacks correctly; itemslots iteration under-counts stacks)
            local items = {}
            local counts = {}
            local names_seen = {}
            if c.inventory.itemslots then
                for _, it in pairs(c.inventory.itemslots) do
                    if it and it.prefab and not names_seen[it.prefab] then
                        names_seen[it.prefab] = true
                        table.insert(items, it.prefab)
                    end
                end
            end
            for _, nm in ipairs(items) do
                local okc, has, cnt = PCALL(function()
                    return c.inventory:Has(nm, 1, true)
                end)
                if okc and has then
                    counts[nm] = cnt or 1
                else
                    counts[nm] = 1
                end
            end
            st.items = items
            st.item_counts = counts
            local equipped = {}
            if c.inventory.equipslots then
                for slot, e in pairs(c.inventory.equipslots) do
                    if e and e.prefab then
                        table.insert(equipped, e.prefab)
                    end
                end
            end
            st.equipped = equipped
            st.activeitem = c.inventory.activeitem and c.inventory.activeitem.prefab or nil
        end
        -- GROUND ITEMS: loose, pickup-able items near the player (INLIMBO filter).
        -- Source: inventoryitem.owner == nil means loose on the ground; held items
        -- carry the INLIMBO tag. FindEntities with must-not-have tags excludes them.
        do
            local ground = {}
            local px, py, pz = myplayer.Transform:GetWorldPosition()
            local okg, gents = PCALL(function()
                return TheSimRef:FindEntities(px, py, pz, 8, {"_inventoryitem"}, {"INLIMBO", "FX", "NOCLICK"})
            end)
            if okg and type(gents) == "table" then
                for _, e in ipairs(gents) do
                    if e and e.prefab then
                        local ex, _, ez = e.Transform:GetWorldPosition()
                        local gd = math.floor(math.sqrt((ex-px)^2 + (ez-pz)^2))
                        local gs = 1
                        if e.components and e.components.stackable and e.components.stackable.stacksize then
                            gs = e.components.stackable.stacksize
                        end
                        table.insert(ground, { n = e.prefab, d = gd, count = gs,
                                               x = math.floor(ex), z = math.floor(ez) })
                    end
                end
            end
            st.ground_items = ground
        end
        -- DERIVED STATE (Claude Q4: expose decision-ready values, not raw facts)
        -- hunger_seconds_remaining: deadline the agent can plan against
        do
            if c.hunger then
                -- source-verified: rate is stored in self.hungerrate (no GetRate method)
                local rate = c.hunger.hungerrate
                if rate and rate < 0 and c.hunger.current then
                    st.hunger_seconds_remaining = math.floor(c.hunger.current / (-rate))
                end
            end
            -- TIME TO NEXT PHASE (Claude Bonus 1): remainingtimeinphase is a net
            -- field in SEGMENT units; *SEG_TIME = seconds.
            do
                local clock = myworld and myworld.components and myworld.components.clock
                if clock and clock.GetTimeUntilPhase then
                    -- source-verified: public API returns SECONDS until the phase
                    local okd, dusk = PCALL(function() return clock:GetTimeUntilPhase("dusk") end)
                    local okn, night = PCALL(function() return clock:GetTimeUntilPhase("night") end)
                    if okd and type(dusk) == "number" then
                        st.seconds_until_dusk = math.floor(dusk)
                    end
                    if okn and type(night) == "number" then
                        st.seconds_until_night = math.floor(night)
                    end
                end
            end
            -- can_build[]: shortlist the agent can act on without recipe memory
            local shortlist = { "axe", "campfire", "torch", "pickaxe", "shovel", "spear", "backpack", "sciencemachine", "log", "cutgrass" }
            local builds = {}
            if c.builder then
                for _, rn in ipairs(shortlist) do
                    local okc, canb = PCALL(function() return c.builder:CanBuild(rn) end)
                    if okc and canb then table.insert(builds, rn) end
                end
            end
            st.can_build = builds
            -- is_busy: the readiness check for swing/act decisions
            st.is_busy = (myplayer.sg and myplayer.sg:HasStateTag("busy")) or false
            -- threats[]: hostiles near the player with distance + targeting info
            local threats = {}
            local tx, ty, tz = myplayer.Transform:GetWorldPosition()
            local okt, tents = PCALL(function()
                return TheSimRef:FindEntities(tx, ty, tz, 20, {"_combat"}, {"playerghost", "INLIMBO"})
            end)
            if okt and type(tents) == "table" then
                for _, e in ipairs(tents) do
                    if e and e.prefab and e ~= myplayer then
                        local ex, _, ez = e.Transform:GetWorldPosition()
                        local d = math.floor(math.sqrt((ex-tx)^2 + (ez-tz)^2))
                        local targeting = false
                        if e.components and e.components.combat then
                            targeting = (e.components.combat.target == myplayer)
                        end
                        local hp = nil
                        if e.components and e.components.health then
                            hp = math.floor(e.components.health.currenthealth)
                        end
                        table.insert(threats, { n = e.prefab, d = d, targeting = targeting, hp = hp })
                    end
                end
            end
            st.threats = threats
            -- COMBAT READY (Claude P0.2): "don't fight" default. Only fight when
            -- a weapon AND armor are equipped AND health > 60%.
            do
                local eq = st.equipped or {}
                local has_weapon = false
                local has_armor = false
                local eqs = c.inventory and c.inventory.equipslots or {}
                for _, it in pairs(eqs) do
                    if it and it.components then
                        if it.components.weapon then has_weapon = true end
                        if it.components.armor then has_armor = true end
                    end
                end
                local hp_pct = 1
                if c.health and c.health.maxhealth and c.health.maxhealth > 0 then
                    hp_pct = c.health.currenthealth / c.health.maxhealth
                end
                st.combat_ready = (has_weapon and has_armor and hp_pct > 0.6)
            end
            -- TEMPERATURE STATE (Claude P0.3): winter prep needs to see it coming
            do
                local tc = c.temperature
                if tc then
                    local okc2, cur = PCALL(function() return tc:GetCurrent() end)
                    local okf2, frz = PCALL(function() return tc:IsFreezing() end)
                    local oko2, ovh = PCALL(function() return tc:IsOverheating() end)
                    if okc2 and cur then st.temperature = math.floor(cur) end
                    if okf2 then st.is_freezing = frz and true or false end
                    if oko2 then st.is_overheating = ovh and true or false end
                end
            end
            -- FIRES STATE (Claude P0.1): fueled entities near the player with
            -- fuel percent + seconds left. Lets the agent/reflex manage fire fuel.
            do
                local fires = {}
                local fx, fy, fz = myplayer.Transform:GetWorldPosition()
                local okf, fents = PCALL(function()
                    return TheSimRef:FindEntities(fx, fy, fz, 20, nil, {"INLIMBO", "FX", "NOCLICK"}, nil)
                end)
                if okf and type(fents) == "table" then
                    for _, e in ipairs(fents) do
                        if e and e.components and e.components.fueled and e ~= myplayer then
                            local ex, _, ez = e.Transform:GetWorldPosition()
                            local d = math.floor(math.sqrt((ex-fx)^2 + (ez-fz)^2))
                            local okpct, pct = PCALL(function() return e.components.fueled:GetPercent() end)
                            local oksec, secs = PCALL(function() return e.components.fueled.currentfuel or 0 end)
                            table.insert(fires, {
                                n = e.prefab, d = d,
                                x = math.floor(ex), z = math.floor(ez),
                                fuel_pct = okpct and pct and math.floor(pct * 100) or -1,
                                secs_left = oksec and math.floor(secs) or -1,
                            })
                        end
                    end
                end
                st.fires = fires
            end
        end
    end
    -- Water awareness: is the player on/next to ocean? (ocean tiles 201-208)
    -- Source-verified access: TheWorld.Map:GetTileAtPoint (world entity captured as myworld)
    do
        local px, py, pz = myplayer.Transform:GetWorldPosition()
        local map = myworld and myworld.Map
        local function is_ocean_tile(t)
            return t ~= nil and t >= 201 and t <= 208
        end
        if map and map.GetTileAtPoint then
            local okm, t = PCALL(function() return map:GetTileAtPoint(px, 0, pz) end)
            if okm then
                st.on_water = is_ocean_tile(t)
                -- probe 4 directions ~8 units out for land vs water
                local dirs = { {8,0,"east"}, {-8,0,"west"}, {0,8,"south"}, {0,-8,"north"} }
                local land = {}
                for _, d in ipairs(dirs) do
                    local okd, dt = PCALL(function() return map:GetTileAtPoint(px+d[1], 0, pz+d[2]) end)
                    if okd and not is_ocean_tile(dt) then table.insert(land, d[3]) end
                end
                st.land_dirs = land   -- which directions have land within ~8 units
            end
        end
    end
    -- Nearby entities (the "eyes"): name + distance within 25 units.
    do
        local px, py, pz = myplayer.Transform:GetWorldPosition()
        local nearby = {}
        -- DIAG: what tags do nearby entities actually have? (unfiltered, radius 20)
        do
            local _, allents = PCALL(function()
                return TheSimRef:FindEntities(px, py, pz, 20, nil, nil, nil)
            end)
            if allents and type(allents) == "table" and #allents > 0 then
                local tags_diag = {}
                for _, e in ipairs(allents) do
                    if e and e.prefab and #tags_diag < 12 then
                        local etags = {}
                        if e.HasTag then
                            for _, t in ipairs({ "plant", "tree", "renewable", "workable", "pickable", "_inventoryitem", "_combat", "hostile", "animal" }) do
                                local okt, hastag = PCALL(function() return e:HasTag(t) end)
                                if okt and hastag then table.insert(etags, t) end
                            end
                        end
                        table.insert(tags_diag, { n = e.prefab, tags = etags })
                    end
                end
                print("[DST-AI-BOT] TAGS " .. tostring(json.encode(tags_diag)))
            end
        end
        if TheSimRef and TheSimRef.FindEntities then
            -- ONEOF tags for resources (source-verified: pickable/workable ARE real
            -- tags; must_have requires ALL so it matched nothing. oneof = any.)
            local _, ents = PCALL(function()
                return TheSimRef:FindEntities(px, py, pz, 25, nil, {"INLIMBO", "FX", "NOCLICK"},
                    {"pickable", "CHOP_workable", "MINE_workable", "DIG_workable", "HAMMER_workable", "_inventoryitem"})
            end)
            if ents and type(ents) == "table" then
                -- dedupe by prefab, keep nearest 3
                local best = {}
                for _, e in ipairs(ents) do
                    if e and e.prefab and e ~= myplayer then
                        local ex, ey, ez = e.Transform:GetWorldPosition()
                        local dist = math.sqrt((ex-px)^2 + (ez-pz)^2)
                        local entry = best[e.prefab]
                        if not entry then
                            best[e.prefab] = { n = e.prefab, d = math.floor(dist),
                                               x = math.floor(ex), z = math.floor(ez), ok = true }
                        elseif dist < entry.d then
                            entry.d = math.floor(dist); entry.x = math.floor(ex); entry.z = math.floor(ez)
                        end
                    end
                end
                -- harvestable check (pickable ready / workable has work left)
                for _, entry in pairs(best) do
                    local e2 = nil
                    local _, ents2 = PCALL(function()
                        return TheSimRef:FindEntities(entry.x, 0, entry.z, 2, nil, nil, nil)
                    end)
                    -- simpler: re-find by prefab+distance
                    for _, e3 in ipairs(ents) do
                        if e3.prefab == entry.n then e2 = e3 break end
                    end
                    if e2 then
                        if e2.components and e2.components.pickable then
                            local okc, cp = PCALL(function() return e2.components.pickable:CanBePicked() end)
                            if okc and cp == false then entry.ok = false end
                        end
                        if e2.components and e2.components.workable then
                            local okw, wl = PCALL(function() return e2.components.workable.workleft or 0 end)
                            if okw and wl ~= nil and wl <= 0 then entry.ok = false end
                        end
                    end
                end
                for _, entry in pairs(best) do table.insert(nearby, entry) end
            end
        end
        st.nearby = nearby
    end
    st.results = results_buf
    -- heartbeat pair: sim_ts stamped by the sim-clock task
    last_sim_ts = TheSimRef:GetRealTime()
    st.sim_ts = last_sim_ts
    local ok, enc = PCALL(json.encode, st)
    if ok and enc then
        PCALL(function() TheSimRef:SetPersistentString(STATE_NAME, enc, false, nil) end)
    end
end

--------------------------------------------------------------------------
-- Command execution
--------------------------------------------------------------------------
--------------------------------------------------------------------------
-- GATHER JOB: walk -> work (loop) -> sweep pickup -> verify -> report
--------------------------------------------------------------------------
-- Expected drops per prefab (for the sweep phase)
local DROPS = {
    evergreen = { log = true, pinecone = true },
    deciduoustree = { log = true, acorn = true },
    twiggytree = { twigs = true, log = true },
    grass = { cutgrass = true },
    sapling = { twigs = true },
    berrybush = { berries = true },
    carrot_planted = { carrot = true },
    flint = { flint = true },
    rock1 = { rocks = true, flint = true },
    rock2 = { rocks = true, flint = true },
    boulder = { rocks = true, flint = true },
}

local function job_report(status, phase, reason, extra)
    if not job then return end
    local rep = {
        id = job.result_cmdid,
        result = {
            status = status, phase = phase, reason = reason,
            swings = job.swings or 0,
            elapsed = math.floor((TheSimRef:GetRealTime() - job.start_time) / 100) / 10,
        },
    }
    if extra then
        for k, v in pairs(extra) do rep.result[k] = v end
    end
    push_result(job.result_cmdid, rep.result)
    -- keep writing the file too (back-compat with the Python side reading it)
    local okenc, enc = PCALL(function() return json.encode(rep) end)
    if okenc and enc then
        PCALL(function() TheSimRef:SetPersistentString("dst_ai_bot_result", enc, false, nil) end)
    end
    -- clean up listener (prevent leaks across jobs) then clear
    if job and job.listener and job.target and job.target:IsValid() then
        PCALL(function() job.target:RemoveEventCallback("workfinished", job.listener) end)
    end
    job = nil
    if job_task then
        job_task:Cancel()
        job_task = nil
    end
end

local function job_snapshot_counts()
    local inv = myplayer and myplayer.components.inventory
    local snap = {}
    if inv then
        local names = {}
        if inv.itemslots then
            for _, it in pairs(inv.itemslots) do
                if it and it.prefab and not names[it.prefab] then
                    names[it.prefab] = true
                    table.insert(names, it.prefab)
                end
            end
        end
        for _, nm in ipairs(names) do
            local okc, has, cnt = PCALL(function() return inv:Has(nm, 1, true) end)
            if okc and has then snap[nm] = cnt or 1 end
        end
    end
    return snap
end

local function player_busy()
    -- Claude Q1: the ONLY reliable busy check is the stategraph tag.
    -- GetBufferedAction() goes nil the instant the action STARTS (animation running).
    if myplayer and myplayer.sg and myplayer.sg:HasStateTag("busy") then
        return true
    end
    return false
end

local function job_sweep_build_queue()
    -- Build the pickup queue from loose drops near the player.
    local queue = {}
    if not myplayer then return queue end
    local px, py, pz = myplayer.Transform:GetWorldPosition()
    local want = {}
    for pname in pairs(job.drops or {}) do want[pname] = true end
    want[job.prefab] = true   -- the target itself may be the drop (e.g. log)
    local okg, gents = PCALL(function()
        return TheSimRef:FindEntities(px, py, pz, 10, {"_inventoryitem"}, {"INLIMBO", "FX", "NOCLICK"})
    end)
    if okg and type(gents) == "table" then
        for _, e in ipairs(gents) do
            if e and e.prefab and want[e.prefab] then
                table.insert(queue, e)
            end
        end
    end
    return queue
end

local function job_finish_ok()
    -- Verify vs snapshot (AFTER pickups have actually completed)
    local after = job_snapshot_counts()
    local gained, lost = {}, {}
    for nm, cnt in pairs(after) do
        local b = job.snapshot[nm] or 0
        if cnt > b then gained[nm] = cnt - b end
    end
    for nm, b in pairs(job.snapshot) do
        local a = after[nm] or 0
        if a < b then lost[nm] = b - a end   -- catches "axe broke"
    end
    job_report("ok", "collected", "worked_down", { gained = gained, lost = lost })
end

local function job_tick()
    if not job or not myplayer then return end

    -- PREEMPT: a higher-priority command (e.g. reflex eat) may cancel us
    if job.preempted then
        job_report("preempted", job.phase, "preempted")
        return
    end

    -- Claude Q1: busy gate via stategraph tag (the only reliable check).
    -- GetBufferedAction() goes nil the instant the action STARTS, before the
    -- animation ends - so we gate on sg:HasStateTag("busy").
    if player_busy() then return end
    if myplayer:GetBufferedAction() ~= nil then return end

    -- ==== SETTLING PHASE: wait for launched drops to land (Q5) ====
    if job.phase == "settling" then
        if TheSimRef:GetRealTime() - job.work_done_at < 1500 then return end
        job.phase = "sweeping"
        job.queue = job_sweep_build_queue()
        return
    end

    -- ==== SWEEPING PHASE: pick up drops one at a time ====
    if job.phase == "sweeping" then
        local e = table.remove(job.queue, 1)
        if e == nil then
            job_finish_ok()
            return
        end
        -- re-validate: another pickup may have consumed a merged stack
        if not (e:IsValid() and not e:IsInLimbo()) then
            return  -- skip invalid; next tick pops the next one
        end
        -- per-item retry: 3 attempts, then GiveItem fallback (Q3)
        local rn = e.prefab or "?"
        job.retries[rn] = (job.retries[rn] or 0) + 1
        if job.retries[rn] > 3 then
            -- GiveItem fallback (source-verified: ACTIONS.PICKUP.fn = GiveItem)
            local okgi = PCALL(function()
                myplayer.components.inventory:GiveItem(e)
            end)
            if okgi then
                print("[DST-AI-BOT] GiveItem fallback for " .. rn)
            end
            return
        end
        myplayer:ClearBufferedAction()
        myplayer.components.locomotor:PushAction(
            GLOBAL.BufferedAction(myplayer, e, GLOBAL.ACTIONS.PICKUP), true)
        return
    end

    -- target gone? (tree replaced by stump)
    if job.target == nil or not job.target:IsValid() then
        job.phase = "settling"
        job.work_done_at = TheSimRef:GetRealTime()
        return
    end

    -- ==== WORKING PHASE ====
    -- PICK mode (grass/sapling/flint): one action, then settle->sweep.
    -- The swing loop only applies to WORK mode (trees/rocks).
    if job.mode == "pick" then
        if job.swings == 0 then
            myplayer:ClearBufferedAction()
            local ba2 = GLOBAL.BufferedAction(myplayer, job.target, job.action)
            myplayer.components.locomotor:PushAction(ba2, true)
            job.swings = job.swings + 1
        else
            job.phase = "settling"
            job.work_done_at = TheSimRef:GetRealTime()
        end
        return
    end
    local w = job.target.components.workable
    if w == nil then
        job.phase = "settling"
        job.work_done_at = TheSimRef:GetRealTime()
        return
    end

    -- stall watchdog (event-based per Claude): no 'work' progress in ~10s (40 ticks)
    -- workleft can legitimately sit still between swings; we key on the workfinished
    -- event now (job.listener sets phase=settling), so this is a true stall signal.
    if job.last_workleft == nil then
        job.last_workleft = w.workleft
    elseif w.workleft == job.last_workleft then
        job.stall_ticks = job.stall_ticks + 1
        if job.stall_ticks > 40 then
            job_report("failed", job.phase, "stalled", { workleft = w.workleft })
            return
        end
    else
        job.last_workleft = w.workleft
        job.stall_ticks = 0
    end

    if job.swings >= job.max_swings then
        job_report("failed", job.phase, "swing_cap", { workleft = w.workleft })
        return
    end

    -- swing!
    myplayer:ClearBufferedAction()
    local ba = GLOBAL.BufferedAction(myplayer, job.target, job.action)
    myplayer.components.locomotor:PushAction(ba, true)
    job.swings = job.swings + 1
end

local function job_start(cmd, target, action)
    job = {
        target = target, action = action,
        mode = (action == GLOBAL.ACTIONS.PICK or action == GLOBAL.ACTIONS.PICKUP) and "pick" or "work",
        swings = 0,
        max_swings = cmd.count or 60,
        last_workleft = nil, stall_ticks = 0,
        id = cmd.id, prefab = cmd.prefab,
        drops = DROPS[cmd.prefab] or {},
        snapshot = job_snapshot_counts(),
        phase = "working",
        start_time = TheSimRef:GetRealTime(),
        result_cmdid = cmd.id,
        preempted = false,
        last_swing = nil,
        work_done_at = nil,
        queue = {},
        retries = {},   -- per-entity pickup retry counts
        listener = nil,
    }
    -- Claude Q5: 'workfinished' fires EXACTLY once when the tree actually falls.
    -- Belt-and-braces with the stall watchdog (which now keys on events, not workleft).
    if target and target:IsValid() then
        job.listener = function()
            job.phase = "settling"
            job.work_done_at = TheSimRef:GetRealTime()
        end
        target:ListenForEvent("workfinished", job.listener)
    end
    if not job_task then
        job_task = myplayer:DoPeriodicTask(0.25, job_tick)
    end
end

local function execute_command(cmd)
    local action = cmd and cmd.action
    if action == "ping" then
        return { ok = true, reply = "pong" }
    elseif action == "get_state" then
        local ok, enc = PCALL(json.encode, { ok = true, note = "state via file" })
        return { ok = true, reply = "pong-getstate" }
    elseif action == "log" then
        print("[DST-AI-BOT] " .. tostring(cmd.text or ""))
        return { ok = true, reply = "logged" }
    elseif action == "move_to" then
        if not myplayer then return { ok = false, error = "no player" } end
        if type(cmd.x) ~= "number" or type(cmd.z) ~= "number" then
            return { ok = false, error = "need x and z" }
        end
        local v3 = GLOBAL.Vector3
        if v3 and myplayer.components.locomotor then
            myplayer.components.locomotor:GoToPoint(v3(cmd.x, 0, cmd.z), nil, true)
        end
        return { ok = true, reply = "moving" }
    elseif action == "gather_job" then
        -- Claude's job-runner: one command runs walk->work->sweep->verify->report.
        if not myplayer then return { ok = false, error = "no player" } end
        -- find nearest matching entity
        local px, py, pz = myplayer.Transform:GetWorldPosition()
        local want = cmd.prefab
        local target = nil
        local best = 9999
        local _, ents = PCALL(function()
            return TheSimRef:FindEntities(px, py, pz, 40, nil, nil, nil)
        end)
        if type(ents) == "table" then
            for _, e in ipairs(ents) do
                if e and e.prefab and e ~= myplayer then
                    if want == nil or e.prefab == want then
                        local ex, _, ez = e.Transform:GetWorldPosition()
                        local d = (ex-px)^2 + (ez-pz)^2
                        if d < best then best = d; target = e end
                    end
                end
            end
        end
        if not target then
            return { ok = false, error = "no matching entity nearby: " .. tostring(want) }
        end
        -- resolve action from components
        local acts = GLOBAL.ACTIONS or {}
        local action = nil
        if target.components.pickable ~= nil and target.components.pickable:CanBePicked() then
            action = acts.PICK
        elseif target.components.inventoryitem ~= nil then
            action = acts.PICKUP
        elseif target.components.workable ~= nil then
            action = target.components.workable:GetWorkAction() or acts.CHOP
        else
            action = acts.PICK
        end
        if not action then return { ok = false, error = "no action for " .. tostring(target.prefab) } end
        -- if a job is running, report it preempted synchronously FIRST
        if job then job_report("preempted", job.phase, "preempted") end
        job_start(cmd, target, action)
        return { ok = true, reply = "job started: " .. want }

    elseif action == "preempt_job" then
        if job then job.preempted = true end
        return { ok = true, reply = "job preempted" }

    elseif action == "dump_knowledge" then
        -- Claude's Q1 answer: dump the game's OWN runtime tables. This is the
        -- authoritative data source - matches THIS game build exactly.
        local what = cmd.what or "all"
        local out = {}
        if what == "all" or what == "tuning" then
            local t = GLOBAL.TUNING or {}
            -- key balance constants only (TUNING is huge)
            local keys = { "TOTAL_DAY_TIME", "SEG_TIME", "WILSON_HEALTH", "WILSON_HUNGER", "WILSON_SANITY",
                           "HUNGER_RATE", "STARVING", "SANITY_RATE", "NIGHT_TIME_DEFAULT",
                           "DEERCLOPS_HEALTH", "DEERCLOPS_ATTACK", "DEERCLOPS_WARNING_TIME",
                           "HOUND_WARNING_TIME", "HOUND_ATTACK_PERIOD", "HOUNDMOUND_HOUNDS_MIN",
                           "FROG_ATTACK_PERIOD", "FROG_DAMAGE", "FROG_HEALTH", "SPIDER_DAMAGE",
                           "SPIDER_HEALTH", "SPIDER_WARRIOR_DAMAGE", "MOSQUITO_DAMAGE", "TENTACLE_DAMAGE",
                           "BERRY_FOOD", "CARROT_FOOD", "COOKEDMEAT_FOOD", "MEAT_FOOD", "SMALLMEAT_FOOD" }
            for _, k in ipairs(keys) do
                if t[k] ~= nil then out[k] = t[k] end
            end
            out["_tuning_size"] = 0
            local n = 0
            for k in pairs(t) do n = n + 1 end
            out["_tuning_size"] = n
        end
        if what == "all" or what == "recipes" then
            local r = GLOBAL.AllRecipes or {}
            local rec = {}
            for name, def in pairs(r) do
                local ing = {}
                if def and def.ingredients then
                    for _, ingr in ipairs(def.ingredients) do
                        table.insert(ing, { type = ingr.type, amount = ingr.amount })
                    end
                end
                rec[name] = { ingredients = ing, level = def and def.level or nil, placer = def and def.placer or nil }
            end
            out["recipes"] = rec
        end
        if what == "all" or what == "strings" then
            -- Wilson's own dialogue lines (for the personality feature)
            local ws = GLOBAL.STRINGS and GLOBAL.STRINGS.CHARACTERS and GLOBAL.STRINGS.CHARACTERS.WILSON
            if ws then
                out["wilson_strings_sample"] = {
                    hungry = ws.ANNOUNCE_HUNGRY or nil,
                    cold = ws.ANNOUNCE_COLD or nil,
                    describe_axe = ws.DESCRIBE and ws.DESCRIBE.axe or nil,
                    describe_campfire = ws.DESCRIBE and ws.DESCRIBE.campfire or nil,
                    describe_berrybush = ws.DESCRIBE and ws.DESCRIBE.berrybush or nil,
                    describe_evergreen = ws.DESCRIBE and ws.DESCRIBE.evergreen or nil,
                }
            end
        end
        local okenc, enc = PCALL(function() return json.encode(out) end)
        if not okenc or not enc then return { ok = false, error = "encode failed" } end
        -- write to a dedicated knowledge dump file
        PCALL(function() TheSimRef:SetPersistentString("dst_ai_bot_knowledge", enc, false, nil) end)
        return { ok = true, reply = "knowledge dumped (" .. what .. ")" }

    elseif action == "revive" then
        -- Revive Wilson from ghost (source-verified: ThePlayer:PushEvent("respawnfromghost"))
        if not myplayer then return { ok = false, error = "no player" } end
        -- source-verified: ghost state = "playerghost" TAG, NOT IsDead()
        -- (IsDead = currenthealth<=0; a ghost can read health 50 with the tag)
        local okg, isghost = PCALL(function() return myplayer:HasTag("playerghost") end)
        if okg and isghost then
            myplayer:PushEvent("respawnfromghost")
            return { ok = true, reply = "revive requested" }
        end
        return { ok = true, reply = "not ghost" }

    elseif action == "deploy" then
        -- Claude P1.1: plant a held item (dug_grass, sapling, berrybush, pinecone)
        -- at coordinates. ACTIONS.DEPLOY with the item + point.
        if not myplayer then return { ok = false, error = "no player" } end
        local inv = myplayer.components.inventory
        local item = nil
        for _, it in pairs(inv.itemslots or {}) do
            if it and it.prefab == cmd.item then item = it break end
        end
        if not item then return { ok = false, error = "not in inventory: " .. tostring(cmd.item) } end
        local pt = GLOBAL.Vector3(cmd.x or 0, 0, cmd.z or 0)
        local okc2, can = PCALL(function()
            return GLOBAL.TheWorld.Map:CanDeployAtPoint(pt, myplayer, item)
        end)
        if okc2 and can == false then
            return { ok = false, error = "cannot deploy there" }
        end
        local ba = GLOBAL.BufferedAction(myplayer, nil, GLOBAL.ACTIONS.DEPLOY, item, pt)
        myplayer:ClearBufferedAction()
        myplayer.components.locomotor:PushAction(ba, true)
        return { ok = true, reply = "deploying " .. tostring(cmd.item) }

    elseif action == "fuel" then
        -- Claude P0.1: add fuel to a nearby fire (campfire/firepit).
        local fire = nil
        local fx2, fy2, fz2 = myplayer.Transform:GetWorldPosition()
        local _, fents2 = PCALL(function()
            return TheSimRef:FindEntities(fx2, fy2, fz2, 20, nil, nil, nil)
        end)
        if type(fents2) == "table" then
            local bestd = 9999
            for _, e in ipairs(fents2) do
                if e and e.components and e.components.fueled and e ~= myplayer then
                    local ex, _, ez = e.Transform:GetWorldPosition()
                    local d = (ex-fx2)^2 + (ez-fz2)^2
                    if d < bestd then bestd = d; fire = e end
                end
            end
        end
        if not fire then return { ok = false, error = "no fire nearby" } end
        local inv = myplayer.components.inventory
        local fuel_item = nil
        for _, it in pairs(inv.itemslots or {}) do
            if it and it.prefab == (cmd.item or "log") then fuel_item = it break end
        end
        if not fuel_item then return { ok = false, error = "no fuel item: " .. tostring(cmd.item or "log") } end
        myplayer:ClearBufferedAction()
        myplayer.components.locomotor:PushAction(
            GLOBAL.BufferedAction(myplayer, fire, GLOBAL.ACTIONS.ADDFUEL, fuel_item), true)
        return { ok = true, reply = "fuelling" }

    elseif action == "equip" then
        -- Claude Q4: a torch in inventory emits NO light - must be equipped.
        local inv = myplayer.components.inventory
        local item = nil
        for _, it in pairs(inv.itemslots or {}) do
            if it and it.prefab == cmd.item then item = it break end
        end
        if not item then return { ok = false, error = "not in inventory: " .. tostring(cmd.item) } end
        local oke = PCALL(function() inv:Equip(item) end)
        return { ok = oke, reply = "equipped " .. tostring(cmd.item) }

    elseif action == "say" then
        if wilson_say(cmd.text or "", cmd.duration or 3) then
            return { ok = true, reply = "said" }
        end
        return { ok = false, error = "say blocked (cooldown or talking)" }

    elseif action == "set_dialogue" then
        -- agent injects lines for the mod to use during idle chatter (Claude's trick)
        if type(cmd.lines) == "table" then
            dialogue_agent = cmd.lines
            return { ok = true, reply = "dialogue set (" .. #dialogue_agent .. " lines)" }
        end
        return { ok = false, error = "need lines array" }

    elseif action == "gather" then
        -- Claude: collapse gather into gather_job. The job-runner does walk ->
        -- act -> verify -> report-with-delta. One item = a job of size one.
        -- This returns GROUND TRUTH (did the item land in inventory?), not a
        -- dispatch receipt. Renamed reply field is 'dispatched' semantics removed.
        if not myplayer then return { ok = false, error = "no player" } end
        local px, py, pz = myplayer.Transform:GetWorldPosition()
        local want = cmd.prefab
        local target = nil
        local best = 9999
        local _, ents = PCALL(function()
            return TheSimRef:FindEntities(px, py, pz, 40, nil, nil, nil)
        end)
        if type(ents) == "table" then
            for _, e in ipairs(ents) do
                if e and e.prefab and e ~= myplayer then
                    if want == nil or e.prefab == want then
                        local ex, _, ez = e.Transform:GetWorldPosition()
                        local d = (ex-px)^2 + (ez-pz)^2
                        if d < best then best = d; target = e end
                    end
                end
            end
        end
        if not target then
            return { ok = false, error = "no matching entity nearby: " .. tostring(want) }
        end
        -- resolve action from components
        local acts = GLOBAL.ACTIONS or {}
        local action = nil
        if target.components.pickable ~= nil and target.components.pickable:CanBePicked() then
            action = acts.PICK
        elseif target.components.inventoryitem ~= nil then
            action = acts.PICKUP
        elseif target.components.workable ~= nil then
            action = target.components.workable:GetWorkAction() or acts.CHOP
        else
            action = acts.PICK
        end
        if not action then return { ok = false, error = "no action for " .. tostring(target.prefab) } end
        -- start a job (size 1) - preempt any running job with a synchronous report
        if job then job_report("preempted", job.phase, "preempted") end
        job_start({ id = cmd.id, prefab = cmd.prefab, count = 1 }, target, action)
        return { ok = true, reply = "job dispatched: " .. want }

elseif action == "eat" then
        -- Eat a food item from inventory (use on self).
        if not myplayer then return { ok = false, error = "no player" } end
        local inv = myplayer.components.inventory
        local item_name = cmd.item
        local target = nil
        if inv and inv.itemslots then
            for _, it in pairs(inv.itemslots) do
                if it and it.prefab and (item_name == nil or it.prefab == item_name) then
                    target = it
                    break
                end
            end
        end
        if not target then
            return { ok = false, error = "no food in inventory: " .. tostring(item_name) }
        end
        local acts = GLOBAL.ACTIONS or {}
        local action = acts.EAT
        if not action then return { ok = false, error = "no EAT action" } end
        local BA = GLOBAL.BufferedAction
        if not BA then return { ok = false, error = "no BufferedAction" } end
        local ba = BA(myplayer, target, action, target)
        myplayer.components.locomotor:PushAction(ba, true)
        return { ok = true, reply = "eating " .. target.prefab }

    elseif action == "attack" then
        -- Attack nearest entity with cmd.prefab (or nearest hostile-flagged).
        if not myplayer then return { ok = false, error = "no player" } end
        local target = nil
        local best = 9999
        local px, py, pz = myplayer.Transform:GetWorldPosition()
        local want = cmd.prefab
        local _, ents = PCALL(function()
            return TheSimRef:FindEntities(px, py, pz, 40, nil, nil, nil)
        end)
        if type(ents) == "table" then
            for _, e in ipairs(ents) do
                if e and e.prefab and e ~= myplayer and e.components and e.components.combat then
                    if want == nil or e.prefab == want then
                        local ex, _, ez = e.Transform:GetWorldPosition()
                        local d = (ex-px)^2 + (ez-pz)^2
                        if d < best then best = d; target = e end
                    end
                end
            end
        end
        if not target then
            return { ok = false, error = "no attackable target nearby: " .. tostring(want) }
        end
        local acts = GLOBAL.ACTIONS or {}
        local action = acts.ATTACK
        if not action then return { ok = false, error = "no ATTACK action" } end
        local BA = GLOBAL.BufferedAction
        if not BA then return { ok = false, error = "no BufferedAction" } end
        local ba = BA(myplayer, target, action)
        myplayer.components.locomotor:PushAction(ba, true)
        return { ok = true, reply = "attacking " .. target.prefab }

    elseif action == "craft" then
        -- Craft a recipe the player knows (e.g. "axe", "campfire")
        if not myplayer then return { ok = false, error = "no player" } end
        local recipe = cmd.recipe
        if type(recipe) ~= "string" then return { ok = false, error = "need recipe" } end
        local builder = myplayer.components.builder
        if not builder then return { ok = false, error = "no builder component" } end
        local okk, knows = PCALL(function() return builder:KnowsRecipe(recipe) end)
        if not okk or knows ~= true then return { ok = false, error = "does not know recipe: " .. recipe } end
        -- Source-verified: Builder:DoBuild(recname, pt, rotation, skin) is the
        -- actual build function (checks ingredients, consumes them, spawns item).
        -- MakeRecipe only queues a UI buffered action and does NOT build.
        -- Claude Q3: for placer recipes (campfire/firepit), pt IS the placer-confirm.
        -- Omit it and ingredients get consumed with no structure spawned. Auto-route.
        local pt = nil
        local recdef = GLOBAL.AllRecipes and GLOBAL.AllRecipes[recipe]
        if recdef and recdef.placer ~= nil then
            local px, py, pz = myplayer.Transform:GetWorldPosition()
            pt = GLOBAL.Vector3(px + 2, 0, pz)  -- ~2 units in front of Wilson
        end
        local okb, resb = PCALL(function()
            return builder:DoBuild(recipe, pt)
        end)
        if not okb then return { ok = false, error = "craft threw: " .. tostring(resb) } end
        if resb == false then
            return { ok = false, error = "DoBuild returned false (missing ingredients? not idle?)" }
        end
        return { ok = true, reply = "crafted " .. recipe }

    else
        return { ok = false, error = "unknown: " .. tostring(action) }
    end
end

--------------------------------------------------------------------------
-- Poll tick: write state; read command file (async).
--------------------------------------------------------------------------
local function heartbeat_tick()
    -- Claude v4 Q1: static scheduler (real-time) - fires even when sim is paused.
    local hb = {
        heartbeat_ts = TheSimRef:GetRealTime(),
        sim_ts = last_sim_ts,
        paused = false,
    }
    local okp, p = PCALL(function() return GLOBAL.TheNet:IsServerPaused() end)
    if okp then hb.paused = (p and true or false) end
    local oke, enc = PCALL(json.encode, hb)
    if oke and enc then
        PCALL(function() TheSimRef:SetPersistentString(HEARTBEAT_NAME, enc, false, nil) end)
    end
end

local function poll()
    write_state()
    -- Ask the game for the command persistent string. Callback handles it.
    PCALL(function()
        TheSimRef:GetPersistentString(CMD_NAME, function(ok, data)
            if ok and data and #data > 0 then
                local stripped = data
                -- Strip KLEI header if present ("KLEI     1 ")
                if string.sub(stripped, 1, 5) == "KLEI " then
                    stripped = string.match(stripped, "KLEI%s+%d%s+(.*)")
                    if not stripped then stripped = data end
                end
                local p, cmd = PCALL(json.decode, stripped)
                if p and type(cmd) == "table" and cmd.id ~= last_cmd_id then
                    last_cmd_id = cmd.id
                    local ok3, ret = PCALL(execute_command, cmd)
                    local resp = { id = cmd.id }
                    if ok3 then resp.result = ret else resp.result = { ok = false, err = tostring(ret) } end
                    local ok2, enc2 = PCALL(json.encode, resp)
                    if ok2 and enc2 then
                        PCALL(function() TheSimRef:SetPersistentString("dst_ai_bot_result", enc2, false, nil) end)
                    end
                end
            end
        end)
    end)
end

--------------------------------------------------------------------------
-- Start polling when the player exists (captured from the hook arg).
--------------------------------------------------------------------------
-- Capture the world entity (TheWorld global is nil in the mod env).
AddPrefabPostInit("world", function(inst)
    myworld = inst
    print("[DST-AI-BOT] world captured")
    -- DIAGNOSTIC: dump what's on the world entity
    local diag = {}
    if inst.state then
        local s = inst.state
        diag.state_fields = {}
        for k, v in pairs(s) do
            diag.state_fields[k] = type(v) == "table" and "table" or tostring(v)
        end
    end
    if inst.components then
        diag.components = {}
        for k in pairs(inst.components) do
            diag.components[k] = true
        end
    end
    local okd, encd = PCALL(json.encode, diag)
    print("[DST-AI-BOT] WORLD DIAG " .. tostring(encd or "encode-fail"))
end)

AddPlayerPostInit(function(inst)
    print("[DST-AI-BOT] AddPlayerPostInit fired")
    myplayer = inst
    armed = true
    -- CRITICAL: on death/respawn DST creates a NEW player entity. The old
    -- poll_task is attached to the DEAD entity and stops firing. Always
    -- cancel any old task and re-attach to the CURRENT player.
    -- Claude v4: cancel-and-reattach ALL tasks on respawn (poll, static, job)
    for _, t in ipairs({ poll_task, static_task, job_task }) do
        if t then PCALL(function() t:Cancel() end) end
    end
    poll_task, static_task, job_task = nil, nil, nil
    job = nil
    poll_task = inst:DoPeriodicTask(1.0, poll)
    static_task = inst:DoStaticPeriodicTask(0.5, heartbeat_tick)
    print("[DST-AI-BOT] poll task (re)started on player")
end)

print("[DST-AI-BOT] v0.5 persistent-string channel loaded")

```
