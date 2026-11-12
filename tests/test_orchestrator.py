import json

import pytest

from f1rag import store, vectors
from f1rag.engine import orchestrator, sql_guard
from f1rag.engine.llm import LLMReply, LLMUnavailableError, ToolCall

# Everything below is placeholder data for tests, not real F1 data


class FakeLLM:
    """Replays scripted replies and records what it was sent"""

    model = "fake-model"

    def __init__(self, replies):
        """Keep the replies to give, in order"""
        self.replies = list(replies)
        self.sent = []

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
        """Record the call and return the next scripted reply"""
        self.sent.append({"contents": list(contents), "tools": tools, "json": json_schema})
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


def calls(*pairs):
    """Build a reply that asks for the given (tool, args) calls"""
    return LLMReply(text="", calls=[ToolCall(n, a) for n, a in pairs], turn=("model", "calls"))


def final(text):
    """Build the final JSON reply"""
    return LLMReply(text=json.dumps({"answer_md": text}), turn=("model", "final"))


@pytest.fixture
def conn(tmp_path):
    """Build a placeholder race database and open it read-only"""
    path = tmp_path / "f1.db"
    rw = store.connect(path)
    store.create_schema(rw)
    with rw:
        rw.execute(
            "INSERT INTO events (season, round, event_name, circuit, country, race_date) "
            "VALUES (2026, 1, 'Placeholder Grand Prix', 'Placeville', 'Placeland', '2026-03-01')"
        )
        rw.executemany(
            "INSERT INTO results (season, round, driver_code, finish_pos) VALUES (2026, 1, ?, ?)",
            [("AAA", 1), ("BBB", 2)],
        )
    rw.close()
    ro = sql_guard.connect_read_only(path)
    yield ro
    ro.close()


@pytest.fixture
def collection(tmp_path, fake_embedding):
    """Open a vector store with one placeholder passage"""
    col = vectors.open_collection(tmp_path / "chroma", embedding_function=fake_embedding)
    chunk = {
        "id": "c1",
        "document": "Placeholder race - Race\nPlaceholder text about the race",
        "metadata": {
            "season": 2026, "round": 1, "source_type": "wiki", "source_name": "Wikipedia",
            "title": "Placeholder race", "section": "Race", "url": "https://example.invalid",
            "published_at": "2026-03-02T00:00:00Z", "chunk_idx": 0,
        },
    }
    vectors.replace_race_docs(col, 2026, 1, "wiki", [chunk])
    return col


WINNER_SQL = "SELECT driver_code FROM results WHERE finish_pos = 1"


def test_hybrid_question_records_evidence_trace_and_citations(conn, collection):
    """Check tool calls become E1, E2, ... and the answer keeps only valid citations"""
    llm = FakeLLM([
        calls(("find_race", {"name": "Placeholder"})),
        calls(("run_sql", {"query": WINNER_SQL}), ("search_docs", {"query": "race"})),
        LLMReply(text="Draft", turn=("model", "draft")),
        final("AAA won [E2]. Text says so [E3]. Made up [E7]."),
    ])
    result = orchestrator.answer("Who won and why?", llm, conn, collection)
    assert [item.id for item in result.evidence] == ["E1", "E2", "E3"]
    assert result.evidence[1].result["rows"] == [["AAA"]]
    assert result.answer_md == "AAA won [E2]. Text says so [E3]. Made up."
    assert result.cited == ["E2", "E3"]
    assert [step["tool"] for step in result.trace] == ["find_race", "run_sql", "search_docs"]
    assert len(result.model_calls) == 4
    assert result.error is None


def test_tool_results_sent_to_the_model_carry_evidence_ids(conn, collection):
    """Check the model sees the evidence id with each result so it can cite it"""
    llm = FakeLLM([calls(("get_data_coverage", {})), LLMReply(text="ok"), final("ok")])
    orchestrator.answer("What is loaded?", llm, conn, collection)
    tool_turn = llm.sent[1]["contents"][-1]
    assert tool_turn[0] == "tools"
    name, result = tool_turn[1][0]
    assert name == "get_data_coverage"
    assert result["evidence_id"] == "E1"


