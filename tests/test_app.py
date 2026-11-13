import json
import re
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from f1rag import store, vectors
from f1rag.engine.llm import LLMReply, LLMUnavailableError, ToolCall

# Everything below is placeholder data for tests, not real F1 data


def shown_ids(html):
    """Find evidence ids such as E1 that appear as text, ignoring colours like #E10600"""
    return re.findall(r"(?<![#0-9A-Fa-f])E\d+\b", re.sub(r'style="[^"]*"', "", html))
APP = str(Path(__file__).resolve().parents[1] / "src" / "f1rag" / "ui" / "app.py")
WINNER = "SELECT driver_code FROM results WHERE season = 2026 AND round = 1 AND finish_pos = 1"


class ScriptedLLM:
    """Replays scripted replies, standing in for Gemini"""

    model = "fake-model"

    def __init__(self, replies):
        """Keep the replies to give, in order"""
        self.replies = list(replies)

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
        """Return or raise the next scripted reply"""
        reply = self.replies.pop(0)
        if isinstance(reply, Exception):
            raise reply
        return reply


@pytest.fixture
def stores(tmp_path, fake_embedding):
    """Build a placeholder race database and an empty vector store"""
    db = tmp_path / "f1.db"
    c = store.connect(db)
    store.create_schema(c)
    with c:
        c.execute("INSERT INTO events (season, round, event_name, circuit, race_date) "
                  "VALUES (2026, 1, 'Placeholder Grand Prix', 'Placeville', '2026-03-01')")
        drivers = (("AAA", "Driver <Alpha>", 1), ("BBB", "Driver Beta", 2),
                   ("CCC", "Driver Gamma", 3))
        for code, name, pos in drivers:
            c.execute("INSERT INTO entries (season, round, driver_code, full_name, team, "
                      "team_color) VALUES (2026, 1, ?, ?, 'Team A', '112233')", (code, name))
            c.execute("INSERT INTO results (season, round, driver_code, grid_pos, finish_pos, "
                      "status, points) VALUES (2026, 1, ?, 1, ?, 'Finished', 10)", (code, pos))
    c.close()
    chroma = tmp_path / "chroma"
    vectors.open_collection(chroma, embedding_function=fake_embedding)
    return db, chroma, fake_embedding


def start(stores, llm=None):
    """Run the app once with the placeholder stores and an optional scripted model"""
    db, chroma, embedding = stores
    at = AppTest.from_file(APP, default_timeout=60)
    at.session_state["db_path"] = str(db)
    at.session_state["chroma_path"] = str(chroma)
    at.session_state["embedding_function"] = embedding
    at.session_state["show_headshots"] = False
    at.session_state["show_team_logos"] = False
    if llm is not None:
        at.session_state["llm"] = llm
    return at.run()


def page_html(at):
    """Join every HTML and Markdown block the app drew"""
    return "\n".join([h.proto.body for h in at.html] + [m.value for m in at.markdown])


def test_first_view_shows_title_race_picker_overview_and_footer(stores):
    """Check the start page draws without errors and shows the default overview"""
    at = start(stores)
    assert not at.exception
    html = page_html(at)
    assert "Formula 1 Race Assistant" in html
    assert "Placeholder Grand Prix" in html and "R1" in html
    assert at.selectbox(key="race").value == (2026, 1)
    # Only three placeholder drivers, so one panel called Classification
    assert "Classification" in html and "not affiliated" in html
    assert "Podium" in html and "Winner" in html
    assert "Driver &lt;Alpha&gt;" in html and "Driver <Alpha>" not in html


def test_a_question_shows_the_cited_answer_evidence_and_new_dashboard(stores):
    """Check asking a question shows the answer with chips, the evidence and the chosen widgets"""
    spec = {"race": {"season": 2026, "round": 1},
            "widgets": [{"type": "classification"}], "evidence": ["E1"]}
    llm = ScriptedLLM([
        LLMReply(text="", calls=[ToolCall("run_sql", {"query": WINNER})]),
        LLMReply(text="Draft"),
        LLMReply(text=json.dumps({"answer_md": "AAA won [E1].", "dashboard": spec})),
    ])
    at = start(stores, llm)
    at.chat_input[0].set_value("Who won?").run()
    assert not at.exception
    html = page_html(at)
    assert "AAA won." in html and "Race data" in html
    # Nothing technical reaches the page: no ids, chips, tool names, queries or trace
    assert shown_ids(html) == []
    for technical in ("f1-chip", "run_sql", "SELECT", "chosen by the model",
                      "How I answered", "Developer details"):
        assert technical not in html
    assert not [b for b in at.button if b.key and b.key.startswith("chip-")]
    assert not at.toggle


