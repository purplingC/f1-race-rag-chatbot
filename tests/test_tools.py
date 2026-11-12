import pytest

from f1rag import store, vectors
from f1rag.engine import sql_guard, tools

# Everything below is placeholder data for tests, not real F1 data
EVENTS = [
    (2025, 1, "Placeholder Alpha Grand Prix", "Alphaville", "Alphaland", "2025-03-02"),
    (2026, 1, "Placeholder Alpha Grand Prix", "Alphaville", "Alphaland", "2026-03-01"),
    (2026, 2, "Placeholder Beta Grand Prix", "Betatown", "Betaland", "2026-04-05"),
    (2026, 3, "Placeholder Gamma Grand Prix", "Gammaburg", "Betaland", "2026-05-10"),
]


@pytest.fixture
def conn(tmp_path):
    """Build a small placeholder database and open it through the SQL guard"""
    path = tmp_path / "f1.db"
    rw = store.connect(path)
    store.create_schema(rw)
    with rw:
        rw.executemany(
            "INSERT INTO events (season, round, event_name, circuit, country, race_date) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            EVENTS,
        )
        rw.execute(
            "INSERT INTO ingest_log (season, round, source, status, rows, ts) "
            "VALUES (2026, 2, 'wikipedia', 'ok', 5, 't')"
        )
        rw.execute(
            "INSERT INTO ingest_log (season, round, source, status, rows, ts) "
            "VALUES (2026, 3, 'wikipedia', 'failed', NULL, 't')"
        )
        rw.execute(
            "INSERT INTO race_summaries (season, round, summary_md) "
            "VALUES (2026, 2, 'placeholder')"
        )
    rw.close()
    ro = sql_guard.connect_read_only(path)
    yield ro
    ro.close()


def keys(result):
    """Return (season, round) pairs of find_race matches"""
    return [(m["season"], m["round"]) for m in result["matches"]]


def test_find_race_by_name_and_season(conn):
    """Check a race name plus a season gives one match and no ambiguity"""
    result = tools.find_race(conn, season=2026, name="beta grand")
    assert keys(result) == [(2026, 2)]
    assert result["ambiguous"] is False


def test_find_race_by_country_returns_every_race_there(conn):
    """Check a country with two races is flagged as ambiguous, latest first"""
    result = tools.find_race(conn, country="Betaland")
    assert keys(result) == [(2026, 3), (2026, 2)]
    assert result["ambiguous"] is True


def test_find_race_by_exact_date(conn):
    """Check an exact race date finds that race"""
    assert keys(tools.find_race(conn, date="2026-05-10")) == [(2026, 3)]


def test_bare_name_in_two_seasons_is_ambiguous(conn):
    """Check a name matching the same race in two seasons is flagged, latest first"""
    result = tools.find_race(conn, name="Alpha")
    assert keys(result) == [(2026, 1), (2025, 1)]
    assert result["ambiguous"] is True


def test_name_also_matches_circuit_and_country(conn):
    """Check the name argument also searches circuit and country, ignoring case"""
    assert keys(tools.find_race(conn, name="GAMMABURG")) == [(2026, 3)]


def test_race_not_ingested_returns_nothing_with_a_message(conn):
    """Check an unknown race gives no matches and points to the coverage tool"""
    result = tools.find_race(conn, name="Nowhere", season=2026)
    assert result["matches"] == []
    assert "get_data_coverage" in result["message"]


def test_find_race_needs_at_least_one_argument(conn):
    """Check a call with no arguments explains what to give"""
    result = tools.find_race(conn)
    assert result["matches"] == []
    assert "at least one" in result["message"]


def test_coverage_lists_races_sources_and_latest(conn):
    """Check coverage shows every loaded race, which sources it has and the latest race"""
    result = tools.get_data_coverage(conn)
    assert [(r["season"], r["round"]) for r in result["races"]] == [
        (2025, 1), (2026, 1), (2026, 2), (2026, 3),
    ]
    beta = next(r for r in result["races"] if r["round"] == 2 and r["season"] == 2026)
    gamma = next(r for r in result["races"] if r["round"] == 3)
    assert (beta["race_data"], beta["text"], beta["summary"]) == (True, True, True)
    assert (gamma["text"], gamma["summary"]) == (False, False)
    assert (result["latest"]["season"], result["latest"]["round"]) == (2026, 3)
    assert "cancelled" in result["note"]


def test_run_sql_returns_rows_or_a_readable_error(conn):
    """Check run_sql passes good queries through and turns refusals into an error field"""
    ok = tools.run_sql(conn, "SELECT round FROM events WHERE season = 2026 ORDER BY round")
    assert ok["ok"] is True
    assert ok["rows"] == [[1], [2], [3]]
    bad = tools.run_sql(conn, "DELETE FROM events")
    assert bad["ok"] is False
    assert "SELECT" in bad["error"]
    assert bad["query"] == "DELETE FROM events"


@pytest.fixture
def collection(tmp_path, fake_embedding):
    """Open a vector store with placeholder chunks for two races"""
    col = vectors.open_collection(tmp_path / "chroma", embedding_function=fake_embedding)
    for season, round_no, n in ((2026, 1, 3), (2026, 2, 2)):
        chunks = [
            {
                "id": f"{season}-{round_no}-{i}",
                "document": f"Placeholder title - Race\nPlaceholder text {i} for round {round_no}",
                "metadata": {
                    "season": season, "round": round_no, "source_type": "wiki",
                    "source_name": "Wikipedia", "title": f"Placeholder race {round_no}",
                    "section": "Race", "url": f"https://example.invalid/{round_no}",
                    "published_at": "2026-05-01T00:00:00Z", "chunk_idx": i,
                },
            }
            for i in range(n)
        ]
        vectors.replace_race_docs(col, season, round_no, "wiki", chunks)
    return col


def test_search_docs_filters_by_race(collection):
    """Check a search limited to one race only returns that race's passages"""
    result = tools.search_docs(collection, "placeholder text", season=2026, round=2)
    assert result["count"] == 2
    assert {p["round"] for p in result["passages"]} == {2}
    first = result["passages"][0]
    wanted = {"title", "section", "url", "published_at", "source_name", "distance", "text"}
    assert wanted <= set(first)


def test_search_docs_respects_k_and_its_upper_limit(collection):
    """Check k limits the number of passages and very large k is capped"""
    assert tools.search_docs(collection, "placeholder", k=2)["count"] == 2
    assert tools.search_docs(collection, "placeholder", k=500)["count"] == 5


def test_search_docs_with_no_match_says_so(collection):
    """Check a filter that matches nothing returns an empty list with a message"""
    result = tools.search_docs(collection, "placeholder", season=1999)
    assert result["passages"] == []
    assert result["message"]
