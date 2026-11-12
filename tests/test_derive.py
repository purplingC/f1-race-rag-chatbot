from f1rag.ingest import derive

# Everything below is placeholder data for tests, not real F1 data


def lap_row(code, number, seconds, start, pit_out=False):
    """Build a placeholder laps table row"""
    return {
        "driver_code": code,
        "lap_number": number,
        "lap_time_s": seconds,
        "date_start": start,
        "is_pit_out_lap": int(pit_out),
        "compound": None,
        "tyre_age": None,
        "stint": None,
        "position": None,
    }


def stint_row(code, number, compound, start, end, age_at_start=0):
    """Build a placeholder stints table row"""
    return {
        "driver_code": code,
        "stint_no": number,
        "compound": compound,
        "start_lap": start,
        "end_lap": end,
        "tyre_age_at_start": age_at_start,
        "avg_lap_time_s": None,
    }


def by_lap(rows, code):
    """Index one driver's rows by lap number"""
    return {r["lap_number"]: r for r in rows if r["driver_code"] == code}


def test_stint_columns_come_from_the_matching_stint():
    """Check compound, stint number and tyre age are copied onto each lap"""
    laps = [lap_row("AAA", n, 100.0, None) for n in (1, 2, 3)]
    stints = [
        stint_row("AAA", 1, "MEDIUM", 1, 2),
        stint_row("AAA", 2, "HARD", 3, 3, age_at_start=5),
    ]
    rows = by_lap(derive.add_stint_columns(laps, stints), "AAA")
    assert (rows[1]["compound"], rows[1]["stint"], rows[1]["tyre_age"]) == ("MEDIUM", 1, 1)
    assert (rows[2]["compound"], rows[2]["stint"], rows[2]["tyre_age"]) == ("MEDIUM", 1, 2)
    # A used set that already had 5 laps is on its 6th lap
    assert (rows[3]["compound"], rows[3]["stint"], rows[3]["tyre_age"]) == ("HARD", 2, 6)


def test_lap_outside_every_stint_gets_no_stint_values():
    """Check a lap that no stint covers keeps empty compound, stint and tyre age"""
    laps = [lap_row("AAA", 4, None, None)]
    stints = [stint_row("AAA", 1, "MEDIUM", 1, 3)]
    row = derive.add_stint_columns(laps, stints)[0]
    assert (row["compound"], row["stint"], row["tyre_age"]) == (None, None, None)


def test_other_drivers_stints_do_not_leak():
    """Check each driver's laps only use that driver's own stints"""
    laps = [lap_row("AAA", 1, 100.0, None), lap_row("BBB", 1, 101.0, None)]
    stints = [stint_row("AAA", 1, "SOFT", 1, 5), stint_row("BBB", 1, "HARD", 1, 5)]
    rows = derive.add_stint_columns(laps, stints)
    assert [r["compound"] for r in rows] == ["SOFT", "HARD"]


def test_unknown_tyre_age_stays_empty():
    """Check a stint with no starting tyre age gives no tyre age on its laps"""
    laps = [lap_row("AAA", 1, 100.0, None)]
    stints = [stint_row("AAA", 1, "SOFT", 1, 3, age_at_start=None)]
    row = derive.add_stint_columns(laps, stints)[0]
    assert row["compound"] == "SOFT"
    assert row["tyre_age"] is None


def test_input_rows_are_not_changed_in_place():
    """Check the original lap rows keep their empty values"""
    laps = [lap_row("AAA", 1, 100.0, None)]
    derive.add_stint_columns(laps, [stint_row("AAA", 1, "SOFT", 1, 3)])
    assert laps[0]["compound"] is None


def event(number, when, position):
    """Build a placeholder OpenF1 position-change event"""
    return {"driver_number": number, "date": when, "position": position}


CODES = {11: "AAA", 22: "BBB"}


def test_build_positions_groups_by_code_and_sorts_by_time():
    """Check position events are grouped per driver in time order and unknown cars skipped"""
    events = [
        event(11, "2026-01-01T10:05:00+00:00", 2),
        event(11, "2026-01-01T10:01:00+00:00", 1),
        event(99, "2026-01-01T10:02:00+00:00", 3),
        event(22, "2026-01-01T10:03:00+00:00", 2),
    ]
    grouped = derive.build_positions(events, CODES)
    assert set(grouped) == {"AAA", "BBB"}
    assert [position for _, position in grouped["AAA"]] == [1, 2]


