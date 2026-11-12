"""Phase 0 spike: fetch the 2026 Bahrain Grand Prix in Malaysia article from Wikipedia"""

import json
import re
from datetime import date
from pathlib import Path

import requests

API_URL = "https://en.wikipedia.org/w/api.php"
TITLE = "2026 Bahrain Grand Prix in Malaysia"
FOCUS_NAME = "Verstappen"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "wikipedia"
# Wikimedia asks every client to send a descriptive User-Agent
HEADERS = {"User-Agent": "f1-race-rag-chatbot/0.1"}
EXCERPT_CHARS = 400


def fetch_article(title):
    """Fetch the plain-text article, its page URL and its revision id from MediaWiki"""
    params = {
        "action": "query",
        "format": "json",
        "formatversion": 2,
        "redirects": 1,
        "titles": title,
        "prop": "extracts|info",
        "explaintext": 1,
        "inprop": "url",
    }
    response = requests.get(API_URL, params=params, headers=HEADERS, timeout=30)
    response.raise_for_status()
    pages = response.json()["query"]["pages"]
    if not pages or pages[0].get("missing"):
        raise SystemExit(f"No Wikipedia page found for {title}")
    return pages[0]


def main():
    """Fetch one race article, save it to the git-ignored cache and print a short summary"""
    page = fetch_article(TITLE)
    text = page["extract"]
    revision = page["lastrevid"]
    url = page["fullurl"]

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    record = {
        "title": page["title"],
        "url": url,
        "revision_id": revision,
        "retrieved": date.today().isoformat(),
        "licence": "CC BY-SA 4.0, https://creativecommons.org/licenses/by-sa/4.0/",
        "text": text,
    }
    out_path = CACHE_DIR / (page["title"].lower().replace(" ", "_") + ".json")
    out_path.write_text(json.dumps(record, indent=1))

    headings = re.findall(r"^==+ (.+?) ==+$", text, flags=re.MULTILINE)
    print(f"Title: {record['title']}")
    print(f"URL: {url} (revision {revision})")
    print(f"Retrieved: {record['retrieved']}")
    print(f"Licence: {record['licence']}")
    print(f"Characters: {len(text)}, words: {len(text.split())}, headings: {len(headings)}")
    print(f"Headings: {', '.join(headings)}")
    print(f"Mentions of {FOCUS_NAME}: {text.count(FOCUS_NAME)}")
    print(f"\nShort excerpt:\n{text[:EXCERPT_CHARS]}...")
    print(f"\nSaved to {out_path}")


if __name__ == "__main__":
    main()
