# DECISIONS.md — Architectural & Technical Decisions

## 2026-08-22 — Do not publish the base mod clone as this project

Decision:
- This repo (`dst-bot`) is the user's own 3-tier bot. The base mod
  (hineios/DST-ArtificialWilson) is referenced via a local `ref`
  symlink, git-ignored — NOT forked into this repo.
Reason:
- The base mod is a third-party project (different owner). Pushing to
  its origin is not authorized. Keeping it as a read-only reference
  symlink avoids confusing ownership and prevents accidental commits.
Rejected:
- Forking hineios's repo as the project root → rejected (wrong owner,
  mixes our WIP with their 2017 code).

## 2026-08-22 — Exclude runtime logs, screenshots, and `ref` from git

Decision:
- `.gitignore` excludes `*.log`, `survival_log.txt`, `agent_out.log`,
  `reflex_out.log`, `*.png`, and `ref` symlink. De-tracked the already
  committed 14 MB `survival_log.txt` + PNGs + `ref` gitlink.
Reason:
- These regenerate every session and bloat clones; `ref` is a local
  machine-specific symlink unsafe to commit.
Rejected:
- Keeping logs for "debug history" → rejected (size, churn, not
  recoverable-source). Use GitHub Issues for incident history instead.

## 2026-08-22 — Keep stale `claude_*.md` drafts

Decision:
- The many `claude_part1_questions*.md`, `claude_mod_review*.md`,
  `question_for_claude.md` working drafts stay in the repo.
Reason:
- User chose to retain them (they document the design dialogue and may
  aid reconstruction). They are small markdown, not secrets.
Rejected:
- Deleting as clutter → rejected per user preference.

## 2026-08-22 — Secrets through Hermes `.env` only

Decision:
- `brain.py` reads `DEEPSEEK_API_KEY` from the Hermes `.env` at
  runtime; never hardcoded or committed.
Reason:
- Keeps the repo publishable (private) without leaking credentials.
Rejected:
- Embedding keys in code or a committed `.env` → rejected (leak risk).
