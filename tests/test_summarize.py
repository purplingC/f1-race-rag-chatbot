import json

import pytest

from f1rag import store, vectors
from f1rag.engine.llm import LLMReply
from f1rag.ingest import summarize

# Everything below is placeholder data for tests, not real F1 data
GENERATED_AT = "2026-07-01T12:00:00+00:00"


class FakeLLM:
    """Returns one scripted summary and records the prompt"""

    model = "fake-model"

    def __init__(self, summary="Placeholder summary text."):
        """Keep the summary to return"""
        self.summary = summary
        self.prompts = []

    def user_turn(self, text):
        """Return a user turn as a plain tuple"""
        return ("user", text)

    def generate(self, contents, system, tools=None, json_schema=None):
        """Record the prompt and return the summary as JSON"""
        self.prompts.append((system, contents[-1][1], tools, json_schema))
        return LLMReply(text=json.dumps({"summary_md": self.summary}))


@pytest.fixture
def conn():
    """Build an in-memory race with two drivers, laps, stints, a stop and a safety car"""
    c = store.connect(":memory:")
    store.create_schema(c)
    with c:
        c.execute(
            "INSERT INTO events (season, round, event_name, circuit, country, race_date) "
            "VALUES (2026, 1, 'Placeholder Grand Prix', 'Placeville', 'Placeland', '2026-03-01')"
        )
        c.executemany(
            "INSERT INTO entries (season, round, driver_code, full_name, team) "
            "VALUES (2026, 1, ?, ?, ?)",
            [("AAA", "Driver Alpha", "Placeholder Team A"),
             ("BBB", "Driver Beta", "Placeholder Team B")],
        )
        c.executemany(
            "INSERT INTO results (season, round, driver_code, grid_pos, finish_pos, status, "
            "points, laps_completed) VALUES (2026, 1, ?, ?, ?, ?, ?, ?)",
            [("AAA", 2, 1, "Finished", 25, 3), ("BBB", 1, None, "DNF", 0, 2)],
        )
        c.executemany(
            "INSERT INTO laps (season, round, driver_code, lap_number, lap_time_s) "
            "VALUES (2026, 1, ?, ?, ?)",
            [("AAA", 1, 91.0), ("AAA", 2, 90.5), ("BBB", 1, 91.2)],
        )
        c.execute(
            "INSERT INTO stints (season, round, driver_code, stint_no, compound, start_lap, "
            "end_lap, n_laps) VALUES (2026, 1, 'AAA', 1, 'MEDIUM', 1, 3, 3)"
        )
        c.execute(
            "INSERT INTO pit_stops (season, round, driver_code, stop_no, lap, pit_lane_time_s) "
            "VALUES (2026, 1, 'AAA', 1, 2, 22.5)"
        )
        c.execute(
            "INSERT INTO race_control (season, round, event_no, lap_number, category, message) "
            "VALUES (2026, 1, 1, 2, 'SafetyCar', 'SAFETY CAR DEPLOYED')"
        )
    yield c
    c.close()


@pytest.fixture
def collection(tmp_path, fake_embedding):
    """Open a vector store with one placeholder wiki passage"""
    col = vectors.open_collection(tmp_path / "chroma", embedding_function=fake_embedding)
    chunk = {
        "id": "w1",
        "document": "Placeholder race - Race\nPlaceholder report text",
        "metadata": {
            "season": 2026, "round": 1, "source_type": "wiki", "source_name": "Wikipedia",
            "title": "Placeholder race", "section": "Race", "url": "https://example.invalid",
            "published_at": "", "chunk_idx": 0,
        },
    }
    vectors.replace_race_docs(col, 2026, 1, "wiki", [chunk])
    return col


def test_fact_sheet_holds_result_fastest_lap_strategy_and_incidents(conn):
    """Check the fact sheet gathers the facts the summary may use, from SQL only"""
    facts = summarize.fact_sheet(conn, 2026, 1)
    assert facts["event"]["event_name"] == "Placeholder Grand Prix"
    first = facts["classification"][0]
    assert (first["driver_code"], first["team"], first["finish_pos"]) == (
        "AAA", "Placeholder Team A", 1,
    )
    assert facts["classification"][-1]["status"] == "DNF"
    assert facts["fastest_lap"] == {"driver_code": "AAA", "lap_number": 2, "lap_time_s": 90.5}
    assert facts["stints"][0]["compound"] == "MEDIUM"
    assert facts["pit_stops"][0]["pit_lane_time_s"] == 22.5
    assert facts["race_control"][0]["message"] == "SAFETY CAR DEPLOYED"


def test_fact_sheet_needs_race_data(conn):
    """Check a race without results is refused instead of summarised from nothing"""
    with pytest.raises(LookupError, match="race data"):
        summarize.fact_sheet(conn, 2026, 9)


def test_summary_is_stored_in_sqlite_and_the_vector_store(conn, collection):
    """Check the summary goes to race_summaries and to Chroma as summary chunks"""
    llm = FakeLLM()
    count = summarize.ingest_race_summary(conn, collection, llm, 2026, 1, GENERATED_AT)
    assert count == 1
    row = conn.execute("SELECT summary_md, generated_at, model FROM race_summaries").fetchone()
    assert tuple(row) == ("Placeholder summary text.", GENERATED_AT, "fake-model")
    stored = collection.get(where={"source_type": "summary"})
    assert stored["metadatas"][0]["title"] == "Placeholder Grand Prix 2026 summary"
    assert stored["documents"][0].endswith("Placeholder summary text.")


def test_prompt_holds_only_the_fact_sheet_and_passages(conn, collection):
    """Check the model gets the facts and passages, JSON mode and no tools"""
    llm = FakeLLM()
    summarize.ingest_race_summary(conn, collection, llm, 2026, 1, GENERATED_AT)
    system, prompt, tools, schema = llm.prompts[0]
    assert "only" in system.lower()
    assert "never state why" in system.lower()
    assert "Placeholder Team A" in prompt and "Placeholder report text" in prompt
    assert tools is None and schema


def test_rerun_replaces_the_summary_without_duplicates(conn, collection):
    """Check generating again keeps one row and one set of chunks"""
    summarize.ingest_race_summary(conn, collection, FakeLLM("First."), 2026, 1, GENERATED_AT)
    summarize.ingest_race_summary(conn, collection, FakeLLM("Second."), 2026, 1, GENERATED_AT)
    rows = conn.execute("SELECT summary_md FROM race_summaries").fetchall()
    assert [r[0] for r in rows] == ["Second."]
    assert len(collection.get(where={"source_type": "summary"})["ids"]) == 1


def test_empty_summary_is_refused(conn, collection):
    """Check an empty model answer is an error, not a stored blank summary"""
    with pytest.raises(ValueError, match="empty"):
        summarize.ingest_race_summary(conn, collection, FakeLLM(""), 2026, 1, GENERATED_AT)
    assert conn.execute("SELECT COUNT(*) FROM race_summaries").fetchone()[0] == 0
