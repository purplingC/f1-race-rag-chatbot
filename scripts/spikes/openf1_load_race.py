"""Phase 0 spike: load one 2025 race from OpenF1 and print what we get"""

import json
import time
from collections import Counter
from pathlib import Path

import requests

BASE_URL = "https://api.openf1.org/v1"
YEAR = 2025
COUNTRY = "Australia"
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


def folder_size_mb(folder):
    """Return the total size of all files under a folder in megabytes"""
    return sum(f.stat().st_size for f in folder.rglob("*") if f.is_file()) / 1_000_000


def main():
    """Load one race session from OpenF1 and print a short summary and data checks"""
    sessions = get_json("sessions", year=YEAR, session_name="Race", country_name=COUNTRY)
    if not sessions:
        raise SystemExit(f"No Race session found for {COUNTRY} {YEAR}")
    session = sessions[0]
    key = session["session_key"]
    print(f"Session: {session['country_name']} {session['year']} Race, session_key {key}")
    print(f"Start: {session['date_start']}")

    data = {name: get_json(name, session_key=key) for name in ENDPOINTS}

    team_of = {d["driver_number"]: (d["name_acronym"], d["team_name"]) for d in data["drivers"]}
    print("\nResults (pos, driver, team, points, laps, dnf):")
    for r in sorted(data["session_result"], key=lambda r: r["position"] or 99):
        code, team = team_of.get(r["driver_number"], ("?", "?"))
        # Position is null for drivers who were not classified
        line = f"{str(r['position']):>4}  {code}  {team:<18} {str(r['points']):>5}"
        print(f"{line}  {str(r['number_of_laps']):>3}  dnf={r['dnf']} dns={r['dns']}")

    print("\nRows per endpoint:")
    for name in ENDPOINTS:
        print(f"  {name}: {len(data[name])}")

    laps = data["laps"]
    print(f"\nLap columns: {', '.join(laps[0].keys())}")
    print(f"Laps with no lap_duration: {sum(1 for lap in laps if lap['lap_duration'] is None)}")
    print(f"Tyre compounds used (stints): {dict(Counter(s['compound'] for s in data['stints']))}")
    print(f"Pit stops with lane_duration: {sum(1 for p in data['pit'] if p['lane_duration'])}")
    print(f"Pit stops with stop_duration: {sum(1 for p in data['pit'] if p['stop_duration'])}")
    print(f"\nCache size: {folder_size_mb(CACHE_DIR):.2f} MB in {CACHE_DIR}")


if __name__ == "__main__":
    main()
