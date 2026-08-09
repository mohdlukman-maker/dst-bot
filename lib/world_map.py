#!/usr/bin/env python3
"""
lib/world_map.py — permanent spatial memory (Task 4).

One JSON file per world at data/maps/<world_key>.json. The bot forgets the
world every restart; this fixes that. Never raises.
"""
import os
import json
import time


def _maps_dir():
    d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "maps")
    os.makedirs(d, exist_ok=True)
    return d


def _path(world_key):
    key = (world_key or "default").replace(os.sep, "_").replace("/", "_")
    return os.path.join(_maps_dir(), f"{key}.json")


def _load(world_key):
    try:
        with open(_path(world_key), "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return {"entries": {}, "base": None}


def _save(world_key, data):
    try:
        with open(_path(world_key), "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1)
        return True
    except OSError:
        return False


def _key_of(prefab, x, z):
    return f"{prefab}|{round(x)}|{round(z)}"


def observe(world_key: str, state: dict) -> int:
    """Merge state["nearby"] into the stored map. Returns # NEW entries added."""
    try:
        if not isinstance(state, dict):
            return 0
        nb = state.get("nearby")
        if not isinstance(nb, list):
            return 0
        data = _load(world_key)
        entries = data["entries"]
        day = state.get("day")
        ts = int(time.time())
        new_count = 0
        for e in nb:
            if not isinstance(e, dict):
                continue
            prefab = e.get("n") or e.get("prefab")
            x = e.get("x")
            z = e.get("z")
            if not prefab or not isinstance(x, (int, float)) or not isinstance(z, (int, float)):
                continue
            x, z = float(x), float(z)
            # merge rule: same prefab within 2 units = same object
            key = _key_of(prefab, x, z)
            found = False
            for k, ent in entries.items():
                if ent.get("prefab") != prefab:
                    continue
                if abs(ent.get("x", 9e9) - x) <= 2 and abs(ent.get("z", 9e9) - z) <= 2:
                    ent["last_seen_ts"] = ts
                    if day is not None:
                        ent["last_seen_day"] = day
                    found = True
                    break
            if not found:
                entries[key] = {
                    "prefab": prefab,
                    "x": round(x, 1),
                    "z": round(z, 1),
                    "last_seen_day": day,
                    "last_seen_ts": ts,
                }
                new_count += 1
        _save(world_key, data)
        return new_count
    except Exception:
        return 0


def find(world_key: str, prefab: str, near_xz: tuple = None, limit: int = 5) -> list:
    """Stored locations of a prefab, nearest first if near_xz given."""
    try:
        data = _load(world_key)
        out = []
        for ent in data["entries"].values():
            if ent.get("prefab") == prefab:
                out.append(dict(ent))
        if near_xz and len(near_xz) == 2:
            nx, nz = float(near_xz[0]), float(near_xz[1])
            out.sort(key=lambda e: (e.get("x", 0) - nx) ** 2 + (e.get("z", 0) - nz) ** 2)
        return out[:limit]
    except Exception:
        return []


def set_base(world_key: str, x: float, z: float) -> bool:
    try:
        data = _load(world_key)
        data["base"] = {"x": round(float(x), 1), "z": round(float(z), 1)}
        return _save(world_key, data)
    except Exception:
        return False


def get_base(world_key: str) -> dict:
    try:
        data = _load(world_key)
        b = data.get("base")
        return b if isinstance(b, dict) else {}
    except Exception:
        return {}


def stats(world_key: str) -> dict:
    """{"total": N, "prefabs": {"grass": 88, ...}}"""
    try:
        data = _load(world_key)
        prefabs = {}
        for ent in data["entries"].values():
            p = ent.get("prefab", "?")
            prefabs[p] = prefabs.get(p, 0) + 1
        return {"total": len(data["entries"]), "prefabs": prefabs}
    except Exception:
        return {"total": 0, "prefabs": {}}
