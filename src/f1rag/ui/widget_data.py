"""Widget data: one read-only, parameterised query per dashboard widget (FR-19)"""

# The model only names widgets, drivers and laps; every number shown comes from these queries

import re
from statistics import median

NEUTRAL_COLOR = "#9A9AA8"
# Drivers shown in a position chart when none are named
TOP_FINISHERS = 5


def rows(conn, sql, params=()):
    """Run a query and return its rows as dictionaries"""
    cursor = conn.execute(sql, params)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def in_order(found, wanted):
    """Sort rows by the position of their driver in the wanted list, keeping lap order"""
    rank = {code: index for index, code in enumerate(wanted)}
    return sorted(found, key=lambda row: rank.get(row["code"], len(rank)))


def placeholders(items):
    """Return ?, ?, ? for a list, for use in an IN clause"""
    return ", ".join("?" for _ in items)


def color(value):
    """Turn a stored team colour into #RRGGBB, or a neutral grey when it is missing"""
    value = (value or "").lstrip("#")
    return f"#{value.upper()}" if re.fullmatch(r"[0-9A-Fa-f]{6}", value) else NEUTRAL_COLOR


def codes(drivers):
    """Upper-case driver codes, keeping their order"""
    return [code.strip().upper() for code in drivers or []]


def race_header(conn, season, round_no):
    """Return the race name, circuit, date, laps and number of starters for the page banner"""
    event = rows(
        conn,
        "SELECT event_name, circuit, country, race_date FROM events "
        "WHERE season = ? AND round = ?",
        (season, round_no),
    )
    header = event[0] if event else {
        "event_name": None, "circuit": None, "country": None, "race_date": None,
    }
    counts = conn.execute(
        "SELECT MAX(laps_completed), COUNT(*) FROM results WHERE season = ? AND round = ?",
        (season, round_no),
    ).fetchone()
    return {**header, "season": season, "round": round_no, "laps": counts[0],
            "drivers": counts[1]}


def race_drivers(conn, season, round_no):
    """Map each driver code of a race to name, team and team colour, for charts and tables"""
    found = rows(
        conn,
        "SELECT driver_code AS code, full_name AS name, team, team_color FROM entries "
        "WHERE season = ? AND round = ?",
        (season, round_no),
    )
    return {
        row["code"]: {
            "name": row["name"], "team": row["team"], "team_color": color(row["team_color"]),
        }
        for row in found
    }


def driver_cards(conn, season, round_no, drivers):
    """Return name, team, colour, number, grid to finish, points and best lap per driver"""
    wanted = codes(drivers)
    if not wanted:
        return []
    found = rows(
        conn,
        "SELECT e.driver_code AS code, e.full_name AS name, e.team, e.team_color, "
        "e.car_number AS number, r.grid_pos AS grid, r.finish_pos AS finish, r.status, "
        "r.points, (SELECT MIN(l.lap_time_s) FROM laps l WHERE l.season = e.season "
        "AND l.round = e.round AND l.driver_code = e.driver_code) AS best_lap_s "
        "FROM entries e LEFT JOIN results r ON r.season = e.season AND r.round = e.round "
        f"AND r.driver_code = e.driver_code WHERE e.season = ? AND e.round = ? "
        f"AND e.driver_code IN ({placeholders(wanted)})",
        (season, round_no, *wanted),
    )
    by_code = {row["code"]: row for row in found}
    cards = []
    for code in wanted:
        if code not in by_code:
            continue
        card = by_code[code]
        card["season"] = season
        card["team_color"] = color(card["team_color"])
        both = card["grid"] is not None and card["finish"] is not None
        card["gained"] = card["grid"] - card["finish"] if both else None
        cards.append(card)
    return cards


def duration_text(seconds, gap=False):
    """Format seconds as a race time (1:35:23.504) or a gap (+14.602s, +1:16.496)"""
    hours, rest = divmod(round(seconds, 3), 3600)
    minutes, secs = divmod(rest, 60)
    if gap:
        return f"+{secs:.3f}s" if not minutes and not hours else f"+{int(minutes)}:{secs:06.3f}"
    if hours:
        return f"{int(hours)}:{int(minutes):02d}:{secs:06.3f}"
    return f"{int(minutes)}:{secs:06.3f}"


def result_time(row, winner):
    """Return a driver's result like the official timing: winner time, gap, laps behind or status"""
    if row.get("status") != "Finished":
        return row.get("status") or "–"
    own, best = row.get("race_time_s"), winner.get("race_time_s")
    if own is not None and row.get("position") == 1:
        return duration_text(own)
    if own is not None and best is not None:
        return duration_text(own - best, gap=True)
    # OpenF1 leaves the race time empty exactly for lapped drivers (all 129 cases in 2026)
    behind = (winner.get("laps") or 0) - (row.get("laps") or 0)
    if behind > 0:
        return f"+{behind} lap{'' if behind == 1 else 's'}"
    return "–"