def test_a_suggested_question_is_asked_when_clicked(stores):
    """Check clicking a starter question sends it like a typed one"""
    llm = ScriptedLLM([
        LLMReply(text="", calls=[ToolCall("run_sql", {"query": WINNER})]),
        LLMReply(text="Draft"),
        LLMReply(text=json.dumps({"answer_md": "AAA won [E1]."})),
    ])
    at = start(stores, llm)
    at.button(key="suggest-0").click().run()
    assert not at.exception
    turns = at.session_state["turns"]
    assert len(turns) == 1 and "Placeholder Grand Prix" in turns[0]["question"]


def test_gemini_being_busy_shows_a_short_error_not_a_crash(stores):
    """Check an unavailable model gives an inline message and no stack trace"""
    llm = ScriptedLLM([LLMUnavailableError("Gemini is still busy (tried 4 times). "
                                           "Try again in a few minutes")])
    at = start(stores, llm)
    at.chat_input[0].set_value("Who won?").run()
    assert not at.exception
    assert any("still busy" in e.value for e in at.error)


def test_an_off_topic_question_gets_the_f1_only_reply(stores):
    """Check a question answered without any lookup shows the friendly F1-only reply"""
    from f1rag.engine import orchestrator

    llm = ScriptedLLM([LLMReply(text="Paris is the capital of France.")])
    at = start(stores, llm)
    at.chat_input[0].set_value("What is the capital of France?").run()
    assert not at.exception
    html = page_html(at)
    assert "only answer questions about the Formula 1 races" in html
    assert orchestrator.OFF_TOPIC in at.session_state["turns"][0]["bundle"].answer_md
    assert "Paris" not in html


def test_an_answer_shows_where_it_came_from_and_offers_follow_ups(stores):
    """Check the plain Answered from line, chip hover text and follow-up questions"""
    llm = ScriptedLLM([
        LLMReply(text="", calls=[ToolCall("run_sql", {"query": WINNER})]),
        LLMReply(text="Draft"),
        LLMReply(text=json.dumps({"answer_md": "AAA won [E1]."})),
    ])
    at = start(stores, llm)
    at.chat_input[0].set_value("Who won?").run()
    assert not at.exception
    html = page_html(at)
    assert "Answered from race data" in html
    assert "The statement cites its source" in html
    follow = [b for b in at.button if b.key and b.key.startswith("follow-")]
    assert len(follow) == 3
    assert all("Placeholder Grand Prix" in b.label for b in follow)


class WatchingLLM(ScriptedLLM):
    """Records what the chat held when the model was first asked"""

    def __init__(self, replies):
        """Keep the replies and a place for what was seen"""
        super().__init__(replies)
        self.seen = None

    def generate(self, contents, system, tools=None, json_schema=None):
        """Note whether the question was already in the chat, then reply"""
        import streamlit as st

        if self.seen is None:
            turns = st.session_state["turns"]
            self.seen = (len(turns), turns[-1]["question"], turns[-1]["bundle"])
        return super().generate(contents, system, tools, json_schema)


def test_the_question_is_in_the_chat_before_the_answer_is_made(stores):
    """Check a sent question joins the conversation before the model starts working"""
    llm = WatchingLLM([
        LLMReply(text="", calls=[ToolCall("run_sql", {"query": WINNER})]),
        LLMReply(text="Draft"),
        LLMReply(text=json.dumps({"answer_md": "AAA won [E1]."})),
    ])
    at = start(stores, llm)
    at.chat_input[0].set_value("Who won?").run()
    assert not at.exception
    assert llm.seen == (1, "Who won?", None)
    assert at.session_state["turns"][0]["bundle"].answer_md.startswith("AAA won")


def test_page_has_no_stripe_across_the_top():
    """Check the red line along the top of the page is gone"""
    from f1rag.ui import theme

    assert "::before" not in theme.page_css()
