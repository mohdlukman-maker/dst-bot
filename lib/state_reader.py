#!/usr/bin/env python3
"""
lib/state_reader.py — safe state + heartbeat reading (Task 1).

The mod writes KLEI-format files (header + JSON). This module is the ONLY
place that parses them, so freshness and errors are handled identically
everywhere. Never raises; always returns a dict.
"""
import os
import re
import time
import json

# module-level memory for heartbeat "advancing" detection
_HEARTBEAT_HISTORY = {}

KLEI_RE = re.compile(r"^KLEI\s+\d+\s+(.*)$", re.DOTALL)


def _read_payload(path):
    """Read a file, strip the KLEI header, return the raw text or None."""
    try:
        with open(path, "rb") as f:
            raw = f.read()
        if not raw:
            return None
        text = raw.decode("utf-8", errors="replace").strip()
        if not text:
            return None
    except OSError:
        return None
    m = KLEI_RE.match(text)
    return m.group(1) if m else text


def _parse_json(text):
    """Parse JSON text, return (obj, error_str) — never raises."""
    try:
        return json.loads(text), ""
    except (ValueError, TypeError) as e:
        return None, str(e)


def read_state(save_dir: str, max_age_s: float = 5.0) -> dict:
    """
    Read and parse dst_ai_bot_state.

    Returns a dict ALWAYS. Never raises.

    {
      "ok":     bool,  # True only if parsed AND fresh
      "fresh":  bool,  # age_s <= max_age_s
      "age_s":  float, # seconds since state["timestamp"], or -1 if unknown
      "state":  dict,  # the parsed state, or {} on failure
      "errors": dict,  # copy of state["_errors"] if the mod reported any, else {}
      "reason": str,   # "" if ok, else why not: "missing" / "unreadable" /
                       # "bad_json" / "stale" / "no_timestamp"
    }
    """
    out = {
        "ok": False,
        "fresh": False,
        "age_s": -1.0,
        "state": {},
        "errors": {},
        "reason": "",
    }
    path = os.path.join(save_dir, "dst_ai_bot_state")
    if not os.path.exists(path):
        out["reason"] = "missing"
        return out
    text = _read_payload(path)
    if text is None:
        out["reason"] = "unreadable"
        return out
    obj, err = _parse_json(text)
    if obj is None or not isinstance(obj, dict):
        out["reason"] = "bad_json"
        return out

    out["state"] = obj
    # mod-reported per-section failures: partial state is still usable
    if isinstance(obj.get("_errors"), dict):
        out["errors"] = dict(obj["_errors"])

    ts = obj.get("timestamp")
    if not isinstance(ts, (int, float)):
        out["reason"] = "no_timestamp"
        return out
    out["age_s"] = max(0.0, time.time() - float(ts))
    out["fresh"] = out["age_s"] <= max_age_s
    if out["fresh"]:
        out["ok"] = True
        out["reason"] = ""
    else:
        out["reason"] = "stale"
    return out


def read_heartbeat(save_dir: str) -> dict:
    """
    Returns: {"ok": bool, "paused": bool, "sim_ts": float|None,
              "heartbeat_ts": float|None, "verdict": str}

    verdict: "running" | "paused" | "dead" | "unknown"
    Advancing = value changed since the previous call (module-level memory).
    """
    res = {
        "ok": False,
        "paused": False,
        "sim_ts": None,
        "heartbeat_ts": None,
        "verdict": "unknown",
    }
    path = os.path.join(save_dir, "dst_ai_bot_heartbeat")
    if not os.path.exists(path):
        res["verdict"] = "dead" if _HEARTBEAT_HISTORY else "unknown"
        return res
    text = _read_payload(path)
    if text is None:
        res["verdict"] = "dead" if _HEARTBEAT_HISTORY else "unknown"
        return res
    obj, _ = _parse_json(text)
    if obj is None or not isinstance(obj, dict):
        res["verdict"] = "dead" if _HEARTBEAT_HISTORY else "unknown"
        return res

    hb_ts = obj.get("heartbeat_ts")
    sim_ts = obj.get("sim_ts")
    paused = bool(obj.get("paused", False))
    res["ok"] = True
    res["paused"] = paused
    res["sim_ts"] = sim_ts
    res["heartbeat_ts"] = hb_ts

    prev = _HEARTBEAT_HISTORY
    hb_adv = prev.get("heartbeat_ts") is None or (isinstance(hb_ts, (int, float)) and hb_ts != prev["heartbeat_ts"])
    sim_adv = prev.get("sim_ts") is None or (isinstance(sim_ts, (int, float)) and sim_ts != prev["sim_ts"])

    _HEARTBEAT_HISTORY["heartbeat_ts"] = hb_ts
    _HEARTBEAT_HISTORY["sim_ts"] = sim_ts

    if not hb_adv and prev.get("heartbeat_ts") is not None:
        res["verdict"] = "dead"
    elif paused:
        res["verdict"] = "paused"
    elif not sim_adv and prev.get("sim_ts") is not None:
        res["verdict"] = "paused"
    elif hb_adv and sim_adv:
        res["verdict"] = "running"
    else:
        res["verdict"] = "unknown"
    return res
