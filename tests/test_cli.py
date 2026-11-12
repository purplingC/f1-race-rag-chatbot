from datetime import datetime, timedelta, timezone

import pytest

from f1rag import store
from f1rag.ingest import cli, structured
from f1rag.ingest.openf1 import OpenF1Client, RateLimitError

# Everything below is placeholder data for tests, not real F1 data
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)


def iso(text):
    """Return a UTC timestamp string for a placeholder race date"""
    return datetime.fromisoformat(text).replace(tzinfo=timezone.utc).isoformat()


def meeting(key, name, location, country, start, cancelled=False):
    """Build a placeholder meeting row"""
    return {
        "meeting_key": key,
        "meeting_name": name,
        "meeting_official_name": f"OFFICIAL {name.upper()}",
        "location": location,
        "country_name": country,
        "circuit_short_name": location,
        "date_start": iso(start),
        "year": 2026,
        "is_cancelled": cancelled,
    }


def race(key, meeting_key, start, cancelled=False):
    """Build a placeholder Race session row that ends two hours after it starts"""
    begin = datetime.fromisoformat(iso(start))
    return {
        "session_key": key,
        "meeting_key": meeting_key,
        "session_name": "Race",
        "date_start": begin.isoformat(),
        "date_end": (begin + timedelta(hours=2)).isoformat(),
        "year": 2026,
        "is_cancelled": cancelled,
    }


# Rounds by date once the cancelled race is left out: Alpha 1, Gamma 2, Epsilon 3, Delta 4
DATA = {
    "meetings": [
        meeting(1, "Placeholder Alpha Grand Prix", "Alphaville", "Alphaland", "2026-03-01T14:00"),
        meeting(2, "Placeholder Beta Grand Prix", "Betatown", "Betaland", "2026-04-01T14:00", True),
        meeting(3, "Placeholder Gamma Grand Prix", "Gammaburg", "Betaland", "2026-05-01T14:00"),
        meeting(6, "Placeholder Epsilon Grand Prix", "Epsilonia", "Alphaland", "2026-06-01T14:00"),
        meeting(4, "Placeholder Delta Grand Prix", "Deltaville", "Deltaland", "2026-08-01T14:00"),
        meeting(5, "Placeholder Pre-Season Testing", "Testville", "Testland", "2026-02-01T09:00"),
    ],
    "sessions": [
        race(101, 1, "2026-03-01T14:00"),
        race(102, 2, "2026-04-01T14:00", cancelled=True),
        race(103, 3, "2026-05-01T14:00"),
        race(106, 6, "2026-06-01T14:00"),
        race(104, 4, "2026-08-01T14:00"),
    ],
}


class FakeResponse:
    """A canned HTTP response with the parts of requests.Response we use"""

    def __init__(self, status_code, rows):
        """Store a canned status code and JSON body"""
        self.status_code = status_code
        self._rows = rows

    def json(self):
        """Return the canned JSON body"""
        return self._rows

    def raise_for_status(self):
        """Do nothing, because the fake only returns 200 and 404"""


class FakeHttp:
    """A fake requests module that filters the canned rows by the query parameters"""

    def get(self, url, params=None, headers=None, timeout=None):
        """Return the rows whose fields match every query parameter, or 404 when none do"""
        endpoint = url.rsplit("/", 1)[1]
        wanted = params or {}
        rows = [r for r in DATA.get(endpoint, []) if all(r.get(k) == v for k, v in wanted.items())]
        return FakeResponse(200, rows) if rows else FakeResponse(404, [])


class FakeIngest:
    """A stand-in for structured.ingest_race that records calls and can be made to fail"""

    def __init__(self):
        """Start with no calls and no failures"""
        self.calls = []
        self.fail = {}

    def __call__(self, conn, client, season, race_session, round_no):
        """Record the call, raise the planned error for this round, else return row counts"""
        self.calls.append(round_no)
        if round_no in self.fail:
            raise self.fail[round_no]
        return {"laps": 10, "results": 2}


class FakeText:
    """A stand-in for the Wikipedia text loader that records calls and can be made to fail"""

    def __init__(self):
        """Start with no calls and no failures"""
        self.calls = []
        self.fail = {}

    def __call__(self, season, entry, ingested_at):
        """Record the round, raise the planned error for it, else return a chunk count"""
        self.calls.append(entry["round"])
        if entry["round"] in self.fail:
            raise self.fail[entry["round"]]
        return 7


@pytest.fixture
def text():
    """Return a recording fake for the Wikipedia text loader"""
    return FakeText()


