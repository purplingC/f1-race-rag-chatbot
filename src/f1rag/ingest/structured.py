"""Turn OpenF1 rows into table rows and load one race into SQLite"""

from datetime import datetime

# Tables filled by ingest_race. Each row carries season and round.
RACE_TABLES = (
    "events",
    "entries",
    "results",
    "laps",
    "stints",
    "pit_stops",
    "weather",
    "race_control",
)

# OpenF1 endpoints queried with the race session key
RACE_ENDPOINTS = ("drivers", "session_result", "laps", "stints", "pit", "weather", "race_control")


def number_rounds(race_sessions):
    """Number non-cancelled race sessions 1, 2, ... by date and return {session_key: round}"""
    ordered = sorted(race_sessions, key=lambda row: row["date_start"])
    return {row["session_key"]: number for number, row in enumerate(ordered, start=1)}


def driver_codes(drivers):
    """Map each car number to the driver's three-letter code for this race"""
    return {row["driver_number"]: row["name_acronym"] for row in drivers}


def code_of(codes, number):
    """Return the driver code for a car number, failing loudly if it is unknown"""
    if number not in codes:
        raise ValueError(f"Driver number {number} is not in the drivers data")
    return codes[number]


def build_event(season, round_no, race_session, meeting, qualifying_key):
    """Build the events row from the race session, its meeting and the qualifying key"""
    return {
        "season": season,
        "round": round_no,
        "event_name": meeting["meeting_name"],
        "official_name": meeting["meeting_official_name"],
        "circuit": race_session["circuit_short_name"],
        "country": race_session["country_name"],
        "race_date": race_session["date_start"][:10],
        "meeting_key": race_session["meeting_key"],
        "race_session_key": race_session["session_key"],
        "qualifying_session_key": qualifying_key,
    }


def build_entries(season, round_no, drivers):
    """Build one entries row per driver, holding the team for this race"""
    return [
        {
            "season": season,
            "round": round_no,
            "driver_code": row["name_acronym"],
            "full_name": row["full_name"],
            "team": row["team_name"],
            "car_number": row["driver_number"],
            "team_color": row["team_colour"],
            "country_code": row["country_code"],
            "headshot_url": row["headshot_url"],
        }
        for row in drivers
    ]


def result_status(row):
    """Turn the did-not-start, disqualified and did-not-finish flags into a status text"""
    if row["dns"]:
        return "DNS"
    if row["dsq"]:
        return "DSQ"
    if row["dnf"]:
        return "DNF"
    return "Finished"


def build_results(season, round_no, session_result, codes, grid):
    """Build results rows, taking the starting grid from the qualifying session data"""
    grid_of = {row["driver_number"]: row["position"] for row in grid}
    rows = []
    for row in session_result:
        duration = row["duration"]
        rows.append(
            {
                "season": season,
                "round": round_no,
                "driver_code": code_of(codes, row["driver_number"]),
                "grid_pos": grid_of.get(row["driver_number"]),
                # Position is null for drivers who were not classified
                "finish_pos": row["position"],
                "status": result_status(row),
                "points": row["points"],
                "laps_completed": row["number_of_laps"],
                "race_time_s": duration if isinstance(duration, (int, float)) else None,
            }
        )
    return rows


def build_laps(season, round_no, laps, codes):
    """Build laps rows, keeping laps with no time and flagging them as no_time"""
    return [
        {
            "season": season,
            "round": round_no,
            "driver_code": code_of(codes, row["driver_number"]),
            "lap_number": row["lap_number"],
            "lap_time_s": row["lap_duration"],
            "s1_s": row["duration_sector_1"],
            "s2_s": row["duration_sector_2"],
            "s3_s": row["duration_sector_3"],
            "is_pit_out_lap": int(bool(row["is_pit_out_lap"])),
            # Filled by the derived-columns step
            "compound": None,
            "tyre_age": None,
            "stint": None,
            "position": None,
            "date_start": row["date_start"],
            "quality_flag": "no_time" if row["lap_duration"] is None else None,
        }
        for row in laps
    ]


def build_stints(season, round_no, stints, codes):
    """Build stints rows with the number of laps in each stint"""
    rows = []
    for row in stints:
        start, end = row["lap_start"], row["lap_end"]
        rows.append(
            {
                "season": season,
                "round": round_no,
                "driver_code": code_of(codes, row["driver_number"]),
                "stint_no": row["stint_number"],
                "compound": row["compound"],
                "start_lap": start,
                "end_lap": end,
                "n_laps": end - start + 1 if start is not None and end is not None else None,
                "tyre_age_at_start": row["tyre_age_at_start"],
                # Filled by the derived-columns step
                "avg_lap_time_s": None,
            }
        )
    return rows


