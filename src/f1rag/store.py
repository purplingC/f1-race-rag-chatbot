"""SQLite storage: connection helper and the relational schema"""

import sqlite3

# Every table carries season and round, all times are seconds (REAL)
# Columns are filled from OpenF1, see the field mapping in planning doc Section 5.3
SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    event_name TEXT,
    official_name TEXT,
    circuit TEXT,
    country TEXT,
    race_date TEXT,
    meeting_key INTEGER,
    race_session_key INTEGER,
    qualifying_session_key INTEGER,
    PRIMARY KEY (season, round)
);

CREATE TABLE IF NOT EXISTS entries (
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    driver_code TEXT NOT NULL,
    full_name TEXT,
    team TEXT,
    car_number INTEGER,
    team_color TEXT,
    country_code TEXT,
    headshot_url TEXT,
    PRIMARY KEY (season, round, driver_code)
);

CREATE TABLE IF NOT EXISTS results (
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    driver_code TEXT NOT NULL,
    grid_pos INTEGER,
    finish_pos INTEGER,
    status TEXT,
    points REAL,
    laps_completed INTEGER,
    race_time_s REAL,
    PRIMARY KEY (season, round, driver_code)
);

CREATE TABLE IF NOT EXISTS laps (
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    driver_code TEXT NOT NULL,
    lap_number INTEGER NOT NULL,
    lap_time_s REAL,
    s1_s REAL,
    s2_s REAL,
    s3_s REAL,
    is_pit_out_lap INTEGER,
    compound TEXT,
    tyre_age INTEGER,
    stint INTEGER,
    position INTEGER,
    date_start TEXT,
    quality_flag TEXT,
    PRIMARY KEY (season, round, driver_code, lap_number)
);

CREATE TABLE IF NOT EXISTS stints (
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    driver_code TEXT NOT NULL,
    stint_no INTEGER NOT NULL,
    compound TEXT,
    start_lap INTEGER,
    end_lap INTEGER,
    n_laps INTEGER,
    tyre_age_at_start INTEGER,
    avg_lap_time_s REAL,
    PRIMARY KEY (season, round, driver_code, stint_no)
);

-- Pit-lane time only (entry to exit), stationary time is never stored
CREATE TABLE IF NOT EXISTS pit_stops (
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    driver_code TEXT NOT NULL,
    stop_no INTEGER NOT NULL,
    lap INTEGER,
    pit_lane_time_s REAL,
    PRIMARY KEY (season, round, driver_code, stop_no)
);

CREATE TABLE IF NOT EXISTS weather (
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    ts TEXT NOT NULL,
    session_time_s REAL,
    air_temp REAL,
    track_temp REAL,
    humidity REAL,
    pressure REAL,
    rainfall INTEGER,
    wind_speed REAL,
    wind_direction INTEGER,
    PRIMARY KEY (season, round, ts)
);

-- Flags, safety car and other race-control messages, ordered by time
CREATE TABLE IF NOT EXISTS race_control (
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    event_no INTEGER NOT NULL,
    ts TEXT,
    lap_number INTEGER,
    category TEXT,
    flag TEXT,
    scope TEXT,
    sector INTEGER,
    driver_code TEXT,
    message TEXT,
    PRIMARY KEY (season, round, event_no)
);

CREATE TABLE IF NOT EXISTS race_summaries (
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    summary_md TEXT,
    generated_at TEXT,
    model TEXT,
    PRIMARY KEY (season, round)
);

CREATE TABLE IF NOT EXISTS ingest_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    season INTEGER NOT NULL,
    round INTEGER NOT NULL,
    source TEXT,
    status TEXT,
    rows INTEGER,
    message TEXT,
    ts TEXT
);
"""


def connect(path):
    """Open a SQLite connection that returns rows addressable by column name"""
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def create_schema(connection):
    """Create every table if it does not exist yet, keeping any existing rows"""
    connection.executescript(SCHEMA)
