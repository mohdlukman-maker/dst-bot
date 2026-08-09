#!/usr/bin/env python3
"""
lib/explore.py — visited-grid frontier explorer (v9 Bug B fix).

Random-direction exploring cycled 4 spots forever. This remembers every cell
it has seen (persisted per world) and picks the nearest unvisited cell within
the leash. It cannot cycle, respects the leash, and "None" means "everything
reachable is explored" — a real answer, not a bug.
"""
import os
import json
import math


CELL = 20   # units per grid cell


def cell_of(x, z):
    return (int(x // CELL), int(z // CELL))


class Explorer:
    def __init__(self, world_key="default"):
        self.world_key = world_key
        self.visited = set()
        self._load()

    def _path(self):
        d = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "data", "explore")
        os.makedirs(d, exist_ok=True)
        return os.path.join(d, f"{self.world_key}.json")

    def _load(self):
        try:
            with open(self._path(), "r", encoding="utf-8") as f:
                data = json.load(f)
            self.visited = {tuple(c) for c in data.get("visited", [])}
        except Exception:
            self.visited = set()

    def save(self):
        try:
            with open(self._path(), "w", encoding="utf-8") as f:
                json.dump({"visited": [list(c) for c in self.visited]}, f)
        except Exception:
            pass

    def observe(self, state):
        """Mark the current cell + all visible entity cells as visited."""
        try:
            pos = state.get("pos") or {}
            x, z = pos.get("x", 0), pos.get("z", 0)
            self.visited.add(cell_of(x, z))
            for e in state.get("nearby", []):
                if isinstance(e.get("x"), (int, float)) and isinstance(e.get("z"), (int, float)):
                    self.visited.add(cell_of(e["x"], e["z"]))
        except Exception:
            pass

    def within_leash(self, xz, base_xz, max_leash):
        try:
            if not base_xz:
                return True
            d = math.sqrt((xz[0] - base_xz[0]) ** 2 + (xz[1] - base_xz[1]) ** 2)
            return d <= max_leash
        except Exception:
            return True

    def next_target(self, state, base_xz=None, max_leash=200):
        """Nearest unvisited cell within leash. None = all explored in range."""
        try:
            pos = state.get("pos") or {}
            x, z = pos.get("x", 0), pos.get("z", 0)
            here = cell_of(x, z)

            # seed: a known resource spot from the world map (if we had one here)
            # (world_map integration is done by the caller; we just do frontier)
            best, best_score = None, 1e18
            for dq in range(-8, 9):
                for dr in range(-8, 9):
                    c = (here[0] + dq, here[1] + dr)
                    if c in self.visited:
                        continue
                    cx, cz = (c[0] + 0.5) * CELL, (c[1] + 0.5) * CELL
                    if not self.within_leash((cx, cz), base_xz, max_leash):
                        continue
                    d = ((cx - x) ** 2 + (cz - z) ** 2) ** 0.5
                    if d < best_score:
                        best, best_score = (round(cx, 1), round(cz, 1)), d
            return best
        except Exception:
            return None
