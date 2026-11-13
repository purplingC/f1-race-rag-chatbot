import typing

import pytest

from f1rag import config, golden, store
from f1rag.engine.dashboard_planner import DashboardSpec, PlanResult, WidgetType
from f1rag.engine.evidence import AnswerBundle, EvidenceItem

# Everything below is placeholder data for tests, not real F1 data

GOLDEN_TOML = """
[groups]
refusal = ["don't have", "not loaded"]

[aliases]
"Placeholder Racing Team" = ["Placeholder"]

[[question]]
id = "S01"
category = "statistical"
question = "Who won the Placeholder Grand Prix?"
race = [2026, 1]
tools = ["run_sql"]
truth = "SELECT driver_code FROM results WHERE season = 2026 AND round = 1 AND finish_pos = 1"
widgets = [["driver_card", "classification"]]
dashboard_drivers = true

[[question]]
id = "T01"
category = "trap"
question = "Who won the 1999 Placeholder Grand Prix?"
keywords = ["@refusal", ["2026"]]
forbid = ["km/h"]
"""


def make_conn():
    """Create an in-memory database with one placeholder race"""
    conn = store.connect(":memory:")
    store.create_schema(conn)
    with conn:
        conn.execute("INSERT INTO events (season, round, event_name) "
                     "VALUES (2026, 1, 'Placeholder Grand Prix')")
        for code, name, team, pos, grid in (
            ("AAA", "Alpha AARDVARK", "Placeholder Racing Team", 1, 3),
            ("BBB", "Beta BÉLANGER", "Other Team", 2, 1),
            ("CCC", "Gamma CORMORANT", "Other Team", 3, 2),
        ):
            conn.execute("INSERT INTO entries (season, round, driver_code, full_name, team) "
                         "VALUES (2026, 1, ?, ?, ?)", (code, name, team))
            conn.execute("INSERT INTO results (season, round, driver_code, finish_pos, grid_pos, "
                         "points) VALUES (2026, 1, ?, ?, ?, ?)", (code, pos, grid, 10.0 / pos))
        conn.execute("INSERT INTO laps (season, round, driver_code, lap_number, lap_time_s) "
                     "VALUES (2026, 1, 'AAA', 7, 92.456)")
    return conn


def question(**fields):
    """Build a golden question with placeholder defaults"""
    base = {"id": "X01", "category": "statistical", "question": "Placeholder?"}
    return golden.Question(**{**base, **fields})


def sql_item(item_id="E1", ok=True):
    """Build a run_sql evidence item"""
    return EvidenceItem(item_id, "run_sql", {"query": "SELECT 1"},
                        {"ok": ok, "query": "SELECT 1", "rows": [[92.456, "AAA"]]}, "SQL result")


def doc_item(item_id="E2", season=2026, round_no=1):
    """Build a search_docs evidence item with one passage"""
    passage = {"season": season, "round": round_no, "title": "Placeholder",
               "text": "The race was stopped on lap 12 after rain."}
    return EvidenceItem(item_id, "search_docs", {"query": "rain"}, {"passages": [passage]}, "doc")


def answer(text, evidence=(), cited=(), tools=(), dashboard=None, error=None):
    """Build an answer bundle with placeholder evidence"""
    trace = [{"tool": tool} for tool in tools]
    return AnswerBundle(question="Q", answer_md=text, evidence=list(evidence), cited=list(cited),
                        trace=trace, dashboard=dashboard, error=error, total_ms=4200)


def board(season=2026, round_no=1, widgets=()):
    """Build a dashboard plan from (type, drivers) pairs"""
    spec = DashboardSpec.model_validate({
        "race": {"season": season, "round": round_no},
        "widgets": [{"type": kind, "drivers": list(drivers)} for kind, drivers in widgets],
    })
    return PlanResult(spec=spec, source="model")


