import pytest
import requests

from f1rag.ingest import wikipedia
from f1rag.ingest.errors import RateLimitError
from f1rag.ingest.wikipedia import WikipediaClient

# Everything below is placeholder data for tests, not real F1 data
F1_TEXT = "The 2026 Placeholder Grand Prix was a Formula One motor race.\n== Race ==\nText."


def page(title, text, revision=111):
    """Build a placeholder MediaWiki page answer"""
    return {
        "title": title,
        "extract": text,
        "fullurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
        "lastrevid": revision,
        "revisions": [{"revid": revision, "timestamp": "2026-10-05T10:00:00Z"}],
    }


class FakeResponse:
    """A canned HTTP response with the parts of requests.Response we use"""

    def __init__(self, data, status_code=200, headers=None):
        """Store the canned JSON body, status code and headers"""
        self._data = data
        self.status_code = status_code
        self.headers = headers or {}

    def json(self):
        """Return the canned JSON body"""
        return self._data

    def raise_for_status(self):
        """Raise HTTPError for 4xx and 5xx codes, like requests does"""
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeWiki:
    """A fake MediaWiki API with canned search results and pages, recording each call"""

    def __init__(self, search, pages, refusals=()):
        """Keep the search results, the pages and any 429 answers to give first"""
        self.search = search
        self.pages = pages
        self.refusals = list(refusals)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        """Answer a search or a page request from the canned data, after any planned 429s"""
        self.calls.append({"url": url, "params": params, "headers": headers})
        if self.refusals:
            return FakeResponse({}, 429, self.refusals.pop(0))
        if params.get("list") == "search":
            titles = self.search.get(params["srsearch"], [])
            return FakeResponse({"query": {"search": [{"title": t} for t in titles]}})
        title = params["titles"]
        found = self.pages.get(title, {"title": title, "missing": True})
        return FakeResponse({"query": {"pages": [found]}})


def make_client(tmp_path, search, pages, refresh=False, refusals=()):
    """Build a client on the fake API with a recorded sleep and a temporary cache"""
    http = FakeWiki(search, pages, refusals)
    sleeps = []
    client = WikipediaClient(tmp_path, http=http, sleep=sleeps.append, refresh=refresh)
    return client, http, sleeps


def test_rank_titles_prefers_exact_then_longer_name_and_drops_other_series():
    """Check the ranking order and that motorcycle, other years and plain titles are left out"""
    titles = [
        "2026 Placeholder motorcycle Grand Prix",
        "Placeholder Grand Prix",
        "2025 Placeholder Grand Prix",
        "2026 Placeholder Grand Prix in Elsewhere",
        "2026 Placeholder Grand Prix",
    ]
    ranked = wikipedia.rank_titles(titles, 2026, "Placeholder Grand Prix", "Mainville")
    assert ranked == ["2026 Placeholder Grand Prix", "2026 Placeholder Grand Prix in Elsewhere"]


def test_rank_titles_falls_back_to_the_location_name():
    """Check a title with the location is used when the race name differs"""
    titles = ["2026 Other Grand Prix", "2026 Elsewhere-Circuit Grand Prix"]
    ranked = wikipedia.rank_titles(titles, 2026, "Elsewhere Grand Prix", "Elsewhere")
    assert ranked == ["2026 Elsewhere-Circuit Grand Prix"]


def test_rank_titles_returns_nothing_when_no_title_fits():
    """Check that unrelated titles are never picked as a guess"""
    titles = ["2026 Other Grand Prix", "Placeholder Grand Prix"]
    assert wikipedia.rank_titles(titles, 2026, "Placeholder Grand Prix", "Mainville") == []


def test_article_for_race_returns_text_url_revision_and_time(tmp_path):
    """Check the article found for a race carries everything needed for attribution"""
    search = {"2026 Placeholder Grand Prix": ["2026 Placeholder Grand Prix"]}
    pages = {"2026 Placeholder Grand Prix": page("2026 Placeholder Grand Prix", F1_TEXT, 777)}
    client, _, _ = make_client(tmp_path, search, pages)
    article = client.article_for_race(2026, "Placeholder Grand Prix", "Mainville")
    assert article == {
        "title": "2026 Placeholder Grand Prix",
        "url": "https://en.wikipedia.org/wiki/2026_Placeholder_Grand_Prix",
        "revision_id": 777,
        "published_at": "2026-10-05T10:00:00Z",
        "text": F1_TEXT,
    }