@pytest.fixture
def ingest(monkeypatch):
    """Replace the real race loader with a recording fake"""
    fake = FakeIngest()
    monkeypatch.setattr(structured, "ingest_race", fake)
    return fake


@pytest.fixture
def client(tmp_path):
    """Build a real OpenF1 client on top of the fake network, with no waiting"""
    return OpenF1Client(tmp_path, http=FakeHttp(), sleep=lambda seconds: None)


@pytest.fixture
def conn():
    """Open an in-memory database with the schema created"""
    connection = store.connect(":memory:")
    store.create_schema(connection)
    yield connection
    connection.close()


def run(conn, client, *argv, text_loader=None):
    """Parse the arguments and run the CLI logic, returning the summary and the printed lines"""
    lines = []
    args = cli.parse_args(["--year", "2026", *argv])
    loader = text_loader or (lambda season, entry, ingested_at: 0)
    summary = cli.run(args, conn, client, NOW, say=lines.append, text_loader=loader)
    return summary, lines


def logged(conn):
    """Return the ingest_log rows as plain tuples"""
    sql = "SELECT round, source, status, rows, message FROM ingest_log ORDER BY id"
    return [tuple(row) for row in conn.execute(sql)]


def test_parse_args_needs_a_year_and_exactly_one_of_gp_or_all():
    """Check that missing or conflicting arguments are rejected"""
    with pytest.raises(SystemExit):
        cli.parse_args(["--gp", "Alpha"])
    with pytest.raises(SystemExit):
        cli.parse_args(["--year", "2026"])
    with pytest.raises(SystemExit):
        cli.parse_args(["--year", "2026", "--gp", "Alpha", "--all"])
    args = cli.parse_args(["--year", "2026", "--gp", "Alpha"])
    assert (args.year, args.gp, args.all, args.refresh) == (2026, "Alpha", False, False)


def test_is_finished_waits_for_the_settle_time():
    """Check that a race only counts as finished some hours after the session ended"""
    session = race(1, 1, "2026-03-01T14:00")
    end = datetime.fromisoformat(session["date_end"])
    assert not cli.is_finished(session, end + timedelta(hours=cli.SETTLE_HOURS - 1))
    assert cli.is_finished(session, end + timedelta(hours=cli.SETTLE_HOURS))


QUERIES = ["gammaburg", "GAMMA", "gamma grand", "gammaburg"]


@pytest.mark.parametrize("query", QUERIES)
def test_gp_matches_name_and_location_ignoring_case(conn, client, ingest, query):
    """Check that the race query matches the race name and location without caring about case"""
    summary, _ = run(conn, client, "--gp", query)
    assert ingest.calls == [2]
    assert summary.exit_code == 0


def test_gp_loads_the_one_held_match_and_notes_the_cancelled_one(conn, client, ingest):
    """Check that a country with a cancelled and a held race loads the held one and says so"""
    summary, lines = run(conn, client, "--gp", "Betaland")
    assert ingest.calls == [2]
    assert [(r["round"], source) for r, source, _ in summary.loaded] == [
        (2, "openf1"),
        (2, "wikipedia"),
    ]
    assert any("Placeholder Beta Grand Prix" in line and "cancelled" in line for line in lines)
    assert summary.exit_code == 0


def test_gp_does_not_match_sponsor_words_in_the_official_name(conn, client, ingest):
    """Check that a word found only in the official name, such as a sponsor, matches no race"""
    summary, lines = run(conn, client, "--gp", "official")
    assert ingest.calls == []
    assert summary.exit_code == 2
    assert "No race matches 'official'" in " ".join(lines)


def test_gp_matching_several_held_races_is_ambiguous(conn, client, ingest):
    """Check that an ambiguous query loads nothing and lists the choices"""
    summary, lines = run(conn, client, "--gp", "Alphaland")
    assert ingest.calls == []
    assert summary.exit_code == 2
    text = " ".join(lines)
    assert "round 1" in text and "round 3" in text
    assert "Alphaville" in text and "Epsilonia" in text


def test_gp_matching_only_a_cancelled_race_says_it_was_cancelled(conn, client, ingest):
    """Check that a cancelled race is reported as cancelled, not as missing"""
    summary, lines = run(conn, client, "--gp", "Betatown")
    assert ingest.calls == []
    assert summary.exit_code == 2
    assert "cancelled" in " ".join(lines)


