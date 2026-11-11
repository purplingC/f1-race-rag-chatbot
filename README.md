# F1 Race Summarizer & RAG Chatbot

A chatbot that answers Formula 1 race questions from ingested data. It routes each question to a SQL database for statistics, a vector database for race reports and steward decisions, or both, then answers with cited evidence and a dashboard that matches the question. Built with Python, OpenF1, ChromaDB, SQLite, the Gemini API and Streamlit, entirely on free tiers. Unofficial project, not affiliated with Formula 1, the FIA or any team.

## Status

Under development. **Phase 0 (setup and spikes) is complete**; the chatbot itself is not built yet.

| Phase | Goal | Status |
|---|---|---|
| 0 | Setup, check that Gemini, OpenF1 and Wikipedia work | Done |
| 1 | Ingestion into SQLite and ChromaDB | Next |
| 2 | RAG engine: four tools, evidence IDs, command-line Q&A | Planned |
| 3 | Streamlit interface with a query-driven dashboard | Planned |
| 4 | Evaluation and hardening | Planned |
| 5 | Portfolio packaging | Planned |

## Planned design

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

Requires Python 3.10 or newer (tested on 3.13).

```bash
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

Then edit `.env` and set `GEMINI_API_KEY` to a free key from [Google AI Studio](https://aistudio.google.com). No billing is needed. `.env` is git-ignored: never commit it.

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

## Licence and disclaimer

The code is released under the [MIT licence](LICENSE). Third-party data keeps its own licence (see Data sources). F1, FORMULA ONE, FORMULA 1 and related marks are trade marks of Formula One Licensing B.V. This is an unofficial fan project, not associated with Formula 1, the FIA or any team.
