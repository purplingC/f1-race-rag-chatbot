"""Dashboard spec: the model picks widgets, code checks them, and rules fill in when it cannot"""

import re
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

# Widget registry (Section 7.4); driver cards sit on top and do not count as main widgets
WidgetType = Literal[
    "driver_card", "classification", "lap_time_chart", "tyre_strategy",
    "pit_stops_table", "position_chart", "race_summary_card",
]
NOT_MAIN = {"driver_card"}
NEEDS_DRIVERS = {"driver_card", "lap_time_chart"}
MAX_MAIN = 4
# Five, so a top-five answer shows every driver it names
MAX_DRIVERS = 5

# Question types for the fallback planner, checked in this order (Section 7.4 table)
QUESTION_TYPES = [
    ("position", re.compile(r"\bwhy\b.*\b(lose|lost|gain|gained|position|places?)\b", re.I),
     ["driver_card", "lap_time_chart", "position_chart", "pit_stops_table"]),
    ("strategy", re.compile(r"\b(tyres?|tires?|pit|pits|stops?|strateg\w*|stints?)\b", re.I),
     ["driver_card", "tyre_strategy", "pit_stops_table"]),
    ("compare", re.compile(r"\b(compare\w*|vs\.?|versus|faster than|slower than)\b", re.I),
     ["driver_card", "lap_time_chart", "tyre_strategy"]),
    ("steward", re.compile(r"\b(penalt\w*|stewards?|rules?|investigat\w*|disqualif\w*)\b", re.I),
     ["driver_card", "classification"]),
    ("summary", re.compile(r"\b(summar\w*|recap|what happened|overview)\b", re.I),
     ["race_summary_card", "classification", "tyre_strategy"]),
    ("result", re.compile(r"\b(won|win|winner|result\w*|podium|finish\w*|classification)\b", re.I),
     ["driver_card", "classification", "race_summary_card"]),
]
DEFAULT_WIDGETS = ["classification", "race_summary_card"]
OVERVIEW_WIDGETS = ["classification", "tyre_strategy", "race_summary_card"]
SEASON_IN_SQL = re.compile(r"\bseason\s*=\s*(\d{4})", re.I)
ROUND_IN_SQL = re.compile(r"\bround\s*=\s*(\d{1,2})\b", re.I)


class RaceRef(BaseModel):
    """The race a dashboard is about"""

    model_config = ConfigDict(extra="forbid")
    season: int
    round: int


class Widget(BaseModel):
    """One widget the dashboard should show, with the drivers and laps it is about"""

    model_config = ConfigDict(extra="forbid")
    type: WidgetType
    drivers: list[str] = []
    laps: Optional[tuple[int, int]] = None

    @field_validator("drivers")
    @classmethod
    def tidy_codes(cls, codes):
        """Upper-case driver codes and drop repeats, keeping their order"""
        tidy = []
        for code in codes:
            code = code.strip().upper()
            if code not in tidy:
                tidy.append(code)
        return tidy


class DashboardSpec(BaseModel):
    """The validated dashboard: a race, its widgets and the evidence it is built on"""

    model_config = ConfigDict(extra="forbid")
    race: RaceRef
    widgets: list[Widget] = []
    evidence: list[str] = []


@dataclass
class PlanResult:
    """The dashboard to show, where it came from and why a model spec was refused"""

    spec: DashboardSpec
    source: str
    problems: list = field(default_factory=list)


def race_drivers(conn, season, round_no):
    """Return the driver codes that took part in a race"""
    sql = "SELECT driver_code FROM entries WHERE season = ? AND round = ?"
    return {row[0] for row in conn.execute(sql, (season, round_no))}


def race_laps(conn, season, round_no):
    """Return the number of laps recorded for a race, or 0"""
    sql = "SELECT MAX(lap_number) FROM laps WHERE season = ? AND round = ?"
    return conn.execute(sql, (season, round_no)).fetchone()[0] or 0


def race_loaded(conn, season, round_no):
    """Tell whether a race is in the database"""
    sql = "SELECT 1 FROM events WHERE season = ? AND round = ?"
    return conn.execute(sql, (season, round_no)).fetchone() is not None


def pydantic_problems(error):
    """Turn pydantic's errors into short readable sentences"""
    problems = []
    for item in error.errors():
        where = ".".join(str(part) for part in item["loc"])
        problems.append(f"{where}: {item['msg']} (got {item.get('input')!r})")
    return problems


def validate(raw, conn, evidence_log):
    """Check a dashboard spec against the registry and the data; return it fixed up or None"""
    if not isinstance(raw, dict):
        return None, ["the dashboard spec is not an object"]
    try:
        spec = DashboardSpec.model_validate(raw)
    except ValidationError as error:
        return None, pydantic_problems(error)
    season, round_no = spec.race.season, spec.race.round
    if not race_loaded(conn, season, round_no):
        return None, [f"race {season} round {round_no} is not loaded"]
    problems = []
    known = race_drivers(conn, season, round_no)
    total_laps = race_laps(conn, season, round_no)
    seen = set()
    for widget in spec.widgets:
        if widget.type in seen:
            problems.append(f"{widget.type} shown twice")
        seen.add(widget.type)
        unknown = [code for code in widget.drivers if code not in known]
        if unknown:
            problems.append(f"{widget.type}: drivers not in this race: {', '.join(unknown)}")
        if widget.type in NEEDS_DRIVERS and not widget.drivers:
            problems.append(f"{widget.type} needs drivers")
        if len(widget.drivers) > MAX_DRIVERS:
            problems.append(f"{widget.type}: more than {MAX_DRIVERS} drivers")
        if widget.laps is not None:
            first, last = sorted(widget.laps)
            if total_laps == 0 or first > total_laps or last < 1:
                problems.append(
                    f"{widget.type}: lap range {first}-{last} is outside the race "
                    f"(laps 1-{total_laps})"
                )
            else:
                # A range that only runs past either end is trimmed to the race
                widget.laps = (max(1, first), min(total_laps, last))
    if sum(w.type not in NOT_MAIN for w in spec.widgets) > MAX_MAIN:
        problems.append(f"more than four main widgets (limit {MAX_MAIN})")
    missing = [i for i in spec.evidence if evidence_log.get(i) is None]
    if missing:
        problems.append(f"evidence {', '.join(missing)} does not exist")
    return (None, problems) if problems else (spec, [])


