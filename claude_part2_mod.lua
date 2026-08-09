# DST AI Bot — PART 2: the full mod file (modmain.lua) for review

Please review this Lua mod (runs inside DST, sandboxed env: pairs/ipairs/print/math/table/type/string/tostring/require/Class/GLOBAL/modname/MODROOT + Add* hooks; use GLOBAL.* for pcall/TheSim/etc). Suggest specific upgrades: bugs, robustness, better state to expose, dialogue implementation, job-runner improvements. It currently: writes state.json via TheSim:SetPersistentString (KLEI files), reads command.json, executes move_to/say/eat/gather/craft/revive/gather_job/preempt_job.

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
        if TheSimRef and TheSimRef.FindEntities then
            local _, ents = PCALL(function()
                return TheSimRef:FindEntities(px, py, pz, 40, nil, nil, nil)
            end)
            if ents and type(ents) == "table" then
                if #ents > 0 then
                    print("[DST-AI-BOT] FindEntities returned " .. tostring(#ents) .. " ents")
                end
                local NOISE = { rain=true, snow=true, pollen=true, lunarhail=true,
                                 lightning=true, groundpoundfx=true, fx=true }
                for _, e in ipairs(ents) do
                    if e and e.prefab and e ~= myplayer and not NOISE[e.prefab] then
                        local ex, ey, ez = e.Transform:GetWorldPosition()
                        local dist = math.sqrt((ex-px)^2 + (ez-pz)^2)
                        if dist < 40 then
                            local ok = true
                            local okc, canpick = PCALL(function()
                                if e.components and e.components.pickable then
                                    return e.components.pickable:CanBePicked()
                                end
                                return nil
                            end)
                            if okc and canpick == false then ok = false end
                            -- workable: is there work left?
                            local okw, wl = PCALL(function()
                                if e.components and e.components.workable then
                                    return e.components.workable.workleft or 0
                                end
                                return nil
                            end)
                            if okw and wl ~= nil and wl <= 0 then ok = false end
                            table.insert(nearby, { n = e.prefab, d = math.floor(dist),
                                                   x = math.floor(ex), z = math.floor(ez),
                                                   ok = ok })
                        end
                    end
                end
            else
                print("[DST-AI-BOT] FindEntities returned nil/non-table: " .. tostring(ents))
            end
        else
            print("[DST-AI-BOT] FindEntities NOT AVAILABLE")
        end
        st.nearby = nearby
    end
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
    evergreen = { "log", "pinecone" },
    deciduoustree = { "log", "acorn" },
    twiggytree = { "twigs", "log" },
    grass = { "cutgrass" },
    sapling = { "twigs" },
    berrybush = { "berries" },
    carrot_planted = { "carrot" },
    flint = { "flint" },
    rock1 = { "rocks", "flint" },
    rock2 = { "rocks", "flint" },
    boulder = { "rocks", "flint" },
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
    local okenc, enc = PCALL(function() return json.encode(rep) end)
    if okenc and enc then
        PCALL(function() TheSimRef:SetPersistentString("dst_ai_bot_result", enc, false, nil) end)
    end
    -- clear the job
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

local function job_sweep_pickup()
    -- Find loose items of the expected drop prefabs near the player and PICKUP each.
    if not myplayer then return {} end
    local px, py, pz = myplayer.Transform:GetWorldPosition()
    local expected = job.drops or {}
    expected[job.prefab] = true   -- also pick up the target itself (e.g. log)
    local picked = {}
    local okg, gents = PCALL(function()
        return TheSimRef:FindEntities(px, py, pz, 8, {"_inventoryitem"}, {"INLIMBO", "FX", "NOCLICK"})
    end)
    if okg and type(gents) == "table" then
        for _, e in ipairs(gents) do
            if e and e.prefab and expected[e.prefab] then
                local okp, res = PCALL(function()
                    local ba = GLOBAL.BufferedAction(myplayer, e, GLOBAL.ACTIONS.PICKUP)
                    myplayer:ClearBufferedAction()
                    myplayer.components.locomotor:PushAction(ba, true)
                    return true
                end)
                if okp and res then
                    picked[e.prefab] = (picked[e.prefab] or 0) + 1
                end
            end
        end
    end
    return picked
end

local function job_tick()
    if not job or not myplayer then return end

    -- PREEMPT: a higher-priority command (e.g. reflex eat) may cancel us
    if job.preempted then
        job_report("preempted", job.phase, "preempted")
        return
    end

    -- target gone?
    if job.target == nil or not job.target:IsValid() then
        job_report("failed", job.phase, "target_gone")
        return
    end

    -- workable done? (tree felled, rock mined)
    local w = job.target.components.workable
    if w == nil or w.workleft <= 0 then
        job.phase = "collected"
        -- sweep pickup
        local picked = job_sweep_pickup()
        -- verify vs snapshot
        local after = job_snapshot_counts()
        local gained = {}
        for nm, cnt in pairs(after) do
            local before = job.snapshot[nm] or 0
            if cnt > before then gained[nm] = cnt - before end
        end
        job_report("ok", "collected", "worked_down", { gained = gained, picked_up = picked })
        return
    end

    -- still swinging? wait (also enforce min 1.2s between swings)
    if myplayer:GetBufferedAction() ~= nil then return end
    if job.last_swing and (TheSimRef:GetRealTime() - job.last_swing) < 1200 then return end

    -- stall watchdog: workleft not moving == unreachable/no tool/broken tool
    if w.workleft == job.last_workleft then
        job.stall_ticks = job.stall_ticks + 1
        if job.stall_ticks > 40 then
            job_report("failed", job.phase, "stalled", { workleft = w.workleft })
            return
        end
    else
        job.last_workleft = w.workleft
        job.stall_ticks = 0
    end

    if job.swings >= job.max_swings and w.workleft > 3 then
        job_report("failed", job.phase, "swing_cap", { workleft = w.workleft })
        return
    end

    -- swing! (record last swing time so we don't spam while animating)
    myplayer:ClearBufferedAction()
    local ba = GLOBAL.BufferedAction(myplayer, job.target, job.action)
    myplayer.components.locomotor:PushAction(ba, true)
    job.swings = job.swings + 1
    job.last_swing = TheSimRef:GetRealTime()
end

local function job_start(cmd, target, action)
    job = {
        target = target, action = action,
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
    }
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
        -- if a job is running, preempt it first
        if job then job.preempted = true end
        job_start(cmd, target, action)
        return { ok = true, reply = "job started: " .. want }

    elseif action == "preempt_job" then
        if job then job.preempted = true end
        return { ok = true, reply = "job preempted" }

    elseif action == "revive" then
        -- Revive Wilson from ghost (source-verified: ThePlayer:PushEvent("respawnfromghost"))
        if not myplayer then return { ok = false, error = "no player" } end
        if myplayer.components.health and myplayer.components.health:IsDead() then
            myplayer:PushEvent("respawnfromghost")
            return { ok = true, reply = "revive requested" }
        end
        return { ok = true, reply = "not dead" }

    elseif action == "say" then
        if myplayer and myplayer.components.talker then
            myplayer.components.talker:Say(cmd.text or "")
        end
        return { ok = true, reply = "said" }

    elseif action == "gather" then
        -- Find nearest entity matching cmd.prefab and push the game's action
        -- using the SAME proven pattern as eat (BufferedAction + PushAction).
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
        -- DIAG: are we on the server (offline host) or a remote client?
        local tw = GLOBAL.TheWorld
        print("[DST-AI-BOT] mastersim check: myplayer.ismastersim=", tostring(myplayer.ismastersim),
              "| GLOBAL.TheWorld=", tostring(tw),
              "| TheWorld.ismastersim=", tostring(tw and tw.ismastersim or "nil"))
        -- Determine the right action from the target's components (source-verified):
        local acts = GLOBAL.ACTIONS or {}
        local action = nil
        if target.components.pickable ~= nil and target.components.pickable:CanBePicked() then
            action = acts.PICK
        elseif target.components.inventoryitem ~= nil then
            action = acts.PICKUP
        elseif target.components.workable ~= nil then
            local wt = target.components.workable:GetWorkAction()
            action = wt or acts.CHOP
        else
            action = acts.PICK
        end
        if not action then return { ok = false, error = "no action for " .. tostring(target.prefab) } end
        -- Claude's fix: PushAction(act, true) = the game pathfinds into range THEN
        -- performs the action (exactly what a real mouse click does). No manual
        -- GoToPoint — it conflicts with the buffered action's own walk-in.
        local BA = GLOBAL.BufferedAction
        local ba = BA(myplayer, target, action)
        myplayer:ClearBufferedAction()
        myplayer.components.locomotor:PushAction(ba, true)
        return { ok = true, reply = "interact " .. tostring(action.id or action) .. " " .. target.prefab }

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
        local knows = PCALL(function() return builder:KnowsRecipe(recipe) end)
        if knows ~= true then return { ok = false, error = "does not know recipe: " .. recipe } end
        -- Source-verified: Builder:DoBuild(recname, pt, rotation, skin) is the
        -- actual build function (checks ingredients, consumes them, spawns item).
        -- MakeRecipe only queues a UI buffered action and does NOT build.
        local okb, resb = PCALL(function()
            return builder:DoBuild(recipe)
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
    if poll_task then
        local okc, errc = PCALL(function() poll_task:Cancel() end)
        poll_task = nil
    end
    poll_task = inst:DoPeriodicTask(1.0, poll)
    print("[DST-AI-BOT] poll task (re)started on player")
end)

print("[DST-AI-BOT] v0.5 persistent-string channel loaded")

```
