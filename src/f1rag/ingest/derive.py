"""Derived columns: tyre data and position on each lap, and average pace per stint"""

from bisect import bisect_right
from datetime import datetime, timedelta


def add_stint_columns(laps, stints):
    """Copy compound, stint number and tyre age from each driver's stints onto their laps"""
    stints_of = {}
    for stint in sorted(stints, key=lambda s: s["stint_no"]):
        stints_of.setdefault(stint["driver_code"], []).append(stint)

    rows = []
    for lap in laps:
        row = dict(lap)
        for stint in stints_of.get(lap["driver_code"], []):
            start, end = stint["start_lap"], stint["end_lap"]
            if start is None or end is None or not start <= lap["lap_number"] <= end:
                continue
            row["compound"] = stint["compound"]
            row["stint"] = stint["stint_no"]
            age = stint["tyre_age_at_start"]
            # Tyre age counts the laps the tyre set has done including this lap
            row["tyre_age"] = None if age is None else age + (lap["lap_number"] - start) + 1
        rows.append(row)
    return rows


def build_positions(events, codes):
    """Group position-change events by driver code in time order, skipping unknown cars"""
    grouped = {}
    for event in events:
        code = codes.get(event["driver_number"])
        if code is None or event["position"] is None:
            continue
        when = datetime.fromisoformat(event["date"])
        grouped.setdefault(code, []).append((when, event["position"]))
    return {code: sorted(items) for code, items in grouped.items()}


def lap_end_times(driver_laps):
    """Return each lap's end time: the next lap's start, else its own start plus lap time"""
    ordered = sorted(driver_laps, key=lambda lap: lap["lap_number"])
    ends = {}
    for index, lap in enumerate(ordered):
        start = lap["date_start"]
        if start is None:
            ends[lap["lap_number"]] = None
            continue
        following = ordered[index + 1]["date_start"] if index + 1 < len(ordered) else None
        if following is not None:
            ends[lap["lap_number"]] = datetime.fromisoformat(following)
        elif lap["lap_time_s"] is not None:
            ends[lap["lap_number"]] = datetime.fromisoformat(start) + timedelta(
                seconds=lap["lap_time_s"]
            )
        else:
            ends[lap["lap_number"]] = None
    return ends


def add_positions(laps, positions):
    """Set each lap's position to the driver's last position change before the lap ended"""
    laps_of = {}
    for lap in laps:
        laps_of.setdefault(lap["driver_code"], []).append(lap)
    ends_of = {code: lap_end_times(rows) for code, rows in laps_of.items()}

    rows = []
    for lap in laps:
        row = dict(lap)
        end = ends_of[lap["driver_code"]][lap["lap_number"]]
        changes = positions.get(lap["driver_code"], [])
        if end is not None and changes:
            index = bisect_right([when for when, _ in changes], end) - 1
            row["position"] = changes[index][1] if index >= 0 else None
        rows.append(row)
    return rows


def add_stint_averages(stints, laps, pit_stops):
    """Set each stint's average lap time over timed laps that are not pit in-laps or out-laps"""
    in_laps = {(stop["driver_code"], stop["lap"]) for stop in pit_stops}
    times = {}
    for lap in laps:
        key = (lap["driver_code"], lap["lap_number"])
        if lap["lap_time_s"] is None or lap["is_pit_out_lap"] or key in in_laps:
            continue
        times[key] = lap["lap_time_s"]

    rows = []
    for stint in stints:
        row = dict(stint)
        start, end = stint["start_lap"], stint["end_lap"]
        if start is not None and end is not None:
            clean = [times[(stint["driver_code"], n)] for n in range(start, end + 1)
                     if (stint["driver_code"], n) in times]
            row["avg_lap_time_s"] = round(sum(clean) / len(clean), 3) if clean else None
        rows.append(row)
    return rows