def test_load_reads_questions_groups_and_aliases(tmp_path):
    """Check questions load with group references expanded and truth as a list"""
    path = tmp_path / "golden.toml"
    path.write_text(GOLDEN_TOML, encoding="utf-8")
    questions, aliases = golden.load(path)
    assert [q.id for q in questions] == ["S01", "T01"]
    assert questions[0].truth == [
        "SELECT driver_code FROM results WHERE season = 2026 AND round = 1 AND finish_pos = 1"
    ]
    assert questions[0].race == (2026, 1)
    assert questions[1].keywords == [["don't have", "not loaded"], ["2026"]]
    assert questions[1].tools is None
    assert aliases == {"Placeholder Racing Team": ["Placeholder"]}


@pytest.mark.parametrize("bad, message", [
    ('[[question]]\nid = "A"\ncategory = "poem"\nquestion = "Q"', "category"),
    ('[[question]]\nid = "A"\ncategory = "trap"\nquestion = "Q"\ncolour = 1', "colour"),
    ('[[question]]\nid = "A"\ncategory = "trap"\nquestion = "Q"\nkeywords = ["@nope"]', "@nope"),
    ('[[question]]\nid = "A"\ncategory = "trap"\nquestion = "Q"\ntools = ["guess"]', "guess"),
    ('[[question]]\nid = "A"\ncategory = "trap"\nquestion = "Q"\n'
     '[[question]]\nid = "A"\ncategory = "trap"\nquestion = "Q"', "twice"),
    ('[[question]]\nid = "A"\ncategory = "statistical"\nquestion = "Q"', "truth"),
])
def test_load_refuses_mistakes_with_a_clear_message(tmp_path, bad, message):
    """Check a broken golden file is refused with a message naming the problem"""
    path = tmp_path / "golden.toml"
    path.write_text(bad, encoding="utf-8")
    with pytest.raises(golden.GoldenError, match=message):
        golden.load(path)


def test_real_golden_set_follows_the_planned_mix():
    """Check the shipped golden set loads and has the Section 9.2 mix of questions"""
    questions, _ = golden.load(config.GOLDEN_PATH)
    counts = {c: sum(q.category == c for q in questions) for c in golden.CATEGORIES}
    assert counts == {"statistical": 12, "narrative": 8, "hybrid": 5, "trap": 5}
    registry = set(typing.get_args(WidgetType))
    for q in questions:
        assert {w for group in q.widgets for w in group} <= registry, q.id
        if q.category in ("statistical", "hybrid"):
            assert q.truth, q.id
        if q.category in ("narrative", "hybrid"):
            assert q.keywords and q.race, q.id


def test_numbers_in_reads_digits_lap_times_and_words_but_not_citations():
    """Check numbers come out with their decimals, lap times in seconds, citations skipped"""
    found = golden.numbers_in("Lap 1:32.456 on lap 7, two stops, 23.4 s [E1, E12].")
    assert [(value, decimals) for _, value, decimals in found] == [
        (92.456, 3), (7, 0), (2, 0), (23.4, 1)
    ]
    assert [v for _, v, _ in golden.numbers_in("two stops", words=False)] == []


@pytest.mark.parametrize("truth, value, decimals, ok", [
    (92.456, 92.456, 3, True),
    (92.456, 92.5, 1, True),
    (92.456, 92.4, 1, False),
    (92.456, 92, 0, False),
    (25.0, 25, 0, True),
    (3, 3, 0, True),
    (3, 4, 0, False),
])
def test_number_matches_allows_rounding_to_the_answers_precision(truth, value, decimals, ok):
    """Check an answer may round a true value but not change it"""
    assert golden.number_matches(truth, value, decimals) is ok


def test_driver_found_by_code_or_surname_with_or_without_accents():
    """Check a driver counts as named by code or by surname, accents ignored"""
    names = golden.driver_names(make_conn())
    assert golden.find_value("BBB", "Belanger was second", names, {}) is not None
    assert golden.find_value("BBB", "BBB was second", names, {}) is not None
    assert golden.find_value("BBB", "Bélanger was second", names, {}) is not None
    # A code only counts in capitals and as a whole word
    assert golden.find_value("AAA", "the baaad lap", names, {}) is None


