import pytest

from f1rag import store
from f1rag.engine import sql_guard
from f1rag.ui import widget_data as wd

# Everything below is placeholder data for tests, not real F1 data
S, R = 2026, 1


@pytest.fixture
def conn(tmp_path):
    """Build one placeholder race and open it read-only, as the app does"""
    path = tmp_path / "f1.db"
    c = store.connect(path)
    store.create_schema(c)
    with c:
        c.execute("INSERT INTO events (season, round, event_name) VALUES (?, ?, 'Placeholder GP')",
                  (S, R))
        entries = [
            ("AAA", "Driver Alpha", "Team A", 1, "112233"),
            ("BBB", "Driver Beta", "Team A", 2, "112233"),
            ("CCC", "Driver Gamma", "Team C", 3, ""),
        ]
        c.executemany(
            "INSERT INTO entries (season, round, driver_code, full_name, team, car_number, "
            "team_color) VALUES (?, ?, ?, ?, ?, ?, ?)",
            [(S, R, *e) for e in entries],
        )
        # BBB starts third and wins, AAA from pole is second, CCC retires
        c.executemany(
            "INSERT INTO results (season, round, driver_code, grid_pos, finish_pos, status, "
            "points, laps_completed) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(S, R, "AAA", 1, 2, "Finished", 18, 4), (S, R, "BBB", 3, 1, "Finished", 25, 4),
             (S, R, "CCC", 2, None, "DNF", 0, 2)],
        )
        laps = [
            ("AAA", 1, 91.0, "SOFT", 0, 1), ("AAA", 2, 90.0, "SOFT", 0, 1),
            ("AAA", 3, 95.0, "HARD", 1, 1), ("AAA", 4, 90.5, "HARD", 0, 2),
            ("BBB", 1, 92.0, "MEDIUM", 0, 3), ("BBB", 2, 89.5, "MEDIUM", 0, 2),
            ("BBB", 3, 89.9, "MEDIUM", 0, 2), ("BBB", 4, None, "MEDIUM", 0, 1),
            ("CCC", 1, 93.0, "SOFT", 0, 2), ("CCC", 2, 94.0, "SOFT", 0, 3),
        ]
        c.executemany(
            "INSERT INTO laps (season, round, driver_code, lap_number, lap_time_s, compound, "
            "is_pit_out_lap, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(S, R, *lap) for lap in laps],
        )
        c.executemany(
            "INSERT INTO stints (season, round, driver_code, stint_no, compound, start_lap, "
            "end_lap, n_laps) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [(S, R, "AAA", 1, "SOFT", 1, 2, 2), (S, R, "AAA", 2, "HARD", 3, 4, 2),
             (S, R, "BBB", 1, "MEDIUM", 1, 4, 4), (S, R, "CCC", 1, "SOFT", 1, 2, 2)],
        )
        c.executemany(
            "INSERT INTO pit_stops (season, round, driver_code, stop_no, lap, pit_lane_time_s) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(S, R, "AAA", 1, 2, 22.0), (S, R, "CCC", 1, 1, 24.0), (S, R, "CCC", 2, 2, 30.0)],
        )
        c.executemany(
            "INSERT INTO race_control (season, round, event_no, lap_number, category, message) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(S, R, 1, 2, "SafetyCar", "SAFETY CAR DEPLOYED"),
             (S, R, 2, 3, "SafetyCar", "VSC DEPLOYED"),
             (S, R, 3, 3, "Other", "RED FLAG - RACE SUSPENDED")],
        )
        c.execute("INSERT INTO race_summaries (season, round, summary_md, generated_at, model) "
                  "VALUES (?, ?, 'Placeholder summary.', '2026-03-01T00:00:00', 'fake')", (S, R))
    c.close()
    ro = sql_guard.connect_read_only(path)
    yield ro
    ro.close()


