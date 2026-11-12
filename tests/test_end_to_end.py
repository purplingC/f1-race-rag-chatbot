import sqlite3
from datetime import datetime, timezone

from f1rag import vectors
from f1rag.ingest import cli, structured, text
from f1rag.ingest.openf1 import OpenF1Client
from f1rag.ingest.wikipedia import WikipediaClient

# Everything below is placeholder data for tests, not real F1 data
NOW = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
RACES = [
    # (meeting_key, race_key, qualifying_key, race start, race name, location)
    (1, 101, 100, "2026-03-01T14:00:00+00:00", "Placeholder Alpha Grand Prix", "Alphaville"),
    (2, 201, 200, "2026-04-01T14:00:00+00:00", "Placeholder Beta Grand Prix", "Betatown"),
]
DRIVERS = [(11, "AAA", "Placeholder Team A"), (22, "BBB", "Placeholder Team B")]


def openf1_rows():
    """Build placeholder OpenF1 answers for two short races with two drivers each"""
    names = ("meetings", "sessions", "starting_grid") + structured.RACE_ENDPOINTS
    rows = {name: [] for name in names}
    for meeting, race, quali, start, name, place in RACES:
        day = start[:10]
        rows["meetings"].append({
            "meeting_key": meeting, "meeting_name": name, "meeting_official_name": name.upper(),
            "location": place, "country_name": "Placeholderland", "circuit_short_name": place,
            "date_start": start, "year": 2026, "is_cancelled": False,
        })
        for key, kind, hour in ((quali, "Qualifying", "11"), (race, "Race", "14")):
            rows["sessions"].append({
                "session_key": key, "meeting_key": meeting, "session_name": kind, "year": 2026,
                "date_start": f"{day}T{hour}:00:00+00:00",
                "date_end": f"{day}T{int(hour) + 2}:00:00+00:00",
                "location": place, "country_name": "Placeholderland", "circuit_short_name": place,
                "is_cancelled": False,
            })
        for position, (number, code, team) in enumerate(DRIVERS, start=1):
            rows["starting_grid"].append(
                {"session_key": quali, "driver_number": number, "position": position}
            )
            rows["drivers"].append({
                "session_key": race, "driver_number": number, "name_acronym": code,
                "full_name": f"{code} Placeholder", "team_name": team, "team_colour": "112233",
                "country_code": "PLA", "headshot_url": "https://example.invalid/x.png",
            })
            rows["session_result"].append({
                "session_key": race, "driver_number": number, "position": position,
                "number_of_laps": 2, "points": 25.0 - 7 * position, "dnf": False, "dns": False,
                "dsq": False, "duration": 200.0 + position, "gap_to_leader": 0,
            })
            rows["stints"].append({
                "session_key": race, "driver_number": number, "stint_number": 1,
                "compound": "MEDIUM", "lap_start": 1, "lap_end": 2, "tyre_age_at_start": 0,
            })
            rows["position"].append({
                "session_key": race, "driver_number": number, "position": position,
                "date": f"{day}T13:59:00+00:00",
            })
            for lap in (1, 2):
                rows["laps"].append({
                    "session_key": race, "driver_number": number, "lap_number": lap,
                    "lap_duration": 100.0, "duration_sector_1": 30.0, "duration_sector_2": 30.0,
                    "duration_sector_3": 40.0, "is_pit_out_lap": False,
                    "date_start": f"{day}T14:0{lap}:00+00:00",
                })
        rows["pit"].append({
            "session_key": race, "driver_number": 11, "lap_number": 1, "lane_duration": 22.0,
            "stop_duration": None, "date": f"{day}T14:02:00+00:00",
        })
        rows["weather"].append({
            "session_key": race, "date": f"{day}T14:00:00+00:00", "air_temperature": 20.0,
            "track_temperature": 30.0, "humidity": 50.0, "pressure": 1000.0, "rainfall": 0,
            "wind_speed": 1.0, "wind_direction": 90,
        })
        rows["race_control"].append({
            "session_key": race, "date": f"{day}T14:00:00+00:00", "lap_number": 1,
            "category": "Flag", "flag": "GREEN", "scope": "Track", "sector": None,
            "driver_number": None, "message": "PLACEHOLDER GREEN LIGHT",
        })
    return rows


