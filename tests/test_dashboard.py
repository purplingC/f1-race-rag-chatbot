import pytest

from f1rag import store
from f1rag.engine import dashboard_planner as dashboard
from f1rag.engine.evidence import EvidenceLog

# Everything below is placeholder data for tests, not real F1 data
RACE = {"season": 2026, "round": 1}


@pytest.fixture
def conn():
    """Build one placeholder race with four drivers and ten laps"""
    c = store.connect(":memory:")
    store.create_schema(c)
    with c:
        c.execute(
            "INSERT INTO events (season, round, event_name, race_date) "
            "VALUES (2026, 1, 'Placeholder Grand Prix', '2026-03-01')"
        )
        c.execute(
            "INSERT INTO events (season, round, event_name, race_date) "
            "VALUES (2026, 2, 'Placeholder Second Grand Prix', '2026-03-15')"
        )
        drivers = [("AAA", 1), ("BBB", 2), ("CCC", 3), ("DDD", None)]
        for code, pos in drivers:
            c.execute(
                "INSERT INTO entries (season, round, driver_code, team) VALUES (2026, 1, ?, 'T')",
                (code,),
            )
            c.execute(
                "INSERT INTO results (season, round, driver_code, finish_pos) "
                "VALUES (2026, 1, ?, ?)",
                (code, pos),
            )
            c.executemany(
                "INSERT INTO laps (season, round, driver_code, lap_number) VALUES (2026, 1, ?, ?)",
                [(code, lap) for lap in range(1, 11)],
            )
    yield c
    c.close()


def evidence(*ids):
    """Build an evidence log with the given number of placeholder items"""
    log = EvidenceLog()
    for _ in ids:
        log.add("run_sql", {"query": "SELECT 1"}, {"ok": True, "rows": [], "row_count": 0})
    return log


def spec(*widgets, race=RACE, cited=("E1",)):
    """Build a raw dashboard spec as the model would write it"""
    return {"race": dict(race), "widgets": list(widgets), "evidence": list(cited)}


def test_a_valid_spec_passes_and_driver_codes_are_tidied(conn):
    """Check a correct spec is accepted, with codes upper-cased"""
    raw = spec(
        {"type": "driver_card", "drivers": ["aaa", "BBB"]},
        {"type": "lap_time_chart", "drivers": ["AAA", "BBB"], "laps": [2, 8]},
        {"type": "pit_stops_table", "drivers": ["AAA"]},
    )
    result, problems = dashboard.validate(raw, conn, evidence("E1"))
    assert problems == []
    assert result.widgets[0].drivers == ["AAA", "BBB"]
    assert result.widgets[1].laps == (2, 8)


@pytest.mark.parametrize(
    "raw, word",
    [
        (spec({"type": "weather_strip"}), "weather_strip"),
        (spec({"type": "driver_card", "drivers": ["ZZZ"]}), "ZZZ"),
        (spec({"type": "classification"}, race={"season": 2026, "round": 9}), "not loaded"),
        (spec({"type": "lap_time_chart", "drivers": ["AAA"], "laps": [20, 30]}), "lap"),
        (spec({"type": "classification"}, cited=("E7",)), "E7"),
        (spec({"type": "lap_time_chart", "laps": [1, 5]}), "needs drivers"),
        (spec({"type": "classification", "colour": "red"}), "colour"),
        (spec({"type": "classification"}, {"type": "classification"}), "twice"),
    ],
)
def test_invalid_specs_are_rejected_with_a_reason(conn, raw, word):
    """Check unknown widgets, drivers, races, laps, evidence and fields are refused"""
    result, problems = dashboard.validate(raw, conn, evidence("E1"))
    assert result is None
    assert any(word in problem for problem in problems)


def test_more_than_four_main_widgets_is_rejected_but_driver_cards_do_not_count(conn):
    """Check the four-widget limit applies to charts and tables, not to driver cards"""
    four = [
        {"type": "classification"}, {"type": "tyre_strategy"},
        {"type": "position_chart"}, {"type": "race_summary_card"},
    ]
    ok, _ = dashboard.validate(
        spec({"type": "driver_card", "drivers": ["AAA"]}, *four), conn, evidence("E1")
    )
    assert ok is not None
    too_many, problems = dashboard.validate(
        spec(*four, {"type": "pit_stops_table"}), conn, evidence("E1")
    )
    assert too_many is None
    assert any("four" in problem for problem in problems)


def test_a_lap_range_that_runs_past_the_end_is_trimmed(conn):
    """Check a range partly outside the race is cut to the race length"""
    raw = spec({"type": "lap_time_chart", "drivers": ["AAA"], "laps": [8, 40]})
    result, problems = dashboard.validate(raw, conn, evidence("E1"))
    assert problems == []
    assert result.widgets[0].laps == (8, 10)


def test_a_spec_that_is_not_an_object_is_rejected(conn):
    """Check text or a list instead of a spec is refused, not crashed on"""
    for raw in (None, "classification please", [1, 2]):
        result, problems = dashboard.validate(raw, conn, evidence())
        assert result is None and problems


def sql_log(rows, query="SELECT driver_code FROM results WHERE season = 2026 AND round = 1"):
    """Build an evidence log with one SQL result"""
    log = EvidenceLog()
    log.add("run_sql", {"query": query}, {"ok": True, "query": query, "columns": ["x"],
                                         "rows": rows, "row_count": len(rows)})
    return log


