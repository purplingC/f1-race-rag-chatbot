"""Phase 0 spike: load the 2026 Bahrain Grand Prix in Malaysia from OpenF1 and print what we get"""

import json
import time
from collections import Counter
from pathlib import Path

import pandas as pd
import requests

BASE_URL = "https://api.openf1.org/v1"
# The Bahrain Grand Prix was held in Kuala Lumpur in 2026 (OpenF1 country_name is "Bahrain")
YEAR = 2026
LOCATION = "Kuala Lumpur"
DRIVER = "VER"
CACHE_DIR = Path(__file__).resolve().parents[2] / "data" / "cache" / "openf1"
HEADERS = {"User-Agent": "f1-race-rag-spike"}
# Free limit is 3 requests/s and 30/min, so wait 2.5 s before every live call
PAUSE_S = 2.5
ENDPOINTS = ["drivers", "session_result", "laps", "stints", "pit", "weather", "race_control"]


def get_json(endpoint, **params):
    """Fetch an OpenF1 endpoint as a list of rows, using the local cache when possible"""
    key = "_".join(f"{k}-{v}" for k, v in sorted(params.items()))
    path = CACHE_DIR / f"{endpoint}_{key}.json"
    if path.exists():
        return json.loads(path.read_text())

    for _ in range(3):
        time.sleep(PAUSE_S)
        url = f"{BASE_URL}/{endpoint}"
        response = requests.get(url, params=params, headers=HEADERS, timeout=30)
        if response.status_code == 429:
            time.sleep(15)
            continue
        if response.status_code == 404:
            rows = []
            break
        response.raise_for_status()
        rows = response.json()
        break
    else:
        raise SystemExit(f"Rate limited on {endpoint} three times, try again in a minute")

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(rows))
    return rows


def find_session(session_name):
    """Return the non-cancelled session of the given type held in Kuala Lumpur in 2026"""
    rows = get_json("sessions", year=YEAR, session_name=session_name, location=LOCATION)
    rows = [r for r in rows if not r["is_cancelled"]]
    if not rows:
        raise SystemExit(f"No {session_name} session found for {LOCATION} {YEAR}")
    return rows[0]


def folder_size_mb(folder):
    """Return the total size of all files under a folder in megabytes"""
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1_000_000


def best_time(duration):
    """Return the last non-empty time from a qualifying result, which may be a list"""
    if isinstance(duration, list):
        duration = [d for d in duration if d is not None]
        return duration[-1] if duration else None
    return duration


def main():
    """Load the race and qualifying sessions and print a summary, checks and the focus driver"""
    race = find_session("Race")
    qualifying = find_session("Qualifying")
    key = race["session_key"]
    print(f"Race: {race['country_name']} ({race['location']}) {race['year']}, session_key {key}")
    print(f"Start: {race['date_start']}")

    data = {name: get_json(name, session_key=key) for name in ENDPOINTS}
    team_of = {d["driver_number"]: (d["name_acronym"], d["team_name"]) for d in data["drivers"]}
    number_of = {code: number for number, (code, _) in team_of.items()}

    print(f"\nQualifying top 3 (session_key {qualifying['session_key']}):")
    quali = get_json("session_result", session_key=qualifying["session_key"])
    for r in sorted((r for r in quali if r["position"]), key=lambda r: r["position"])[:3]:
        code, team = team_of.get(r["driver_number"], ("?", "?"))
        print(f"  {r['position']}  {code}  {team:<18} best time {best_time(r['duration'])} s")

    print("\nRace results (pos, driver, team, points, laps, status):")
    for r in sorted(data["session_result"], key=lambda r: r["position"] or 99):
        code, team = team_of.get(r["driver_number"], ("?", "?"))
        # Position is null for drivers who were not classified
        line = f"{str(r['position']):>4}  {code}  {team:<18} {str(r['points']):>5}"
        print(f"{line}  {str(r['number_of_laps']):>3}  dnf={r['dnf']} dns={r['dns']}")

    print("\nRows per endpoint:")
    for name in ENDPOINTS:
        print(f"  {name}: {len(data[name])}")

    laps = data["laps"]
    laps_df = pd.DataFrame(laps)
    print(f"\nLaps DataFrame: {laps_df.shape[0]} rows x {laps_df.shape[1]} columns")
    print(f"Lap columns: {', '.join(laps[0].keys())}")
    print(f"Laps with no lap_duration: {sum(1 for lap in laps if lap['lap_duration'] is None)}")
    print(f"Tyre compounds used (stints): {dict(Counter(s['compound'] for s in data['stints']))}")
    print(f"Pit stops with lane_duration: {sum(1 for p in data['pit'] if p['lane_duration'])}")
    print(f"Pit stops with stop_duration: {sum(1 for p in data['pit'] if p['stop_duration'])}")

    number = number_of.get(DRIVER)
    if number is None:
        raise SystemExit(f"Driver {DRIVER} is not in the drivers data")
    print(f"\nFocus driver: {DRIVER} (car number {number}, {team_of[number][1]})")
    mine = laps_df[(laps_df["driver_number"] == number) & laps_df["lap_duration"].notna()]
    fastest = mine.loc[mine["lap_duration"].idxmin()]
    print(f"  Laps with a time: {len(mine)}")
    print(f"  Fastest lap: lap {int(fastest['lap_number'])}, {fastest['lap_duration']} s")
    for s in data["stints"]:
        if s["driver_number"] == number:
            laps_range = f"{s['lap_start']}-{s['lap_end']}"
            print(f"  Stint {s['stint_number']}: {s['compound']}, laps {laps_range}")
    for p in data["pit"]:
        if p["driver_number"] == number:
            print(f"  Pit stop on lap {p['lap_number']}: lane time {p['lane_duration']} s")

    print(f"\nCache size: {folder_size_mb(CACHE_DIR):.2f} MB in {CACHE_DIR}")


if __name__ == "__main__":
    main()
