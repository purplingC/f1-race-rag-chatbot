import pytest
import requests

from f1rag.ingest import openf1
from f1rag.ingest.openf1 import OpenF1Client, RateLimitError


class FakeResponse:
    """A canned HTTP response with the parts of requests.Response we use"""

    def __init__(self, status_code=200, data=None):
        """Store a canned status code and JSON body"""
        self.status_code = status_code
        self._data = data if data is not None else []

    def json(self):
        """Return the canned JSON body"""
        return self._data

    def raise_for_status(self):
        """Raise HTTPError for 4xx and 5xx codes, like requests does"""
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")


class FakeHttp:
    """A fake requests module that replays canned responses and records calls"""

    def __init__(self, *responses):
        """Keep the responses to replay, repeating the last one when they run out"""
        self.responses = list(responses)
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        """Record the call and return the next canned response"""
        self.calls.append({"url": url, "params": params, "headers": headers})
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]


def make_client(tmp_path, *responses):
    """Build a client with a fake network, a recorded sleep and a temporary cache"""
    http = FakeHttp(*responses)
    sleeps = []
    client = OpenF1Client(tmp_path, http=http, sleep=sleeps.append)
    return client, http, sleeps


# Placeholder rows, not real F1 data
ROWS = [{"session_key": 1, "label": "placeholder"}]


def test_get_returns_rows_and_sends_params_and_user_agent(tmp_path):
    """Check that a live call returns the rows and sends the query and a User-Agent"""
    client, http, _ = make_client(tmp_path, FakeResponse(200, ROWS))
    rows = client.get("laps", session_key=1)
    assert rows == ROWS
    call = http.calls[0]
    assert call["url"] == f"{openf1.BASE_URL}/laps"
    assert call["params"] == {"session_key": 1}
    assert call["headers"]["User-Agent"] == openf1.USER_AGENT


def test_cached_response_makes_no_second_call(tmp_path):
    """Check that a repeated request is served from disk with no call and no wait"""
    client, http, sleeps = make_client(tmp_path, FakeResponse(200, ROWS))
    client.get("laps", session_key=1)
    sleeps.clear()
    assert client.get("laps", session_key=1) == ROWS
    assert len(http.calls) == 1
    assert sleeps == []


def test_cache_is_shared_between_clients(tmp_path):
    """Check that a new client reuses what an earlier client saved on disk"""
    first, _, _ = make_client(tmp_path, FakeResponse(200, ROWS))
    first.get("laps", session_key=1)
    second, http, _ = make_client(tmp_path, FakeResponse(500))
    assert second.get("laps", session_key=1) == ROWS
    assert http.calls == []


def test_different_params_are_cached_separately(tmp_path):
    """Check that two different queries do not share a cache file"""
    other = [{"session_key": 2}]
    client, http, _ = make_client(tmp_path, FakeResponse(200, ROWS), FakeResponse(200, other))
    assert client.get("laps", session_key=1) == ROWS
    assert client.get("laps", session_key=2) == other
    assert len(http.calls) == 2


def test_live_calls_are_paced(tmp_path):
    """Check that the client waits before every live call to stay under the limit"""
    client, http, sleeps = make_client(tmp_path, FakeResponse(200, ROWS))
    client.get("laps", session_key=1)
    client.get("laps", session_key=2)
    assert sleeps == [openf1.PAUSE_S, openf1.PAUSE_S]


def test_429_is_retried_with_backoff_then_succeeds(tmp_path):
    """Check that too-many-requests answers are retried after a growing wait"""
    client, http, sleeps = make_client(
        tmp_path, FakeResponse(429), FakeResponse(429), FakeResponse(200, ROWS)
    )
    assert client.get("laps", session_key=1) == ROWS
    assert len(http.calls) == 3
    pause, backoff = openf1.PAUSE_S, openf1.BACKOFF_S
    assert sleeps == [pause, backoff, pause, 2 * backoff, pause]


def test_429_on_every_attempt_raises_rate_limit_error(tmp_path):
    """Check that the client gives up with a clear error after the last retry"""
    client, http, _ = make_client(tmp_path, FakeResponse(429))
    with pytest.raises(RateLimitError):
        client.get("laps", session_key=1)
    assert len(http.calls) == openf1.MAX_ATTEMPTS


def test_404_means_no_rows_and_is_not_cached(tmp_path):
    """Check that no-results answers give an empty list and are asked again next time"""
    client, http, _ = make_client(tmp_path, FakeResponse(404), FakeResponse(200, ROWS))
    assert client.get("starting_grid", session_key=1) == []
    assert client.get("starting_grid", session_key=1) == ROWS
    assert len(http.calls) == 2


def test_other_http_errors_are_raised(tmp_path):
    """Check that a server error is not hidden as an empty result"""
    client, _, _ = make_client(tmp_path, FakeResponse(500))
    with pytest.raises(requests.HTTPError):
        client.get("laps", session_key=1)


def test_refresh_bypasses_the_cache(tmp_path):
    """Check that refresh=True asks OpenF1 again and replaces the saved answer"""
    newer = [{"session_key": 1, "label": "newer placeholder"}]
    client, http, _ = make_client(tmp_path, FakeResponse(200, ROWS), FakeResponse(200, newer))
    client.get("laps", session_key=1)
    assert client.get("laps", refresh=True, session_key=1) == newer
    assert client.get("laps", session_key=1) == newer
    assert len(http.calls) == 2


def test_race_sessions_skips_cancelled_and_sorts_by_date(tmp_path):
    """Check that cancelled races are dropped and the rest are in date order"""
    sessions = [
        {"session_key": 3, "date_start": "2026-03-15T07:00:00+00:00", "is_cancelled": False},
        {"session_key": 1, "date_start": "2026-03-01T04:00:00+00:00", "is_cancelled": False},
        {"session_key": 2, "date_start": "2026-03-08T04:00:00+00:00", "is_cancelled": True},
    ]
    client, http, _ = make_client(tmp_path, FakeResponse(200, sessions))
    races = client.race_sessions(2026)
    assert [r["session_key"] for r in races] == [1, 3]
    assert http.calls[0]["params"] == {"year": 2026, "session_name": "Race"}


def test_client_refresh_option_bypasses_the_cache_for_every_call(tmp_path):
    """Check that a client built with refresh=True never reads saved answers"""
    http = FakeHttp(FakeResponse(200, ROWS), FakeResponse(200, ROWS))
    sleeps = []
    first = OpenF1Client(tmp_path, http=http, sleep=sleeps.append)
    first.get("laps", session_key=1)
    second = OpenF1Client(tmp_path, http=http, sleep=sleeps.append, refresh=True)
    second.get("laps", session_key=1)
    assert len(http.calls) == 2