class FakeResponse:
    """A canned HTTP response with the parts of requests.Response we use"""

    def __init__(self, status_code, data):
        """Store the status code and JSON body"""
        self.status_code = status_code
        self._data = data
        self.headers = {}

    def json(self):
        """Return the JSON body"""
        return self._data

    def raise_for_status(self):
        """Do nothing, because the fakes only answer 200 or 404"""


class FakeOpenF1:
    """A fake OpenF1 API that filters the placeholder rows by the query parameters"""

    def __init__(self):
        """Prepare the placeholder answers"""
        self.rows = openf1_rows()

    def get(self, url, params=None, headers=None, timeout=None):
        """Return the rows of the endpoint whose fields match every parameter, or 404"""
        endpoint = url.rsplit("/", 1)[1]
        rows = self.rows.get(endpoint, [])
        found = [r for r in rows if all(r.get(k) == v for k, v in params.items())]
        return FakeResponse(200 if found else 404, found)


class FakeWikipedia:
    """A fake MediaWiki API with one placeholder article per race"""

    def get(self, url, params=None, headers=None, timeout=None):
        """Answer a search with the race's title, or a page request with its text"""
        if params.get("list") == "search":
            title = params["srsearch"]
            return FakeResponse(200, {"query": {"search": [{"title": title}]}})
        title = params["titles"]
        text_body = (
            f"The {title} was a Formula One motor race. It was a placeholder event.\n"
            "== Race ==\nThe placeholder race was held. Nothing real happened here."
        )
        page = {
            "title": title, "extract": text_body, "lastrevid": 7,
            "fullurl": f"https://en.wikipedia.org/wiki/{title.replace(' ', '_')}",
            "revisions": [{"revid": 7, "timestamp": "2026-05-01T00:00:00Z"}],
        }
        return FakeResponse(200, {"query": {"pages": [page]}})


def table_counts(db_path):
    """Count the rows of every race table and the successful loads in ingest_log"""
    con = sqlite3.connect(db_path)
    tables = structured.RACE_TABLES
    counts = {t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in tables}
    rounds = {t: {r[0] for r in con.execute(f"SELECT DISTINCT round FROM {t}")} for t in tables}
    sql = "SELECT source, status, COUNT(*) FROM ingest_log GROUP BY source, status"
    log = con.execute(sql).fetchall()
    con.close()
    return counts, rounds, sorted(log)


def test_two_races_load_end_to_end_and_reload_without_duplicates(tmp_path, fake_embedding):
    """Check the Phase 1 exit criterion: two races load fully and reloading duplicates nothing"""
    client = OpenF1Client(tmp_path / "openf1", http=FakeOpenF1(), sleep=lambda s: None)
    wiki = WikipediaClient(tmp_path / "wiki", http=FakeWikipedia(), sleep=lambda s: None)
    collection = vectors.open_collection(tmp_path / "chroma", embedding_function=fake_embedding)

    def load_text(season, entry, ingested_at):
        """Load one race's placeholder article into the temporary vector store"""
        return text.ingest_race_text(
            collection, wiki, season, entry["round"], entry["meeting"], ingested_at
        )

    db = tmp_path / "f1.db"
    argv = ["--year", "2026", "--all", "--db", str(db)]
    assert cli.main(argv, client=client, now=NOW, text_loader=load_text) == 0
    counts, rounds, log = table_counts(db)
    chunks = collection.count()

    # Both races are in every table, and both sources loaded for both races
    assert all(found == {1, 2} for found in rounds.values())
    assert counts["laps"] == 8 and counts["entries"] == 4 and counts["events"] == 2
    assert chunks >= 2
    assert log == [("openf1", "ok", 2), ("wikipedia", "ok", 2)]

    # A forced reload of everything gives exactly the same rows and chunks
    assert cli.main(argv + ["--refresh"], client=client, now=NOW, text_loader=load_text) == 0
    again, _, log_again = table_counts(db)
    assert again == counts
    assert collection.count() == chunks
    assert log_again == [("openf1", "ok", 4), ("wikipedia", "ok", 4)]

    # A normal rerun skips both races and both sources and changes nothing
    assert cli.main(argv, client=client, now=NOW, text_loader=load_text) == 0
    assert table_counts(db) == (counts, rounds, log_again)
    assert collection.count() == chunks
