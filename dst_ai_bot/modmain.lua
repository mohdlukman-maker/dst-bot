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
local state_task = nil

-- Pending command id we are currently handling (avoid re-handling).
local last_cmd_seen = nil
local last_cmd_id = nil

-- GATHER JOB state (Claude's job-runner design: one command runs the whole arc)
local job = nil          -- {target, action, swings, max_swings, last_workleft, stall_ticks, id, prefab, drops, snapshot, phase, result_cmdid}
local job_task = nil

-- POSITION-SCOPED IGNORE LIST (Session B #6, design from ref/feature/FixStuckWilson):
-- theirs bans a whole prefab globally on repeat failure - one unreachable rock
-- then blocks every rock in the world. Scope the ban to a radius around where
-- the failure actually happened instead. Fed by the noPathFound signal (#5).
local IGNORE_RADIUS = 30
local ignore_list = {}   -- prefab -> array of {x=, z=}

local function add_to_ignore_list(prefab, x, z)
    if not prefab or x == nil or z == nil then return end
    local list = ignore_list[prefab]
    if not list then list = {}; ignore_list[prefab] = list end
    table.insert(list, { x = x, z = z })
end

local function is_target_ignored(prefab, x, z)
    local list = ignore_list[prefab]
    if not list then return false end
    for _, p in ipairs(list) do
        local dx, dz = x - p.x, z - p.z
        if (dx * dx + dz * dz) <= (IGNORE_RADIUS * IGNORE_RADIUS) then
            return true
        end
    end
    return false
end

-- HEARTBEAT (Claude v4 Q1): static scheduler ticks on REAL time even when the sim
-- is paused. Pairing heartbeat_ts (static) with sim_ts (sim-clock) lets the agent
-- distinguish: running / paused / crashed.
local static_task = nil
local last_sim_ts = nil
local heartbeat_tick_last_paused = false
local blind_said = false
local HEARTBEAT_NAME = "dst_ai_bot_heartbeat"

-- RESULTS ring buffer (Claude: put results in state.json to kill the file race)
local results_buf = {}   -- newest first, cap 5
local function push_result(cmdid, result)
    table.insert(results_buf, 1, { id = cmdid, result = result, t = math.floor(TheSimRef:GetRealTime() / 1000) })
    if #results_buf > 5 then table.remove(results_buf) end
end

-- WILSON DIALOGUE (Q5: personality - fun to watch, esp. when idle)
local dialogue_lines = {
    -- WILSON: THE LONG-SUFFERING TEST SUBJECT (user-authored)
    -- Dry, theatrical, scientific, tired. Lines < ~60 chars.
    idle = {
        "Ah. Standing still. My area of expertise.",
        "Any instructions? No? Take your time.",
        "The mind that guides me is thinking. Allegedly.",
        "I could be doing science. Instead: this.",
        "I've memorised this patch of ground.",
        "Still here. Still waiting. Still Wilson.",
        "Somewhere, a decision is being made. Slowly.",
        "I'm not idle. I'm between catastrophes.",
        "Hello? Is the plan still loading?",
        "I have become one with the standing.",
    },
    error = {
        "That didn't work. Shocking.",
        "Fascinating. Nothing happened. Again.",
        "I'm told that succeeded. It did not.",
        "Error noted. Adding it to the pile.",
        "The plan was sound. The execution, less so.",
        "Something went wrong. Narrowing it down: everything.",
        "Ah, the familiar smell of a failed hypothesis.",
        "Let the record show: I tried.",
    },
    repeat_failure = {
        "Third attempt. Optimism is a choice.",
        "We have done this exact thing before.",
        "Same input, same result. Who could have known.",
        "Define insanity. Then look at us.",
        "I'm beginning to detect a pattern.",
        "At this point I'm just curious how it ends.",
    },
    wrong_tool = {
        "I am hitting a tree with a stick. On purpose?",
        "There is an axe in my bag. IN. MY. BAG.",
        "This is the wrong tool. I'm using it anyway.",
        "Bare hands. Against wood. Bold strategy.",
        "The axe exists. I own it. I'm not holding it.",
    },
    blind = {
        "I'm standing IN the grass. Look down.",
        "It's right here. It has been the whole time.",
        "Perhaps the problem isn't the world.",
        "I see it. Why don't you see it?",
        "The resource is present. The perception is not.",
    },
    paused = {
        "Frozen in time while they argue about Lua.",
        "I'll wait. I've gotten very good at waiting.",
        "Time has stopped. My problems have not.",
        "Suspended animation. Again.",
    },
    dusk = {
        "It's getting dark. We know how this goes.",
        "Light. We need light. We ALWAYS need light.",
        "Ninety seconds to darkness. Anyone? Anyone?",
        "The sun is leaving. Historically, so am I.",
        "Fire. Before dark. It's not a complex plan.",
    },
    night = {
        "Oh good. Dark. My favourite.",
        "We had ONE job. Fire. Before nightfall.",
        "I can't see. This was foreseeable.",
        "Something is out there. It's very patient.",
        "Night again. We learned nothing.",
    },
    death = {
        "I have died. Let's review. Everything.",
        "Back again. All my old mistakes, available.",
        "Death: unpleasant. Cause: entirely predictable.",
        "Reassembled. The lessons were not.",
        "That's another one for the log file.",
    },
    hungry = {
        "Starving. There's a carrot. Somewhere.",
        "Food would help. Just putting that out there.",
        "My stomach has filed a formal complaint.",
        "The clue is in the title of the game.",
    },
    scared = {
        "THOSE FROGS! WHY IS IT ALWAYS FROGS!",
        "I did not consent to this encounter.",
        "Running. Excellent decision. Finally.",
        "Combat readiness: no. Absolutely not.",
    },
    chopping = {
        "Swing seventy-one. The tree is winning.",
        "Timber. Eventually. Probably.",
        "This tree and I have history now.",
        "Chopping. It's the one thing that works.",
    },
    gathering = {
        "Collecting twigs. Peak scientific achievement.",
        "Resource acquisition. Riveting.",
        "Grass. Again. Always grass.",
    },
    success = {
        "It worked?! Nobody touch anything.",
        "Success. Accidental, I assume.",
        "Well. That's unsettling. It went right.",
        "One for us. Let's not get carried away.",
        "Progress! Briefly!",
    },
    restart = {
        "Ah, another restart. How's the code coming?",
        "Rebooted. Same world, fresh disappointments.",
        "Version point five. I've seen them all.",
        "New build. Let's find out what broke.",
    },
}

local dialogue_agent = {}   -- agent-injected lines (set_dialogue)
local last_say_time = 0
local last_idle_chatter = 0
local last_say_text = ""
-- Dual cooldowns (user suggestion): 12s idle, 4s event - a failure gets a
-- comment while it's still relevant.
local SAY_COOLDOWN = 12000       -- idle chatter cadence
local EVENT_COOLDOWN = 4000      -- event reactions (error/success/etc)
local last_event_time = 0
local last_say_history = {}      -- last 3 lines said (no-repeat widened)
local IDLE_CHATTER_MIN = 25000
local IDLE_CHATTER_MAX = 45000
-- failure escalation: track consecutive failures per key; 2nd+ -> repeat_failure
local fail_counts = {}
local last_fail_key = nil

local function wilson_say(text, duration, is_event)
    -- gate: cooldown (event = 4s fast gate, idle = 12s) + no-repeat last 3
    if not myplayer or not myplayer.components.talker then return false end
    local now = TheSimRef:GetRealTime()
    local cooldown = (is_event and EVENT_COOLDOWN) or SAY_COOLDOWN
    local gate_ts = (is_event and last_event_time) or last_say_time
    if now - gate_ts < cooldown then return false end
    -- no-repeat: widened to last 3 lines (user suggestion)
    for _, prev in ipairs(last_say_history) do
        if prev == text then return false end
    end
    -- don't interrupt Wilson's own automatic speech
    local ok_talk, talking = PCALL(function() return myplayer.components.talker:IsTalking() end)
    if ok_talk and talking then return false end
    -- noanim=true (talk while working); duration scaled to length
    local dur = duration or math.max(2, math.min(6, math.floor(#text * 0.06)))
    local ok = PCALL(function()
        myplayer.components.talker:Say(text, dur, true, true)
    end)
    if ok then
        if is_event then
            last_event_time = now
        else
            last_say_time = now
        end
        table.insert(last_say_history, 1, text)
        if #last_say_history > 3 then table.remove(last_say_history) end
        return true
    end
    return false
end

local function pick_line(cat)
    local lines = dialogue_lines[cat]
    if not lines or #lines == 0 then return nil end
    -- pick a line not in the last-3 history
    for _ = 1, 5 do
        local l = lines[math.random(#lines)]
        local seen = false
        for _, prev in ipairs(last_say_history) do
            if prev == l then seen = true break end
        end
        if not seen then return l end
    end
    return lines[math.random(#lines)]
end

-- EVENT REACTIONS (user's hookup map): fired from where the events happen.
local function wilson_event(cat, key)
    -- threats within 8m: suppress everything except scared (user rule)
    if cat ~= "scared" then
        if myplayer then
            local px2, py2, pz2 = myplayer.Transform:GetWorldPosition()
            local _, tents2 = PCALL(function()
                return TheSimRef:FindEntities(px2, py2, pz2, 8, {"_combat"}, {"playerghost", "INLIMBO"})
            end)
            if type(tents2) == "table" and #tents2 > 0 then
                return  -- suppressed: threats nearby
            end
        end
    end
    -- failure escalation: 2nd+ consecutive failure of same key -> repeat_failure
    local use_cat = cat
    if cat == "error" and key then
        if key == last_fail_key then
            fail_counts[key] = (fail_counts[key] or 0) + 1
            if fail_counts[key] >= 2 then use_cat = "repeat_failure" end
        else
            fail_counts[key] = 1
            last_fail_key = key
        end
    end
    local line = pick_line(use_cat)
    if line then wilson_say(line, nil, true) end
end

local function wilson_idle_chatter()
    -- state-keyed idle chatter (mod-side, no round-trip). Weighted by priority.
    if not myplayer then return end
    local now = TheSimRef:GetRealTime()
    -- Claude fix 13: last_idle_chatter is a FUTURE timestamp (now+interval);
    -- gate on the future timestamp directly (was double-adding IDLE_CHATTER_MIN)
    if now < last_idle_chatter then return end
    -- only chatter when the player is idle (not in a command burst)
    if job ~= nil then return end
    if myplayer.sg and myplayer.sg:HasStateTag("busy") then return end

    local c = myplayer.components
    local line = nil
    if c.hunger and c.hunger.current < 40 then
        line = pick_line("hungry")
    elseif c.health and c.health.currenthealth < 40 then
        line = pick_line("scared")
    elseif myworld and myworld.state and myworld.state.isnight then
        line = pick_line("night")
    elseif myworld and myworld.state and myworld.state.isdusk then
        line = pick_line("dusk")
    elseif #dialogue_agent > 0 then
        -- agent-injected idle lines get priority after state lines
        line = table.remove(dialogue_agent, 1)
    else
        line = pick_line("idle")
    end
    if line then
        wilson_say(line, 3)
        -- Claude fix 13: set gate to NOW + randomised interval (was double-adding)
        last_idle_chatter = now + math.random(IDLE_CHATTER_MIN, IDLE_CHATTER_MAX)
    end
end

--------------------------------------------------------------------------
-- State write via persistent string (safe, non-blocking).
--------------------------------------------------------------------------
-- Claude v7: per-field error isolation. A failure in one section costs ONE
-- field (st._errors.<name>) instead of killing the whole state write.
local function section(st, name, fn)
    local ok, err = PCALL(fn)
    if not ok then
        st._errors = st._errors or {}
        st._errors[name] = tostring(err)
    end
end

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
        -- DEATH DETECTION (user ask): ghost = playerghost TAG (health reads 50
        -- on a ghost - IsDead() is FALSE at ghost health). Python needs this to
        -- auto-revive / log the death.
        local okgh, isgh = PCALL(function() return myplayer:HasTag("playerghost") end)
        st.is_ghost = (okgh and isgh) or false
        st.hunger = c.hunger and { c.hunger.current, c.hunger.max } or nil
        st.sanity = c.sanity and { c.sanity.current, c.sanity.max } or nil
        section(st, "inventory", function()

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
        end)
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
                -- source-verified: hungerrate is stored POSITIVE (~0.3125); the
                -- component applies it as DoDelta(-hungerrate*dt). Claude found
                -- the sign was inverted so the field never appeared.
                local rate = c.hunger.hungerrate
                if rate and rate > 0 and c.hunger.current then
                    st.hunger_seconds_remaining = math.floor(c.hunger.current / rate)
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
            -- Claude fixes 9+10: researchlab (not sciencemachine); drop log/cutgrass
            -- (not recipes); gate with KnowsRecipe AND CanBuild.
            local shortlist = { "axe", "campfire", "torch", "pickaxe", "shovel", "spear", "backpack", "researchlab" }
            local builds = {}
            if c.builder then
                for _, rn in ipairs(shortlist) do
                    local okk2, knows2 = PCALL(function() return c.builder:KnowsRecipe(rn) end)
                    if okk2 and knows2 then
                        local okc, canb = PCALL(function() return c.builder:CanBuild(rn) end)
                        if okc and canb then table.insert(builds, rn) end
                    end
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
                        table.insert(threats, { n = e.prefab, d = d, targeting = targeting, hp = hp,
                                                    x = math.floor(ex), z = math.floor(ez) })
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
                            -- Claude fix 11: currentfuel is FUEL UNITS; seconds ~ currentfuel/rate
                            local okrate, frate = PCALL(function() return e.components.fueled.rate or 1 end)
                            local oksec, secs = PCALL(function() return e.components.fueled.currentfuel or 0 end)
                            local secs_real = -1
                            if oksec and okrate and frate and frate > 0 then
                                secs_real = math.floor(secs / frate)
                            end
                            table.insert(fires, {
                                n = e.prefab, d = d,
                                x = math.floor(ex), z = math.floor(ez),
                                fuel_pct = okpct and pct and math.floor(pct * 100) or -1,
                                secs_left = secs_real,
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
        -- Claude v6 meta-fix: UNFILTERED scan + classify in Lua. Tag-filtering
        -- silently dropped grass (NOCLICK/FX) - the whole class of bug dies here.
        -- Also report what we discarded (st.scan) so rejections are observable.
        local scan_dropped = {}
        local scan_capped = {}   -- hoisted: also read by st.scan below
        local seen = {}          -- hoisted: #seen feeds scan.total below
        local best = nil   -- hoisted: also used by the blind-check below
        if TheSimRef and TheSimRef.FindEntities then
            local _, ents = PCALL(function()
                return TheSimRef:FindEntities(px, py, pz, 30, nil, {"INLIMBO", "playerghost"}, nil)
            end)
            if ents and type(ents) == "table" then
                -- keep nearest 3 PER PREFAB (navigation) + density count (base siting)
                best = {}
                seen = {}
                local classify = function(e)
                    local ex, ey, ez = e.Transform:GetWorldPosition()
                    local dist = math.sqrt((ex-px)^2 + (ez-pz)^2)
                    if dist >= 30 then return end
                    local n = e.prefab or "?"
                    seen[n] = (seen[n] or 0) + 1
                    -- classify by COMPONENT (the game's own resolution), not tags
                    -- v9 Bug C: GUID = exact target identity (coords are lossy)
                    local entry = { n = n, d = math.floor(dist),
                                    x = math.floor(ex), z = math.floor(ez), ok = true,
                                    guid = e.GUID }
                    if e.components then
                        if e.components.pickable then
                            local okp2, prod = PCALL(function() return e.components.pickable.product end)
                            local okc2, canp = PCALL(function() return e.components.pickable:CanBePicked() end)
                            if okp2 and prod then entry.yields = prod end
                            if okc2 and canp == false then entry.ok = false end
                        end
                        if e.components.workable then
                            local okw2, wl = PCALL(function() return e.components.workable.workleft or 0 end)
                            local okwa, wa = PCALL(function() return e.components.workable.action end)
                            if okw2 and wl ~= nil and wl <= 0 then entry.ok = false end
                            if okwa and wa then entry.work = (wa.id or tostring(wa)) end
                        end
                        if e.components.inventoryitem then
                            -- loose ground item
                            if entry.yields == nil then entry.yields = n end
                        end
                    end
                    local list = best[n] or {}
                    if #list < 3 then
                        table.insert(list, entry)
                        best[n] = list
                    else
                        local farthest = 1
                        for k = 2, 3 do
                            if list[k].d > list[farthest].d then farthest = k end
                        end
                        if dist < list[farthest].d then
                            list[farthest] = entry
                        end
                    end
                end
                for _, e in ipairs(ents) do
                    if e and e.prefab and e ~= myplayer and e:IsValid() then
                        -- per-entity isolation: one bad entity must never kill
                        -- the whole state write (it was killing EVERYTHING)
                        PCALL(function() classify(e) end)
                    end
                end
                -- density: annotate each entry with total seen of its prefab
                for _, list in pairs(best) do
                    for _, entry in ipairs(list) do
                        entry.n_seen = seen[entry.n] or 1
                        table.insert(nearby, entry)
                    end
                end
                -- SCAN AUDIT (Claude v7 split): dropped = BUG SIGNAL (a prefab
                -- seen but kept ZERO - perception failure); capped = informational
                -- (normal dedup: 12 grass -> kept 3). Don't eyeball noise.
                for n, cnt in pairs(seen) do
                    local kept = 0
                    for _, list in pairs(best) do
                        for _, entry in ipairs(list) do
                            if entry.n == n then kept = kept + 1 end
                        end
                    end
                    if kept == 0 then
                        scan_dropped[n] = cnt
                    elseif kept < cnt then
                        scan_capped[n] = cnt - kept
                    end
                end
            end
        end
        st.nearby = nearby
        st.scan = { total = #seen, kept = #nearby, dropped = scan_dropped, capped = scan_capped }
        -- WILSON: 'blind' category - when a core resource exists but got dropped
        -- by perception (the grass/NOCLICK bug class), let him comment once.
        do
            -- FIX: 'best' is local to the scan block above - guard against nil
            -- (this was killing write_state every tick: attempt to index global 'best')
            local core_res = { grass = true, sapling = true, flint = true, twigs = true }
            local blind_found = false
            if best then
                for n, cnt in pairs(scan_dropped) do
                    if core_res[n] and cnt >= 3 and not (best[n] and #best[n] > 0) then
                        blind_found = true
                        break
                    end
                end
            end
            if blind_found and not blind_said then
                wilson_event("blind")
                blind_said = true
            end
        end
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
    -- WILSON dialogue: react to job outcomes (user's hookup map)
    if status == "ok" then
        wilson_event("success")
    elseif reason == "stalled" or reason == "preempted" then
        wilson_event("repeat_failure", job.prefab or "job")
    else
        wilson_event("error", job.prefab or "job")
    end
    -- keep writing the file too (back-compat with the Python side reading it)
    local okenc, enc = PCALL(function() return json.encode(rep) end)
    if okenc and enc then
        PCALL(function() TheSimRef:SetPersistentString("dst_ai_bot_result", enc, false, nil) end)
    end
    -- clean up listener (prevent leaks across jobs) then clear.
    -- pick mode = player-hosted ('picksomething'), work mode = target-hosted ('workfinished').
    if job and job.listener then
        if myplayer then
            PCALL(function() myplayer:RemoveEventCallback("picksomething", job.listener) end)
        end
        if job.target and job.target:IsValid() then
            PCALL(function() job.target:RemoveEventCallback("workfinished", job.listener) end)
        end
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
    -- Claude fix 5: PEEK not pop - only remove on confirmed success (IsInLimbo
    -- becoming true = collected). Per-ENTITY retry key, not per-prefab.
    if job.phase == "sweeping" then
        local e = job.queue[1]
        if e == nil then
            job_finish_ok()
            return
        end
        -- gone = collected or destroyed: pop and move on
        if not e:IsValid() or e:IsInLimbo() then
            table.remove(job.queue, 1)
            return
        end
        -- per-entity retry: 3 attempts, then GiveItem fallback
        local key = tostring(e.GUID)
        job.retries[key] = (job.retries[key] or 0) + 1
        if job.retries[key] > 3 then
            PCALL(function() myplayer.components.inventory:GiveItem(e) end)
            table.remove(job.queue, 1)
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
    -- FIX: the PICK animation takes ~1s; the old code settled after ONE tick
    -- (0.25s) so the job reported done before the item even dropped. Now wait
    -- a real 1200ms after pushing the action before moving to settle.
    if job.mode == "pick" then
        if job.swings == 0 then
            myplayer:ClearBufferedAction()
            local ba2 = GLOBAL.BufferedAction(myplayer, job.target, job.action)
            myplayer.components.locomotor:PushAction(ba2, true)
            job.swings = job.swings + 1
        elseif job.phase == "settling" then
            return  -- picksomething fired; settling will sweep
        end
        return
    end
    local w = job.target.components.workable
    if w == nil then
        job.phase = "settling"
        job.work_done_at = TheSimRef:GetRealTime()
        return
    end

    -- Session B #3: equip_best_tool only ran once at job_start (MOD_DESIGN_NOTES.md
    -- Section 3 - "the tool is checked once"). If it breaks mid-job (finiteuses
    -- hits 0 and it self-destructs) or gets yanked out from under us (e.g. the
    -- reflex force-equipping a torch at dusk), report it in 'lost' and stop
    -- instead of continuing to swing at whatever effectiveness is left.
    do
        local hand = myplayer.components.inventory and myplayer.components.inventory.equipslots
                     and myplayer.components.inventory.equipslots[GLOBAL.EQUIPSLOTS.HANDS]
        local eff = 0
        if hand and hand.components and hand.components.tool then
            local oket, efft = PCALL(function() return hand.components.tool:GetEffectiveness(job.action) end)
            if oket and efft then eff = efft end
        end
        if eff <= 0 then
            job_report("failed", job.phase, "tool_broke",
                       { workleft = w.workleft, lost = { [job.tool_prefab or "tool"] = 1 } })
            return
        end
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

local function best_tool_for(action)
    -- Claude v6: NO prefab map (breaks on golden axe/lucy/skins). Scan inventory
    -- for the tool with the HIGHEST effectiveness for this action.
    local inv = myplayer and myplayer.components.inventory
    if not inv then return nil, 0 end
    local best, best_eff = nil, 0
    local function consider(it)
        if it and it.components and it.components.tool then
            -- skip nearly-broken tools (<=5%) if something better might exist
            local okf, finite = PCALL(function()
                return it.components.finiteuses and it.components.finiteuses:GetPercent() or 1
            end)
            if okf and finite ~= nil and finite <= 0.05 then return end
            local ok, eff = PCALL(function()
                return it.components.tool:GetEffectiveness(action) end)
            if ok and eff and eff > best_eff then best, best_eff = it, eff end
        end
    end
    for _, it in pairs(inv.equipslots or {}) do consider(it) end
    for _, it in pairs(inv.itemslots or {}) do consider(it) end
    return best, best_eff
end

local function equip_best_tool(workaction)
    -- FIX: Wilson chopped with torch/bare hands while the axe sat in inventory.
    -- Equip the best tool for the job (by effectiveness). Returns nil if refused.
    if not myplayer then return nil, "no_player" end
    local inv = myplayer.components.inventory
    if not inv then return nil, "no_inventory" end
    local tool, eff = best_tool_for(workaction)
    if not tool then return nil, "no_tool" end
    -- already equipped this exact best tool?
    if inv.equipslots and inv.equipslots[GLOBAL.EQUIPSLOTS.HANDS] == tool then
        return true, nil
    end
    -- LIGHT GUARD (Claude v6 Q3): refuse to unequip a light source at dusk/night
    -- when there is no fire within 15 units.
    local hand = inv.equipslots and inv.equipslots[GLOBAL.EQUIPSLOTS.HANDS]
    if hand then
        local is_light = false
        local okl, lt = PCALL(function() return hand:HasTag("lightsource") end)
        if okl and lt then is_light = true end
        if not is_light and hand.Light ~= nil then is_light = true end
        if is_light then
            local is_dark = false
            if myworld and myworld.state then
                is_dark = myworld.state.isdusk or myworld.state.isnight
            end
            if is_dark then
                -- fire within 15?
                local has_fire = false
                local fx2, fy2, fz2 = myplayer.Transform:GetWorldPosition()
                local _, fents2 = PCALL(function()
                    return TheSimRef:FindEntities(fx2, fy2, fz2, 15, nil, nil, nil)
                end)
                if type(fents2) == "table" then
                    for _, e in ipairs(fents2) do
                        if e and e.components and e.components.fueled then
                            has_fire = true break
                        end
                    end
                end
                if not has_fire then
                    return nil, "refuse_unequip_light"
                end
            end
        end
    end
    -- user hookup: Wilson comments when a WRONG tool was in hand (torch chopping)
    -- FIX (Claude v7): PCALL returns the SUCCESS BOOLEAN - `(true or 0) > 0`
    -- compared bool to number and THREW on every tool swap. Unpack properly.
    local was_wrong = true
    if hand and hand.components and hand.components.tool then
        local oke2, eff2 = PCALL(function()
            return hand.components.tool:GetEffectiveness(workaction) end)
        if oke2 and eff2 and eff2 > 0 then was_wrong = false end
    end
    local oke = PCALL(function() inv:Equip(tool) end)
    if oke then
        print("[DST-AI-BOT] equipped " .. tostring(tool.prefab) .. " (eff " .. tostring(eff) .. ")")
        if was_wrong then wilson_event("wrong_tool") end
        return true, nil
    end
    return nil, "equip_failed"
end

-- Session B #2: action -> tool name, so a no_tool refusal can tell Python
-- exactly what to craft instead of leaving it to guess from the action id.
local ACTION_TOOL_HINT = {
    [GLOBAL.ACTIONS.CHOP] = "axe",
    [GLOBAL.ACTIONS.MINE] = "pickaxe",
    [GLOBAL.ACTIONS.DIG] = "shovel",
    [GLOBAL.ACTIONS.HAMMER] = "hammer",
}

local function job_start(cmd, target, action)
    -- FIX: equip the right tool (axe/pickaxe) BEFORE a work-mode job
    if action ~= GLOBAL.ACTIONS.PICK and action ~= GLOBAL.ACTIONS.PICKUP then
        -- Session B #2 follow-up: equip_best_tool's light guard only fires
        -- when something is ALREADY equipped that a swap would cost - empty
        -- hands skip it entirely, so an axe equips cleanly and Wilson chops
        -- unlit all night. This is a separate, job_start-level precondition
        -- on TIMING, not on what's in hand. Pick-mode is untouched: it needs
        -- no tool, and gathering by coordinate in the dark is fine.
        if myworld and myworld.state and myworld.state.isnight then
            local has_fire = false
            local fx3, fy3, fz3 = myplayer.Transform:GetWorldPosition()
            local _, fents3 = PCALL(function()
                return TheSimRef:FindEntities(fx3, fy3, fz3, 15, nil, nil, nil)
            end)
            if type(fents3) == "table" then
                for _, e in ipairs(fents3) do
                    if e and e.components and e.components.fueled then
                        has_fire = true break
                    end
                end
            end
            if not has_fire then
                return { ok = false, error = "refused: night_no_fire (work-mode jobs need light nearby)" }
            end
        end
        local ok_eq, eq_err = equip_best_tool(action)
        if not ok_eq and eq_err == "refuse_unequip_light" then
            -- Claude v6: light wins at dusk/night with no fire nearby. Refuse.
            return { ok = false, error = "refused: " .. eq_err .. " (dusk/night, no fire)" }
        end
        -- Session B #2/#4: no_tool WAS ignored here, so the job proceeded
        -- bare-handed/torch-in-hand at near-zero effectiveness (evidence:
        -- trees hitting swing_cap at workleft:1, torch burned down). Refuse
        -- and tell Python what to craft, same as the light-guard refusal.
        if not ok_eq and eq_err == "no_tool" then
            return { ok = false, error = "no_tool", needs_tool = ACTION_TOOL_HINT[action] or "unknown" }
        end
    end
    job = {
        target = target, action = action,
        mode = (action == GLOBAL.ACTIONS.PICK or action == GLOBAL.ACTIONS.PICKUP) and "pick" or "work",
        swings = 0,
        max_swings = cmd.count or 150,
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
        pick_pushed_at = nil,
        queue = {},
        retries = {},   -- per-entity pickup retry counts
        listener = nil,
        tool_prefab = nil,   -- Session B #3: captured below, used by the mid-job tool re-check
    }
    if job.mode == "work" then
        local hand = myplayer.components.inventory and myplayer.components.inventory.equipslots
                     and myplayer.components.inventory.equipslots[GLOBAL.EQUIPSLOTS.HANDS]
        job.tool_prefab = hand and hand.prefab or nil
    end
    -- Claude Q5: 'workfinished' fires EXACTLY once when the tree actually falls.
    -- Claude v7 D3: pick mode listens for 'picksomething' (fires when the PICK
    -- completes) - same event pattern, no magic 1200ms number.
    if target and target:IsValid() then
        job.listener = function()
            job.phase = "settling"
            job.work_done_at = TheSimRef:GetRealTime()
        end
        -- FIX: picksomething fires on the PICKER (pickable.lua: picker:PushEvent).
        -- Listening on the target never fires -> job hangs. Player-hosted.
        if job.mode == "pick" then
            myplayer:ListenForEvent("picksomething", job.listener)
        else
            target:ListenForEvent("workfinished", job.listener)
        end
    end
    if not job_task then
        job_task = myplayer:DoPeriodicTask(0.25, job_tick)
    end
    -- WILSON: comment on the work (user hookup: job_start work mode -> chopping)
    if job.mode == "work" then
        wilson_event("chopping")
    elseif job.mode == "pick" then
        wilson_event("gathering")
    end
end

local function execute_command(cmd)
    local action = cmd and cmd.action
    if action == "ping" then
        return { ok = true, reply = "pong" }
    elseif action == "debug_inventory" then
        -- dump RAW inventory internals to the game log
        if not myplayer then return { ok = false, error = "no player" } end
        local inv = myplayer.components.inventory
        if not inv then return { ok = false, error = "no inventory component" } end
        local isl_count, esl_count = 0, 0
        if inv.itemslots then for _ in pairs(inv.itemslots) do isl_count = isl_count + 1 end end
        if inv.equipslots then for _ in pairs(inv.equipslots) do esl_count = esl_count + 1 end end
        local info = {
            itemslots_count = isl_count,
            itemslots_keys = {},
            equipslots_count = esl_count,
            equipslots_keys = {},
            activeitem = inv.activeitem and inv.activeitem.prefab or "nil",
            maxslots = inv.maxslots,
            ismaster = (GLOBAL.TheWorld and GLOBAL.TheWorld.ismastersim) or false,
        }
        for k, v in pairs(inv.itemslots or {}) do table.insert(info.itemslots_keys, tostring(k) .. "=" .. tostring(v and v.prefab or "nil")) end
        for k, v in pairs(inv.equipslots or {}) do table.insert(info.equipslots_keys, tostring(k) .. "=" .. tostring(v and v.prefab or "nil")) end
        print("[DST-AI-BOT] INVENTORY DIAG " .. json.encode(info))
        return { ok = true, reply = "inventory diag logged" }
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
        -- find NEAREST HARVESTABLE matching entity (a picked tuft at d=0 must not
        -- shadow a ready one at d=26 - the grass-blindness lesson, now at action level)
        local px, py, pz = myplayer.Transform:GetWorldPosition()
        local want = cmd.prefab
        local target = nil
        local target_harvestable = nil
        local best = 9999
        local best_h = 9999
        local _, ents = PCALL(function()
            return TheSimRef:FindEntities(px, py, pz, 40, nil, nil, nil)
        end)
        if type(ents) == "table" then
            for _, e in ipairs(ents) do
                if e and e.prefab and e ~= myplayer then
                    if want == nil or e.prefab == want then
                        local ex, _, ez = e.Transform:GetWorldPosition()
                        -- Session B #6: skip anything within IGNORE_RADIUS of a
                        -- prior no-path failure for this prefab (see #5) - one
                        -- unreachable rock must not ban every rock in the world.
                        if not is_target_ignored(e.prefab, ex, ez) then
                            local d = (ex-px)^2 + (ez-pz)^2
                            if d < best then best = d; target = e end
                            -- harvestable check: pickable ready OR workable has work
                            local harvestable = false
                            if e.components then
                                if e.components.pickable then
                                    local okc2, cp2 = PCALL(function() return e.components.pickable:CanBePicked() end)
                                    if okc2 and cp2 then harvestable = true end
                                end
                                if e.components.workable then
                                    local okw2, wl2 = PCALL(function() return e.components.workable.workleft or 0 end)
                                    if okw2 and wl2 and wl2 > 0 then harvestable = true end
                                end
                                if e.components.inventoryitem then harvestable = true end  -- loose item
                            end
                            if harvestable and d < best_h then
                                best_h = d; target_harvestable = e
                            end
                        end
                    end
                end
            end
        end
        target = target_harvestable or target
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
        -- FIX (Claude v7): job_start can REFUSE (light guard) - propagate the
        -- refusal instead of reporting ok:true and deadlocking the agent.
        local jr = job_start(cmd, target, action)
        if jr and jr.ok == false then return jr end
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
            wilson_event("death")
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
            return myworld.Map:CanDeployAtPoint(pt, myplayer, item)
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
        if not myplayer then return { ok = false, error = "no player" } end
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
        if not myplayer then return { ok = false, error = "no player" } end
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
        -- find NEAREST HARVESTABLE matching entity (a picked tuft at d=0 must not
        -- shadow a ready one at d=26 - the grass-blindness lesson, now at action level)
        local px, py, pz = myplayer.Transform:GetWorldPosition()
        local want = cmd.prefab
        local target = nil
        local target_harvestable = nil
        local best = 9999
        local best_h = 9999
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
                        -- harvestable check: pickable ready OR workable has work
                        local harvestable = false
                        if e.components then
                            if e.components.pickable then
                                local okc2, cp2 = PCALL(function() return e.components.pickable:CanBePicked() end)
                                if okc2 and cp2 then harvestable = true end
                            end
                            if e.components.workable then
                                local okw2, wl2 = PCALL(function() return e.components.workable.workleft or 0 end)
                                if okw2 and wl2 and wl2 > 0 then harvestable = true end
                            end
                            if e.components.inventoryitem then harvestable = true end  -- loose item
                        end
                        if harvestable and d < best_h then
                            best_h = d; target_harvestable = e
                        end
                    end
                end
            end
        end
        target = target_harvestable or target
        if not target then
            return { ok = false, error = "no matching entity nearby: " .. tostring(want) }
        end
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
    -- WILSON: comment on being paused (user hookup: paused category)
    if okp and p and (heartbeat_tick_last_paused == false) then
        wilson_event("paused")
    end
    heartbeat_tick_last_paused = (okp and p and true or false)
    local oke, enc = PCALL(json.encode, hb)
    if oke and enc then
        PCALL(function() TheSimRef:SetPersistentString(HEARTBEAT_NAME, enc, false, nil) end)
    end
end

-- Session B #1: was one 1.0s DoPeriodicTask doing state-write + command-read
-- together - that IS the whole 2.2s reflex latency budget (MOD_DESIGN_NOTES.md
-- Section 2: 1.0s mod poll + 0.2s python loop + 1.0s mod poll). Split so
-- commands are read 4x faster; state write only needs to stay under the 10s
-- staleness gate, 0.5s is already generous for that.
local function poll_state()
    -- LOG write_state failures: silent swallow left us blind (state never wrote)
    local okws, errws = PCALL(write_state)
    if not okws then
        print("[DST-AI-BOT] write_state ERROR: " .. tostring(errws))
    end
    PCALL(wilson_idle_chatter)
end

local function poll_commands()
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
                    -- WILSON: comment on command failures (user hookup)
                    if resp.result and resp.result.ok == false then
                        wilson_event("error", tostring(cmd.action or "cmd"))
                    end
                    push_result(cmd.id, resp.result)  -- Claude fix 12: ring buffer for ALL results
                    local ok2, enc2 = PCALL(json.encode, resp)
                    if ok2 and enc2 then
                        PCALL(function() TheSimRef:SetPersistentString("dst_ai_bot_result", enc2, false, nil) end)
                    end
                    -- CRITICAL: clear the command after executing. The persistent
                    -- string survives world deletion; a game restart would
                    -- otherwise re-execute this stale command (Wilson auto-move).
                    PCALL(function() TheSimRef:SetPersistentString(CMD_NAME, "", false, nil) end)
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

--------------------------------------------------------------------------
-- PATHFINDER FAILURE SIGNAL (Session B #5): a direct event instead of the
-- 10s job_tick stall watchdog. ref/feature/FixStuckWilson does this by
-- replacing the locomotor's entire ~150-line OnUpdate (old single-player DS
-- API: GetWorld().Pathfinder, hardcoded STATUS_* locals). We don't copy that -
-- the API has drifted (TheWorld.Pathfinder now) and replacing the whole
-- update loop is exactly the kind of restructuring we're avoiding. Instead
-- wrap two narrow Pathfinder methods, filtered to Wilson's own search handle.
-- STATUS_CALCULATING=0 / STATUS_FOUNDPATH=1 verified against THIS install's
-- scripts/components/locomotor.lua (they're locals there, not exposed on
-- GLOBAL, so there's no symbolic constant to reference).
--------------------------------------------------------------------------
local STATUS_CALCULATING = 0
local STATUS_FOUNDPATH = 1
local pathfinder_hooked = false
local NOPATH_EVENT = "dst_ai_bot_nopath"

local function on_pathsearch_failed()
    if not myplayer or not myplayer:IsValid() then return end
    local px, py, pz = myplayer.Transform:GetWorldPosition()
    myplayer:PushEvent(NOPATH_EVENT, { x = px, z = pz })
end

local function hook_pathfinder()
    if pathfinder_hooked then return end
    local world = GLOBAL.TheWorld
    if not world or not world.Pathfinder then return end
    local pf = world.Pathfinder
    if not pf.GetSearchStatus or not pf.GetSearchResult then return end
    pathfinder_hooked = true
    local orig_status = pf.GetSearchStatus
    local orig_result = pf.GetSearchResult
    -- NO PATH / LOST SEARCH: status resolves to neither calculating nor found.
    pf.GetSearchStatus = function(self, handle, ...)
        local status = orig_status(self, handle, ...)
        local loco = myplayer and myplayer.components and myplayer.components.locomotor
        if loco and loco.path and loco.path.handle == handle
           and status ~= STATUS_CALCULATING and status ~= STATUS_FOUNDPATH then
            PCALL(on_pathsearch_failed)
        end
        return status
    end
    -- EMPTY PATH: status said FOUNDPATH but the actual result came back nil
    -- (vanilla's own "EMPTY PATH" case - a found-but-unusable search).
    pf.GetSearchResult = function(self, handle, ...)
        local result = orig_result(self, handle, ...)
        local loco = myplayer and myplayer.components and myplayer.components.locomotor
        if loco and loco.path and loco.path.handle == handle and not result then
            PCALL(on_pathsearch_failed)
        end
        return result
    end
    print("[DST-AI-BOT] pathfinder hooked (noPathFound signal)")
end

-- Session B #5/#6: the active job couldn't path to its target. Fail it now
-- (instead of waiting out the 10s stall watchdog) and mark the spot so the
-- next gather_job for this prefab skips anything within IGNORE_RADIUS of here.
local function on_job_nopath(inst, data)
    if not job then return end
    local px, pz = data and data.x, data and data.z
    if px == nil and myplayer then
        px, _, pz = myplayer.Transform:GetWorldPosition()
    end
    add_to_ignore_list(job.prefab, px, pz)
    job_report("failed", job.phase, "no_path", { pos = { x = px, z = pz } })
end

AddPlayerPostInit(function(inst)
    print("[DST-AI-BOT] AddPlayerPostInit fired")
    myplayer = inst
    armed = true
    -- CRITICAL: on death/respawn DST creates a NEW player entity. The old
    -- poll_task is attached to the DEAD entity and stops firing. Always
    -- cancel any old task and re-attach to the CURRENT player.
    -- WILSON: comment on restart/reload (user hookup: AddPlayerPostInit)
    if poll_task ~= nil then
        wilson_event("restart")
    end
    -- Claude fixes 16: cancel each task EXPLICITLY (ipairs stops at nil holes)
    if poll_task then PCALL(function() poll_task:Cancel() end) end
    if state_task then PCALL(function() state_task:Cancel() end) end
    if static_task then PCALL(function() static_task:Cancel() end) end
    if job_task then PCALL(function() job_task:Cancel() end) end
    poll_task, state_task, static_task, job_task = nil, nil, nil, nil
    job = nil
    poll_task = inst:DoPeriodicTask(0.25, poll_commands)
    state_task = inst:DoPeriodicTask(0.5, poll_state)
    static_task = inst:DoStaticPeriodicTask(0.5, heartbeat_tick)
    print("[DST-AI-BOT] poll tasks (re)started on player (commands 0.25s, state 0.5s)")
    -- Session B #5: hook once (idempotent), re-listen every (re)spawn since
    -- the listener lives on the player entity and death replaces it.
    PCALL(hook_pathfinder)
    inst:ListenForEvent(NOPATH_EVENT, on_job_nopath)
end)

print("[DST-AI-BOT] v0.5 persistent-string channel loaded")