def test_driver_cards_hold_identity_result_and_best_lap(conn):
    """Check each card has name, team, colour, grid to finish, points and best lap"""
    cards = {c["code"]: c for c in wd.driver_cards(conn, S, R, ["bbb", "AAA"])}
    assert list(cards) == ["BBB", "AAA"]
    assert cards["BBB"]["name"] == "Driver Beta" and cards["BBB"]["team"] == "Team A"
    assert cards["BBB"]["team_color"] == "#112233"
    assert (cards["BBB"]["grid"], cards["BBB"]["finish"], cards["BBB"]["gained"]) == (3, 1, 2)
    assert cards["BBB"]["best_lap_s"] == 89.5
    assert cards["AAA"]["points"] == 18


def test_unknown_or_missing_team_colour_gets_a_neutral_colour(conn):
    """Check a driver with no team colour still gets a usable colour"""
    card = wd.driver_cards(conn, S, R, ["CCC"])[0]
    assert card["team_color"] == wd.NEUTRAL_COLOR
    assert card["finish"] is None and card["status"] == "DNF"


def test_classification_lists_classified_drivers_first(conn):
    """Check the order is by finishing position, with unclassified drivers at the end"""
    rows = wd.classification(conn, S, R)
    assert [r["code"] for r in rows] == ["BBB", "AAA", "CCC"]
    assert rows[0]["position"] == 1 and rows[2]["position"] is None
    assert rows[2]["status"] == "DNF"


def test_lap_times_skip_laps_with_no_time_and_mark_pit_laps(conn):
    """Check lap times leave out untimed laps, respect the lap range and list pit laps"""
    data = wd.lap_times(conn, S, R, ["AAA", "BBB"], laps=(2, 4))
    by_driver = {}
    for row in data["laps"]:
        by_driver.setdefault(row["code"], []).append(row["lap"])
    assert by_driver == {"AAA": [2, 3, 4], "BBB": [2, 3]}
    assert data["pit_laps"] == {"AAA": [2]}


def test_tyre_strategy_follows_the_finishing_order(conn):
    """Check stints come per driver in finishing order, or only for the drivers asked for"""
    rows = wd.tyre_strategy(conn, S, R)
    assert [(r["code"], r["compound"], r["start_lap"], r["end_lap"]) for r in rows] == [
        ("BBB", "MEDIUM", 1, 4), ("AAA", "SOFT", 1, 2), ("AAA", "HARD", 3, 4),
        ("CCC", "SOFT", 1, 2),
    ]
    assert {r["code"] for r in wd.tyre_strategy(conn, S, R, ["CCC"])} == {"CCC"}


def test_pit_stops_compare_each_stop_with_the_race_median(conn):
    """Check each stop shows its pit-lane time and the difference to the race median"""
    data = wd.pit_stops(conn, S, R)
    assert data["median_s"] == 24.0
    rows = {(r["code"], r["stop"]): r for r in data["stops"]}
    assert rows[("AAA", 1)]["vs_median_s"] == -2.0
    assert rows[("CCC", 2)]["vs_median_s"] == 6.0
    assert [r["code"] for r in wd.pit_stops(conn, S, R, ["AAA"])["stops"]] == ["AAA"]


def test_positions_default_to_the_top_finishers(conn):
    """Check positions per lap, for the asked drivers or else the top finishers"""
    rows = wd.positions(conn, S, R, ["AAA"])
    assert [(r["lap"], r["position"]) for r in rows] == [(1, 1), (2, 1), (3, 1), (4, 2)]
    codes = {r["code"] for r in wd.positions(conn, S, R)}
    assert codes == {"AAA", "BBB"}


def test_race_summary_card_has_the_summary_and_key_facts(conn):
    """Check the card shows the stored summary, winner, fastest lap and interruptions"""
    card = wd.race_summary(conn, S, R)
    assert card["summary_md"] == "Placeholder summary."
    assert card["winner"] == "Driver Beta"
    assert (card["fastest_lap"]["code"], card["fastest_lap"]["time_s"]) == ("BBB", 89.5)
    assert (card["safety_cars"], card["virtual_safety_cars"], card["red_flags"]) == (1, 1, 1)