def classification(conn, season, round_no):
    """Return the final classification with car numbers and times, unclassified drivers last"""
    found = rows(
        conn,
        "SELECT r.finish_pos AS position, r.driver_code AS code, e.full_name AS name, e.team, "
        "e.team_color, e.car_number AS number, r.grid_pos AS grid, r.status, r.points, "
        "r.laps_completed AS laps, r.race_time_s "
        "FROM results r LEFT JOIN entries e ON e.season = r.season AND e.round = r.round "
        "AND e.driver_code = r.driver_code WHERE r.season = ? AND r.round = ? "
        "ORDER BY r.finish_pos IS NULL, r.finish_pos, r.laps_completed DESC",
        (season, round_no),
    )
    winner = next((row for row in found if row["position"] == 1), {})
    for row in found:
        row["season"] = season
        row["team_color"] = color(row["team_color"])
        row["time"] = result_time(row, winner)
    return found


def lap_times(conn, season, round_no, drivers, laps=None):
    """Return timed laps of the drivers, in the lap range if given, and their pit laps"""
    wanted = codes(drivers)
    if not wanted:
        return {"laps": [], "pit_laps": {}}
    first, last = laps or (1, 10_000)
    found = rows(
        conn,
        "SELECT driver_code AS code, lap_number AS lap, lap_time_s, compound, is_pit_out_lap "
        f"FROM laps WHERE season = ? AND round = ? AND driver_code IN ({placeholders(wanted)}) "
        "AND lap_number BETWEEN ? AND ? AND lap_time_s IS NOT NULL "
        "ORDER BY driver_code, lap_number",
        (season, round_no, *wanted, first, last),
    )
    stops = rows(
        conn,
        "SELECT driver_code AS code, lap FROM pit_stops WHERE season = ? AND round = ? "
        f"AND driver_code IN ({placeholders(wanted)}) AND lap BETWEEN ? AND ? "
        "ORDER BY driver_code, lap",
        (season, round_no, *wanted, first, last),
    )
    pit_laps = {}
    for stop in stops:
        pit_laps.setdefault(stop["code"], []).append(stop["lap"])
    return {"laps": in_order(found, wanted), "pit_laps": pit_laps}


def finishing_order(conn, season, round_no):
    """Return driver codes in finishing order, unclassified drivers last"""
    return [row["code"] for row in classification(conn, season, round_no)]


def tyre_strategy(conn, season, round_no, drivers=None):
    """Return each driver's stints in finishing order, for all or the given drivers"""
    found = rows(
        conn,
        "SELECT driver_code AS code, stint_no AS stint, compound, start_lap, end_lap, n_laps "
        "FROM stints WHERE season = ? AND round = ? ORDER BY stint_no",
        (season, round_no),
    )
    wanted = codes(drivers) or finishing_order(conn, season, round_no)
    rank = {code: index for index, code in enumerate(wanted)}
    # OpenF1 sometimes misses stints (2026 round 16, after the rain delay): every pit stop
    # should be followed by a stint that starts on the next lap; a driver with a stop that
    # is not is marked incomplete so the page can say so
    starts = {}
    for row in found:
        starts.setdefault(row["code"], set()).add(row["start_lap"])
    stop_laps = conn.execute(
        "SELECT driver_code, lap FROM pit_stops WHERE season = ? AND round = ? "
        "AND lap IS NOT NULL",
        (season, round_no),
    ).fetchall()
    incomplete = {
        code for code, lap in stop_laps
        if not starts.get(code, set()) & {lap, lap + 1}
    }
    chosen = [row for row in found if row["code"] in rank]
    for row in chosen:
        row["complete"] = row["code"] not in incomplete
    return sorted(chosen, key=lambda row: (rank[row["code"]], row["stint"]))


def pit_stops(conn, season, round_no, drivers=None):
    """Return pit-lane times per stop and how each compares with the race's median stop"""
    found = rows(
        conn,
        "SELECT driver_code AS code, stop_no AS stop, lap, pit_lane_time_s FROM pit_stops "
        "WHERE season = ? AND round = ? ORDER BY lap, driver_code",
        (season, round_no),
    )
    times = [row["pit_lane_time_s"] for row in found if row["pit_lane_time_s"] is not None]
    middle = round(median(times), 3) if times else None
    wanted = set(codes(drivers))
    chosen = [row for row in found if not wanted or row["code"] in wanted]
    for row in chosen:
        both = middle is not None and row["pit_lane_time_s"] is not None
        row["vs_median_s"] = round(row["pit_lane_time_s"] - middle, 3) if both else None
    return {"stops": chosen, "median_s": middle}


