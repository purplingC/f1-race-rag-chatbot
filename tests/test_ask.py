import json

from f1rag import ask, store, vectors
from f1rag.engine.llm import LLMReply, ToolCall

# Everything below is placeholder data for tests, not real F1 data


class ScriptedLLM:
    """Asks for one SQL query, then gives a cited final answer"""

    model = "fake-model"

    def __init__(self):
        """Prepare the two replies"""
        query = {"query": "SELECT driver_code FROM results WHERE finish_pos = 1"}
        self.replies = [
            LLMReply(text="", calls=[ToolCall("run_sql", query)]),
            LLMReply(text="Draft"),
            LLMReply(text=json.dumps({"answer_md": "AAA won [E1]."})),
        ]

    def user_turn(self, text):
        """Return a user turn as a plain tuple"""
        return ("user", text)

    def model_turn(self, text):
        """Return a model turn as a plain tuple"""
        return ("model", text)

    def tool_results_turn(self, results):
        """Return the tool results as a plain tuple"""
        return ("tools", results)

    def generate(self, contents, system, tools=None, json_schema=None):
        """Return the next scripted reply"""
        return self.replies.pop(0)


def make_stores(tmp_path, fake_embedding):
    """Create a placeholder database and an empty vector store, return their paths"""
    db = tmp_path / "f1.db"
    conn = store.connect(db)
    store.create_schema(conn)
    with conn:
        conn.execute(
            "INSERT INTO results (season, round, driver_code, finish_pos) "
            "VALUES (2026, 1, 'AAA', 1)"
        )
    conn.close()
    chroma = tmp_path / "chroma"
    vectors.open_collection(chroma, embedding_function=fake_embedding)
    return db, chroma


def test_prints_answer_how_line_and_evidence(tmp_path, fake_embedding):
    """Check the command prints the cited answer, the tool summary and the evidence list"""
    db, chroma = make_stores(tmp_path, fake_embedding)
    printed = []
    code = ask.main(
        ["Who won?", "--db", str(db), "--chroma", str(chroma), "--trace"],
        llm=ScriptedLLM(), out=printed.append,
    )
    text = printed[0]
    assert code == 0
    assert text.startswith("AAA won [E1].")
    assert "How I answered: run_sql (1 query)" in text
    assert "E1  run_sql: SQL result (1 row)" in text
    assert "SELECT driver_code FROM results" in text
    assert "  1. Gemini, choosing tools: " in text
    assert "       run_sql({'query': 'SELECT driver_code" in text
    assert "  3. Gemini, writing the answer: " in text


def test_missing_database_gives_a_hint_and_exit_code_1(tmp_path):
    """Check a missing database is explained instead of being created"""
    printed = []
    missing = tmp_path / "none.db"
    code = ask.main(["Q", "--db", str(missing)], llm=ScriptedLLM(), out=printed.append)
    assert code == 1
    assert "python -m f1rag.ingest" in printed[0]
    assert not missing.exists()


def test_uncited_evidence_is_marked(tmp_path):
    """Check evidence the answer did not cite is labelled in the list"""
    from f1rag.engine.evidence import AnswerBundle, EvidenceItem

    item = EvidenceItem("E1", "get_data_coverage", {}, {"races": []}, "coverage list (0 races)")
    bundle = AnswerBundle(question="Q", answer_md="Nothing.", evidence=[item], cited=[])
    assert "E1  get_data_coverage: coverage list (0 races)  (not cited)" in ask.render(bundle)


def test_passages_from_one_section_are_grouped():
    """Check several chunks of one section show as one line with a count"""
    from f1rag.engine.evidence import AnswerBundle, EvidenceItem

    passage = {
        "title": "Placeholder race", "section": "Race", "source_name": "Wikipedia",
        "published_at": "2026-03-02T00:00:00Z", "url": "https://example.invalid",
    }
    other = {**passage, "section": "Background"}
    item = EvidenceItem(
        "E1", "search_docs", {}, {"passages": [passage, passage, other]}, "3 passages"
    )
    text = ask.render(AnswerBundle(question="Q", answer_md="A [E1].", evidence=[item]))
    assert text.count("Placeholder race > Race (2 passages)") == 1
    assert "Placeholder race > Background, Wikipedia" in text


def test_uncited_sentences_and_model_time_are_shown(tmp_path):
    """Check the output warns about uncited sentences and the trace shows model call times"""
    from f1rag.engine.evidence import AnswerBundle

    bundle = AnswerBundle(
        question="Q", answer_md="It rained.", uncited=["It rained."],
        model_calls=[
            {"step": 1, "purpose": "tools", "ms": 1200, "retries": 1},
            {"step": 2, "purpose": "final", "ms": 800, "retries": 0},
        ],
        trace=[{
            "step": 1, "tool": "find_race", "args": {"name": "Alpha"}, "evidence": "E1",
            "summary": "1 matching race", "ok": True, "error": None, "ms": 2,
        }],
        total_ms=2500,
    )
    lines = ask.render(bundle, show_trace=True).split("\n")
    assert "Warning: 1 sentence has no citation:" in lines
    start = lines.index("Trace (2 model calls, model , 2.5 s in total):")
    assert lines[start + 1:start + 4] == [
        "  1. Gemini, choosing tools: 1.2 s, 1 retry",
        "       find_race({'name': 'Alpha'}) -> E1, ok, 2 ms",
        "  2. Gemini, writing the answer: 0.8 s",
    ]
