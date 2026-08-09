#!/usr/bin/env python3
"""
lib/lessons.py — turn refuted predictions into readable rules (Task 5).

Read data/decisions.jsonl, group refuted records by (action.action,
action.prefab), emit lesson lines for groups with 3+ refutations.
Never raises.
"""
import os
import json
from datetime import datetime

_DATA_DIR = None


def _data_dir():
    global _DATA_DIR
    if _DATA_DIR is None:
        _DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
    return _DATA_DIR


def _decisions_path():
    return os.path.join(_data_dir(), "decisions.jsonl")


def _lessons_path():
    return os.path.join(_data_dir(), "lessons.md")


def _read_decisions():
    out = []
    path = _decisions_path()
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


def build_lessons() -> str:
    """Group refuted decisions by (action.action, action.prefab).
    Groups with 3+ refutations become lesson lines. Writes data/lessons.md."""
    try:
        decisions = _read_decisions()
        total = len(decisions)
        refuted = [d for d in decisions if d.get("verdict") == "refuted"]

        groups = {}
        for d in refuted:
            action = d.get("action") or {}
            a = action.get("action") or "?"
            p = action.get("prefab") or ""
            key = (a, p)
            groups.setdefault(key, []).append(d)

        # bucket by count
        high = []   # 5+
        med = []    # 3-4
        for key, recs in groups.items():
            action_name, prefab = key
            label = f"{action_name}" + (f" {prefab}" if prefab else "")
            # most common expectation
            exp_counts = {}
            for r in recs:
                exp = r.get("expected") or {}
                e_str = json.dumps(exp) if isinstance(exp, dict) else str(exp)
                exp_counts[e_str] = exp_counts.get(e_str, 0) + 1
            common_exp = max(exp_counts, key=exp_counts.get) if exp_counts else ""
            try:
                common_exp = json.loads(common_exp) if common_exp else ""
            except (ValueError, TypeError):
                pass
            last_day = max((r.get("day") or 0 for r in recs), default=0)
            entry = {
                "label": label,
                "count": len(recs),
                "exp": common_exp,
                "last_day": last_day,
            }
            if len(recs) >= 5:
                high.append(entry)
            elif len(recs) >= 3:
                med.append(entry)

        high.sort(key=lambda e: -e["count"])
        med.sort(key=lambda e: -e["count"])

        now = datetime.now().strftime("%Y-%m-%dT%H:%M:%S")
        lines = ["# Learned Lessons",
                 f"_generated {now} from {total} decisions_", ""]
        if not high and not med:
            lines.append("_no patterns yet_")
        else:
            if high:
                lines.append("## High confidence (5+ refutations)")
                for e in high:
                    exp_s = json.dumps(e["exp"]) if isinstance(e["exp"], dict) else str(e["exp"])
                    lines.append(f"- `{e['label']}` failed {e['count']} times. "
                                 f"Most common expectation: `{exp_s}`. Last seen day {e['last_day']}.")
                lines.append("")
            if med:
                lines.append("## Medium confidence (3-4 refutations)")
                for e in med:
                    exp_s = json.dumps(e["exp"]) if isinstance(e["exp"], dict) else str(e["exp"])
                    lines.append(f"- `{e['label']}` failed {e['count']} times. "
                                 f"Most common expectation: `{exp_s}`. Last seen day {e['last_day']}.")
                lines.append("")
        content = "\n".join(lines).rstrip() + "\n"
        try:
            with open(_lessons_path(), "w", encoding="utf-8") as f:
                f.write(content)
        except OSError:
            pass
        return content
    except Exception:
        return "# Learned Lessons\n\n_no patterns yet_\n"
