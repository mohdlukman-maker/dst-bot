#!/usr/bin/env python3
# TODO (Session A, deferred by design): cap/slim the _DECISIONS dict.
# _DECISIONS stores the full `before` state (incl. nearby[]) and only
# log_outcome pops entries - any path that never closes a decision leaks
# a snapshot. Do NOT fix with a flat "field: value" digest: _get_path
# splits on dots and walks NESTED dicts ("item_counts.log" resolves as
# state["item_counts"]["log"]), so a flat key returns None for every
# lookup, every expectation scores inconclusive, and the learning data
# goes quietly worthless. If slimmed, keep the same nested shape the
# expectations reference (or change _get_path to match). Leak magnitude:
# a few hundred snapshots over a long run - acceptable for now.
"""
lib/decision_log.py — falsifiable predictions (Task 3).

Record what you expect BEFORE acting, compare AFTER. Wrong predictions are
the training data. Append-only JSONL at data/decisions.jsonl. Never raises.
"""
import os
import json
import time

_DECISIONS = {}   # decision_id -> pending record (before outcome)
_COUNTER = 0
_DATA_DIR = None


def _data_dir():
    global _DATA_DIR
    if _DATA_DIR is None:
        _DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    os.makedirs(_DATA_DIR, exist_ok=True)
    return _DATA_DIR


def _path():
    return os.path.join(_data_dir(), "decisions.jsonl")


def _get_path(state, path):
    """Dot-path lookup: "item_counts.log" -> state["item_counts"]["log"].
    Missing -> None."""
    if not isinstance(state, dict):
        return None
    cur = state
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def _eval_expectation(exp, before, after):
    """Evaluate one expectation against before/after states.
    Returns True (held), False (refuted), None (inconclusive)."""
    # split "field: +N" style? No - the spec uses {"item_counts.log": "+1"}
    if not isinstance(exp, dict) or len(exp) != 1:
        return None
    field, expr = next(iter(exp.items()))
    expr = str(expr).strip()
    b = _get_path(before, field)
    a = _get_path(after, field)

    if expr in ("increases", "decreases", "changes", "unchanged"):
        if expr == "changes":
            if b is None and a is None:
                return None
            return (b != a) if (b is not None or a is not None) else None
        if expr == "unchanged":
            if b is None and a is None:
                return None
            return (b == a) if (b is not None or a is not None) else None
        # numeric increases/decreases; missing counts as 0
        bv = b if isinstance(b, (int, float)) else 0
        av = a if isinstance(a, (int, float)) else 0
        if expr == "increases":
            return av > bv
        if expr == "decreases":
            return av < bv
        return None

    # +/- N forms
    if expr.startswith("+") or expr.startswith("-"):
        try:
            n = float(expr[1:])
        except (ValueError, TypeError):
            return None
        bv = b if isinstance(b, (int, float)) else 0
        av = a if isinstance(a, (int, float)) else 0
        if expr.startswith("+"):
            return av >= bv + n
        else:
            return av <= bv - n

    return None


def log_decision(run_id: str, state: dict, goal: str, action: dict,
                 why: str, expected: dict) -> str:
    """Record a decision BEFORE acting. Returns a decision_id. "" on failure."""
    global _COUNTER
    try:
        _COUNTER += 1
        did = f"d-{_COUNTER:06d}"
        _DECISIONS[did] = {
            "decision_id": did,
            "run_id": run_id or "",
            "ts": int(time.time()),
            "day": (state or {}).get("day"),
            "phase": (state or {}).get("phase"),
            "goal": goal or "",
            "action": action or {},
            "why": why or "",
            "expected": expected or {},
            "before": state or {},
        }
        return did
    except Exception:
        return ""


def log_outcome(decision_id: str, state_after: dict) -> str:
    """Close a decision by comparing state_after vs the recorded expectation.
    Returns the verdict: confirmed / refuted / inconclusive / ""."""
    try:
        rec = _DECISIONS.pop(decision_id, None)
        if rec is None:
            return ""
        before = rec.get("before") or {}
        after = state_after if isinstance(state_after, dict) else {}
        expected = rec.get("expected") or {}

        verdict = "confirmed"
        results = []
        if isinstance(expected, dict):
            for field, expr in expected.items():
                r = _eval_expectation({field: expr}, before, after)
                if r is None:
                    results.append("inconclusive")
                elif r:
                    results.append("confirmed")
                else:
                    results.append("refuted")
        if not results:
            verdict = "inconclusive"
        elif "refuted" in results:
            verdict = "refuted"
        elif "confirmed" in results:
            verdict = "confirmed"
        else:
            verdict = "inconclusive"

        record = {
            "decision_id": decision_id,
            "run_id": rec.get("run_id", ""),
            "ts": rec.get("ts", int(time.time())),
            "day": rec.get("day"),
            "phase": rec.get("phase"),
            "goal": rec.get("goal", ""),
            "action": rec.get("action", {}),
            "why": rec.get("why", ""),
            "expected": expected,
            "observed": {f: _get_path(after, f) for f in (expected.keys() if isinstance(expected, dict) else [])},
            "verdict": verdict,
        }
        with open(_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        return verdict
    except Exception:
        return ""


def _read_decisions():
    out = []
    path = _path()
    if not os.path.exists(path):
        return out
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except (ValueError, TypeError):
                    continue
    except OSError:
        return out
    return out


def refuted(run_id: str = None, limit: int = 50) -> list:
    """Refuted decision records, newest first. Optionally filtered by run."""
    recs = _read_decisions()
    out = [r for r in recs if r.get("verdict") == "refuted"]
    if run_id:
        out = [r for r in out if r.get("run_id") == run_id]
    out.sort(key=lambda r: r.get("ts", 0), reverse=True)
    return out[:limit]
