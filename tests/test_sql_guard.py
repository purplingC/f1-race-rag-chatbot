import pytest

from f1rag import store
from f1rag.engine import sql_guard
from f1rag.engine.sql_guard import SqlError

# Everything below is placeholder data for tests, not real F1 data


@pytest.fixture
def db_path(tmp_path):
    """Create a small database file with placeholder rows and return its path"""
    path = tmp_path / "f1.db"
    conn = store.connect(path)
    store.create_schema(conn)
    with conn:
        conn.execute(
            "INSERT INTO events (season, round, event_name) VALUES (2026, 1, 'Placeholder GP')"
        )
        for n in range(1, 301):
            conn.execute(
                "INSERT INTO laps (season, round, driver_code, lap_number, lap_time_s) "
                "VALUES (2026, 1, 'AAA', ?, ?)",
                (n, 90.0 + n / 100),
            )
        conn.execute(
            "INSERT INTO entries (season, round, driver_code, team) "
            "VALUES (2026, 1, 'AAA', 'Placeholder Team A')"
        )
    conn.close()
    return path


@pytest.fixture
def ro(db_path):
    """Open the guarded read-only connection"""
    conn = sql_guard.connect_read_only(db_path)
    yield conn
    conn.close()


def test_simple_select_returns_columns_rows_and_count(ro):
    """Check a plain SELECT returns its columns and rows"""
    result = sql_guard.execute_safe(ro, "SELECT season, round, event_name FROM events")
    assert result["columns"] == ["season", "round", "event_name"]
    assert result["rows"] == [[2026, 1, "Placeholder GP"]]
    assert result["row_count"] == 1
    assert result["truncated"] is False


def test_select_with_join_and_aggregate_is_allowed(ro):
    """Check joins, aggregates, CTEs and a trailing semicolon are accepted"""
    sql = (
        "WITH fast AS (SELECT driver_code, MIN(lap_time_s) AS best_s "
        "FROM laps GROUP BY driver_code) "
        "SELECT e.team, f.best_s FROM fast f JOIN entries e ON e.driver_code = f.driver_code;"
    )
    result = sql_guard.execute_safe(ro, sql)
    assert result["rows"] == [["Placeholder Team A", 90.01]]


@pytest.mark.parametrize(
    "sql",
    [
        "DROP TABLE laps",
        "DELETE FROM laps",
        "UPDATE laps SET lap_time_s = 0",
        "INSERT INTO events (season, round) VALUES (2027, 1)",
        "ATTACH DATABASE 'other.db' AS other",
        "PRAGMA table_info(laps)",
        "CREATE TABLE x (a INTEGER)",
        "SELECT load_extension('evil')",
    ],
)
def test_anything_but_reading_is_rejected(ro, sql):
    """Check writes, schema changes, ATTACH, PRAGMA and extensions are all refused"""
    with pytest.raises(SqlError):
        sql_guard.execute_safe(ro, sql)


def test_data_is_untouched_after_rejected_writes(ro, db_path):
    """Check that refused statements really changed nothing"""
    for sql in ("DELETE FROM laps", "DROP TABLE laps"):
        with pytest.raises(SqlError):
            sql_guard.execute_safe(ro, sql)
    assert sql_guard.execute_safe(ro, "SELECT COUNT(*) FROM laps")["rows"] == [[300]]


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT 1; DROP TABLE laps",
        "SELECT 1; -- harmless\nDELETE FROM laps",
        "SELECT 1 /* hide */ ; SELECT 2",
    ],
)
def test_more_than_one_statement_is_rejected(ro, sql):
    """Check a second statement is refused, even behind a comment"""
    with pytest.raises(SqlError, match="one"):
        sql_guard.execute_safe(ro, sql)


@pytest.mark.parametrize("sql", ["", "   ", "-- only a comment"])
def test_empty_input_is_rejected(ro, sql):
    """Check that empty or comment-only input gives a clear error"""
    with pytest.raises(SqlError, match="empty"):
        sql_guard.execute_safe(ro, sql)


def test_row_cap_truncates_large_results(ro):
    """Check results are cut at the row cap and marked as truncated"""
    result = sql_guard.execute_safe(ro, "SELECT lap_number FROM laps ORDER BY lap_number")
    assert result["row_count"] == sql_guard.ROW_CAP
    assert len(result["rows"]) == sql_guard.ROW_CAP
    assert result["truncated"] is True


def test_slow_query_is_stopped_by_the_time_limit(ro):
    """Check a runaway query is interrupted instead of hanging"""
    sql = (
        "WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x + 1 FROM n) "
        "SELECT COUNT(*) FROM n"
    )
    with pytest.raises(SqlError, match="time limit"):
        sql_guard.execute_safe(ro, sql, time_limit_s=0.2)


def test_sql_errors_come_back_as_readable_messages(ro):
    """Check a mistake in the query is reported with SQLite's message"""
    with pytest.raises(SqlError, match="no such column"):
        sql_guard.execute_safe(ro, "SELECT no_such_column FROM laps")


def test_connection_is_read_only_even_without_the_authorizer(db_path):
    """Check the connection itself refuses writes, as a second layer"""
    conn = sql_guard.connect_read_only(db_path)
    conn.set_authorizer(None)
    with pytest.raises(Exception):
        conn.execute("DELETE FROM laps")
    conn.close()
