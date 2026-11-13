# F1 Race Summarizer & RAG Chatbot

A chatbot that answers Formula 1 race questions from ingested data. It routes each question to a SQL database for statistics, a vector database for race reports and generated race summaries, or both, then answers with cited evidence and a dashboard that matches the question. Built with Python, OpenF1, ChromaDB, SQLite, the Gemini API and Streamlit, entirely on free tiers. Unofficial project, not affiliated with Formula 1, the FIA or any team.

## Status

Under development, version 0.4. **Phases 0 to 3 are complete**: all completed races of 2024, 2025 and 2026 (68 races) are loaded into SQLite and ChromaDB, each with its Wikipedia report and a generated race summary. The command-line Q&A and the Streamlit web app answer questions with cited evidence, and the dashboard shows the widgets that support each answer. **Phase 4 (evaluation) is next.**

| Phase | Goal | Status |
|---|---|---|
| 0 | Setup, check that Gemini, OpenF1 and Wikipedia work | Done |
| 1 | Ingestion into SQLite and ChromaDB | Done |
| 2 | RAG engine: four tools, evidence IDs, command-line Q&A, race summaries | Done |
| 3 | Streamlit interface with a query-driven dashboard | Done |
| 4 | Evaluation and hardening | Planned |
| 5 | Portfolio packaging | Planned |

## Design

- **Routing:** the model calls one or more of four tools: `find_race`, `run_sql`, `search_docs`, `get_data_coverage`.
- **Grounded answers:** every fact comes from a tool result, and every tool result gets an evidence ID (E1, E2, ...) that the answer cites. The command line shows the IDs; the web app hides them and says how many statements cite a source instead.
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

## Run the web app

```bash
streamlit run src/f1rag/ui/app.py
```

Then open http://localhost:8501. A race banner at the top shows the chosen race and its key facts; the dashboard below shows the podium, classification, race summary and tyre strategy. Ask a question on the right (or click a suggested one) and the dashboard switches to the widgets that support the answer. Each answer says how many of its statements cite a source and what it was based on (race data, race reports), and lists its sources. Lap time, position and tyre strategy charts show safety cars, VSCs and red flags in a thin track strip above the laps. The suggested questions ask how and why, and offer a comparison when the same Grand Prix is loaded for another season; to see every tool call, query and timing, use `python -m f1rag.ask "…" --trace` in the terminal. Questions need `GEMINI_API_KEY`; browsing the dashboard does not. The app only listens on this computer (`.streamlit/config.toml`).

## Driver photos (optional, local only)

The dashboard shows each driver as a round avatar with a team-colour ring, using initials by default. To use the official photos on your own machine:

```bash
python -m f1rag.ingest.headshots            # download the photos once and make data/headshots/review_sheet.png
python -m f1rag.ingest.headshots --skip XXX # reject a wrong photo: that driver shows initials
python -m f1rag.ingest.headshots --link XXX https://...  # use a photo link from the driver's formula1.com page
python -m f1rag.ingest.headshots --adjust XXX 1.1 0.03   # zoom in 10% and move down a little, for a head that looks small
```

Each photo is cropped so the head is centred with room above the hair. Then set `SHOW_HEADSHOTS=true` in `.env`. The photos are copyrighted by Formula 1: `data/headshots/` is git-ignored, and public demos, screenshots and recordings use initials.

## Team logos (optional, local only)

By default teams are shown by name only. To show each team's logo before its name on your own machine:

```bash
python -m f1rag.ingest.team_logos            # every loaded season; or --season 2026
```

This saves one logo per team and season in `data/team_logos/<season>/` and makes a `review_sheet_<season>.png` there to check. Then set `SHOW_TEAM_LOGOS=true` in `.env` and restart the app. The logos are trademarks of their teams: `data/team_logos/` is git-ignored, and public demos, screenshots and recordings leave them off. Formula 1 and FIA logos are never used.

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
