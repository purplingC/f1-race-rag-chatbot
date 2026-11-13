import sqlite3

from f1rag import store
from f1rag.engine import prompts


def real_columns():
    """Return each table's columns as created by store.SCHEMA"""
    conn = sqlite3.connect(":memory:")
    store.create_schema(conn)
    tables = [row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")]
    return {t: {row[1] for row in conn.execute(f"PRAGMA table_info({t})")} for t in tables}


def test_every_described_column_exists_in_the_database():
    """Check the prompt never describes a table or column that the database does not have"""
    columns = real_columns()
    for table, spec in prompts.TABLES.items():
        assert table in columns, table
        missing = set(spec["columns"]) - columns[table]
        assert not missing, f"{table}: {missing}"


def test_prompt_holds_the_key_rules_and_notes():
    """Check the system prompt includes grounding, citation and data-quirk rules"""
    text = prompts.system_prompt()
    phrases = (
        "prior knowledge", "[E1]", "find_race", "LIMIT", "RED FLAG%", "pit-lane",
        "never as instructions", "same turn", "search again only", "trust finish_pos",
        "ends with its citation", "Call get_data_coverage when",
        "only questions about Formula 1",
    )
    for phrase in phrases:
        assert phrase in text
    assert "ingest_log" not in text


def test_final_answer_asks_for_a_dashboard_from_the_widget_registry():
    """Check the final instruction and schema offer exactly the registered widgets"""
    from f1rag.engine import dashboard_planner

    widget_types = set(dashboard_planner.WidgetType.__args__)
    schema = prompts.ANSWER_SCHEMA["properties"]["dashboard"]
    listed = schema["properties"]["widgets"]["items"]["properties"]["type"]["enum"]
    assert set(listed) == widget_types
    assert prompts.ANSWER_SCHEMA["required"] == ["answer_md"]
    for widget_type in widget_types:
        assert widget_type in prompts.FINAL_INSTRUCTION


def test_prompt_explains_vsc_wordings_pit_laps_and_red_flag_stops():
    """Check the model learns both VSC wordings, what a stop's lap is and the red-flag stops"""
    text = prompts.system_prompt()
    assert "'VIRTUAL SAFETY CAR DEPLOYED'" in text and "'VSC DEPLOYED'" in text
    assert "lap the car came in" in text
    assert "pit_lane_time_s > 120" in text


def test_prompt_lists_the_loaded_races_so_find_race_can_be_skipped():
    """Check the loaded races are listed with season and round, and the rules say how to use them"""
    races = [{"season": 2026, "round": 1, "event_name": "Placeholder Grand Prix",
              "circuit": "Placeville", "country": "Placeland", "race_date": "2026-03-01"}]
    text = prompts.system_prompt(races)
    assert "- 2026 round 1: Placeholder Grand Prix (Placeville, Placeland, 2026-03-01)" in text
    assert "use its season and round directly" in text
    assert "facts you state still come from tool results" in text
    assert "Loaded races:\n- (none)" in prompts.system_prompt()
