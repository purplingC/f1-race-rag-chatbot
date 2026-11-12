import sqlite3

import pytest

from f1rag import store

EXPECTED_TABLES = {
    "events",
    "entries",
    "results",
    "laps",
    "stints",
    "pit_stops",
    "weather",
    "race_control",
    "race_summaries",
    "ingest_log",
}


@pytest.fixture
def conn():
    """Open an in-memory database with the schema created"""
    connection = store.connect(":memory:")
    store.create_schema(connection)
    yield connection
    connection.close()


def table_names(connection):
    """Return the names of all user tables in the database"""
    rows = connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
    )
    return {row["name"] for row in rows}


def column_names(connection, table):
    """Return the column names of one table"""
    return {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}


def test_create_schema_makes_all_tables(conn):
    """Check that every planned table exists after create_schema"""
    assert table_names(conn) == EXPECTED_TABLES


def test_create_schema_twice_keeps_existing_rows(conn):
    """Check that running create_schema again does not fail or wipe data"""
    conn.execute(
        "INSERT INTO events (season, round, event_name) VALUES (2026, 1, 'Placeholder Grand Prix')"
    )
    store.create_schema(conn)
    assert conn.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 1


def test_duplicate_primary_key_is_rejected(conn):
    """Check that the same race cannot be inserted twice as a plain insert"""
    insert = "INSERT INTO events (season, round, event_name) VALUES (2026, 1, 'Placeholder')"
    conn.execute(insert)
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(insert)


def test_every_table_carries_season_and_round(conn):
    """Check the rule that every row carries season and round"""
    for table in EXPECTED_TABLES:
        columns = column_names(conn, table)
        assert {"season", "round"} <= columns, table


def test_pit_stops_store_only_pit_lane_time(conn):
    """Check that pit stops hold pit-lane time and no stationary time column"""
    columns = column_names(conn, "pit_stops")
    assert "pit_lane_time_s" in columns
    assert not {c for c in columns if "stationary" in c or "stop_duration" in c}


def test_entries_are_keyed_per_race_driver(conn):
    """Check that the same driver can have a different team in two races"""
    # TST and the team names are placeholders, not real F1 data
    insert = "INSERT INTO entries (season, round, driver_code, team) VALUES (?, ?, 'TST', ?)"
    conn.execute(insert, (2025, 1, "Placeholder Team A"))
    conn.execute(insert, (2026, 1, "Placeholder Team B"))
    teams = [r["team"] for r in conn.execute("SELECT team FROM entries ORDER BY season")]
    assert teams == ["Placeholder Team A", "Placeholder Team B"]


def test_ingest_log_keeps_a_message_for_failed_loads(conn):
    """Check that ingest_log can hold an error message next to the status"""
    assert {"status", "rows", "ts", "message"} <= column_names(conn, "ingest_log")
