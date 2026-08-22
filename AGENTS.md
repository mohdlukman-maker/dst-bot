# AGENTS.md — DST Survival Bot

This repository is the persistent, recoverable home for the DST
survival bot. Treat GitHub as the source of truth; the local tree is a
temporary workspace that may be deleted and re-cloned.

## Before Starting Work
Always:
1. Read `AGENTS.md` (this file).
2. Read `.ai/PROJECT.md`, `.ai/STATUS.md`, `.ai/NEXT_TASK.md`,
   `.ai/DECISIONS.md`.
3. Inspect `git status` and recent commits.
4. Read `CLAUDE.md` (the operator playbook) before touching agent logic.
5. Inspect the actual code before assuming prior sessions were correct.

## During Development
- Make the smallest reasonable change; preserve working functionality.
- Follow the 3-tier split: Lua mod (game hands), reflex.py (200ms
  fail-safes, never blocks on LLM), agent (planning, slow).
- Every Lua mod edit needs a full DST restart (~5 min) — review syntax
  and nil-crashes before saving.
- Do not introduce cloud services; keep it local/offline-first.
- Do not expose secrets. The DeepSeek key is loaded from the Hermes
  `.env` at runtime (DEEPSEEK_API_KEY) — never hardcode or commit it.
- Do not bypass `.gitignore` (`git add -f` on logs/PNGs/`ref`).
- Verify changes rather than assuming they work.

## Before Ending Work
Always:
1. Run appropriate tests (`python -m pytest tests/`).
2. Update `.ai/STATUS.md` and `.ai/NEXT_TASK.md`.
3. Record important decisions in `.ai/DECISIONS.md`.
4. Review `git diff`.
5. Ensure no secrets or bloated runtime files are staged.
6. Commit meaningful changes with a conventional message.
7. Push to the private GitHub remote when appropriate.
8. Confirm the working tree is clean.

## Recovery
After a fresh clone: read `.ai/PROJECT.md` → `STATUS.md` →
`NEXT_TASK.md` → `DECISIONS.md` → `SESSION_LOG.md` → `README.md` →
git history → `CLAUDE.md` → actual source/tests. Then
`pip install` any deps and run `pytest`.