def types(result):
    """List the widget types of a plan"""
    return [w.type for w in result.spec.widgets]


@pytest.mark.parametrize(
    "question, expected",
    [
        ("Who won the race?", ["driver_card", "classification", "race_summary_card"]),
        ("Summarise the race", ["race_summary_card", "classification", "tyre_strategy"]),
        ("What was AAA's tyre strategy and pit stops?",
         ["driver_card", "tyre_strategy", "pit_stops_table"]),
        ("Compare AAA and BBB", ["driver_card", "lap_time_chart", "tyre_strategy"]),
        ("Why did BBB lose a position?",
         ["driver_card", "lap_time_chart", "position_chart", "pit_stops_table"]),
        ("Was there a steward penalty?", ["driver_card", "classification"]),
    ],
)
def test_fallback_picks_widgets_for_each_question_type(conn, question, expected):
    """Check the rule-based planner follows the question-type table of Section 7.4"""
    log = sql_log([["AAA"], ["BBB"]])
    result = dashboard.plan(question, None, log, conn)
    assert result.source == "fallback"
    assert types(result) == expected
    assert (result.spec.race.season, result.spec.race.round) == (2026, 1)


def test_fallback_takes_drivers_from_the_results_and_podium_for_winners(conn):
    """Check drivers come from tool results, and a result question shows the podium"""
    compare = dashboard.plan("Compare them", None, sql_log([["BBB", 1.5], ["CCC", 2.0]]), conn)
    assert compare.spec.widgets[0].drivers == ["BBB", "CCC"]
    won = dashboard.plan("Who won?", None, sql_log([["AAA"]]), conn)
    assert won.spec.widgets[0].drivers == ["AAA", "BBB", "CCC"]


def test_the_models_spec_is_used_when_valid_and_replaced_when_not(conn):
    """Check a valid model spec wins, and an invalid one falls back with the reasons kept"""
    log = sql_log([["AAA"]])
    good = dashboard.plan("Who won?", spec({"type": "classification"}), log, conn)
    assert good.source == "model" and types(good) == ["classification"]
    bad = dashboard.plan("Who won?", spec({"type": "weather_strip"}), log, conn)
    assert bad.source == "fallback"
    assert any("weather_strip" in p for p in bad.problems)


def test_no_evidence_means_no_new_widgets(conn):
    """Check an answer with no evidence keeps the default overview instead of new widgets"""
    assert dashboard.plan("Hello", None, EvidenceLog(), conn) is None


def test_race_comes_from_find_race_before_the_default(conn):
    """Check the race found by find_race is used, and the default only when none was found"""
    log = EvidenceLog()
    log.add("find_race", {"name": "x"}, {"matches": [{"season": 2026, "round": 2}]})
    log.add("run_sql", {"query": "SELECT 1"}, {"ok": True, "rows": [], "row_count": 0})
    found = dashboard.plan("Summarise the race", None, log, conn)
    assert (found.spec.race.season, found.spec.race.round) == (2026, 2)
    plain = EvidenceLog()
    plain.add("run_sql", {"query": "SELECT 1"}, {"ok": True, "rows": [], "row_count": 0})
    default = dashboard.plan("Summarise the race", None, plain, conn, default_race=RACE)
    assert (default.spec.race.season, default.spec.race.round) == (2026, 1)


def test_default_overview_before_the_first_question(conn):
    """Check the start page shows classification, tyre strategy and the race summary"""
    overview = dashboard.default_overview(conn, RACE)
    assert [w.type for w in overview.widgets] == [
        "classification", "tyre_strategy", "race_summary_card",
    ]


def test_ordinary_words_in_the_question_are_not_taken_for_driver_codes(conn):
    """Check a lower-case word that looks like a code is ignored, a capital code is used"""
    with conn:
        conn.execute(
            "INSERT INTO entries (season, round, driver_code, team) VALUES (2026, 1, 'PER', 'T')"
        )
    log = sql_log([])
    plain = dashboard.plan("Compare laps per stint", None, log, conn)
    assert all("PER" not in w.drivers for w in plain.spec.widgets)
    named = dashboard.plan("Compare PER and AAA", None, log, conn)
    assert named.spec.widgets[0].drivers == ["PER", "AAA"]


def test_drivers_named_in_the_question_are_the_ones_shown(conn):
    """Check a question that names drivers, by code or surname, shows only those drivers"""
    with conn:
        conn.execute(
            "UPDATE entries SET full_name = 'Driver BRAVOSON' WHERE season = 2026 AND round = 1 "
            "AND driver_code = 'BBB'"
        )
    log = sql_log([["AAA"], ["CCC"], ["BBB"]])
    by_code = dashboard.plan("Why did BBB lose a position?", None, log, conn)
    assert by_code.spec.widgets[0].drivers == ["BBB"]
    by_name = dashboard.plan("Why did Bravoson lose a position?", None, log, conn)
    assert by_name.spec.widgets[0].drivers == ["BBB"]
    unnamed = dashboard.plan("Why did they lose a position?", None, log, conn)
    assert unnamed.spec.widgets[0].drivers == ["AAA", "CCC", "BBB"]