def race_from_evidence(evidence_log):
    """Find the race the tools worked on: find_race first, then search filters, then SQL text"""
    for item in evidence_log.items:
        matches = item.result.get("matches") if item.tool == "find_race" else None
        if matches:
            return {"season": matches[0]["season"], "round": matches[0]["round"]}
    for item in evidence_log.items:
        if item.tool == "search_docs" and item.args.get("season") and item.args.get("round"):
            return {"season": item.args["season"], "round": item.args["round"]}
    for item in evidence_log.items:
        if item.tool == "run_sql":
            query = item.args.get("query", "")
            season, round_no = SEASON_IN_SQL.search(query), ROUND_IN_SQL.search(query)
            if season and round_no:
                return {"season": int(season.group(1)), "round": int(round_no.group(1))}
    return None


def latest_race(conn):
    """Return the most recent loaded race"""
    row = conn.execute(
        "SELECT season, round FROM events ORDER BY race_date DESC LIMIT 1"
    ).fetchone()
    return {"season": row[0], "round": row[1]} if row else None


def surnames(conn, season, round_no):
    """Map each driver's surname, in lower case, to their code for one race"""
    sql = "SELECT driver_code, full_name FROM entries WHERE season = ? AND round = ?"
    names = {}
    for code, full_name in conn.execute(sql, (season, round_no)):
        parts = (full_name or "").split()
        # Very short surnames are skipped, so ordinary words are not taken for names
        if parts and len(parts[-1]) >= 4:
            names[parts[-1].lower()] = code
    return names


def drivers_in_question(question, known, names):
    """Return the drivers a question names, by capital code or by surname, in order"""
    found = []
    for match in re.finditer(r"[A-Za-z\u00C0-\u017F]+", question or ""):
        word = match.group(0)
        code = word if word in known else names.get(word.lower())
        if code and code not in found:
            found.append(code)
    return found


def drivers_from_evidence(evidence_log, question, known, names=None):
    """Return the drivers the question names, or else those in the SQL rows, in order"""
    named = drivers_in_question(question, known, names or {})
    if named:
        return named[:MAX_DRIVERS]
    found = []
    for item in evidence_log.items:
        if item.tool != "run_sql" or not item.result.get("ok"):
            continue
        for row in item.result.get("rows", []):
            for value in row:
                if isinstance(value, str) and value.upper() in known and value.upper() not in found:
                    found.append(value.upper())
    return found[:MAX_DRIVERS]


def podium(conn, season, round_no):
    """Return the top three finishers of a race"""
    sql = (
        "SELECT driver_code FROM results WHERE season = ? AND round = ? "
        "AND finish_pos IS NOT NULL ORDER BY finish_pos LIMIT 3"
    )
    return [row[0] for row in conn.execute(sql, (season, round_no))]


def fallback(question, evidence_log, conn, race):
    """Build a dashboard from the question type and the drivers seen in the tool results"""
    season, round_no = race["season"], race["round"]
    kind, widgets = "default", DEFAULT_WIDGETS
    for name, pattern, chosen in QUESTION_TYPES:
        if pattern.search(question or ""):
            kind, widgets = name, chosen
            break
    known = race_drivers(conn, season, round_no)
    drivers = drivers_from_evidence(
        evidence_log, question, known, surnames(conn, season, round_no)
    )
    if kind == "result":
        drivers = podium(conn, season, round_no) or drivers
    built = []
    for widget_type in widgets:
        if widget_type in NEEDS_DRIVERS and not drivers:
            continue
        uses_drivers = widget_type not in {"classification", "race_summary_card"}
        built.append(Widget(type=widget_type, drivers=drivers if uses_drivers else []))
    cited = [item.id for item in evidence_log.items if item.result.get("ok", True)]
    return DashboardSpec(race=RaceRef(**race), widgets=built, evidence=cited)


def plan(question, raw_spec, evidence_log, conn, default_race=None):
    """Use the model's dashboard spec if it is valid, otherwise build one by rules"""
    if not evidence_log.items:
        # No evidence, no new widgets: the page keeps its default overview
        return None
    problems = []
    if raw_spec is not None:
        spec, problems = validate(raw_spec, conn, evidence_log)
        if spec is not None:
            return PlanResult(spec=spec, source="model")
    race = race_from_evidence(evidence_log) or default_race or latest_race(conn)
    if race is None or not race_loaded(conn, race["season"], race["round"]):
        return None
    built = fallback(question, evidence_log, conn, race)
    return PlanResult(spec=built, source="fallback", problems=problems)


def default_overview(conn, race):
    """Return the dashboard shown before the first question: result, strategy and summary"""
    widgets = [Widget(type=widget_type) for widget_type in OVERVIEW_WIDGETS]
    return DashboardSpec(race=RaceRef(**race), widgets=widgets)