def build_pit_stops(season, round_no, pit, codes):
    """Build pit_stops rows numbered per driver in time order, with pit-lane time only"""
    ordered = sorted(pit, key=lambda row: (row["date"] or "", row["lap_number"] or 0))
    stop_count = {}
    rows = []
    for row in ordered:
        code = code_of(codes, row["driver_number"])
        stop_count[code] = stop_count.get(code, 0) + 1
        rows.append(
            {
                "season": season,
                "round": round_no,
                "driver_code": code,
                "stop_no": stop_count[code],
                "lap": row["lap_number"],
                # lane_duration is pit entry to exit; stop_duration is never stored
                "pit_lane_time_s": row["lane_duration"],
            }
        )
    return rows


def build_weather(season, round_no, weather, race_start):
    """Build weather rows with the time in seconds from the race start"""
    start = datetime.fromisoformat(race_start)
    return [
        {
            "season": season,
            "round": round_no,
            "ts": row["date"],
            "session_time_s": (datetime.fromisoformat(row["date"]) - start).total_seconds(),
            "air_temp": row["air_temperature"],
            "track_temp": row["track_temperature"],
            "humidity": row["humidity"],
            "pressure": row["pressure"],
            "rainfall": None if row["rainfall"] is None else int(row["rainfall"]),
            "wind_speed": row["wind_speed"],
            "wind_direction": row["wind_direction"],
        }
        for row in weather
    ]


def build_race_control(season, round_no, events, codes):
    """Build race_control rows numbered in time order, with the driver code when given"""
    ordered = sorted(events, key=lambda row: (row["date"] or "", row["message"] or ""))
    rows = []
    for number, row in enumerate(ordered, start=1):
        driver = row["driver_number"]
        rows.append(
            {
                "season": season,
                "round": round_no,
                "event_no": number,
                "ts": row["date"],
                "lap_number": row["lap_number"],
                "category": row["category"],
                "flag": row["flag"],
                "scope": row["scope"],
                "sector": row["sector"],
                "driver_code": None if driver is None else code_of(codes, driver),
                "message": row["message"],
            }
        )
    return rows


def fetch_race(client, race_session):
    """Fetch every OpenF1 dataset for one race and return them with the qualifying key"""
    meeting_key = race_session["meeting_key"]
    meeting = client.get("meetings", meeting_key=meeting_key)[0]
    qualifying = client.get("sessions", meeting_key=meeting_key, session_name="Qualifying")
    qualifying_key = qualifying[0]["session_key"] if qualifying else None

    # starting_grid is stored under the Qualifying session, not the Race session
    grid = client.get("starting_grid", session_key=qualifying_key) if qualifying_key else []
    race_key = race_session["session_key"]
    data = {name: client.get(name, session_key=race_key) for name in RACE_ENDPOINTS}
    data.update({"meeting": meeting, "grid": grid, "qualifying_key": qualifying_key})
    return data


def replace_rows(conn, season, round_no, tables):
    """Replace one race's rows in every table inside a single transaction"""
    with conn:
        for table in RACE_TABLES:
            conn.execute(f"DELETE FROM {table} WHERE season = ? AND round = ?", (season, round_no))
        for table in RACE_TABLES:
            rows = tables[table]
            if not rows:
                continue
            columns = list(rows[0])
            names = ", ".join(columns)
            values = ", ".join(f":{column}" for column in columns)
            conn.executemany(f"INSERT INTO {table} ({names}) VALUES ({values})", rows)


def ingest_race(conn, client, season, race_session, round_no):
    """Fetch one race from OpenF1 and replace its rows in SQLite, returning the row counts"""
    data = fetch_race(client, race_session)
    codes = driver_codes(data["drivers"])
    tables = {
        "events": [
            build_event(season, round_no, race_session, data["meeting"], data["qualifying_key"])
        ],
        "entries": build_entries(season, round_no, data["drivers"]),
        "results": build_results(season, round_no, data["session_result"], codes, data["grid"]),
        "laps": build_laps(season, round_no, data["laps"], codes),
        "stints": build_stints(season, round_no, data["stints"], codes),
        "pit_stops": build_pit_stops(season, round_no, data["pit"], codes),
        "weather": build_weather(season, round_no, data["weather"], race_session["date_start"]),
        "race_control": build_race_control(season, round_no, data["race_control"], codes),
    }
    replace_rows(conn, season, round_no, tables)
    return {table: len(rows) for table, rows in tables.items()}
