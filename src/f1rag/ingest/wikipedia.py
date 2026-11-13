"""Find and fetch the Wikipedia article of a race through the MediaWiki API, with a disk cache"""

import json
import re
import time
from pathlib import Path

import requests

from f1rag import __version__
from f1rag.ingest.errors import RateLimitError

API_URL = "https://en.wikipedia.org/w/api.php"
# Wikimedia asks every client for a descriptive User-Agent; no personal details are sent
USER_AGENT = f"f1-race-rag-chatbot/{__version__}"
# Wikimedia allows 10 requests per minute for clients that send no contact details,
# so wait 6.5 s before every live call
PAUSE_S = 6.5
MAX_ATTEMPTS = 3
# Wait after a 429 with no Retry-After header, the policy asks for at least 5 s
BACKOFF_S = 10
TIMEOUT_S = 30
# Words that mark articles about other racing series with similar titles
OTHER_SERIES = ("motorcycle",)
# Every race article we want says this, which filters out pages about other events
REQUIRED_PHRASE = "Formula One"


def plain(title):
    """Lower-case a title and treat hyphens as spaces, as in Emilia-Romagna"""
    return title.lower().replace("-", " ")


def rank_titles(titles, season, event_name, location):
    """Order candidate titles: exact race name, then longer names, then ones with the location"""
    usable = [
        t
        for t in titles
        if t.startswith(f"{season} ")
        and "Grand Prix" in t
        and not any(word in t.lower() for word in OTHER_SERIES)
    ]
    wanted = plain(f"{season} {event_name}")
    exact = [t for t in usable if plain(t) == wanted]
    longer = [t for t in usable if plain(t).startswith(wanted) and t not in exact]
    place = [t for t in usable if location.lower() in t.lower() and t not in exact + longer]
    return exact + longer + place


def retry_after(response):
    """Return the whole seconds a 429 answer asks us to wait, or None if it does not say"""
    value = response.headers.get("Retry-After", "")
    return int(value) if value.isdigit() else None


def cache_name(prefix, text):
    """Turn a query or title into a safe cache file name"""
    return f"{prefix}_{re.sub(r'[^A-Za-z0-9]+', '_', text).strip('_').lower()}"


class WikipediaClient:
    """Searches for and fetches race articles, caching each answer as a JSON file"""

    def __init__(self, cache_dir, http=requests, sleep=time.sleep, refresh=False):
        """Create a client that caches answers under cache_dir, or ignores the cache if refresh"""
        self.cache_dir = Path(cache_dir)
        self.http = http
        self.sleep = sleep
        self.refresh = refresh

    def _get(self, params, name):
        """Return the API answer for these parameters, from disk if saved, else live and saved"""
        path = self.cache_dir / f"{name}.json"
        if path.exists() and not self.refresh:
            return json.loads(path.read_text())
        data = self._fetch(params)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data

    def _fetch(self, params):
        """Call the API politely, waiting as asked after a 429, and give up after a few tries"""
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.sleep(PAUSE_S)
            response = self.http.get(
                API_URL, params=params, headers={"User-Agent": USER_AGENT}, timeout=TIMEOUT_S
            )
            if response.status_code == 429:
                if attempt < MAX_ATTEMPTS:
                    self.sleep(retry_after(response) or BACKOFF_S * attempt)
                continue
            response.raise_for_status()
            return response.json()
        raise RateLimitError(f"Wikipedia answered 429 {MAX_ATTEMPTS} times")

    def search_titles(self, query):
        """Return the article titles Wikipedia's search gives for a query"""
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": 10,
            "format": "json",
            "formatversion": 2,
        }
        data = self._get(params, cache_name("search", query))
        return [result["title"] for result in data["query"]["search"]]

    def fetch_article(self, title):
        """Return the article's plain text, URL, revision id and revision time, or None"""
        params = {
            "action": "query",
            "titles": title,
            "prop": "extracts|info|revisions",
            "explaintext": 1,
            "inprop": "url",
            "rvprop": "ids|timestamp",
            "redirects": 1,
            "format": "json",
            "formatversion": 2,
        }
        found = self._get(params, cache_name("article", title))["query"]["pages"][0]
        if found.get("missing") or not found.get("extract"):
            return None
        return {
            "title": found["title"],
            "url": found["fullurl"],
            "revision_id": found["lastrevid"],
            "published_at": found["revisions"][0]["timestamp"],
            "text": found["extract"],
        }

    def article_for_race(self, season, event_name, location):
        """Return the Formula One article for a race, or raise LookupError rather than guess"""
        titles = self.search_titles(f"{season} {event_name}")
        for title in rank_titles(titles, season, event_name, location):
            article = self.fetch_article(title)
            if article and REQUIRED_PHRASE in article["text"]:
                return article
        raise LookupError(f"No Wikipedia article found for {season} {event_name} ({location})")
