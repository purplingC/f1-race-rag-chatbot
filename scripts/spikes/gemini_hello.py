"""Phase 0 spike: send one public prompt to Gemini and print the reply"""

import os

from dotenv import load_dotenv
from google import genai

PROMPT = "Say hello to a Formula 1 fan in one short sentence"


def main():
    """Send one public prompt to the model named in .env and print the reply"""
    load_dotenv()
    api_key = os.getenv("GEMINI_API_KEY")
    model = os.getenv("GEMINI_MODEL")
    if not api_key or not model:
        raise SystemExit("Set GEMINI_API_KEY and GEMINI_MODEL in .env first")

    client = genai.Client(api_key=api_key)
    response = client.models.generate_content(model=model, contents=PROMPT)

    print(f"Model: {model}")
    print(f"Reply: {response.text}")
    print(f"Total tokens: {response.usage_metadata.total_token_count}")


if __name__ == "__main__":
    main()
