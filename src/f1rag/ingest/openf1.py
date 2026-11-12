"""OpenF1 REST client: paced, cached on disk and polite about rate limits"""

import json
import re
import time
from pathlib import Path

import requests

from f1rag.ingest.errors import RateLimitError

__all__ = ["OpenF1Client", "RateLimitError"]

BASE_URL = "https://api.openf1.org/v1"
USER_AGENT = "f1-race-rag-chatbot/0.1"
# Free limit is 3 requests/s and 30/min; waiting 2.5 s keeps us at about 24 per minute
PAUSE_S = 2.5
MAX_ATTEMPTS = 3
# Extra wait after a 429 answer: 15 s, then 30 s
BACKOFF_S = 15
TIMEOUT_S = 30


class OpenF1Client:
    """Fetches OpenF1 endpoints as lists of rows, caching each answer as a JSON file"""

    def __init__(self, cache_dir, http=requests, sleep=time.sleep, pause_s=PAUSE_S, refresh=False):
        """Create a client that caches answers under cache_dir, or ignores the cache if refresh"""
        self.cache_dir = Path(cache_dir)
        self.refresh = refresh
        self.http = http
        self.sleep = sleep
        self.pause_s = pause_s

    def _cache_path(self, endpoint, params):
        """Return the cache file for one endpoint and one set of query parameters"""
        key = "_".join(f"{name}-{params[name]}" for name in sorted(params))
        safe_key = re.sub(r"[^A-Za-z0-9._-]", "-", key)
        return self.cache_dir / f"{endpoint}_{safe_key}.json"

    def get(self, endpoint, refresh=False, **params):
        """Return the rows for an endpoint, from disk if cached, else from OpenF1"""
        path = self._cache_path(endpoint, params)
        if path.exists() and not (refresh or self.refresh):
            return json.loads(path.read_text())

        rows = self._fetch(endpoint, params)
        # An empty answer is not saved, so data that appears later is not hidden
        if rows:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows))
        return rows

    def _fetch(self, endpoint, params):
        """Call OpenF1 with pacing and retries, returning an empty list when nothing matches"""
        url = f"{BASE_URL}/{endpoint}"
        headers = {"User-Agent": USER_AGENT}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self.sleep(self.pause_s)
            response = self.http.get(url, params=params, headers=headers, timeout=TIMEOUT_S)
            if response.status_code == 429:
                if attempt < MAX_ATTEMPTS:
                    self.sleep(BACKOFF_S * attempt)
                continue
            if response.status_code == 404:
                return []
            response.raise_for_status()
            return response.json()
        raise RateLimitError(f"OpenF1 answered 429 {MAX_ATTEMPTS} times for {endpoint}")

    def race_sessions(self, year):
        """Return the year's Race sessions that were not cancelled, in date order"""
        rows = self.get("sessions", year=year, session_name="Race")
        held = [row for row in rows if not row.get("is_cancelled")]
        return sorted(held, key=lambda row: row["date_start"])