def test_position_is_the_last_change_before_the_lap_ends():
    """Check each lap gets the position held when the lap ended"""
    laps = [
        lap_row("AAA", 1, 60.0, "2026-01-01T10:01:00+00:00"),
        lap_row("AAA", 2, 60.0, "2026-01-01T10:02:00+00:00"),
        lap_row("AAA", 3, 60.0, "2026-01-01T10:03:00+00:00"),
    ]
    events = [
        event(11, "2026-01-01T10:00:30+00:00", 3),
        event(11, "2026-01-01T10:02:30+00:00", 1),
    ]
    positions = derive.build_positions(events, CODES)
    rows = by_lap(derive.add_positions(laps, positions), "AAA")
    # Lap 1 ends at 10:02:00, lap 2 at 10:03:00, lap 3 at 10:03:00 plus 60 s
    assert [rows[n]["position"] for n in (1, 2, 3)] == [3, 1, 1]


def test_last_lap_ends_at_start_plus_lap_time():
    """Check the final lap uses its start time plus its duration as the end"""
    laps = [lap_row("AAA", 1, 90.0, "2026-01-01T10:00:00+00:00")]
    events = [
        event(11, "2026-01-01T09:59:00+00:00", 4),
        event(11, "2026-01-01T10:01:00+00:00", 2),
        event(11, "2026-01-01T10:02:00+00:00", 1),
    ]
    row = derive.add_positions(laps, derive.build_positions(events, CODES))[0]
    # The lap ends at 10:01:30, so the 10:01:00 change counts and 10:02:00 does not
    assert row["position"] == 2


def test_lap_with_no_end_time_has_no_position():
    """Check a last lap with no lap time gets no position instead of a guess"""
    laps = [lap_row("AAA", 1, None, "2026-01-01T10:00:00+00:00")]
    events = [event(11, "2026-01-01T09:59:00+00:00", 4)]
    row = derive.add_positions(laps, derive.build_positions(events, CODES))[0]
    assert row["position"] is None


def test_lap_with_no_start_time_has_no_position():
    """Check a lap with no start time gets no position"""
    laps = [lap_row("AAA", 1, 90.0, None)]
    events = [event(11, "2026-01-01T09:59:00+00:00", 4)]
    row = derive.add_positions(laps, derive.build_positions(events, CODES))[0]
    assert row["position"] is None


def test_no_position_before_the_first_event():
    """Check a lap that ended before any position event gets no position"""
    laps = [lap_row("AAA", 1, 60.0, "2026-01-01T10:00:00+00:00")]
    events = [event(11, "2026-01-01T11:00:00+00:00", 4)]
    row = derive.add_positions(laps, derive.build_positions(events, CODES))[0]
    assert row["position"] is None


def test_driver_with_no_events_has_no_positions():
    """Check a driver missing from the position data gets empty positions"""
    laps = [lap_row("BBB", 1, 60.0, "2026-01-01T10:00:00+00:00")]
    row = derive.add_positions(laps, {})[0]
    assert row["position"] is None


def test_stint_average_skips_in_laps_out_laps_and_missing_times():
    """Check the average only uses timed laps that are not pit in-laps or out-laps"""
    laps = [
        lap_row("AAA", 1, 130.0, None, pit_out=True),
        lap_row("AAA", 2, 100.0, None),
        lap_row("AAA", 3, None, None),
        lap_row("AAA", 4, 102.0, None),
        lap_row("AAA", 5, 140.0, None),
    ]
    stints = [stint_row("AAA", 1, "SOFT", 1, 5)]
    pit_stops = [{"driver_code": "AAA", "lap": 5}]
    row = derive.add_stint_averages(stints, laps, pit_stops)[0]
    assert row["avg_lap_time_s"] == 101.0


def test_stint_with_no_clean_laps_has_no_average():
    """Check a stint with no usable laps has an empty average"""
    laps = [lap_row("AAA", 1, 130.0, None, pit_out=True)]
    stints = [stint_row("AAA", 1, "SOFT", 1, 1)]
    assert derive.add_stint_averages(stints, laps, [])[0]["avg_lap_time_s"] is None


def test_stint_average_only_uses_that_drivers_laps():
    """Check another driver's laps do not affect a stint average"""
    laps = [lap_row("AAA", 1, 100.0, None), lap_row("BBB", 1, 200.0, None)]
    stints = [stint_row("AAA", 1, "SOFT", 1, 1)]
    assert derive.add_stint_averages(stints, laps, [])[0]["avg_lap_time_s"] == 100.0