def test_article_that_is_not_about_formula_one_is_skipped(tmp_path):
    """Check that a matching title whose text does not mention Formula One is not used"""
    search = {
        "2026 Placeholder Grand Prix": [
            "2026 Placeholder Grand Prix",
            "2026 Placeholder Grand Prix in Elsewhere",
        ]
    }
    pages = {
        "2026 Placeholder Grand Prix": page("2026 Placeholder Grand Prix", "A horse race."),
        "2026 Placeholder Grand Prix in Elsewhere": page(
            "2026 Placeholder Grand Prix in Elsewhere", F1_TEXT
        ),
    }
    client, _, _ = make_client(tmp_path, search, pages)
    article = client.article_for_race(2026, "Placeholder Grand Prix", "Mainville")
    assert article["title"] == "2026 Placeholder Grand Prix in Elsewhere"


def test_no_suitable_article_raises_lookup_error(tmp_path):
    """Check that a race with no fitting article fails with a clear error instead of guessing"""
    search = {"2026 Placeholder Grand Prix": ["Placeholder Grand Prix"]}
    client, _, _ = make_client(tmp_path, search, {})
    with pytest.raises(LookupError, match="Placeholder Grand Prix"):
        client.article_for_race(2026, "Placeholder Grand Prix", "Mainville")


def test_answers_are_cached_on_disk(tmp_path):
    """Check that a second client reuses the saved search and page with no network call"""
    search = {"2026 Placeholder Grand Prix": ["2026 Placeholder Grand Prix"]}
    pages = {"2026 Placeholder Grand Prix": page("2026 Placeholder Grand Prix", F1_TEXT)}
    first, _, _ = make_client(tmp_path, search, pages)
    first.article_for_race(2026, "Placeholder Grand Prix", "Mainville")
    second, http, _ = make_client(tmp_path, {}, {})
    article = second.article_for_race(2026, "Placeholder Grand Prix", "Mainville")
    assert article["text"] == F1_TEXT
    assert http.calls == []


def test_refresh_asks_again(tmp_path):
    """Check that refresh=True ignores the saved answers"""
    search = {"2026 Placeholder Grand Prix": ["2026 Placeholder Grand Prix"]}
    pages = {"2026 Placeholder Grand Prix": page("2026 Placeholder Grand Prix", F1_TEXT)}
    first, _, _ = make_client(tmp_path, search, pages)
    first.article_for_race(2026, "Placeholder Grand Prix", "Mainville")
    second, http, _ = make_client(tmp_path, search, pages, refresh=True)
    second.article_for_race(2026, "Placeholder Grand Prix", "Mainville")
    assert len(http.calls) == 2


def test_requests_send_a_user_agent_and_wait_between_calls(tmp_path):
    """Check the polite User-Agent header and the pause before each live call"""
    search = {"2026 Placeholder Grand Prix": ["2026 Placeholder Grand Prix"]}
    pages = {"2026 Placeholder Grand Prix": page("2026 Placeholder Grand Prix", F1_TEXT)}
    client, http, sleeps = make_client(tmp_path, search, pages)
    client.article_for_race(2026, "Placeholder Grand Prix", "Mainville")
    assert all(call["headers"]["User-Agent"] == wikipedia.USER_AGENT for call in http.calls)
    assert all(call["url"] == wikipedia.API_URL for call in http.calls)
    assert sleeps == [wikipedia.PAUSE_S, wikipedia.PAUSE_S]


SEARCH = {"2026 Placeholder Grand Prix": ["2026 Placeholder Grand Prix"]}
PAGES = {"2026 Placeholder Grand Prix": page("2026 Placeholder Grand Prix", F1_TEXT)}


def test_429_waits_for_the_retry_after_time_then_succeeds(tmp_path):
    """Check that a too-many-requests answer waits as long as the server asks, then retries"""
    client, http, sleeps = make_client(tmp_path, SEARCH, PAGES, refusals=[{"Retry-After": "7"}])
    article = client.article_for_race(2026, "Placeholder Grand Prix", "Mainville")
    assert article["revision_id"] == 111
    assert 7 in sleeps
    assert len(http.calls) == 3


def test_429_without_retry_after_backs_off_at_least_five_seconds(tmp_path):
    """Check the fallback wait when the server gives no Retry-After header"""
    client, _, sleeps = make_client(tmp_path, SEARCH, PAGES, refusals=[{}])
    client.article_for_race(2026, "Placeholder Grand Prix", "Mainville")
    assert wikipedia.BACKOFF_S in sleeps
    assert wikipedia.BACKOFF_S >= 5


def test_429_on_every_attempt_raises_rate_limit_error(tmp_path):
    """Check that the client stops with a clear error so the run can be resumed later"""
    refusals = [{}] * wikipedia.MAX_ATTEMPTS
    client, http, _ = make_client(tmp_path, SEARCH, PAGES, refusals=refusals)
    with pytest.raises(RateLimitError):
        client.article_for_race(2026, "Placeholder Grand Prix", "Mainville")
    assert len(http.calls) == wikipedia.MAX_ATTEMPTS


def test_pause_keeps_under_ten_requests_a_minute():
    """Check the pause matches the limit for clients that send no contact details"""
    assert 60 / wikipedia.PAUSE_S < 10