def test_final_call_uses_json_mode_without_tools(conn, collection):
    """Check the last call asks for JSON and offers no tools"""
    llm = FakeLLM([LLMReply(text="No tools needed"), final("I don't have that.")])
    orchestrator.answer("Hello", llm, conn, collection)
    assert llm.sent[0]["tools"] and llm.sent[0]["json"] is None
    assert llm.sent[-1]["tools"] is None and llm.sent[-1]["json"]


def test_failed_sql_gets_one_retry_then_is_refused(conn, collection):
    """Check the model may fix one bad query, and a third attempt is refused in code"""
    bad = {"query": "SELECT nope FROM results"}
    llm = FakeLLM([
        calls(("run_sql", bad)), calls(("run_sql", bad)), calls(("run_sql", {"query": WINNER_SQL})),
        LLMReply(text="Sorry"), final("The query failed."),
    ])
    result = orchestrator.answer("Who won?", llm, conn, collection)
    assert [step["ok"] for step in result.trace] == [False, False, False]
    assert "no such column" in result.trace[0]["error"]
    assert "failed twice" in result.trace[2]["error"]


def test_unknown_tool_and_missing_arguments_come_back_as_errors(conn, collection):
    """Check a made-up tool or a missing required argument does not crash the loop"""
    llm = FakeLLM([
        calls(("delete_everything", {}), ("run_sql", {})),
        LLMReply(text="Sorry"), final("Sorry."),
    ])
    result = orchestrator.answer("Break it", llm, conn, collection)
    assert "Unknown tool" in result.trace[0]["error"]
    assert "Wrong arguments" in result.trace[1]["error"]


def test_made_up_arguments_are_ignored_and_reported(conn, collection):
    """Check an argument the tool does not have is dropped so the call still runs"""
    args = {"query": "race", "season": 2026, "source_name": "wiki"}
    llm = FakeLLM([calls(("search_docs", args)), LLMReply(text="ok"), final("ok [E1].")])
    result = orchestrator.answer("Q", llm, conn, collection)
    assert result.trace[0]["ok"] is True
    assert result.evidence[0].result["ignored_arguments"] == ["source_name"]
    assert result.evidence[0].result["count"] == 1


def test_model_calls_are_timed_and_uncited_sentences_reported(conn, collection):
    """Check the bundle records each model call and lists answer sentences with no citation"""
    llm = FakeLLM([
        calls(("run_sql", {"query": WINNER_SQL})),
        LLMReply(text="Draft"), final("AAA won [E1]. It rained."),
    ])
    result = orchestrator.answer("Who won?", llm, conn, collection)
    assert [c["purpose"] for c in result.model_calls] == ["tools", "tools", "final"]
    assert all("ms" in c and "retries" in c for c in result.model_calls)
    assert result.total_ms >= 0
    assert result.uncited == ["It rained."]


def test_loop_stops_after_max_steps_and_still_answers(conn, collection):
    """Check a model that keeps calling tools is cut off and the final answer is asked for"""
    looping = [calls(("get_data_coverage", {})) for _ in range(3)]
    llm = FakeLLM([*looping, final("Stopped [E3].")])
    result = orchestrator.answer("Loop", llm, conn, collection, max_steps=3)
    assert len(result.trace) == 3
    assert result.answer_md == "Stopped [E3]."


def test_invalid_final_json_falls_back_to_the_draft(conn, collection):
    """Check a final reply that is not JSON falls back to the last plain answer"""
    llm = FakeLLM([LLMReply(text="Plain draft"), LLMReply(text="not json")])
    result = orchestrator.answer("Hi", llm, conn, collection)
    assert result.answer_md == "Plain draft"


def test_rate_limit_gives_a_clear_error_bundle(conn, collection):
    """Check a Gemini rate limit ends with a readable message, not a crash"""
    llm = FakeLLM([LLMUnavailableError("Rate limit reached - try again in a minute")])
    result = orchestrator.answer("Who won?", llm, conn, collection)
    assert result.error == "Rate limit reached - try again in a minute"
    assert result.answer_md == result.error


def test_history_is_sent_before_the_new_question(conn, collection):
    """Check earlier questions and answers go first so follow-ups have context"""
    llm = FakeLLM([LLMReply(text="ok"), final("ok")])
    history = [("user", "Earlier question"), ("model", "Earlier answer")]
    orchestrator.answer("Follow-up", llm, conn, collection, history=history)
    assert llm.sent[0]["contents"] == [
        ("user", "Earlier question"), ("model", "Earlier answer"), ("user", "Follow-up"),
    ]
