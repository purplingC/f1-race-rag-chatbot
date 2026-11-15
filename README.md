# F1 Race Summarizer & RAG Chatbot

Ask questions about Formula 1 races and get answers with their sources. Each question is routed to SQL for statistics, a vector store for race reports, or both, and the dashboard shows the data behind the answer. Python, OpenF1, SQLite, ChromaDB, Gemini and Streamlit, all on free tiers.

**Status:** version 1.0, all five phases complete. Covers all completed races of 2024, 2025 and 2026. How it is built: [ARCHITECTURE.md](ARCHITECTURE.md).

## How it works

- **Four tools:** the model picks `find_race`, `run_sql`, `search_docs` or `get_data_coverage`, as often as the question needs.
- **Grounded:** every fact comes from a tool result and cites it; the app shows how many statements cite a source.
- **Safe SQL:** `run_sql` is read-only and SELECT-only, enforced in code.
- **Honest:** missing data is said, with the coverage, never guessed.

## Setup

Python 3.11 or newer.

```bash
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt && pip install -e .
cp .env.example .env    # then set GEMINI_API_KEY (free key from Google AI Studio)
```

## Use

A fresh clone works straight away on the bundled sample: three races in `sample/` (see its [data notice](sample/DATA_NOTICE.md)).

```bash
streamlit run src/f1rag/ui/app.py               # web app at http://localhost:8501
python -m f1rag.ask "Why was the 2026 Bahrain Grand Prix held in Malaysia?" --trace   # terminal, every tool call
python -m f1rag.ingest --year 2026 --all        # load full seasons (resumable; --gp "<name>" for one race)
```

Once full data is loaded into `data/` (git-ignored), it is used instead of the sample. `--only data|text|summary` loads one part. One question uses about 3 to 4 of Gemini's 500 free daily requests; the first question downloads ChromaDB's small embedding model once (about 83 MB).

**Runs locally only:** the app listens on this computer. Hosting it online is a possible future step, not part of this version.

**Optional, local only:** official driver photos (`python -m f1rag.ingest.headshots`, then `SHOW_HEADSHOTS=true`) and team logos (`python -m f1rag.ingest.team_logos`, then `SHOW_TEAM_LOGOS=true`). They are never committed and stay off in public demos.

## Evaluation

30 golden questions (`eval/golden.toml`) with expected answers computed from the database at run time:

```bash
python -m f1rag.eval --check    # expected answers only, no Gemini calls
python -m f1rag.eval            # full run on the full data, about 105 requests and 18 minutes
```

Latest run, 15 November 2026, `gemini-3.5-flash-lite`:

| Metric | Result | Target |
|---|---|---|
| Numeric accuracy | 12/12 | ≥ 90% |
| Narrative / hybrid answers | 8/8 · 5/5 | hybrid ≥ 80% |
| Routing accuracy | 25/26 (96%) | ≥ 85% |
| Dashboard relevance | 24/24 | ≥ 85% |
| Evidence linkage | 36/36 | 100% |
| Refusals (trick questions) | 5/5 | 100% |
| Latency, median / 95th percentile | 4.3 s / 6.4 s | ≤ 10 s / ≤ 20 s |

Every answer was checked against its evidence; no fact came from outside the tool results.

## Tests

```bash
pytest && ruff check .
```

CI runs both on Python 3.11 and 3.13 for every push to `dev` or `main` and every pull request to `main`. No test needs the network or an API key.

## Data and licence

| Source | Used for | Licence |
|---|---|---|
| [OpenF1](https://openf1.org) | Results, laps, tyres, pit-lane times, weather, race control | CC BY-NC-SA 4.0 (non-commercial) |
| Wikipedia | Race reports | CC BY-SA 4.0 |

Code: [MIT](LICENSE). Because of OpenF1's licence, this project is non-commercial. F1 and related marks belong to Formula One Licensing B.V.; this is an unofficial fan project, not associated with Formula 1, the FIA or any team.
