import re

import pytest

from f1rag import store
from f1rag.engine import dashboard_planner as dp
from f1rag.engine import sql_guard
from f1rag.engine.evidence import EvidenceLog
from f1rag.ui import page

# Everything below is placeholder data for tests, not real F1 data


def shown_ids(html):
    """Find evidence ids such as E1 that appear as text, ignoring colours like #E10600"""
    return re.findall(r"(?<![#0-9A-Fa-f])E\d+\b", re.sub(r'style="[^"]*"', "", html))


@pytest.fixture
def conn(tmp_path):
    """Build two placeholder races and open them read-only"""
    path = tmp_path / "f1.db"
    c = store.connect(path)
    store.create_schema(c)
    with c:
        c.executemany(
            "INSERT INTO events (season, round, event_name, circuit, race_date) "
            "VALUES (?, ?, ?, ?, ?)",
            [(2026, 1, "Placeholder Grand Prix", "Placeville", "2026-03-01"),
             (2026, 2, "Second <Placeholder> GP", "Townsville", "2026-03-15")],
        )
        c.executemany(
            "INSERT INTO results (season, round, driver_code, finish_pos) VALUES (2026, 2, ?, ?)",
            [("AAA", 1), ("BBB", 2), ("CCC", 3), ("DDD", 4)],
        )
        c.execute(
            "INSERT INTO ingest_log (season, round, source, status, rows, ts) "
            "VALUES (2026, 2, 'openf1', 'ok', 10, '2026-03-16T08:00:00+00:00')"
        )
    c.close()
    ro = sql_guard.connect_read_only(path)
    yield ro
    ro.close()


def test_race_options_are_latest_first_with_readable_labels(conn):
    """Check the race picker lists loaded races, latest first, with round, name and circuit"""
    options = page.race_options(conn)
    assert [(o["season"], o["round"]) for o in options] == [(2026, 2), (2026, 1)]
    assert page.race_label(options[1]) == "R1 · Placeholder Grand Prix (Placeville)"


def test_footer_names_the_sources_and_disclaimer_only():
    """Check the footer keeps the licences and disclaimer but no race counts or load date"""
    text = page.footer_html()
    assert "OpenF1" in text and "Wikipedia" in text and "not affiliated" in text
    assert "races of" not in text and "last loaded" not in text


def test_suggested_questions_are_analytical_and_name_the_chosen_race():
    """Check the starter questions ask how and why about the race, and state no facts"""
    race = {"event_name": "Placeholder Grand Prix", "season": 2026}
    questions = page.suggested_questions(race)
    assert len(questions) == 4
    assert all(q.endswith("?") for q in questions)
    assert all("2026 Placeholder Grand Prix" in q for q in questions)
    assert not any(q.startswith("Who won") for q in questions)


def test_a_race_held_in_other_seasons_offers_a_comparison():
    """Check the latest other season of the same Grand Prix is offered for comparison"""
    race = {"event_name": "Placeholder Grand Prix", "season": 2026}
    questions = page.suggested_questions(race, other_seasons=[2025, 2024])
    assert "How did the 2026 Placeholder Grand Prix compare with the 2025 race?" in questions
    follow = page.follow_up_questions(race, [], count=10, other_seasons=[2024])
    assert any("compare with the 2024 race" in q for q in follow)


def test_other_seasons_of_the_same_grand_prix_are_found(conn):
    """Check only other seasons with the same event name are listed, latest first"""
    race = {"season": 2026, "event_name": "Placeholder Grand Prix"}
    assert page.same_event_seasons(conn, race) == []
    assert page.same_event_seasons(conn, {**race, "season": 2025}) == [2026]


def test_small_widgets_are_paired_side_by_side():
    """Check only small tables share a row; classification, summary and charts are full width"""
    kinds = ["driver_card", "classification", "race_summary_card", "pit_stops_table",
             "tyre_strategy"]
    rows = page.layout_rows([dp.Widget(type=k, drivers=["AAA"]) for k in kinds])
    assert [[w.type for w in row] for row in rows] == [
        ["driver_card"], ["classification"], ["race_summary_card"], ["pit_stops_table"],
        ["tyre_strategy"],
    ]


def test_overview_puts_the_summary_after_the_classification(conn):
    """Check the overview order: podium, classification, race summary, then strategy"""
    view = page.dashboard_view(None, conn, {"season": 2026, "round": 2})
    assert [w.type for w in view["widgets"]][1:] == [
        "classification", "race_summary_card", "tyre_strategy",
    ]


def test_classification_splits_into_top_ten_and_the_rest_with_no_repeats():
    """Check P1 to P10 and the rest of the field are two separate lists, nothing shown twice"""
    rows = [{"code": f"D{n:02d}"} for n in range(1, 23)]
    top, rest = page.split_classification(rows)
    assert len(top) == 10 and len(rest) == 12
    assert {r["code"] for r in top}.isdisjoint({r["code"] for r in rest})
    assert page.split_classification(rows[:8]) == (rows[:8], [])


def test_chat_bubbles_escape_the_question():
    """Check a question typed by the user is escaped inside its bubble"""
    bubble = page.user_bubble_html("<img src=x onerror=alert(1)>")
    assert "<img" not in bubble and "&lt;img" in bubble


def test_before_any_question_the_overview_shows_podium_cards(conn):
    """Check the default dashboard is podium cards, classification, summary and strategy"""
    view = page.dashboard_view(None, conn, {"season": 2026, "round": 2})
    assert view["race"] == {"season": 2026, "round": 2}
    assert [w.type for w in view["widgets"]] == [
        "driver_card", "classification", "race_summary_card", "tyre_strategy",
    ]
    assert view["widgets"][0].drivers == ["AAA", "BBB", "CCC"]
    assert view["source"] == "overview"


