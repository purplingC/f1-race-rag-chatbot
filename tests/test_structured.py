import copy
import sqlite3

import pytest

from f1rag import store
from f1rag.ingest import structured

# Everything below is placeholder data for tests, not real F1 data
RACE_SESSION = {
    "session_key": 100,
    "meeting_key": 10,
    "date_start": "2026-01-01T10:00:00+00:00",
    "circuit_short_name": "Placeholder Circuit",
    "country_name": "Placeholderland",
    "is_cancelled": False,
}
MEETING = {
    "meeting_key": 10,
    "meeting_name": "Placeholder Grand Prix",
    "meeting_official_name": "PLACEHOLDER GRAND PRIX 2026",
}
QUALIFYING_KEY = 99


def driver(number, code, team):
    """Build a placeholder driver row"""
    return {
        "driver_number": number,
        "name_acronym": code,
        "full_name": f"{code.title()} Placeholder",
        "team_name": team,
        "team_colour": "112233",
        "country_code": "PLA",
        "headshot_url": f"https://example.invalid/{code}.png",
    }


def result(position, number, laps, points, **flags):
    """Build a placeholder session_result row"""
    row = {
        "position": position,
        "driver_number": number,
        "number_of_laps": laps,
        "points": points,
        "dnf": False,
        "dns": False,
        "dsq": False,
        "duration": 300.5 if position else None,
        "gap_to_leader": 0,
    }
    row.update(flags)
    return row


def lap(number, lap_number, seconds):
    """Build a placeholder lap row"""
    return {
        "driver_number": number,
        "lap_number": lap_number,
        "lap_duration": seconds,
        "duration_sector_1": 30.0 if seconds else None,
        "duration_sector_2": 30.0 if seconds else None,
        "duration_sector_3": 40.0 if seconds else None,
        "is_pit_out_lap": lap_number == 2,
        "date_start": f"2026-01-01T10:0{lap_number}:00+00:00",
    }


def make_data():
    """Build a small placeholder race with three drivers and the data quirks we saw"""
    return {
        "meetings": [MEETING],
        "sessions": [{"session_key": QUALIFYING_KEY, "session_name": "Qualifying"}],
        "starting_grid": [
            {"position": 2, "driver_number": 11},
            {"position": 1, "driver_number": 22},
            {"position": 3, "driver_number": 33},
        ],
        "drivers": [
            driver(11, "AAA", "Placeholder Team A"),
            driver(22, "BBB", "Placeholder Team B"),
            driver(33, "CCC", "Placeholder Team A"),
        ],
        "session_result": [
            result(1, 11, 3, 25.0),
            result(2, 22, 3, 18.0),
            result(None, 33, 1, 0.0, dnf=True),
        ],
        "laps": [
            lap(11, 1, 100.0),
            lap(11, 2, 99.0),
            lap(11, 3, 98.0),
            lap(11, 4, None),
            lap(22, 1, 101.0),
            lap(22, 2, 100.0),
            lap(22, 3, 99.5),
            lap(33, 1, 102.0),
            lap(33, 2, None),
        ],
        "stints": [
            {"driver_number": 11, "stint_number": 1, "compound": "MEDIUM",
             "lap_start": 1, "lap_end": 2, "tyre_age_at_start": 0},
            {"driver_number": 11, "stint_number": 2, "compound": "HARD",
             "lap_start": 3, "lap_end": 3, "tyre_age_at_start": 0},
            {"driver_number": 22, "stint_number": 1, "compound": "MEDIUM",
             "lap_start": 1, "lap_end": 3, "tyre_age_at_start": 0},
        ],
        # Listed out of time order on purpose; stop_duration must be ignored
        "pit": [
            {"driver_number": 11, "lap_number": 2, "lane_duration": 22.5,
             "stop_duration": None, "date": "2026-01-01T10:05:00+00:00"},
            {"driver_number": 11, "lap_number": 1, "lane_duration": 23.1,
             "stop_duration": 2.4, "date": "2026-01-01T10:02:00+00:00"},
        ],
        "weather": [
            {"date": "2026-01-01T09:58:00+00:00", "air_temperature": 20.0,
             "track_temperature": 30.0, "humidity": 50.0, "pressure": 1000.0,
             "rainfall": 0, "wind_speed": 1.5, "wind_direction": 90},
            {"date": "2026-01-01T10:01:00+00:00", "air_temperature": 21.0,
             "track_temperature": 31.0, "humidity": 55.0, "pressure": 1001.0,
             "rainfall": 1, "wind_speed": 2.5, "wind_direction": 100},
        ],
        # Listed out of time order on purpose
        "race_control": [
            {"date": "2026-01-01T10:03:00+00:00", "lap_number": 2, "category": "Flag",
             "flag": "YELLOW", "scope": "Sector", "sector": 5, "driver_number": None,
             "message": "PLACEHOLDER YELLOW IN SECTOR 5"},
            {"date": "2026-01-01T10:00:00+00:00", "lap_number": 1, "category": "Other",
             "flag": None, "scope": None, "sector": None, "driver_number": None,
             "message": "PLACEHOLDER RACE START"},
            {"date": "2026-01-01T10:04:00+00:00", "lap_number": 2, "category": "Other",
             "flag": None, "scope": None, "sector": None, "driver_number": 33,
             "message": "PLACEHOLDER CAR STOPPED"},
        ],
        "position": [
            {"driver_number": 11, "date": "2026-01-01T10:00:30+00:00", "position": 2},
            {"driver_number": 11, "date": "2026-01-01T10:02:30+00:00", "position": 1},
            {"driver_number": 22, "date": "2026-01-01T10:00:30+00:00", "position": 1},
            {"driver_number": 22, "date": "2026-01-01T10:02:30+00:00", "position": 2},
            {"driver_number": 33, "date": "2026-01-01T10:00:30+00:00", "position": 3},
        ],
    }


