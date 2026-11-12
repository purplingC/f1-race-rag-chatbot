# F1 Race Summarizer & RAG Chatbot

A chatbot that answers Formula 1 race questions from ingested data. It routes each question to a SQL database for statistics, a vector database for race reports and steward decisions, or both, then answers with cited evidence and a dashboard that matches the question. Built with Python, OpenF1, ChromaDB, SQLite, the Gemini API and Streamlit, entirely on free tiers. Unofficial project, not affiliated with Formula 1, the FIA or any team.

## Status

Under development, version 0.2. **Phases 0 and 1 are complete**: the 2026 races are loaded into SQLite and ChromaDB. **Phase 2 is in progress**: the RAG engine and command-line Q&A are built and tested with a fake model; the check with the real Gemini model is next.

| Phase | Goal | Status |
|---|---|---|
| 0 | Setup, check that Gemini, OpenF1 and Wikipedia work | Done |
| 1 | Ingestion into SQLite and ChromaDB | Done |
| 2 | RAG engine: four tools, evidence IDs, command-line Q&A, race summaries | In progress |
| 3 | Streamlit interface with a query-driven dashboard | Planned |
| 4 | Evaluation and hardening | Planned |
| 5 | Portfolio packaging | Planned |

## Design

- **Routing:** the model calls one or more of four tools: `find_race`, `run_sql`, `search_docs`, `get_data_coverage`.
- **Grounded answers:** every fact comes from a tool result, and every tool result gets an evidence ID (E1, E2, ...) that the answer cites.
- **Safe SQL:** `run_sql` is SELECT-only, enforced in code (read-only connection, authorizer, row and time limits), not by the prompt.
- **Honest about gaps:** if the data is missing, the bot says so and states its coverage.
- **Scope:** Race sessions for the 2024, 2025 and 2026 seasons (completed rounds), lap-level data, ingested manually from the command line.

## Data sources

| Source | Used for | Licence |
|---|---|---|
| [OpenF1](https://openf1.org) | Results, laps, stints and tyres, pit-lane times, weather, race-control events | CC BY-NC-SA 4.0, non-commercial |
| Wikipedia | Race reports (short excerpts, with attribution and revision) | CC BY-SA 4.0 |

OpenF1 is a free community project and is not affiliated with Formula 1. Because of its non-commercial licence, this project is non-commercial too.

## Setup

Requires Python 3.11 or newer (CI tests 3.11 and 3.13).

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install -e .
cp .env.example .env
```

`pip install -e .` installs the project itself in editable mode, so `python -m f1rag...` commands work from anywhere and code changes take effect without reinstalling.

Then edit `.env` and set `GEMINI_API_KEY` to a free key from [Google AI Studio](https://aistudio.google.com). No billing is needed. `.env` is git-ignored: never commit it.

## Load race data

Race data comes from OpenF1 and goes into a local SQLite database, `data/f1_stats.db`. Race reports come from Wikipedia and go into a local ChromaDB vector store, `data/chroma/`. Both are git-ignored and need no key.

```bash
python -m f1rag.ingest --year 2026 --gp "Kuala Lumpur"   # one race
python -m f1rag.ingest --year 2026 --all                # every finished race of the season
```

- `--gp` matches the race name, location, country or circuit. Cancelled races are skipped with a note, and a query that matches several races lists them instead of guessing.
- `--all` is resumable: a rerun skips rounds already loaded and retries failed ones. A first run takes roughly 15 to 20 minutes per season because OpenF1 is called politely (one request every 2.5 seconds); later runs read from the cache.
- `--only data` loads only the race data and `--only text` only the Wikipedia reports; the default loads both.
- `--only summary` writes a short race summary with Gemini from the loaded data and reports (needs `GEMINI_API_KEY`, one request per race). It only runs when chosen.
- Wikipedia is called slowly on purpose (one request every 6.5 seconds, its limit for clients that send no contact details), so text adds about 15 seconds per race on a first run.
- The first text load downloads ChromaDB's local embedding model once (about 83 MB, into `~/.cache/chroma`).
- `--refresh` asks both sources again and reloads everything; `--db` and `--chroma` use another database file or vector store folder.

## Ask questions

Needs `GEMINI_API_KEY` in `.env` and at least one loaded race.

```bash
python -m f1rag.ask "Who won the Bahrain Grand Prix in 2026?"
python -m f1rag.ask "Why was the Bahrain race moved?" --trace
```

The answer cites evidence IDs such as `[E1]`. Below it the command lists each piece of evidence (the SQL query or the report passages with links) and, with `--trace`, every tool call the model made. One question uses about 3 to 5 Gemini requests of the free 500 a day.

## Try the Phase 0 spikes

Small scripts in `scripts/spikes/` that show each service working, using the 2026 Bahrain Grand Prix in Malaysia:

```bash
python scripts/spikes/openf1_load_race.py        # no key needed
python scripts/spikes/wikipedia_race_report.py   # no key needed
python scripts/spikes/gemini_hello.py            # needs GEMINI_API_KEY
python scripts/spikes/gemini_tool_hello.py       # needs GEMINI_API_KEY
```

Downloaded data is cached in `data/cache/`, which is git-ignored.

## Tests

```bash
pytest && ruff check .
```

GitHub Actions runs the same lint check and tests on Python 3.11 and 3.13 for every push to `dev` or `main` and every pull request to `main` (`.github/workflows/ci.yml`). No test needs the network, an API key or the embedding model.

## Licence and disclaimer

The code is released under the [MIT licence](LICENSE). Third-party data keeps its own licence (see Data sources). F1, FORMULA ONE, FORMULA 1 and related marks are trade marks of Formula One Licensing B.V. This is an unofficial fan project, not associated with Formula 1, the FIA or any team.