def test_after_a_question_the_checked_plan_is_shown_with_its_race(conn):
    """Check a dashboard plan from an answer replaces the overview, with its race and evidence"""
    spec = dp.DashboardSpec(race=dp.RaceRef(season=2026, round=1),
                            widgets=[dp.Widget(type="classification")], evidence=["E2"])
    plan = dp.PlanResult(spec=spec, source="model")
    view = page.dashboard_view(plan, conn, {"season": 2026, "round": 2})
    assert view["race"] == {"season": 2026, "round": 1}
    assert [w.type for w in view["widgets"]] == ["classification"]
    assert view["evidence"] == ["E2"] and view["source"] == "model"


def test_widget_titles_are_plain_words():
    """Check every widget type has a readable panel title"""
    for widget_type in dp.WidgetType.__args__:
        assert page.WIDGET_TITLES[widget_type][0].isupper()


def plain_log():
    """Build evidence with one SQL result and one report passage"""
    log = EvidenceLog()
    log.add("run_sql", {"query": "SELECT full_name, team FROM results"},
            {"ok": True, "query": "SELECT full_name, team FROM results",
             "columns": ["full_name", "team"],
             "rows": [["Driver Alpha", "Team <A>"], ["Driver Beta", "Team B"]], "row_count": 2})
    log.add("search_docs", {"query": "q"}, {"passages": [
        {"title": "Placeholder race", "section": "Race > Report", "source_name": "Wikipedia",
         "url": "https://example.invalid", "published_at": "", "text": "x"}], "count": 1})
    return log


def test_answered_from_lists_the_kinds_of_source_in_plain_words():
    """Check the badge names the cited kinds of source, without tool names"""
    log = plain_log()
    assert page.answered_from(log.items, ["E1", "E2"]) == "Answered from race data and race reports"
    assert page.answered_from(log.items, ["E2"]) == "Answered from race reports"
    assert page.answered_from(log.items, []) == ""


def test_sourced_line_counts_statements_that_cite_a_source():
    """Check the badge says every statement is sourced, or how many are not"""
    full = page.sourced_text("AAA won [E1]. BBB was second [E1, E2].", cited=["E1"], uncited=[])
    assert full == "✓ All 2 statements cite their source"
    one = page.sourced_text("AAA won [E1].", cited=["E1"], uncited=[])
    assert one == "✓ The statement cites its source"
    part = page.sourced_text("AAA won [E1]. BBB was second.", cited=["E1"],
                             uncited=["BBB was second."])
    assert part == "1 of 2 statements cite a source"
    assert page.sourced_text("I don't have that in the ingested data.", [], []) == ""


def test_follow_up_questions_skip_what_was_already_asked():
    """Check three follow-ups about the race, none repeating a question already asked"""
    race = {"season": 2026, "event_name": "Placeholder Grand Prix"}
    asked = ["Who won the 2026 Placeholder Grand Prix?"]
    follow = page.follow_up_questions(race, asked)
    assert len(follow) == 3 and asked[0] not in follow
    assert all("2026 Placeholder Grand Prix" in q for q in follow)


def test_answer_text_is_escaped_and_citation_ids_are_hidden():
    """Check model text is escaped, bold and lists kept, and no [E1] or chip is shown"""
    html = page.answer_html("**AAA** won <script>x</script> [E1, E2].\n\n- second point [E3]")
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "<strong>AAA</strong>" in html and "<li>" in html
    assert shown_ids(html) == [] and "f1-chip" not in html
    assert "won &lt;script&gt;x&lt;/script&gt;." in html


def test_source_cards_use_plain_words_with_no_ids_tool_names_or_queries():
    """Check a source shows its kind, plain column names, rows and safe links only"""
    log = EvidenceLog()
    log.add("run_sql", {"query": "SELECT full_name FROM t WHERE a < 3"},
            {"ok": True, "query": "SELECT full_name FROM t WHERE a < 3",
             "columns": ["full_name"], "rows": [["<b>AAA</b>"]], "row_count": 1})
    log.add("search_docs", {"query": "q"}, {"passages": [
        {"title": "Placeholder race", "section": "Race", "source_name": "Wikipedia",
         "url": "https://example.invalid/a", "published_at": "2026-03-02T00:00:00Z",
         "text": "Some text"},
        {"title": "Bad link", "section": "Race", "source_name": "Wikipedia",
         "url": "javascript:alert(1)", "published_at": "", "text": "x"},
    ], "count": 2})
    data = page.source_card_html(log.items[0])
    assert "Race data" in data and ">full name<" in data and "&lt;b&gt;AAA&lt;/b&gt;" in data
    assert shown_ids(data) == []
    for technical in ("run_sql", "SELECT", "full_name", "Show query"):
        assert technical not in data
    report = page.source_card_html(log.items[1])
    assert "Race reports" in report and 'href="https://example.invalid/a"' in report
    assert "javascript:" not in report and "search_docs" not in report


def test_only_cited_sources_are_listed():
    """Check lookups the answer did not use are left out of the sources"""
    log = EvidenceLog()
    log.add("find_race", {"name": "x"}, {"matches": []})
    log.add("run_sql", {"query": "SELECT 1"}, {"ok": True, "rows": [], "row_count": 0})
    assert [i.id for i in page.visible_evidence(log.items, ["E2"])] == ["E2"]


def test_panel_titles_carry_no_evidence_ids():
    """Check widget titles are plain words without tags"""
    html = page.panel_header("Classification")
    assert "Classification" in html and shown_ids(html) == [] and "f1-chip" not in html


def test_classification_panels_have_plain_racing_names():
    """Check the two classification panels are called by the points they decide"""
    assert page.CLASSIFICATION_TITLES == ("Points finishers", "Outside the points")