class FakeClient:
    """A fake OpenF1 client that returns canned rows per endpoint and records the calls"""

    def __init__(self, data):
        """Keep the canned rows, keyed by endpoint name"""
        self.data = data
        self.calls = []

    def get(self, endpoint, refresh=False, **params):
        """Record the call and return the canned rows for the endpoint"""
        self.calls.append((endpoint, params))
        return copy.deepcopy(self.data.get(endpoint, []))


@pytest.fixture
def data():
    """Return a fresh placeholder race for each test"""
    return make_data()


@pytest.fixture
def conn():
    """Open an in-memory database with the schema created"""
    connection = store.connect(":memory:")
    store.create_schema(connection)
    yield connection
    connection.close()


def count(connection, table, season=None, round_no=None):
    """Count the rows in a table, optionally for one season and round"""
    sql = f"SELECT COUNT(*) FROM {table}"
    if season is None:
        return connection.execute(sql).fetchone()[0]
    sql += " WHERE season = ? AND round = ?"
    return connection.execute(sql, (season, round_no)).fetchone()[0]


def all_counts(connection):
    """Return the row count of every table that ingestion fills"""
    return {table: count(connection, table) for table in structured.RACE_TABLES}


def codes_of(rows):
    """Build the driver number to code lookup used by the row builders"""
    return structured.driver_codes(rows)


def test_number_rounds_orders_races_by_date():
    """Check that rounds are numbered 1, 2, ... by race date, whatever the input order"""
    sessions = [
        {"session_key": 5, "date_start": "2026-03-01T10:00:00+00:00"},
        {"session_key": 3, "date_start": "2026-02-01T10:00:00+00:00"},
        {"session_key": 9, "date_start": "2026-04-01T10:00:00+00:00"},
    ]
    assert structured.number_rounds(sessions) == {3: 1, 5: 2, 9: 3}


def test_code_of_unknown_driver_fails_loudly(data):
    """Check that a driver number missing from the drivers data raises a clear error"""
    codes = codes_of(data["drivers"])
    assert structured.code_of(codes, 11) == "AAA"
    with pytest.raises(ValueError, match="99"):
        structured.code_of(codes, 99)


def test_build_event_uses_meeting_and_session_details():
    """Check that the event row takes names from the meeting and keys from the sessions"""
    row = structured.build_event(2026, 1, RACE_SESSION, MEETING, QUALIFYING_KEY)
    assert row["event_name"] == "Placeholder Grand Prix"
    assert row["official_name"] == "PLACEHOLDER GRAND PRIX 2026"
    assert row["circuit"] == "Placeholder Circuit"
    assert row["country"] == "Placeholderland"
    assert row["race_date"] == "2026-01-01"
    assert (row["race_session_key"], row["qualifying_session_key"]) == (100, 99)


def test_build_entries_gives_team_for_that_race(data):
    """Check that each entry holds the driver code, team and identity data for the race"""
    rows = structured.build_entries(2026, 1, data["drivers"])
    first = rows[0]
    assert first["driver_code"] == "AAA"
    assert first["team"] == "Placeholder Team A"
    assert first["car_number"] == 11
    assert first["team_color"] == "112233"
    assert (first["season"], first["round"]) == (2026, 1)


