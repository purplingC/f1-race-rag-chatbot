from f1rag.engine.evidence import EvidenceLog

# Everything below is placeholder data for tests, not real F1 data


def make_log():
    """Build a log with one SQL result and one document search"""
    log = EvidenceLog()
    log.add("run_sql", {"query": "SELECT 1"}, {"ok": True, "rows": [[1]], "row_count": 1})
    log.add(
        "search_docs",
        {"query": "placeholder"},
        {"passages": [{"title": "Placeholder race", "url": "https://example.invalid"}], "count": 1},
    )
    return log


def test_ids_are_numbered_in_order_and_can_be_looked_up():
    """Check evidence ids are E1, E2, ... and each one returns its item"""
    log = make_log()
    assert [item.id for item in log.items] == ["E1", "E2"]
    assert log.get("E2").tool == "search_docs"
    assert log.get("E9") is None


def test_each_item_has_a_short_summary():
    """Check every evidence item says in a few words what it holds"""
    log = make_log()
    assert log.get("E1").summary == "SQL result (1 row)"
    assert log.get("E2").summary == "1 passage: Placeholder race"


def test_unknown_citations_are_removed_and_known_ones_kept():
    """Check a citation to an id that does not exist is dropped from the answer"""
    text, cited = make_log().validate_citations("Fact one [E1]. Fact two [E9]. Fact three [E2].")
    assert text == "Fact one [E1]. Fact two. Fact three [E2]."
    assert cited == ["E1", "E2"]


def test_grouped_citations_keep_only_valid_ids():
    """Check a group such as [E1, E9] keeps only the ids that exist"""
    text, cited = make_log().validate_citations("Both facts [E1, E9, E2].")
    assert text == "Both facts [E1, E2]."
    assert cited == ["E1", "E2"]


def test_every_cited_id_resolves_to_an_item():
    """Check that after validation every citation points to stored evidence"""
    log = make_log()
    _, cited = log.validate_citations("A [E2] and B [E1] and C [E1] and D [E7].")
    assert cited == ["E2", "E1"]
    assert all(log.get(i) is not None for i in cited)


def test_failed_sql_is_summarised_as_an_error():
    """Check a failed query is recorded and summarised as an error"""
    log = EvidenceLog()
    failed = {"ok": False, "error": "Only SELECT queries are allowed"}
    log.add("run_sql", {"query": "DELETE"}, failed)
    assert log.get("E1").summary == "SQL error"


def test_sentences_without_a_citation_are_listed():
    """Check every fact sentence that cites nothing is reported, headings and lists excluded"""
    text = (
        "**What happened:**\n"
        "- Fact with a source [E1]. Fact with none.\n"
        "Another cited fact [E1, E2].\n"
        "| col | col |\n"
        "I don't have that in the ingested data."
    )
    assert make_log().uncited_sentences(text) == ["Fact with none."]


def test_sentences_with_a_citation_are_counted():
    """Check sourced sentences are counted the same way uncited ones are found"""
    text = (
        "**What happened:**\n"
        "- Fact with a source [E1]. Fact with none.\n"
        "Another cited fact [E1, E2].\n"
        "I don't have that in the ingested data."
    )
    assert make_log().cited_sentences(text) == ["Fact with a source [E1].",
                                                "Another cited fact [E1, E2]."]
