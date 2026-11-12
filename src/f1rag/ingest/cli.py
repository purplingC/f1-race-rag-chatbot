"""Command line entry point: load race data from OpenF1 and race reports from Wikipedia"""

import argparse
import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from f1rag import config, store, vectors
from f1rag.ingest import structured, text
from f1rag.ingest.errors import RateLimitError
from f1rag.ingest.openf1 import OpenF1Client
from f1rag.ingest.wikipedia import WikipediaClient

SOURCE = "openf1"
TEXT_SOURCE = "wikipedia"
# What --only selects, in loading order
SOURCES_FOR = {None: (SOURCE, TEXT_SOURCE), "data": (SOURCE,), "text": (TEXT_SOURCE,)}
SOURCE_NAMES = {SOURCE: "race data", TEXT_SOURCE: "Wikipedia text"}
UNITS = {SOURCE: "rows", TEXT_SOURCE: "chunks"}
# OpenF1 fills some data (such as pit durations) a few hours after a session ends
SETTLE_HOURS = 6
# Meeting fields that a --gp query is matched against, the official name is left out
# because it holds sponsor names such as an airline that appear in unrelated races
MATCH_FIELDS = ("meeting_name", "location", "country_name", "circuit_short_name")


class SelectionError(Exception):
    """Raised when a --gp query does not lead to exactly one race that can be loaded"""


@dataclass
class Summary:
    """What a run did, used for the final line and the exit code"""

    loaded: list = field(default_factory=list)
    already: list = field(default_factory=list)
    not_finished: list = field(default_factory=list)
    cancelled: list = field(default_factory=list)
    failed: list = field(default_factory=list)
    error: str = None
    stopped: str = None

    @property
    def exit_code(self):
        """Return 0 when all went well, 1 when a load failed or stopped, 2 for a bad selection"""
        if self.error:
            return 2
        if self.failed or self.stopped:
            return 1
        return 0

    def totals_line(self):
        """Return one line that counts what happened"""
        return (
            f"Done: {len(self.loaded)} loaded, {len(self.already)} already loaded, "
            f"{len(self.not_finished)} not finished, {len(self.cancelled)} cancelled, "
            f"{len(self.failed)} failed"
        )


def parse_args(argv=None):
    """Read the command line arguments"""
    parser = argparse.ArgumentParser(
        prog="python -m f1rag.ingest",
        description="Load race data from OpenF1 and race reports from Wikipedia",
    )
    parser.add_argument("--year", type=int, required=True, help="season to load, for example 2026")
    which = parser.add_mutually_exclusive_group(required=True)
    which.add_argument(
        "--gp", help="race to load, matched against its name, country, location or circuit"
    )
    which.add_argument(
        "--all", action="store_true", help="load every finished, non-cancelled race of the season"
    )
    parser.add_argument(
        "--refresh",
        action="store_true",
        help="ask OpenF1 again instead of using saved answers, and reload rounds already loaded",
    )
    parser.add_argument(
        "--only",
        choices=["data", "text"],
        help="load only race data (SQLite) or only Wikipedia text (ChromaDB), default both",
    )
    parser.add_argument(
        "--db", default=str(config.DB_PATH), help="database file, default data/f1_stats.db"
    )
    parser.add_argument(
        "--chroma", default=str(config.CHROMA_DIR), help="vector store folder, default data/chroma"
    )
    return parser.parse_args(argv)


def describe(meeting, race_day=None):
    """Return a short label for a meeting, with the race day when it is known"""
    when = race_day or f"weekend of {meeting['date_start'][:10]}"
    return f"{meeting['meeting_name']} ({meeting['location']}, {when})"


def describe_race(race):
    """Return the label of a held race, using the day of its race session"""
    return describe(race["meeting"], race["session"]["date_start"][:10])


def is_finished(session, now):
    """Tell whether a race session ended at least SETTLE_HOURS ago"""
    ended = datetime.fromisoformat(session["date_end"])
    return ended + timedelta(hours=SETTLE_HOURS) <= now


def list_races(client, year):
    """Return the year's held races with their round numbers, plus the cancelled meetings"""
    sessions = client.race_sessions(year)
    rounds = structured.number_rounds(sessions)
    meetings = client.get("meetings", year=year)
    meeting_of = {m["meeting_key"]: m for m in meetings}
    races = [
        {
            "round": rounds[s["session_key"]],
            "session": s,
            "meeting": meeting_of[s["meeting_key"]],
        }
        for s in sessions
    ]
    cancelled = [m for m in meetings if m.get("is_cancelled")]
    return races, cancelled


def matches(meeting, query):
    """Tell whether the query appears, ignoring case, in any of the meeting's name fields"""
    needle = query.lower()
    return any(needle in str(meeting.get(name) or "").lower() for name in MATCH_FIELDS)


def select_race(races, cancelled, query):
    """Pick the one held race that matches the query, with notes about cancelled matches"""
    held = [r for r in races if matches(r["meeting"], query)]
    dropped = [m for m in cancelled if matches(m, query)]
    notes = [f"'{query}' also matches {describe(m)}, which was cancelled" for m in dropped]
    if len(held) > 1:
        options = "; ".join(f"round {r['round']} {describe_race(r)}" for r in held)
        raise SelectionError(f"'{query}' matches several races, be more specific: {options}")
    if not held and dropped:
        raise SelectionError(f"'{query}' only matches cancelled races: {'; '.join(notes)}")
    if not held:
        raise SelectionError(f"No race matches '{query}'")
    return held[0], notes