def test_build_results_takes_grid_from_qualifying_and_status_from_flags(data):
    """Check grid, finish position, status and unclassified drivers in the results"""
    codes = codes_of(data["drivers"])
    rows = {
        r["driver_code"]: r
        for r in structured.build_results(
            2026, 1, data["session_result"], codes, data["starting_grid"]
        )
    }
    assert (rows["AAA"]["grid_pos"], rows["AAA"]["finish_pos"]) == (2, 1)
    assert rows["AAA"]["status"] == "Finished"
    assert rows["AAA"]["points"] == 25.0
    assert rows["AAA"]["race_time_s"] == 300.5
    assert rows["BBB"]["grid_pos"] == 1
    assert rows["CCC"]["finish_pos"] is None
    assert rows["CCC"]["status"] == "DNF"
    assert rows["CCC"]["laps_completed"] == 1
    assert rows["CCC"]["race_time_s"] is None


@pytest.mark.parametrize("flags, status", [({"dns": True}, "DNS"), ({"dsq": True}, "DSQ")])
def test_result_status_for_dns_and_dsq(flags, status):
    """Check that did-not-start and disqualified flags become the status text"""
    assert structured.result_status(result(None, 1, 0, 0.0, **flags)) == status


def test_laps_without_a_time_are_kept_and_flagged(data):
    """Check that laps with no lap time stay in the data with a quality flag"""
    codes = codes_of(data["drivers"])
    rows = structured.build_laps(2026, 1, data["laps"], codes)
    assert len(rows) == 9
    by_key = {(r["driver_code"], r["lap_number"]): r for r in rows}
    assert by_key[("AAA", 4)]["lap_time_s"] is None
    assert by_key[("AAA", 4)]["quality_flag"] == "no_time"
    assert by_key[("AAA", 1)]["quality_flag"] is None
    assert by_key[("AAA", 1)]["lap_time_s"] == 100.0
    assert by_key[("AAA", 2)]["is_pit_out_lap"] == 1
    assert by_key[("AAA", 1)]["is_pit_out_lap"] == 0


def test_stints_get_lap_count(data):
    """Check that each stint gets its number of laps from the start and end lap"""
    codes = codes_of(data["drivers"])
    rows = structured.build_stints(2026, 1, data["stints"], codes)
    by_key = {(r["driver_code"], r["stint_no"]): r for r in rows}
    assert by_key[("AAA", 1)]["n_laps"] == 2
    assert by_key[("AAA", 2)]["n_laps"] == 1
    assert by_key[("BBB", 1)]["n_laps"] == 3
    assert by_key[("AAA", 2)]["compound"] == "HARD"


def test_pit_stops_are_numbered_in_time_order_and_use_lane_time(data):
    """Check stop numbers follow time order and only pit-lane time is stored"""
    codes = codes_of(data["drivers"])
    rows = structured.build_pit_stops(2026, 1, data["pit"], codes)
    assert [(r["stop_no"], r["lap"], r["pit_lane_time_s"]) for r in rows] == [
        (1, 1, 23.1),
        (2, 2, 22.5),
    ]
    assert not any("stop_duration" in r or "stationary" in str(r.keys()) for r in rows)


def test_weather_time_is_seconds_from_race_start(data):
    """Check that session_time_s counts from the race start and can be negative"""
    rows = structured.build_weather(2026, 1, data["weather"], RACE_SESSION["date_start"])
    assert [r["session_time_s"] for r in rows] == [-120.0, 60.0]
    assert [r["rainfall"] for r in rows] == [0, 1]
    assert rows[0]["air_temp"] == 20.0
    assert rows[1]["track_temp"] == 31.0


def test_race_control_is_numbered_in_time_order(data):
    """Check that race-control events are ordered by time and carry the driver code"""
    codes = codes_of(data["drivers"])
    rows = structured.build_race_control(2026, 1, data["race_control"], codes)
    assert [r["event_no"] for r in rows] == [1, 2, 3]
    assert [r["message"] for r in rows] == [
        "PLACEHOLDER RACE START",
        "PLACEHOLDER YELLOW IN SECTOR 5",
        "PLACEHOLDER CAR STOPPED",
    ]
    assert rows[1]["flag"] == "YELLOW"
    assert rows[1]["sector"] == 5
    assert rows[2]["driver_code"] == "CCC"
    assert rows[0]["driver_code"] is None


