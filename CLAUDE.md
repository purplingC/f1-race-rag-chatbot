# F1 Race Summarizer & RAG Chatbot

Zero-cost portfolio project. Answers Formula 1 race questions from ingested data by routing to SQL (statistics), a vector DB (narrative text), or both, and cites evidence for every fact. Goal: showcase RAG, vector search, LLM tool calling, grounded answers and clear data provenance. Python.

**Source of truth:** `docs/planning-design.md`. Read the relevant section before building. If code and the doc disagree, ask. When a decision changes, update the doc and this file. Anything outside the MVP is a Future Enhancement (Section 13): do not build it.

## MVP scope
2024, 2025 and 2026 seasons (completed, non-cancelled rounds only), Race sessions only, lap-level data. Manual CLI ingestion. No scheduler, live data, telemetry, team radio, Ollama, MCP or React. Pit data is pit-lane time (OpenF1 `lane_duration`), never stationary time.

## Data source
OpenF1 (`api.openf1.org/v1`), historical data only: free, no login, 2023 onwards, limit 3 requests/s and 30/min. Do not use FastF1 for data: since Oct 2026 it needs a paid F1TV login (see Appendix E.1 of the planning doc). OpenF1 data is CC BY-NC-SA 4.0: keep the project non-commercial and ship a third-party-data notice with any sample database.

## Rules
1. Ingestion and querying are separate; the query path only reads.
2. Facts come from tools, never model memory. Driver-team pairing comes from the `entries` table for that race.
3. Four tools: `find_race`, `run_sql`, `search_docs`, `get_data_coverage`.
4. `run_sql` is SELECT-only, enforced in code (read-only connection, authorizer, row and time caps), not by the prompt.
5. Every tool result becomes an evidence ID (E1, E2…). Answers cite only IDs that exist.
6. The dashboard comes from a validated JSON spec. The model picks widgets; code fetches the data. Model text never supplies chart data.
7. Ingestion is idempotent and resumable. Every row and chunk carries `season` and `round`.
8. If data is missing, say so and state coverage. Never guess.

## Guardrails
- Never commit or print `.env`, the raw API cache or headshot images. Avatars default to initials; headshots only via `SHOW_HEADSHOTS=true`, at runtime.
- Send only public data to Gemini. No F1, FIA or team logos. No paid services.
- HTML-escape database text in the UI.
- Stack: Python 3.10+, requests (OpenF1), pandas, SQLite, chromadb (default embeddings), google-genai (model from `GEMINI_MODEL`), streamlit, plotly, pydantic, pytest, ruff. No LangChain. Ask before adding dependencies.

## Commands
```
python -m f1rag.ingest --year 2025 --gp "<name>"   # or --all
streamlit run src/f1rag/ui/app.py
pytest && ruff check .
python -m f1rag.eval
```

## Workflow
- Follow the Section 11 roadmap one phase at a time, starting at Phase 0. Meet each exit criterion.
- Plan in a few lines first and wait for approval on architecture changes. Keep steps small and runnable; run tests and lint before calling a task done; suggest a commit message.
- Write tests first for `sql_guard`, idempotency, chunking, `find_race` and spec validation.
- Choose the simplest solution that meets the requirement. Briefly explain non-obvious choices: the owner is learning.
- Never invent F1 data, results or quotes. Use labelled placeholders or small fixtures.
- Docstrings: every function gets a one-line docstring that says exactly what it does. Start with a capital letter and do not end with a full stop (e.g. `"""Reject any SQL statement that is not a single SELECT"""`).
- Git: work on the `dev` branch, never commit to `main`. Commit messages are short imperative sentences (e.g. "Add SQL guard"), no `type:` prefixes. I never run `git commit` or `git push`; the owner does. At the end of each phase, give the exact commands to push `dev`, the PR title and description for dev -> main, and the tag command (e.g. `v0.1-phase0`).
- Lessons learned: whenever a tech or data issue forces a change to the plan (model, library, data source, limits, cost, licence), record it in `docs/planning-design.md` Appendix E (planned, found, why, impact, change, how to avoid). Before adding any external dependency, service or model, check its current cost, auth requirement, limits, licence and deprecation status from official sources, and record the date (checklist in Appendix E.0).
- Consistency check: before each commit batch and again before the phase-end PR, review every tracked file (README.md, CLAUDE.md, `.env.example`, `requirements.txt`, `pyproject.toml`, scripts, tests) for stale or conflicting information (data source, scope, model, status, commands) and fix it or tell the owner.
- Build log: keep `docs/build-log.md` updated for the owner's personal use. It is git-ignored: never stage or commit it. After each task, append a dated entry: what was done, files changed, key decisions and why, and anything left open. Never write secrets or `.env` values in it.
- Open decisions (Section 14) are undecided unless marked. Ask. Decided: Q1 = 2024, 2025 and completed 2026 rounds (widened 2026-11-12); Q2 = `gemini-3.5-flash-lite` (free quota 500 requests/day, 15/min; tool calling confirmed in Phase 0).
