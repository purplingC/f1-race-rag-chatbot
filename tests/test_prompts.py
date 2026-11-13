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
        "ends with its citation", "get_data_coverage only when",
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
