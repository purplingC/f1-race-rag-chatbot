"""Phase 0 spike: let Gemini call one dummy tool and use its result in the answer"""

import os

from dotenv import load_dotenv
from google import genai
from google.genai import types

PROMPT = "What was the fastest lap time of driver VER? Use the tool to find out"

DUMMY_TOOL = types.FunctionDeclaration(
    name="get_dummy_lap_time",
    description="Return the fastest lap time in seconds for a driver code",
    parameters_json_schema={
        "type": "object",
        "properties": {
            "driver": {"type": "string", "description": "Three-letter driver code, e.g. VER"},
        },
        "required": ["driver"],
    },
)


def get_dummy_lap_time(driver):
    """Return a labelled placeholder lap time, never real F1 data"""
    return {"driver": driver, "fastest_lap_s": 90.0, "note": "DUMMY placeholder, not real data"}


def main():
    """Run one manual tool-calling round trip and print each step"""
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL")
    if not api_key or not model:
        raise SystemExit("Set GEMINI_API_KEY and GEMINI_MODEL in .env first")

    client = genai.Client(api_key=api_key)
    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=[DUMMY_TOOL])],
        # Manual loop on purpose: we want to see and control each step
        automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
    )
    contents = [types.Content(role="user", parts=[types.Part.from_text(text=PROMPT)])]

    first = client.models.generate_content(model=model, contents=contents, config=config)
    calls = first.function_calls
    if not calls:
        raise SystemExit(f"FAIL: model did not call the tool. Reply: {first.text}")

    call = calls[0]
    print(f"Model: {model}")
    print(f"1. Model asked to call: {call.name}({dict(call.args)})")

    result = get_dummy_lap_time(**call.args)
    print(f"2. Tool returned: {result}")

    # Send the model's own turn back, then our tool result as a user turn
    contents.append(first.candidates[0].content)
    contents.append(
        types.Content(
            role="user",
            parts=[types.Part.from_function_response(name=call.name, response=result)],
        )
    )
    second = client.models.generate_content(model=model, contents=contents, config=config)
    print(f"3. Final answer: {second.text}")


if __name__ == "__main__":
    main()
