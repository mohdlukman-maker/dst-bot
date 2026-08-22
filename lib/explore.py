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

    def next_target(self, state, base_xz=None, max_leash=200, target_prefab=None):
        """Nearest unvisited cell within leash, biased outward from base.
        v10.6: Uses land_dirs to avoid ocean cells. Expands radius when
        inner cells exhausted. If target_prefab given, heads toward known
        resource locations from the world map."""
        try:
            pos = state.get("pos") or {}
            x, z = pos.get("x", 0), pos.get("z", 0)
            here = cell_of(x, z)

            # v10.6: If looking for a specific resource, check the world map first
            if target_prefab:
                try:
                    from lib import world_map as wm
                    hits = wm.find("default", target_prefab, near_xz=(x, z), limit=5) or []
                    for hit in hits:
                        hx, hz = hit.get("x", 0), hit.get("z", 0)
                        d = ((hx - x) ** 2 + (hz - z) ** 2) ** 0.5
                        if d > 10:  # not right here — go to it
                            return (round(hx, 1), round(hz, 1))
                except Exception:
                    pass

            # v10.6: Read land_dirs to avoid ocean cells
            land_dirs = state.get("land_dirs") or []
            on_water = state.get("on_water") or False

            # heading bias: push away from base (straight-line outward)
            heading_dx, heading_dz = 0, 0
            if base_xz:
                bdx = x - base_xz[0]
                bdz = z - base_xz[1]
                bmag = (bdx * bdx + bdz * bdz) ** 0.5 or 1
                heading_dx, heading_dz = bdx / bmag, bdz / bmag

            # v10.6: Dynamic radius — expand when inner cells exhausted
            for search_radius in (8, 12, 16, 20):
                best, best_score = None, 1e18
                found_any = False
                for dq in range(-search_radius, search_radius + 1):
                    for dr in range(-search_radius, search_radius + 1):
                        c = (here[0] + dq, here[1] + dr)
                        if c in self.visited:
                            continue
                        found_any = True
                        cx, cz = (c[0] + 0.5) * CELL, (c[1] + 0.5) * CELL
                        if not self.within_leash((cx, cz), base_xz, max_leash):
                            continue
                        dx = cx - x
                        dz = cz - z
                        dist = (dx * dx + dz * dz) ** 0.5
                        # v10.6: Skip cells in directions that have no land (ocean)
                        if land_dirs:
                            cell_dir = ""
                            if abs(dx) > abs(dz):
                                cell_dir = "east" if dx > 0 else "west"
                            else:
                                cell_dir = "south" if dz > 0 else "north"
                            # If we're at the shore and the cell direction has no land, skip
                            if on_water or dist < 5:
                                pass  # at shore, need to pick ANY direction
                            elif cell_dir not in land_dirs and dist > 15:
                                continue  # this direction is ocean — skip
                        # alignment with heading (outward direction)
                        align = (dx * heading_dx + dz * heading_dz) if (heading_dx or heading_dz) else 0
                        score = dist - align * 0.6
                        if score < best_score:
                            best_score = score
                            best = (round(cx, 1), round(cz, 1))
                if best:
                    return best
                if not found_any:
                    continue  # all visited in this radius — expand
            # all radii exhausted — pick a random land direction
            if land_dirs:
                import random
                dir_map = {"east": (1, 0), "west": (-1, 0), "south": (0, 1), "north": (0, -1)}
                valid = [dir_map[d] for d in land_dirs if d in dir_map]
                if valid:
                    dx, dz = random.choice(valid)
                    return (round(x + dx * 60, 1), round(z + dz * 60, 1))
            return None
        except Exception:
            return None