def test_other_strings_found_by_name_or_alias():
    """Check a team or compound counts when written in any case or as an alias"""
    aliases = {"Placeholder Racing Team": ["Placeholder"]}
    assert golden.find_value("Placeholder Racing Team", "for placeholder", {}, aliases) == 4
    assert golden.find_value("HARD", "on the hard tyres", {}, {}) is not None
    assert golden.find_value("SOFT", "on the hard tyres", {}, {}) is None


def test_truth_runs_with_pandas_and_reports_empty_results():
    """Check truth queries return their rows, and an empty result is a golden-set problem"""
    conn = make_conn()
    rows, problems = golden.truth_rows(conn, question(truth=[
        "SELECT driver_code, lap_time_s FROM laps WHERE lap_number = 7",
    ]))
    assert rows == [["AAA", 92.456]] and problems == []
    _, problems = golden.truth_rows(conn, question(truth=["SELECT 1 FROM laps WHERE 0"]))
    assert problems == ["truth query returned no rows: SELECT 1 FROM laps WHERE 0"]
    _, problems = golden.truth_rows(conn, question(truth=["SELECT nope FROM laps"]))
    assert problems[0].startswith("truth query failed")


def test_answer_check_finds_values_order_keywords_and_forbidden_words():
    """Check the answer check reports each missing value, wrong order, keyword and banned word"""
    names = golden.driver_names(make_conn())
    q = question(ordered=True, keywords=[["rain", "wet"]], forbid=["km/h"])
    rows = [["AAA"], ["BBB"], ["CCC"]]
    good = answer("Aardvark won [E1], ahead of Belanger and Cormorant, in the rain.")
    assert golden.check_answer(q, good, rows, names, {}) == []
    bad = answer("Belanger beat Aardvark at 300 km/h.")
    assert golden.check_answer(q, bad, rows, names, {}) == [
        "missing CCC", "wrong order", "missing one of: rain / wet", "says km/h",
    ]
    assert golden.check_answer(q, answer("", error="Gemini busy"), rows, names, {}) == [
        "no answer: Gemini busy"
    ]


def test_routing_compares_the_data_tools_used():
    """Check routing passes on the expected data tools and names any missing or extra one"""
    q = question(tools=["run_sql"])
    assert golden.check_routing(q, answer("x", tools=["find_race", "run_sql"])) == []
    assert golden.check_routing(q, answer("x", tools=["search_docs"])) == [
        "did not call run_sql", "also called search_docs",
    ]
    assert golden.check_routing(question(), answer("x", tools=["run_sql"])) is None


def test_sources_need_cited_evidence_of_each_tool_about_the_race():
    """Check each expected data tool has a cited, working result about the question's race"""
    q = question(tools=["run_sql", "search_docs"], race=(2026, 1))
    both = answer("x", evidence=[sql_item(), doc_item()], cited=["E1", "E2"])
    assert golden.check_sources(q, both) == []
    uncited = answer("x", evidence=[sql_item(), doc_item()], cited=["E1"])
    assert golden.check_sources(q, uncited) == ["no cited search_docs result"]
    other_race = answer("x", evidence=[sql_item(), doc_item(round_no=2)], cited=["E1", "E2"])
    assert golden.check_sources(q, other_race) == ["no cited passage about 2026 round 1"]


def test_dashboard_needs_the_race_widgets_and_drivers():
    """Check the dashboard has the right race, one widget of each group and the truth drivers"""
    names = golden.driver_names(make_conn())
    q = question(race=(2026, 1), widgets=[["driver_card", "classification"]],
                 dashboard_drivers=True)
    rows = [["AAA"]]
    good = answer("x", dashboard=board(widgets=[("driver_card", ["AAA"])]))
    assert golden.check_dashboard(q, good, rows, names) == []
    # A whole-field widget with no drivers named shows every driver
    field = answer("x", dashboard=board(widgets=[("classification", [])]))
    assert golden.check_dashboard(q, field, rows, names) == []
    wrong = answer("x", dashboard=board(round_no=2, widgets=[("lap_time_chart", ["BBB"])]))
    assert golden.check_dashboard(q, wrong, rows, names) == [
        "race 2026 round 2, expected 2026 round 1",
        "no widget of: driver_card / classification",
        "AAA not shown",
    ]
    assert golden.check_dashboard(q, answer("x"), rows, names) == ["no dashboard"]
    assert golden.check_dashboard(question(), answer("x"), rows, names) is None


