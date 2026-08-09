#!/usr/bin/env python3
"""
lib/run_log.py — append-only JSONL run history (Task 2).

The single most important measurement: "is the bot getting better?"
One line per run in data/runs.jsonl. Never raises.
"""
import os
import json
import time
from datetime import datetime

_RUNS = {}          # run_id -> {started_at, mod_version, notes}
_CLOSED = set()     # run_ids already written (idempotency)
_DATA_DIR = None    # set lazily from the project root


def _data_dir():
    global _DATA_DIR
    if _DATA_DIR is None:
        _DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(_DATA_DIR, exist_ok=True)
    return _DATA_DIR


def _runs_path():
    return os.path.join(_data_dir(), "runs.jsonl")


def _safe(v, default):
    return v if v is not None else default


def start_run(mod_version: str, notes: str = "") -> str:
    """Begin a run. Returns a unique run_id. Holds state in memory."""
    base = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
    run_id = base
    n = 1
    # _CLOSED holds (run_id, bucket) tuples since Session A2 T5 - a string
    # `in` check would never match. Check the first element of each key.
    while run_id in _RUNS or any(k[0] == run_id for k in _CLOSED):
        run_id = f"{base}-{n}"
        n += 1
    _RUNS[run_id] = {
        "started_at": int(time.time()),
        "mod_version": _safe(mod_version, ""),
        "notes": _safe(notes, ""),
    }
    return run_id


def end_run(run_id: str, final_state: dict, cause: str, extra: dict = None) -> bool:
    """Close out a run and append ONE line to data/runs.jsonl. True on success.
    Idempotent: a run_id is only ever written once (v9 fix - the try/except/
    finally triple-call produced double entries)."""
    try:
        # Session A2 T4: a falsy run_id must never poison _CLOSED - give it
        # a distinct generated id before the guard.
        if not run_id:
            run_id = f"orphan-{int(time.time())}-{id(final_state) & 0xFFFF}"
        # Session A2 T5: dedupe on (run_id, 10s bucket). A run can die many
        # times, but not twice within ten seconds - and a flapping is_ghost
        # read within 2s must record once. (Keying on run_id alone dropped
        # every death after the first; discarding the key recorded the same
        # death twice. The bucket keeps both ends honest.)
        dedupe_key = (run_id, int(time.time()) // 10)
        if dedupe_key in _CLOSED:
            return False
        _CLOSED.add(dedupe_key)
        meta = _RUNS.pop(run_id, None)
        fs = final_state if isinstance(final_state, dict) else {}
        ex = extra if isinstance(extra, dict) else {}

        started_at = (meta or {}).get("started_at", int(time.time()))
        ended_at = int(time.time())
        line = {
            "run_id": run_id,
            "started_at": started_at,
            "ended_at": ended_at,
            "duration_s": max(0, ended_at - started_at),
            "mod_version": (meta or {}).get("mod_version", ""),
            "day_reached": int(fs.get("day") or 0),
            "season": fs.get("season") or "",
            "cause": _safe(cause, "unknown"),
            "final_health": _safe(fs.get("health"), None),
            "final_hunger": _safe(fs.get("hunger"), None),
            "structures": list(ex.get("structures") or []),
            "notes": (meta or {}).get("notes", ""),
        }
        # health/hunger may be [current, max] lists - keep the current value
        if isinstance(line["final_health"], list) and line["final_health"]:
            line["final_health"] = line["final_health"][0]
        if isinstance(line["final_hunger"], list) and line["final_hunger"]:
            line["final_hunger"] = line["final_hunger"][0]

        with open(_runs_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(line) + "\n")
        return True
    except Exception:
        return False


def _read_runs():
    """Read all runs, skipping corrupt lines. Returns list of dicts."""
    runs = []
    path = _runs_path()
    if not os.path.exists(path):
        return runs
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    runs.append(json.loads(line))
                except (ValueError, TypeError):
                    continue  # corrupt line: skip, not fatal
    except OSError:
        return runs
    return runs


def summarize(n: int = 20) -> str:
    """Plain-text table of the last n runs + summary stats."""
    runs = _read_runs()
    if not runs:
        return "no runs recorded"
    runs = runs[-n:]

    rows = []
    for r in runs:
        run_id = r.get("run_id", "?")
        ver = r.get("mod_version", "")
        day = r.get("day_reached", 0)
        cause = r.get("cause", "?")
        dur = r.get("duration_s", 0)
        dur_s = f"{dur // 60}m" if dur >= 60 else f"{dur}s"
        rows.append(f"  {run_id:<20} {ver:<4} {day:>3}  {cause:<12} {dur_s:>6}")

    days = [r.get("day_reached", 0) for r in runs]
    causes = [r.get("cause", "?") for r in runs]
    best = max(days) if days else 0
    median = sorted(days)[len(days) // 2] if days else 0
    most_cause = max(set(causes), key=causes.count) if causes else "?"

    table = "\n".join(rows)
    return (
        f"  run_id               ver  day  cause        dur\n"
        f"{table}\n"
        f"\n"
        f"  Runs: {len(runs)}   Best day: {best}   Median day: {median}   "
        f"Most common cause: {most_cause}"
    )
