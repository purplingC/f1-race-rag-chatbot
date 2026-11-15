import json

from f1rag import eval as evaluate
from f1rag import store, vectors
from f1rag.engine.llm import LLMReply, LLMUnavailableError, ToolCall

# Everything below is placeholder data for tests, not real F1 data

GOLDEN = """
[[question]]
id = "S01"
category = "statistical"
question = "Who won the Placeholder Grand Prix?"
race = [2026, 1]
tools = ["run_sql"]
truth = "SELECT driver_code FROM results WHERE season = 2026 AND round = 1 AND finish_pos = 1"

[[question]]
id = "S02"
category = "statistical"
question = "Who won the Placeholder Grand Prix again?"
tools = ["run_sql"]
truth = "SELECT driver_code FROM results WHERE season = 2026 AND round = 1 AND finish_pos = 1"
"""


class ScriptedLLM:
    """Answers every question with one SQL query and a cited final answer"""

    model = "fake-model"

    def __init__(self, fail=False):
        """Start with no calls made"""
        self.fail = fail
        self.calls = 0

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
        """Ask for a query first, then stop, then give the final answer"""
        self.calls += 1
        if self.fail:
            raise LLMUnavailableError("Gemini is busy")
        if json_schema:
            dashboard = {"race": {"season": 2026, "round": 1},
                         "widgets": [{"type": "classification"}]}
            return LLMReply(text=json.dumps({"answer_md": "Aardvark won [E1].",
                                             "dashboard": dashboard}))
        if contents[-1][0] == "tools":
            return LLMReply(text="Draft")
        query = {"query": "SELECT driver_code FROM results WHERE finish_pos = 1"}
        return LLMReply(text="", calls=[ToolCall("run_sql", query)])


def make_files(tmp_path, fake_embedding, golden_text=GOLDEN):
    """Create a placeholder database, vector store and golden file; return the arguments"""
    db = tmp_path / "f1.db"
    conn = store.connect(db)
    store.create_schema(conn)
    with conn:
        conn.execute("INSERT INTO events (season, round, event_name) "
                     "VALUES (2026, 1, 'Placeholder Grand Prix')")
        conn.execute("INSERT INTO entries (season, round, driver_code, full_name) "
                     "VALUES (2026, 1, 'AAA', 'Alpha AARDVARK')")
        conn.execute("INSERT INTO results (season, round, driver_code, finish_pos) "
                     "VALUES (2026, 1, 'AAA', 1)")
    conn.close()
    chroma = tmp_path / "chroma"
    vectors.open_collection(chroma, embedding_function=fake_embedding)
    path = tmp_path / "golden.toml"
    path.write_text(golden_text, encoding="utf-8")
    return ["--golden", str(path), "--db", str(db), "--chroma", str(chroma),
            "--out", str(tmp_path / "reports")]


def test_run_scores_each_question_prints_metrics_and_saves_a_report(tmp_path, fake_embedding):
    """Check every question is asked, scored and written to the report, with pauses between"""
    args = make_files(tmp_path, fake_embedding)
    printed, waits = [], []
    code = evaluate.main(args, llm=ScriptedLLM(), out=printed.append, sleep=waits.append)
    text = "\n".join(printed)
    assert code == 0
    assert "S01  statistical  pass" in text and "S02  statistical  pass" in text
    assert "Numeric accuracy          2/2 (100%)" in text
    assert len(waits) == 1 and 34 <= waits[0] <= 35
    report = next((tmp_path / "reports").glob("eval-*.md")).read_text(encoding="utf-8")
    assert "| Numeric accuracy | 2/2 (100%) | ≥ 90% | yes |" in report
    assert "Expected values: AAA" in report
    assert "- E1 SQL result (1 row): `SELECT driver_code FROM results" in report
    assert "Gemini calls: 0.0 s choosing tools, 0.0 s choosing tools, 0.0 s writing" in report


def test_only_and_category_pick_questions(tmp_path, fake_embedding):
    """Check --only runs just the named questions"""
    args = make_files(tmp_path, fake_embedding)
    printed = []
    evaluate.main([*args, "--only", "s02"], llm=ScriptedLLM(), out=printed.append,
                  sleep=lambda s: None)
    text = "\n".join(printed)
    assert "S02" in text and "S01 " not in text
    code = evaluate.main([*args, "--category", "trap"], llm=ScriptedLLM(), out=printed.append)
    assert code == 1 and printed[-1] == "No questions match --only or --category."


def test_check_shows_expected_values_without_calling_gemini(tmp_path, fake_embedding):
    """Check --check runs the truth queries only and never calls the model"""
    args = make_files(tmp_path, fake_embedding)
    llm, printed = ScriptedLLM(), []
    code = evaluate.main([*args, "--check"], llm=llm, out=printed.append)
    assert code == 0 and llm.calls == 0
    assert printed == ["S01  expected: AAA", "S02  expected: AAA"]


def test_broken_truth_query_stops_before_any_gemini_call(tmp_path, fake_embedding):
    """Check a truth query that fails is reported and no quota is spent"""
    broken = GOLDEN.replace("finish_pos = 1\"", "finish_pos = 99\"", 1)
    args = make_files(tmp_path, fake_embedding, broken)
    llm, printed = ScriptedLLM(), []
    code = evaluate.main(args, llm=llm, out=printed.append)
    assert code == 1 and llm.calls == 0
    assert printed[-1] == "1 truth problem(s); fix eval/golden.toml before running."


def test_run_stops_after_two_failed_answers_in_a_row(tmp_path, fake_embedding):
    """Check the run stops when Gemini keeps failing and says so"""
    args = make_files(tmp_path, fake_embedding, GOLDEN + GOLDEN.replace('"S0', '"S1'))
    printed = []
    code = evaluate.main(args, llm=ScriptedLLM(fail=True), out=printed.append,
                         sleep=lambda s: None)
    assert code == 1
    assert printed[-1] == "Stopped after 2 of 4 questions: Gemini kept failing."


def test_missing_database_is_explained(tmp_path, fake_embedding):
    """Check a missing database gives a hint instead of being created"""
    args = make_files(tmp_path, fake_embedding)
    args[args.index("--db") + 1] = str(tmp_path / "none.db")
    printed = []
    assert evaluate.main(args, llm=ScriptedLLM(), out=printed.append) == 1
    assert printed[0].startswith("No database at")
    assert not (tmp_path / "none.db").exists()
