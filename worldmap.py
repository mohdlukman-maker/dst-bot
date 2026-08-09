#!/usr/bin/env python3
"""
WORLD MAP - persistent memory of the DST world (Claude P0.4).
Every state read merges nearby[] into a per-world map file keyed by world seed.
Turns "wander until you find grass" into "walk to the grass at (412, -88)."

Map structure (JSON):
{
  "seed": <world day at first sight>,
  "landmarks": {"pigking": [x,z], "beefalo": [[x,z],...], "rocks": [[x,z],...]},
  "resources": {"grass": [[x,z,last_seen_day],...], "sapling": [...], ...},
  "hazards": {"pond": [[x,z]], "spiderden": [[x,z]]},
  "base": [x,z] | null
}
"""
import os, json, time

MAP_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "world_map.json")

# What counts as landmarks vs resources vs hazards
LANDMARKS = {"pigking", "beefalo", "pig_house", "rocky", "mosaic", "desert",
             "cactus", "tumbleweed", "reeds", "cave_entrance", "wormhole", "pond"}
HAZARDS = {"pond", "spiderden", "spider_hole", "tentacle", "walrus_camp", "houndmound"}
RESOURCES = {"grass", "sapling", "twiggytree", "flint", "rock1", "rock2", "boulder",
             "evergreen", "deciduoustree", "berrybush", "carrot_planted", "reeds",
             "cactus", "tumbleweed", "blue_mushroom", "green_mushroom", "red_mushroom"}

def load():
    if os.path.exists(MAP_FILE):
        try:
            with open(MAP_FILE, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"landmarks": {}, "resources": {}, "hazards": {}, "base": None, "created": time.time()}

def save(m):
    with open(MAP_FILE, "w", encoding="utf-8") as f:
        json.dump(m, f, indent=1)

def _merge_point(cat, name, x, z, day):
    m = load()
    bucket = m[cat]
    if name not in bucket:
        bucket[name] = []
    # dedupe by proximity (within 5 units = same spot)
    for entry in bucket[name]:
        if abs(entry[0] - x) < 5 and abs(entry[1] - z) < 5:
            return m  # already known
    bucket[name].append([x, z, day])
    save(m)
    return m

def record_state(st):
    """Merge a state.json read into the map. Call on every state poll."""
    day = st.get("day", 0)
    m = load()
    changed = False
    for e in st.get("nearby") or []:
        n, x, z = e.get("n"), e.get("x"), e.get("z")
        if not n or x is None or z is None:
            continue
        cat = None
        if n in HAZARDS: cat = "hazards"
        elif n in RESOURCES: cat = "resources"
        elif n in LANDMARKS: cat = "landmarks"
        if not cat: continue
        bucket = m[cat]
        if n not in bucket: bucket[n] = []
        known = False
        for entry in bucket[n]:
            if abs(entry[0] - x) < 5 and abs(entry[1] - z) < 5:
                known = True
                break
        if not known:
            bucket[n].append([x, z, day])
            changed = True
    # player position is a hint of explored area - not stored per se
    if changed:
        save(m)
    return m

def nearest(cat, name, px, pz):
    """Nearest known point of a resource type. Returns [x,z,day] or None."""
    m = load()
    pts = m.get(cat, {}).get(name, [])
    best, bd = None, 1e9
    for p in pts:
        d = (p[0]-px)**2 + (p[1]-pz)**2
        if d < bd:
            bd = d; best = p
    return best

def summary():
    m = load()
    out = {"landmarks": {}, "resources": {}, "hazards": {}}
    for cat in ("landmarks", "resources", "hazards"):
        for k, v in m.get(cat, {}).items():
            out[cat][k] = len(v)
    out["base"] = m.get("base")
    return out

if __name__ == "__main__":
    print(json.dumps(summary(), indent=1))
