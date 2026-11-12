"""Gemini wrapper: the only module that talks to the Gemini API, so the vendor stays in one place"""

import json
import os
import time
from dataclasses import dataclass, field

from dotenv import load_dotenv
from google import genai
from google.genai import errors, types

# Wait before each retry when Gemini answers 429 (rate limit) or 503 (busy) or does not
# answer in time, max 3 retries, so at most 4 attempts
RETRY_WAITS_S = (5, 15, 40)
ATTEMPTS = len(RETRY_WAITS_S) + 1
RETRY_CODES = {429, 503}
# A normal call takes 1 to 6 s; without a limit one slow answer once took 108 s
TIMEOUT_S = 30


def transient_errors():
    """Return the timeout and connection errors of the HTTP libraries google-genai can use"""
    found = []
    for name in ("httpx", "httpx2"):
        try:
            module = __import__(name)
        except ImportError:
            continue
        found += [getattr(module, "TimeoutException", None), getattr(module, "ConnectError", None)]
    return tuple(cls for cls in found if cls is not None)


# google-genai sends requests with httpx or httpx2, both installed with the pinned packages
TRANSIENT_ERRORS = transient_errors()
# What each refusal is called in the messages the user sees
REASONS = {
    429: ("Gemini rate limit reached", "Try again in a minute"),
    503: ("Gemini is busy", "Try again in a few minutes"),
    "timeout": (f"Gemini did not answer within {TIMEOUT_S} s", "Try again in a few minutes"),
}
GIVE_UP = {
    429: "Gemini rate limit reached",
    503: "Gemini is still busy",
    "timeout": f"Gemini did not answer within {TIMEOUT_S} s",
}


class LLMUnavailableError(Exception):
    """Raised when Gemini still refuses after the retries, with a message for the user"""


@dataclass
class ToolCall:
    """One tool call the model asked for"""

    name: str
    args: dict


@dataclass
class LLMReply:
    """What the model said: its text, the tool calls it wants, and its turn to send back"""

    text: str
    calls: list = field(default_factory=list)
    turn: object = None
    ms: int = 0
    retries: int = 0


def load_settings():
    """Read the Gemini key and model name from .env or the environment"""
    load_dotenv()
    api_key, model = os.getenv("GEMINI_API_KEY"), os.getenv("GEMINI_MODEL")
    if not api_key or not model:
        raise LLMUnavailableError("Set GEMINI_API_KEY and GEMINI_MODEL in .env first")
    return api_key, model


class GeminiLLM:
    """Calls Gemini with manual tool calling, JSON output and retries on rate limits"""

    def __init__(self, api_key=None, model=None, client=None, sleep=time.sleep, on_wait=None):
        """Create the client from arguments, or from .env when they are not given"""
        if (client is None and api_key is None) or model is None:
            env_key, env_model = load_settings()
            api_key, model = api_key or env_key, model or env_model
        self.model = model
        self.client = client or genai.Client(
            api_key=api_key, http_options=types.HttpOptions(timeout=TIMEOUT_S * 1000)
        )
        self.sleep = sleep
        # Called with a short sentence before each wait, so a command can say why it pauses
        self.on_wait = on_wait

    def user_turn(self, text):
        """Wrap user text as a conversation turn"""
        return types.Content(role="user", parts=[types.Part.from_text(text=text)])

    def model_turn(self, text):
        """Wrap an earlier answer as a model turn, for chat history"""
        return types.Content(role="model", parts=[types.Part.from_text(text=text)])

    def tool_results_turn(self, results):
        """Wrap (tool name, result dict) pairs as one turn that answers the model's calls"""
        parts = [types.Part.from_function_response(name=n, response=r) for n, r in results]
        return types.Content(role="user", parts=parts)

    def generate(self, contents, system, tools=None, json_schema=None):
        """Send the conversation once, with tools or with JSON output, and return the reply"""
        config = types.GenerateContentConfig(system_instruction=system)
        if tools:
            config.tools = [types.Tool(function_declarations=[
                types.FunctionDeclaration(
                    name=t["name"], description=t["description"],
                    parameters_json_schema=t["parameters"],
                )
                for t in tools
            ])]
            # Manual loop on purpose: code runs each tool and records it as evidence
            config.automatic_function_calling = types.AutomaticFunctionCallingConfig(disable=True)
        if json_schema:
            config.response_mime_type = "application/json"
            config.response_json_schema = json_schema
        started = time.monotonic()
        response, retries = self.call_with_retry(contents, config)
        calls = [ToolCall(c.name, dict(c.args or {})) for c in (response.function_calls or [])]
        turn = response.candidates[0].content if response.candidates else None
        return LLMReply(
            text=response_text(response), calls=calls, turn=turn,
            ms=round((time.monotonic() - started) * 1000), retries=retries,
        )

    def call_with_retry(self, contents, config):
        """Call Gemini, waiting and retrying on 429, 503 or no answer, and return it and retries"""
        for attempt, wait in enumerate((*RETRY_WAITS_S, None), start=1):
            try:
                response = self.client.models.generate_content(
                    model=self.model, contents=contents, config=config
                )
                return response, attempt - 1
            except errors.APIError as error:
                if error.code not in RETRY_CODES:
                    raise
                reason, cause = error.code, error
            except TRANSIENT_ERRORS as error:
                reason, cause = "timeout", error
            if wait is None:
                raise LLMUnavailableError(
                    f"{GIVE_UP[reason]} (tried {ATTEMPTS} times). {REASONS[reason][1]}"
                ) from cause
            if self.on_wait:
                self.on_wait(
                    f"{REASONS[reason][0]}, please wait {wait} s "
                    f"(attempt {attempt + 1} of {ATTEMPTS})"
                )
            self.sleep(wait)


def response_text(response):
    """Return the text parts of a response, without the warning the SDK gives for tool calls"""
    if not response.candidates or not response.candidates[0].content:
        return ""
    parts = response.candidates[0].content.parts or []
    return "".join(p.text for p in parts if p.text and not getattr(p, "thought", False))


def parse_json(text):
    """Parse the JSON the model returned, or return None when it is not valid JSON"""
    try:
        value = json.loads(text)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None