def already_loaded(conn, season, round_no, source=SOURCE):
    """Tell whether ingest_log already has a successful load of this source for this round"""
    sql = (
        "SELECT 1 FROM ingest_log "
        "WHERE season = ? AND round = ? AND source = ? AND status = 'ok' LIMIT 1"
    )
    return conn.execute(sql, (season, round_no, source)).fetchone() is not None


def log_load(conn, season, round_no, status, rows, message, now, source=SOURCE):
    """Add one row to ingest_log for a finished or failed load of one source"""
    with conn:
        conn.execute(
            "INSERT INTO ingest_log (season, round, source, status, rows, message, ts) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (season, round_no, source, status, rows, message, now.isoformat()),
        )


def choose(args, races, cancelled, now, summary, say):
    """Return the races to load for --gp or --all, or an empty list after setting an error"""
    if args.all:
        summary.cancelled = cancelled
        for meeting in cancelled:
            say(f"Cancelled, not loaded: {describe(meeting)}")
        if not races:
            summary.error = f"No races found for {args.year}"
        return races
    try:
        entry, notes = select_race(races, cancelled, args.gp)
        if not is_finished(entry["session"], now):
            raise SelectionError(
                f"Round {entry['round']} {describe_race(entry)} is not finished yet"
            )
    except SelectionError as error:
        summary.error = str(error)
        return []
    for note in notes:
        say(f"Note: {note}")
    return [entry]


def load_source(source, args, conn, client, entry, clock, text_loader):
    """Load one source for one race and return how many rows or chunks were written"""
    if source == SOURCE:
        counts = structured.ingest_race(conn, client, args.year, entry["session"], entry["round"])
        return sum(counts.values())
    if text_loader is None:
        raise ValueError("No text loader was given for Wikipedia text")
    return text_loader(args.year, entry, clock().isoformat())


def run(args, conn, client, now, say=lambda line: None, clock=None, text_loader=None):
    """Load the chosen races, log each source of each race, and return a summary"""
    # clock gives the time written to ingest_log, so each load records when it finished
    clock = clock or (lambda: now)
    summary = Summary()
    races, cancelled = list_races(client, args.year)
    chosen = choose(args, races, cancelled, now, summary, say)
    if summary.error:
        say(f"Error: {summary.error}")
        return summary

    for entry in chosen:
        label = f"round {entry['round']} {describe_race(entry)}"
        if not is_finished(entry["session"], now):
            summary.not_finished.append(entry)
            say(f"Skipped {label}: not finished yet")
            continue
        for source in SOURCES_FOR[args.only]:
            what = f"{label}, {SOURCE_NAMES[source]}"
            if args.all and not args.refresh and already_loaded(
                conn, args.year, entry["round"], source
            ):
                summary.already.append((entry, source))
                say(f"Skipped {what}: already loaded")
                continue

            say(f"Loading {what}")
            try:
                rows = load_source(source, args, conn, client, entry, clock, text_loader)
            except RateLimitError as error:
                message = f"{type(error).__name__}: {error}"
                log_load(conn, args.year, entry["round"], "failed", None, message, clock(), source)
                summary.failed.append((entry, source, message))
                summary.stopped = (
                    f"rate limit reached while loading {SOURCE_NAMES[source]}, "
                    "run the same command again to resume"
                )
                say(f"Stopped: {summary.stopped}")
                break
            except Exception as error:
                message = f"{type(error).__name__}: {error}"
                log_load(conn, args.year, entry["round"], "failed", None, message, clock(), source)
                summary.failed.append((entry, source, message))
                say(f"Failed {what}: {message}")
                continue

            log_load(conn, args.year, entry["round"], "ok", rows, None, clock(), source)
            summary.loaded.append((entry, source, rows))
            say(f"Loaded {what}: {rows} {UNITS[source]}")
        if summary.stopped:
            break
    return summary


def make_text_loader(args):
    """Build the Wikipedia loader with the vector store and a cached Wikipedia client"""
    collection = vectors.open_collection(Path(args.chroma))
    wiki = WikipediaClient(config.CACHE_DIR / "wikipedia", refresh=args.refresh)

    def load(season, entry, ingested_at):
        """Load one race's Wikipedia text and return the chunk count"""
        return text.ingest_race_text(
            collection, wiki, season, entry["round"], entry["meeting"], ingested_at
        )

    return load


def main(argv=None, client=None, now=None, text_loader=None):
    """Run the command line tool and return the exit code"""
    args = parse_args(argv)
    now_given = now is not None
    now = now or datetime.now(timezone.utc)
    client = client or OpenF1Client(config.CACHE_DIR / "openf1", refresh=args.refresh)
    db_path = Path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with contextlib.closing(store.connect(db_path)) as conn:
        store.create_schema(conn)
        clock = None if now_given else (lambda: datetime.now(timezone.utc))
        if text_loader is None and TEXT_SOURCE in SOURCES_FOR[args.only]:
            text_loader = make_text_loader(args)
        summary = run(args, conn, client, now, say=print, clock=clock, text_loader=text_loader)
    if not summary.error:
        print(summary.totals_line())
    return summary.exit_code