def test_unsupported_numbers_lists_numbers_missing_from_tool_output():
    """Check numbers that appear in no tool result are listed for review"""
    bundle = answer("The best lap was 1:32.5 on lap 12, with 99 laps of rain [E1, E2].",
                    evidence=[sql_item(), doc_item()])
    assert golden.unsupported_numbers(bundle) == ["99"]


def test_unsupported_numbers_skip_list_markers_and_numbers_from_the_question():
    """Check list numbers and numbers the user wrote in the question are not flagged"""
    text = "Ranked [E1]: 1. Aardvark [E1] 2. Belanger\n3. Cormorant. No 1999 data on lap 4. "
    bundle = answer(text + "Then 99 laps.", evidence=[sql_item()])
    bundle.question = "Who won in 1999?"
    assert golden.unsupported_numbers(bundle) == ["4", "99"]


def test_wait_seconds_adds_up_the_rate_limit_pauses():
    """Check the time spent waiting on Gemini's rate limit is worked out from the retries"""
    bundle = answer("x")
    bundle.model_calls = [{"retries": 0}, {"retries": 2}, {"retries": 3, "failed": True}]
    assert golden.wait_seconds(bundle) == 5 + 15 + 5 + 15 + 40


def test_score_combines_the_checks():
    """Check a score passes only when every check that applies passes"""
    conn = make_conn()
    names = golden.driver_names(conn)
    q = question(race=(2026, 1), tools=["run_sql"],
                 truth=["SELECT driver_code FROM results WHERE finish_pos = 1"],
                 widgets=[["classification"]])
    bundle = answer("Aardvark won [E1].", evidence=[sql_item()], cited=["E1"], tools=["run_sql"],
                    dashboard=board(widgets=[("classification", [])]))
    score = golden.score(q, bundle, conn, names, {})
    assert score.passed and score.expected == [["AAA"]] and score.seconds == 4.2
    bundle.answer_md = "Belanger won [E1]."
    assert not golden.score(q, bundle, conn, names, {}).passed


def test_metrics_compare_results_with_the_targets():
    """Check each metric reports its share, its target and whether it was met"""
    conn = make_conn()
    names = golden.driver_names(conn)
    stat = question(truth=["SELECT driver_code FROM results WHERE finish_pos = 1"],
                    tools=["run_sql"])
    trap = question(id="T01", category="trap", keywords=[["don't have"]])
    scores = [
        golden.score(stat, answer("Aardvark won [E1].", evidence=[sql_item()], cited=["E1"],
                                  tools=["run_sql"]), conn, names, {}),
        golden.score(trap, answer("I don't have that."), conn, names, {}),
    ]
    rows = {row["metric"]: row for row in golden.metrics(scores)}
    assert rows["Numeric accuracy"]["result"] == "1/1 (100%)"
    assert rows["Numeric accuracy"]["met"] is True
    assert rows["Routing accuracy"]["result"] == "1/1 (100%)"
    assert rows["Refusal correctness"]["met"] is True
    assert rows["Evidence linkage"]["result"] == "1/1 (100%)"
    assert rows["Latency p50"]["result"] == "4.2 s"
    assert rows["Latency p50 without waits"]["result"] == "4.2 s"
    assert rows["Groundedness (automatic)"]["met"] is None
    # A category with no questions is shown as not run rather than as a pass
    assert rows["Hybrid answers"]["result"] == "not run"


def test_unsupported_numbers_skip_rank_cells_in_a_table():
    """Check a rank column such as | 1 | in a Markdown table is not flagged"""
    bundle = answer("| Rank | Driver |\n|---|---|\n| 1 | Aardvark [E1] |\n| 2 | Belanger |",
                    evidence=[sql_item()])
    assert golden.unsupported_numbers(bundle) == []
