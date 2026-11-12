import pytest
from google.genai import errors, types

from f1rag.engine import llm as llm_module
from f1rag.engine.llm import GeminiLLM, LLMUnavailableError

# Fakes only: these tests never call the Gemini API


def response(*parts):
    """Build a Gemini response holding the given parts"""
    content = types.Content(role="model", parts=list(parts))
    return types.GenerateContentResponse(candidates=[types.Candidate(content=content)])


class FakeModels:
    """Stands in for client.models, replaying responses or errors"""

    def __init__(self, outcomes):
        """Keep the outcomes to give, in order"""
        self.outcomes = list(outcomes)
        self.configs = []

    def generate_content(self, model, contents, config):
        """Record the config and return or raise the next outcome"""
        self.configs.append(config)
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeClient:
    """Stands in for genai.Client"""

    def __init__(self, outcomes):
        """Hold the fake models endpoint"""
        self.models = FakeModels(outcomes)


def make(outcomes, waits=None):
    """Build a GeminiLLM on a fake client that records its waits"""
    waits = [] if waits is None else waits
    return GeminiLLM(model="fake-model", client=FakeClient(outcomes), sleep=waits.append)


def api_error(code):
    """Build a Gemini API error with the given HTTP code"""
    return errors.APIError(code, {"error": {"code": code, "message": "placeholder"}})


TOOL = {"name": "find_race", "description": "d", "parameters": {"type": "object", "properties": {}}}


def test_tool_calls_and_text_are_read_from_the_response():
    """Check function calls and text come back in vendor-neutral form"""
    call = types.Part(function_call=types.FunctionCall(name="find_race", args={"season": 2026}))
    llm = make([response(types.Part(text="Looking it up"), call)])
    reply = llm.generate([llm.user_turn("q")], "system", tools=[TOOL])
    assert reply.text == "Looking it up"
    assert [(c.name, c.args) for c in reply.calls] == [("find_race", {"season": 2026})]
    config = llm.client.models.configs[0]
    assert config.automatic_function_calling.disable is True
    assert config.tools[0].function_declarations[0].name == "find_race"


def test_json_mode_sets_the_schema_and_no_tools():
    """Check the final call asks for JSON with the given schema"""
    llm = make([response(types.Part(text='{"answer_md": "x"}'))])
    reply = llm.generate([llm.user_turn("q")], "system", json_schema={"type": "object"})
    config = llm.client.models.configs[0]
    assert config.response_mime_type == "application/json"
    assert config.tools is None
    assert llm_module.parse_json(reply.text) == {"answer_md": "x"}


def test_rate_limit_is_retried_then_succeeds():
    """Check a 429 is retried after a wait and the next answer is used"""
    waits = []
    llm = make([api_error(429), response(types.Part(text="ok"))], waits)
    assert llm.generate([llm.user_turn("q")], "system").text == "ok"
    assert waits == [llm_module.RETRY_WAITS_S[0]]


def test_rate_limit_gives_up_after_three_retries():
    """Check four 429s in a row end with a clear message for the user"""
    waits = []
    llm = make([api_error(429)] * 4, waits)
    with pytest.raises(LLMUnavailableError, match="rate limit reached"):
        llm.generate([llm.user_turn("q")], "system")
    assert waits == list(llm_module.RETRY_WAITS_S)


def test_other_api_errors_are_not_retried():
    """Check an error such as a bad request is raised at once"""
    llm = make([api_error(400)])
    with pytest.raises(errors.APIError):
        llm.generate([llm.user_turn("q")], "system")


def test_parse_json_rejects_text_that_is_not_an_object():
    """Check bad or non-object JSON gives None instead of an exception"""
    assert llm_module.parse_json("not json") is None
    assert llm_module.parse_json("[1, 2]") is None


def test_reply_records_time_and_retries_and_waits_are_announced():
    """Check each reply says how long it took and how often it retried, in plain words"""
    notices = []
    llm = make([api_error(503), api_error(429), response(types.Part(text="ok"))])
    llm.on_wait = notices.append
    reply = llm.generate([llm.user_turn("q")], "system")
    assert reply.retries == 2
    assert reply.ms >= 0
    assert notices == [
        "Gemini is busy, please wait 5 s (attempt 2 of 4)",
        "Gemini rate limit reached, please wait 15 s (attempt 3 of 4)",
    ]


def test_busy_gives_up_with_a_busy_message():
    """Check four 503s end with a message that says Gemini is busy, not rate limited"""
    llm = make([api_error(503)] * 4)
    with pytest.raises(LLMUnavailableError, match="Gemini is still busy"):
        llm.generate([llm.user_turn("q")], "system")


class FakeTimeout(Exception):
    """Stands in for the HTTP library's timeout error"""


def test_a_call_that_takes_too_long_is_retried(monkeypatch):
    """Check a request with no answer within the time limit is retried, then explained"""
    monkeypatch.setattr(llm_module, "TRANSIENT_ERRORS", (FakeTimeout,))
    notices = []
    llm = make([FakeTimeout(), response(types.Part(text="ok"))])
    llm.on_wait = notices.append
    assert llm.generate([llm.user_turn("q")], "system").text == "ok"
    assert notices == ["Gemini did not answer within 30 s, please wait 5 s (attempt 2 of 4)"]
    llm = make([FakeTimeout()] * 4)
    with pytest.raises(LLMUnavailableError, match="did not answer"):
        llm.generate([llm.user_turn("q")], "system")


def test_client_gets_a_time_limit():
    """Check the real client is created with a per-request time limit"""
    llm = GeminiLLM(api_key="placeholder-not-a-key", model="fake-model")
    assert llm.client._api_client._http_options.timeout == llm_module.TIMEOUT_S * 1000