def test_ingest_race_fills_every_table_with_season_and_round(conn, data):
    """Check that one ingest fills all tables and returns the row counts"""
    counts = structured.ingest_race(conn, FakeClient(data), 2026, RACE_SESSION, 1)
    assert counts == {
        "events": 1,
        "entries": 3,
        "results": 3,
        "laps": 9,
        "stints": 3,
        "pit_stops": 2,
        "weather": 2,
        "race_control": 3,
    }
    for table, expected in counts.items():
        assert count(conn, table, 2026, 1) == expected, table
        assert count(conn, table) == expected, table


def test_grid_is_read_from_the_qualifying_session(conn, data):
    """Check that the starting grid is fetched with the qualifying session key"""
    client = FakeClient(data)
    structured.ingest_race(conn, client, 2026, RACE_SESSION, 1)
    assert ("starting_grid", {"session_key": QUALIFYING_KEY}) in client.calls
    assert ("laps", {"session_key": 100}) in client.calls
    grid = conn.execute("SELECT grid_pos FROM results WHERE driver_code = 'AAA'").fetchone()
    assert grid[0] == 2


def test_missing_qualifying_session_leaves_grid_empty(conn, data):
    """Check that a race with no qualifying session still loads, with no grid positions"""
    data["sessions"] = []
    structured.ingest_race(conn, FakeClient(data), 2026, RACE_SESSION, 1)
    grids = [r[0] for r in conn.execute("SELECT grid_pos FROM results")]
    assert grids == [None, None, None]
    event = conn.execute("SELECT qualifying_session_key FROM events").fetchone()
    assert event[0] is None


def test_ingesting_twice_gives_identical_counts(conn, data):
    """Check idempotency: a second run of the same race creates no duplicates"""
    structured.ingest_race(conn, FakeClient(data), 2026, RACE_SESSION, 1)
    first = all_counts(conn)
    structured.ingest_race(conn, FakeClient(data), 2026, RACE_SESSION, 1)
    assert all_counts(conn) == first


def test_reingest_removes_rows_that_no_longer_exist(conn, data):
    """Check that a changed source replaces the old rows instead of leaving stale ones"""
    structured.ingest_race(conn, FakeClient(data), 2026, RACE_SESSION, 1)
    data["laps"] = [row for row in data["laps"] if row["lap_duration"] is not None]
    structured.ingest_race(conn, FakeClient(data), 2026, RACE_SESSION, 1)
    assert count(conn, "laps") == 7


def test_ingest_leaves_other_races_untouched(conn, data):
    """Check that loading one round does not delete rows from other rounds"""
    structured.ingest_race(conn, FakeClient(data), 2026, RACE_SESSION, 1)
    structured.ingest_race(conn, FakeClient(data), 2026, RACE_SESSION, 2)
    structured.ingest_race(conn, FakeClient(data), 2026, RACE_SESSION, 1)
    assert count(conn, "laps", 2026, 2) == 9
    assert count(conn, "laps", 2026, 1) == 9
    assert count(conn, "laps") == 18


def test_failed_ingest_rolls_back_and_keeps_old_rows(conn, data):
    """Check that an error half way through leaves the earlier data exactly as it was"""
    structured.ingest_race(conn, FakeClient(data), 2026, RACE_SESSION, 1)
    before = all_counts(conn)
    broken = copy.deepcopy(data)
    broken["drivers"][0]["name_acronym"] = None
    with pytest.raises(sqlite3.IntegrityError):
        structured.ingest_race(conn, FakeClient(broken), 2026, RACE_SESSION, 1)
    assert all_counts(conn) == before


def test_ingest_race_fills_derived_columns(conn, data):
    """Check that tyre data, lap positions and stint averages are filled after a load"""
    client = FakeClient(data)
    structured.ingest_race(conn, client, 2026, RACE_SESSION, 1)
    assert ("position", {"session_key": 100}) in client.calls

    laps = {(r["driver_code"], r["lap_number"]): r for r in conn.execute("SELECT * FROM laps")}
    first = laps[("AAA", 1)]
    assert (first["compound"], first["stint"], first["tyre_age"], first["position"]) == (
        "MEDIUM",
        1,
        1,
        2,
    )
    assert laps[("AAA", 3)]["compound"] == "HARD"
    # The trailing lap with no time is outside every stint and has no end time
    assert laps[("AAA", 4)]["compound"] is None
    assert laps[("AAA", 4)]["position"] is None
    assert [laps[("BBB", n)]["position"] for n in (1, 2, 3)] == [1, 2, 2]

    stints = conn.execute("SELECT driver_code, stint_no, avg_lap_time_s FROM stints")
    averages = {(r["driver_code"], r["stint_no"]): r["avg_lap_time_s"] for r in stints}
    assert averages == {("AAA", 1): None, ("AAA", 2): 98.0, ("BBB", 1): 100.25}