def test_gp_with_no_match_is_reported(conn, client, ingest):
    """Check that an unknown race gives a clear message and loads nothing"""
    summary, lines = run(conn, client, "--gp", "Nowhere")
    assert ingest.calls == []
    assert summary.exit_code == 2
    assert "No race matches 'Nowhere'" in " ".join(lines)


def test_gp_for_a_race_not_finished_yet_loads_nothing(conn, client, ingest):
    """Check that a race that has not happened yet is not loaded"""
    summary, lines = run(conn, client, "--gp", "Delta")
    assert ingest.calls == []
    assert summary.exit_code == 2
    assert "not finished" in " ".join(lines)


def test_gp_reloads_a_race_that_is_already_logged(conn, client, ingest):
    """Check that naming a race loads it again even when it was loaded before"""
    run(conn, client, "--gp", "Alphaville")
    run(conn, client, "--gp", "Alphaville")
    assert ingest.calls == [1, 1]


def test_all_loads_finished_races_in_round_order(conn, client, ingest):
    """Check that --all loads finished races by round and skips the unfinished one"""
    summary, lines = run(conn, client, "--all")
    assert ingest.calls == [1, 2, 3]
    assert [r["round"] for r, source, _ in summary.loaded if source == "openf1"] == [1, 2, 3]
    assert [r["round"] for r in summary.not_finished] == [4]
    assert [m["meeting_name"] for m in summary.cancelled] == ["Placeholder Beta Grand Prix"]
    assert summary.exit_code == 0
    # Counts are per race and source: 3 races with race data and text
    assert "6 loaded" in summary.totals_line() and "1 cancelled" in summary.totals_line()


def test_all_skips_rounds_already_logged_as_ok(conn, client, ingest):
    """Check that a rerun of --all resumes by skipping rounds marked ok in ingest_log"""
    cli.log_load(conn, 2026, 2, "ok", 12, None, NOW)
    summary, _ = run(conn, client, "--all")
    assert ingest.calls == [1, 3]
    assert [(r["round"], source) for r, source in summary.already] == [(2, "openf1")]


def test_all_with_refresh_loads_everything_again(conn, client, ingest):
    """Check that --refresh ignores the log and reloads rounds that were already ok"""
    cli.log_load(conn, 2026, 2, "ok", 12, None, NOW)
    run(conn, client, "--all", "--refresh")
    assert ingest.calls == [1, 2, 3]


def test_ingest_log_records_the_row_count_source_and_time(conn, client, ingest):
    """Check that each successful load adds one ingest_log row with the counts"""
    run(conn, client, "--gp", "Alphaville")
    assert logged(conn) == [(1, "openf1", "ok", 12, None), (1, "wikipedia", "ok", 0, None)]
    stamp = conn.execute("SELECT ts FROM ingest_log").fetchone()[0]
    assert stamp == NOW.isoformat()


def test_a_failed_round_is_logged_and_the_run_goes_on(conn, client, ingest):
    """Check that one failing round is logged, the others still load and the exit code is 1"""
    ingest.fail[2] = ValueError("Driver number 99 is not in the drivers data")
    summary, lines = run(conn, client, "--all")
    assert ingest.calls == [1, 2, 3]
    assert summary.exit_code == 1
    assert [(r["round"], source) for r, source, _ in summary.failed] == [(2, "openf1")]
    data_rows = [row for row in logged(conn) if row[1] == "openf1"]
    assert [row[:3] for row in data_rows] == [
        (1, "openf1", "ok"),
        (2, "openf1", "failed"),
        (3, "openf1", "ok"),
    ]
    assert "Driver number 99" in data_rows[1][4]
    assert any("Driver number 99" in line for line in lines)


def test_a_rerun_retries_only_the_failed_round(conn, client, ingest):
    """Check that failed rounds are not skipped by the resume logic"""
    ingest.fail[2] = ValueError("planned failure")
    run(conn, client, "--all")
    ingest.calls.clear()
    ingest.fail.clear()
    summary, _ = run(conn, client, "--all")
    assert ingest.calls == [2]
    assert summary.exit_code == 0


def test_rate_limit_stops_the_run_so_it_can_be_resumed(conn, client, ingest):
    """Check that a rate limit error stops the run and later rounds are left for the next run"""
    ingest.fail[2] = RateLimitError("OpenF1 answered 429 3 times")
    summary, lines = run(conn, client, "--all")
    assert ingest.calls == [1, 2]
    assert summary.exit_code == 1
    assert summary.stopped
    assert [row[:3] for row in logged(conn)] == [
        (1, "openf1", "ok"),
        (1, "wikipedia", "ok"),
        (2, "openf1", "failed"),
    ]
    assert any("rate limit" in line.lower() for line in lines)