def test_a_race_without_data_gives_empty_results_not_errors(conn):
    """Check a race with nothing loaded returns empty data rather than failing"""
    assert wd.classification(conn, S, 9) == []
    assert wd.race_summary(conn, S, 9)["summary_md"] is None
    assert wd.pit_stops(conn, S, 9) == {"stops": [], "median_s": None}


def test_widget_data_is_fetched_by_widget_type(conn):
    """Check one entry point serves every widget type in the registry"""
    from f1rag.engine import dashboard_planner as dp

    for widget_type in dp.WidgetType.__args__:
        widget = dp.Widget(type=widget_type, drivers=["AAA"])
        assert wd.fetch(conn, S, R, widget) is not None


def test_stints_that_miss_pit_stops_are_flagged_as_incomplete(conn, tmp_path):
    """Check a driver whose stints do not cover every pit stop is marked incomplete"""
    rows = wd.tyre_strategy(conn, S, R)
    complete = {r["code"]: r["complete"] for r in rows}
    # AAA: 2 stints, 1 stop; CCC: 1 stint but 2 stops in the placeholder data
    assert complete == {"BBB": True, "AAA": True, "CCC": False}


def test_a_suspended_start_is_reported_apart_from_red_flags(tmp_path):
    """Check a suspended starting procedure is its own fact, not counted as a red flag"""
    path = tmp_path / "f1.db"
    c = store.connect(path)
    store.create_schema(c)
    with c:
        c.execute(
            "INSERT INTO race_control (season, round, event_no, lap_number, category, message) "
            "VALUES (2026, 1, 1, 1, 'Other', 'STARTING PROCEDURE SUSPENDED')"
        )
    c.close()
    ro = sql_guard.connect_read_only(path)
    card = wd.race_summary(ro, 2026, 1)
    ro.close()
    assert card["red_flags"] == 0
    assert card["start_suspended"] is True


def test_a_stop_with_no_stint_after_it_is_incomplete_even_if_counts_match(tmp_path):
    """Check stints are matched to stops by lap, not just counted"""
    path = tmp_path / "f1.db"
    c = store.connect(path)
    store.create_schema(c)
    with c:
        c.execute("INSERT INTO results (season, round, driver_code, finish_pos) "
                  "VALUES (2026, 1, 'XXX', 1)")
        # Two short stints around a suspended start, then one stint that hides a stop on lap 5
        c.executemany(
            "INSERT INTO stints (season, round, driver_code, stint_no, compound, start_lap, "
            "end_lap, n_laps) VALUES (2026, 1, 'XXX', ?, 'SOFT', ?, ?, ?)",
            [(1, 1, 1, 1), (2, 2, 2, 1), (3, 3, 10, 8)],
        )
        c.execute("INSERT INTO pit_stops (season, round, driver_code, stop_no, lap, "
                  "pit_lane_time_s) VALUES (2026, 1, 'XXX', 1, 5, 22.0)")
    c.close()
    ro = sql_guard.connect_read_only(path)
    rows = wd.tyre_strategy(ro, 2026, 1)
    ro.close()
    assert {r["complete"] for r in rows} == {False}


def test_race_drivers_maps_codes_to_name_team_and_colour(conn):
    """Check the driver map used by charts gives name, team and a usable colour per code"""
    drivers = wd.race_drivers(conn, S, R)
    assert drivers["AAA"] == {"name": "Driver Alpha", "team": "Team A", "team_color": "#112233"}
    assert drivers["CCC"]["team_color"] == wd.NEUTRAL_COLOR


def test_chart_rows_follow_the_requested_or_finishing_order(conn):
    """Check rows come in the order asked for, or finishing order, so the leader is drawn first"""
    codes = [r["code"] for r in wd.lap_times(conn, S, R, ["BBB", "AAA"])["laps"]]
    assert list(dict.fromkeys(codes)) == ["BBB", "AAA"]
    default = [r["code"] for r in wd.positions(conn, S, R)]
    assert list(dict.fromkeys(default)) == ["BBB", "AAA"]
