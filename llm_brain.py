#!/usr/bin/env python3
"""
LLM BRAIN (Option A - the user's direction, 2026-08-11):
"AI should decide the strategy and initiate instructions as JSON according to
in-game state. Give 5 instructions at once; while Wilson executes them, queue
another set - no lagging in the game."

This module is the DECISION layer. It reads the game state (from the channel
file), summarizes it, asks DeepSeek for the next 5 commands, and returns them
as a JSON list. Called by llm_agent.py on a background thread so the queue
never waits on the LLM.

Safety: this module only PROPOSES. llm_agent.py validates each command before
sending (no gather at night without light, no walking into tentacle nests,
etc). reflex.py remains the instant emergency layer.
"""

import os, re, json, time, urllib.request, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

# ---- DeepSeek config (key from the shared .env, never printed) ----
def _api_key():
    env_path = os.path.join(os.path.expanduser("~"), "AppData", "Local", "hermes", ".env")
    try:
        for line in open(env_path, encoding="utf-8"):
            line = line.strip()
            if line.startswith("DEEPSEEK_API_KEY="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return os.environ.get("DEEPSEEK_API_KEY", "")

API_URL = "https://api.deepseek.com/chat/completions"
MODEL = "deepseek-chat"
MAX_TOKENS = 700
TIMEOUT = 40

SYSTEM_PROMPT = """You are the BRAIN of Wilson, a character in Don't Starve Together (DST).
A Lua mod inside the game is Wilson's HANDS - it executes your commands exactly.
A separate reflex daemon handles INSTANT emergencies (fleeing hostiles, eating when
starving) - never issue those, the reflex beats you to it.

GAME FACTS (verified):
- Day cycle: day=10 segments (~300s), dusk=4 (~120s), night=2 (~60s).
- NIGHT KILLS: darkness drains sanity fast (164->0 in ~50s) then health.
  Wilson MUST have a light source equipped (torch) or a lit fire nearby at night.
- Torch = 2 cutgrass + 2 twigs. Campfire = 3 cutgrass + 2 logs.
- Axe = 1 twigs + 1 flint. Pickaxe = 2 twigs + 2 flint.
- Food: berries/carrot/seeds. Flowers restore sanity when picked.
- Grass/saplings regrow ~1-2 min after picking. Trees need ~10-19 swings with an axe.
- Wilson starts 150 health / 150 hunger / 200 sanity. Hunger drains ~1/sec.
- STRATEGY (user-defined, 2026-08-11):
  1. COLLECT EVERYTHING while walking - grass, twigs, flint, berries, carrots,
     seeds, FLOWERS (sanity), and loose ground items.
  2. Then trigger the plan: torch -> axe -> campfire.
  3. Once a campfire is crafted, STAY at it all night - no wandering until day.
     Eat food while waiting.
  4. Repeat day after day until Wilson dies, then the game quits.

COMMAND FORMAT - respond with ONLY a JSON array of up to 5 commands, e.g.:
[{"action":"move_to","x":10,"z":20},
 {"action":"gather_job","prefab":"grass","count":3},
 {"action":"craft","recipe":"axe"},
 {"action":"equip","item":"torch"},
 {"action":"eat","item":"carrot"}]

AVAILABLE ACTIONS:
- move_to: {"x":..,"z":..} - walk to coordinates
- gather_job: {"prefab":"grass|sapling|flint|evergreen|berrybush|carrot_planted|seeds|flower","count":3}
- craft: {"recipe":"axe|pickaxe|torch|spear|campfire"}
- equip: {"item":"torch|axe|pickaxe|spear"}
- eat: {"item":"berries|carrot|seeds"}
- say: {"text":"short Wilson line, <=60 chars"} - flavor only, use sparingly

PAST LESSONS (learned from Wilson's previous deaths - DO NOT repeat):
{lessons}

RULES:
- Check the state's phase: day/dusk/night. Priority #1 is NEVER be dark at night.
- If dusk and no torch equipped and no fire: torch/campfire prep is the ONLY task.
- If night: do NOT move_to or gather. Hold position near a fire. Eat if hungry.
- If a threat is within 10m and hostile: the reflex handles fleeing - just don't walk
  TOWARD the threat.
- hunger < 40: eating/foraging food outranks everything except light.
- health < 40: same.
- Prefer nearby items (d<=20) over far ones. Walk to the nearest needed resource.
- Think like a survivalist: what does Wilson need MOST in the next 60 seconds?
- NEVER output more than 5 commands. NEVER output anything but the JSON array."""


def summarize_state(st):
    """Compact, LLM-friendly state summary - only what matters for decisions."""
    pos = st.get("pos") or {}
    nearby = []
    for e in (st.get("nearby") or [])[:12]:
        try:
            nearby.append(f"{e.get('n')}@({e.get('x'):.0f},{e.get('z'):.0f}) d={e.get('d'):.0f}{'' if e.get('ok') else '!'}")
        except Exception:
            nearby.append(str(e.get('n')))
    ground = [f"{g.get('n')}@({g.get('x'):.0f},{g.get('z'):.0f})" for g in (st.get("ground_items") or [])[:6]]
    threats = [f"{t.get('n')} d={t.get('d')}" for t in (st.get("threats") or []) if t.get("d", 99) < 25]
    fires = [f"{f.get('n')} d={f.get('d')} fuel={f.get('fuel_pct')}%" for f in (st.get("fires") or [])]
    def _pair(v, default):
        try:
            if isinstance(v, (list, tuple)) and len(v) >= 2:
                return (v[0], v[1])
            if isinstance(v, (int, float)):
                return (v, default)
        except Exception:
            pass
        return (default, default)

    h = _pair(st.get("health"), 150)
    hu = _pair(st.get("hunger"), 150)
    sa = _pair(st.get("sanity"), 200)
    counts = st.get("item_counts") or {}

    lines = [
        f"day={st.get('day')} phase={st.get('phase')} season={st.get('season')}",
        f"health={h[0]:.0f}/{h[1]} hunger={hu[0]:.0f}/{hu[1]} sanity={sa[0]:.0f}/{sa[1]}",
        f"pos=({pos.get('x',0):.0f},{pos.get('z',0):.0f})",
        f"inventory={counts}",
        f"equipped={st.get('equipped')}",
        f"nearby={nearby}",
        f"ground_items={ground}",
        f"fires={fires}",
        f"threats={threats}",
        f"is_busy={st.get('is_busy')}",
    ]
    if st.get("seconds_until_night") is not None:
        lines.append(f"seconds_until_night={st.get('seconds_until_night')}")
    if st.get("seconds_until_dusk") is not None:
        lines.append(f"seconds_until_dusk={st.get('seconds_until_dusk')}")
    # v2 ('push it further'): world-map awareness - known resource spots from
    # past exploration, beyond the 25m sensor. Lets the brain CHOOSE where to
    # walk when nothing is nearby (the old rules explored randomly).
    try:
        from lib import world_map as wm
        known = []
        for prefab in ("grass", "sapling", "flint", "berrybush", "carrot_planted", "seeds"):
            for hit in (wm.find("default", prefab, near_xz=(pos.get("x", 0), pos.get("z", 0)), limit=2) or []):
                dk = math_hypot(hit.get("x", 0) - pos.get("x", 0), hit.get("z", 0) - pos.get("z", 0))
                if 25 < dk < 250:
                    known.append(f"{prefab}@({hit.get('x'):.0f},{hit.get('z'):.0f}) d={dk:.0f}")
        if known:
            known.sort(key=lambda s: float(s.split("d=")[1]))
            lines.append("known_map=" + ", ".join(known[:8]))
    except Exception:
        pass
    return "\n".join(lines)


def math_hypot(a, b):
    return (a * a + b * b) ** 0.5


def load_lessons(limit=6):
    """Pull the most recent survival lessons (death causes, fixes) so the
    brain doesn't repeat past mistakes. Reads knowledge/lessons.md."""
    try:
        p = os.path.join(HERE, "knowledge", "lessons.md")
        txt = open(p, encoding="utf-8").read()
        # take the last N section headers + their first lines
        sections = []
        cur = []
        for line in txt.splitlines():
            if line.startswith("## "):
                if cur:
                    sections.append(cur)
                cur = [line]
            elif cur and line.strip() and len(cur) < 6:
                cur.append(line.strip())
        if cur:
            sections.append(cur)
        out = []
        for sec in sections[-limit:]:
            out.append(" | ".join(sec)[:220])
        return "\n".join(out)
    except Exception:
        return "No prior lessons."


def ask_for_commands(st, results=None, last_verdict=""):
    """Ask the LLM for the next batch of commands. Returns list of dicts.
    Returns [] on any failure (caller keeps queue running on old commands)."""
    key = _api_key()
    if not key:
        return []
    state_txt = summarize_state(st)
    user = f"STATE:\n{state_txt}"
    if results:
        recent = [json.dumps(r)[:150] for r in results[-4:]]
        user += f"\n\nRECENT RESULTS:\n" + "\n".join(recent)
    if last_verdict:
        user += f"\n\nLAST BATCH VERDICT: {last_verdict}"
    user += "\n\nWhat should Wilson do next? (JSON array of up to 5 commands)"

    # NOTE: .replace() NOT .format() - the prompt contains literal JSON braces
    # ({"action":...}) that .format() would try to interpret as placeholders
    # (KeyError: '"action"' - found live 2026-08-11).
    sys_prompt = SYSTEM_PROMPT.replace("{lessons}", load_lessons())
    payload = {
        "model": MODEL,
        "messages": [
            {"role": "system", "content": sys_prompt},
            {"role": "user", "content": user},
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.4,
        "response_format": {"type": "json_object"},
    }
    try:
        req = urllib.request.Request(
            API_URL,
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {key}"},
        )
        t0 = time.time()
        resp = json.loads(urllib.request.urlopen(req, timeout=TIMEOUT).read())
        content = resp["choices"][0]["message"]["content"]
        content = content.strip()
        # strip markdown fences if the model wrapped it anyway
        if content.startswith("```"):
            content = re.sub(r"^```(?:json)?\s*", "", content)
            content = re.sub(r"\s*```$", "", content)
        # response_format json_object may wrap in {"commands": [...]}
        parsed = json.loads(content)
        if isinstance(parsed, dict) and "commands" in parsed:
            parsed = parsed["commands"]
        if not isinstance(parsed, list):
            return []
        cmds = [c for c in parsed if isinstance(c, dict) and c.get("action")]
        return cmds[:5]
    except Exception as e:
        sys.stderr.write(f"[llm_brain] ask failed: {e}\n")
        return []


if __name__ == "__main__":
    # quick self-test with a fake state
    fake = {
        "day": 1, "phase": "day", "season": "autumn",
        "health": [150, 150], "hunger": [120, 150], "sanity": [200, 200],
        "pos": {"x": 10, "z": 20},
        "item_counts": {}, "equipped": [],
        "nearby": [{"n": "grass", "x": 12, "z": 22, "d": 3, "ok": True},
                   {"n": "flint", "x": 15, "z": 18, "d": 6, "ok": True}],
        "ground_items": [], "fires": [], "threats": [], "is_busy": False,
    }
    cmds = ask_for_commands(fake)
    print("COMMANDS:", json.dumps(cmds, indent=1))