def test_all_for_a_year_with_no_races_is_reported(conn, client, ingest):
    """Check that a season with no data gives a clear message"""
    args = cli.parse_args(["--year", "1999", "--all"])
    lines = []
    summary = cli.run(args, conn, client, NOW, say=lines.append)
    assert summary.exit_code == 2
    assert "No races found for 1999" in " ".join(lines)


def test_main_uses_the_given_database_and_prints_progress(tmp_path, client, ingest, capsys):
    """Check that main creates the database file, loads the race and prints a summary line"""
    db_path = tmp_path / "sub" / "test.db"
    argv = ["--year", "2026", "--gp", "Alphaville", "--db", str(db_path)]
    code = cli.main(argv, client=client, now=NOW, text_loader=lambda *args: 5)
    out = capsys.readouterr().out
    assert code == 0
    assert db_path.exists()
    assert "Placeholder Alpha Grand Prix" in out
    assert "2 loaded" in out


def test_each_log_row_gets_the_time_of_its_own_load(conn, client, ingest):
    """Check that a long --all run logs when each race finished, not when the run began"""
    times = iter([NOW + timedelta(minutes=m) for m in (1, 2, 3)])
    args = cli.parse_args(["--year", "2026", "--all", "--only", "data"])
    cli.run(args, conn, client, NOW, clock=lambda: next(times))
    stamps = [row[0] for row in conn.execute("SELECT ts FROM ingest_log ORDER BY id")]
    assert stamps == [(NOW + timedelta(minutes=m)).isoformat() for m in (1, 2, 3)]


def test_only_accepts_data_or_text():
    """Check that --only takes data or text and nothing else"""
    with pytest.raises(SystemExit):
        cli.parse_args(["--year", "2026", "--all", "--only", "pictures"])
    assert cli.parse_args(["--year", "2026", "--all", "--only", "text"]).only == "text"
    assert cli.parse_args(["--year", "2026", "--all"]).only is None


def test_default_loads_race_data_then_text_for_each_race(conn, client, ingest, text):
    """Check that without --only both sources load, race data first"""
    run(conn, client, "--gp", "Alphaville", text_loader=text)
    assert ingest.calls == [1]
    assert text.calls == [1]
    assert [row[1] for row in logged(conn)] == ["openf1", "wikipedia"]


def test_only_data_skips_text(conn, client, ingest, text):
    """Check that --only data never calls the text loader"""
    run(conn, client, "--all", "--only", "data", text_loader=text)
    assert ingest.calls == [1, 2, 3]
    assert text.calls == []


def test_only_text_skips_race_data(conn, client, ingest, text):
    """Check that --only text never calls the race data loader"""
    run(conn, client, "--all", "--only", "text", text_loader=text)
    assert ingest.calls == []
    assert text.calls == [1, 2, 3]


def test_text_failure_is_logged_on_its_own(conn, client, ingest, text):
    """Check that a missing article fails only the text load and the exit code is 1"""
    text.fail[2] = LookupError("No Wikipedia article found for placeholder race")
    summary, lines = run(conn, client, "--all", text_loader=text)
    assert summary.exit_code == 1
    assert [(r["round"], source) for r, source, _ in summary.failed] == [(2, "wikipedia")]
    statuses = {(row[0], row[1]): row[2] for row in logged(conn)}
    assert statuses[(2, "openf1")] == "ok"
    assert statuses[(2, "wikipedia")] == "failed"
    assert any("No Wikipedia article" in line for line in lines)


def test_all_resumes_each_source_separately(conn, client, ingest, text):
    """Check that a source already logged ok is skipped while the other source still loads"""
    cli.log_load(conn, 2026, 2, "ok", 7, None, NOW, source="wikipedia")
    run(conn, client, "--all", text_loader=text)
    assert ingest.calls == [1, 2, 3]
    assert text.calls == [1, 3]


def test_text_rate_limit_also_stops_the_run(conn, client, ingest, text):
    """Check that a Wikipedia rate limit stops the run like an OpenF1 one, to resume later"""
    text.fail[2] = RateLimitError("Wikipedia answered 429 3 times")
    summary, lines = run(conn, client, "--all", text_loader=text)
    assert text.calls == [1, 2]
    assert ingest.calls == [1, 2]
    assert summary.stopped
    assert any("Wikipedia text" in line and "rate limit" in line.lower() for line in lines)