def positions(conn, season, round_no, drivers=None):
    """Return the running position per lap for the drivers, or the top finishers"""
    wanted = codes(drivers) or finishing_order(conn, season, round_no)[:TOP_FINISHERS]
    classified = {r["code"] for r in classification(conn, season, round_no) if r["position"]}
    if not drivers:
        wanted = [code for code in wanted if code in classified]
    if not wanted:
        return []
    found = rows(
        conn,
        "SELECT driver_code AS code, lap_number AS lap, position FROM laps "
        f"WHERE season = ? AND round = ? AND driver_code IN ({placeholders(wanted)}) "
        "AND position IS NOT NULL ORDER BY driver_code, lap_number",
        (season, round_no, *wanted),
    )
    # The first driver drawn gets the solid line, so the higher finisher comes first
    return in_order(found, wanted)


def race_summary(conn, season, round_no):
    """Return the stored race summary with the winner, fastest lap and interruptions"""
    stored = rows(
        conn,
        "SELECT summary_md, generated_at, model FROM race_summaries "
        "WHERE season = ? AND round = ?",
        (season, round_no),
    )
    card = stored[0] if stored else {"summary_md": None, "generated_at": None, "model": None}
    winner = rows(
        conn,
        "SELECT e.full_name FROM results r JOIN entries e ON e.season = r.season "
        "AND e.round = r.round AND e.driver_code = r.driver_code "
        "WHERE r.season = ? AND r.round = ? AND r.finish_pos = 1",
        (season, round_no),
    )
    card["winner"] = winner[0]["full_name"] if winner else None
    fastest = rows(
        conn,
        "SELECT driver_code AS code, lap_number AS lap, lap_time_s AS time_s FROM laps "
        "WHERE season = ? AND round = ? AND lap_time_s IS NOT NULL ORDER BY lap_time_s LIMIT 1",
        (season, round_no),
    )
    card["fastest_lap"] = fastest[0] if fastest else None
    counts = rows(
        conn,
        "SELECT SUM(message = 'SAFETY CAR DEPLOYED') AS sc, "
        "SUM(message IN ('VSC DEPLOYED', 'VIRTUAL SAFETY CAR DEPLOYED')) AS vsc, "
        "SUM(message LIKE 'RED FLAG%') AS red, "
        "SUM(message = 'STARTING PROCEDURE SUSPENDED') AS suspended "
        "FROM race_control WHERE season = ? AND round = ?",
        (season, round_no),
    )[0]
    card["safety_cars"] = counts["sc"] or 0
    card["virtual_safety_cars"] = counts["vsc"] or 0
    card["red_flags"] = counts["red"] or 0
    # A start stopped before the race (for example for rain) is logged without RED FLAG
    card["start_suspended"] = bool(counts["suspended"])
    return card


def track_periods(conn, season, round_no):
    """List the safety car and VSC laps and the red flags of a race, in lap order"""
    # 2026 says VSC, 2024 and 2025 spell out VIRTUAL SAFETY CAR
    starts = {"SAFETY CAR DEPLOYED": "SC", "VSC DEPLOYED": "VSC",
              "VIRTUAL SAFETY CAR DEPLOYED": "VSC"}
    ends = {"SAFETY CAR IN THIS LAP": "SC", "VSC ENDING": "VSC",
            "VIRTUAL SAFETY CAR ENDING": "VSC"}
    events = rows(
        conn,
        "SELECT lap_number AS lap, message FROM race_control WHERE season = ? AND round = ? "
        "AND lap_number IS NOT NULL ORDER BY event_no",
        (season, round_no),
    )
    last = rows(conn, "SELECT MAX(lap_number) AS lap FROM laps WHERE season = ? AND round = ?",
                (season, round_no))[0]["lap"]
    periods, open_period = [], None
    for event in events:
        message, lap = event["message"] or "", event["lap"]
        if message in starts and open_period is None:
            open_period = {"kind": starts[message], "start": lap, "end": lap}
        elif open_period and ends.get(message) == open_period["kind"]:
            periods.append({**open_period, "end": lap})
            open_period = None
        elif message.startswith("RED FLAG"):
            # A red flag stops the race, so a safety car running then ends with it
            if open_period:
                periods.append({**open_period, "end": lap})
                open_period = None
            periods.append({"kind": "RED", "start": lap, "end": lap})
    if open_period:
        periods.append({**open_period, "end": max(last or 0, open_period["start"])})
    return periods


def fetch(conn, season, round_no, widget):
    """Fetch the data for one validated widget"""
    kind, drivers = widget.type, widget.drivers
    if kind == "driver_card":
        return driver_cards(conn, season, round_no, drivers)
    if kind == "classification":
        return classification(conn, season, round_no)
    if kind == "lap_time_chart":
        return lap_times(conn, season, round_no, drivers, widget.laps)
    if kind == "tyre_strategy":
        return tyre_strategy(conn, season, round_no, drivers)
    if kind == "pit_stops_table":
        return pit_stops(conn, season, round_no, drivers)
    if kind == "position_chart":
        return positions(conn, season, round_no, drivers)
    if kind == "race_summary_card":
        return race_summary(conn, season, round_no)
    raise ValueError(f"No data for widget {kind}")
