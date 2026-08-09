#!/usr/bin/env python3
"""
lib/targets.py — TargetTracker (v9 Bug C fix).

Coordinates are lossy identity; two saplings can share a rounded position.
GUIDs are exact. The tracker remembers what we fired at and never retries a
target (harvested OR failed) during the same roam — that was the x3 retry loop.
"""
import time


class TargetTracker:
    def __init__(self):
        self.in_flight = None      # guid currently being worked
        self.done = set()          # guids harvested or failed this roam
        self._cleared_at = time.time()

    def pick(self, state):
        """Nearest harvestable entity not done and not in flight."""
        try:
            for e in sorted((state or {}).get("nearby", []), key=lambda e: e.get("d", 99)):
                g = e.get("guid")
                if g is None:
                    continue                  # mod without guid support: skip (safe)
                if g in self.done:
                    continue
                if g == self.in_flight:
                    continue
                if not e.get("ok", True):
                    continue
                return e
        except Exception:
            pass
        return None

    def fired(self, entity):
        try:
            self.in_flight = entity.get("guid")
        except Exception:
            self.in_flight = None

    def reported(self, job_result):
        """Absorb failures too: gained:{} means don't retry this roam."""
        if self.in_flight is not None:
            self.done.add(self.in_flight)
            self.in_flight = None

    def clear(self):
        """End of roam (or ~3 min): regrown resources become available again."""
        self.done = set()
        self.in_flight = None
        self._cleared_at = time.time()

    def maybe_clear(self, max_age_s=180):
        """Auto-clear after max_age_s so regrown tufts are retryable."""
        if time.time() - self._cleared_at > max_age_s:
            self.clear()
