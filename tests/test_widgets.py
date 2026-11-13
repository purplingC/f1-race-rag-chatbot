from PIL import Image

from f1rag.ui import avatars, theme, widgets

# Everything below is placeholder data for tests, not real F1 data
DRIVERS = {
    "AAA": {"name": "Driver Alpha", "team": "Team A", "team_color": "#112233"},
    "BBB": {"name": "Driver Beta", "team": "Team A", "team_color": "#112233"},
    "CCC": {"name": "Driver <Gamma>", "team": "Team & C", "team_color": "#445566"},
}


def test_avatar_uses_initials_unless_photos_are_enabled_and_present(tmp_path):
    """Check initials by default, and the local photo only when enabled and the file exists"""
    plain = avatars.avatar_html("AAA", "Driver Alpha", "#112233", folder=tmp_path, show=False)
    assert "DA" in plain and "<img" not in plain and "#112233" in plain
    missing = avatars.avatar_html("AAA", "Driver Alpha", "#112233", folder=tmp_path, show=True)
    assert "<img" not in missing
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(tmp_path / "AAA.png")
    photo = avatars.avatar_html("AAA", "Driver Alpha", "#112233", folder=tmp_path, show=True)
    assert 'src="data:image/png;base64,' in photo and 'alt="Driver Alpha"' in photo


def test_avatar_escapes_names_and_refuses_bad_codes(tmp_path):
    """Check names are HTML-escaped and a code cannot point outside the photo folder"""
    html = avatars.avatar_html("../x", "<b>Bad</b>", "#112233", folder=tmp_path, show=True)
    assert "<b>" not in html and "&lt;b&gt;" in html
    assert "<img" not in html


def test_show_headshots_setting_is_read_from_the_environment(monkeypatch):
    """Check SHOW_HEADSHOTS=true turns photos on and anything else leaves them off"""
    monkeypatch.setenv("SHOW_HEADSHOTS", "true")
    assert avatars.headshots_enabled() is True
    monkeypatch.setenv("SHOW_HEADSHOTS", "false")
    assert avatars.headshots_enabled() is False
    monkeypatch.delenv("SHOW_HEADSHOTS")
    assert avatars.headshots_enabled() is False


def test_driver_cards_show_identity_and_result_with_text_escaped(tmp_path):
    """Check a card carries avatar, name, team, grid to finish and best lap, all escaped"""
    card = {"code": "CCC", "name": "Driver <Gamma>", "team": "Team & C", "team_color": "#445566",
            "number": 3, "grid": 5, "finish": 2, "gained": 3, "status": "Finished",
            "points": 18, "best_lap_s": 89.5}
    html = widgets.driver_cards_html([card], folder=tmp_path, show=False)
    assert "Driver &lt;Gamma&gt;" in html and "Team &amp; C" in html
    assert "<Gamma>" not in html
    assert "P5" in html and "P2" in html and "+3" in html and "1:29.500" in html
    assert "18 pts" in html


def test_lap_time_chart_gives_teammates_different_line_styles_and_marks_pit_laps():
    """Check one line per driver in team colour, teammates told apart, pit laps marked"""
    data = {
        "laps": [{"code": c, "lap": n, "lap_time_s": 90 + n} for c in ("AAA", "BBB")
                 for n in (1, 2, 3)],
        "pit_laps": {"AAA": [2]},
    }
    fig = widgets.lap_time_figure(data, DRIVERS)
    lines = [t for t in fig.data if t.mode and "lines" in t.mode]
    assert [t.name for t in lines] == ["AAA", "BBB"]
    assert lines[0].line.color == lines[1].line.color == "#112233"
    assert lines[0].line.dash != lines[1].line.dash
    pits = [t for t in fig.data if t.name == "AAA pit"]
    assert list(pits[0].x) == [2]


def test_lap_times_are_shown_as_minutes_and_seconds():
    """Check lap times format as m:ss.sss"""
    assert widgets.lap_time_text(89.5) == "1:29.500"
    assert widgets.lap_time_text(None) == "–"


def test_tyre_strategy_bars_use_compound_colours_and_letters():
    """Check each stint is a bar of its compound colour, labelled with its letter"""
    rows = [
        {"code": "AAA", "stint": 1, "compound": "SOFT", "start_lap": 1, "end_lap": 10,
         "n_laps": 10, "complete": True},
        {"code": "AAA", "stint": 2, "compound": "HARD", "start_lap": 11, "end_lap": 20,
         "n_laps": 10, "complete": True},
        {"code": "BBB", "stint": 1, "compound": "UNKNOWN", "start_lap": 1, "end_lap": 20,
         "n_laps": 20, "complete": False},
    ]
    fig = widgets.tyre_strategy_figure(rows)
    colours = [bar.marker.color for bar in fig.data]
    assert colours == [theme.TYRE_COLORS["SOFT"], theme.TYRE_COLORS["HARD"],
                       theme.TYRE_COLORS["UNKNOWN"]]
    assert [bar.text[0] for bar in fig.data] == ["S", "H", "?"]
    assert all(bar.textangle == 0 for bar in fig.data)
    assert "BBB*" in list(fig.layout.yaxis.categoryarray)


def test_position_chart_puts_first_place_at_the_top():
    """Check positions are drawn with P1 at the top"""
    rows = [{"code": "AAA", "lap": n, "position": 1} for n in (1, 2)]
    fig = widgets.position_figure(rows + [{"code": "BBB", "lap": 1, "position": 4}], DRIVERS)
    low, high = fig.layout.yaxis.range
    # Drawn from the last position down to P1 at the top, with no P0 on the axis
    assert (low, high) == (4.5, 0.5)


def test_tables_and_summary_escape_database_text(tmp_path):
    """Check classification, pit stops and summary escape text from the database"""
    rows = [{"position": 1, "code": "CCC", "name": "Driver <Gamma>", "team": "Team & C",
             "team_color": "#445566", "grid": 2, "status": "Finished", "points": 25, "laps": 50}]
    table = widgets.classification_html(rows, folder=tmp_path, show=False)
    assert "Driver &lt;Gamma&gt;" in table and "<Gamma>" not in table
    pits = widgets.pit_stops_html({"stops": [{"code": "CCC", "stop": 1, "lap": 9,
                                              "pit_lane_time_s": 22.4, "vs_median_s": -0.6}],
                                   "median_s": 23.0}, DRIVERS)
    assert "22.4" in pits and "-0.6" in pits and "pit-lane" in pits.lower()
    summary = widgets.race_summary_html({
        "summary_md": "Line <script>alert(1)</script>\n\nSecond paragraph",
        "winner": "Driver <Gamma>", "fastest_lap": {"code": "CCC", "lap": 3, "time_s": 89.5},
        "safety_cars": 1, "virtual_safety_cars": 0, "red_flags": 0, "start_suspended": True,
    })
    assert "<script>" not in summary and "&lt;script&gt;" in summary
    assert summary.count("<p>") == 2 and "suspended" in summary.lower()


def test_empty_data_draws_a_message_instead_of_failing(tmp_path):
    """Check every widget copes with a race that has no data"""
    assert "No" in widgets.classification_html([], folder=tmp_path, show=False)
    assert widgets.lap_time_figure({"laps": [], "pit_laps": {}}, {}).layout.annotations
    assert widgets.tyre_strategy_figure([]).layout.annotations
    assert widgets.position_figure([], {}).layout.annotations
    assert "No" in widgets.pit_stops_html({"stops": [], "median_s": None}, {})
    assert "No stored summary" in widgets.race_summary_html({"summary_md": None})


def test_points_show_without_needless_decimals():
    """Check whole points show as 25 and half points as 0.5"""
    assert widgets.points_text(25.0) == "25"
    assert widgets.points_text(0.5) == "0.5"
    assert widgets.points_text(None) == "0"


def podium_cards():
    """Build three placeholder cards for the top three finishers"""
    return [
        {"code": code, "name": name, "team": "Team A", "team_color": "#112233", "number": n,
         "grid": n, "finish": n, "gained": 0, "status": "Finished", "points": 10,
         "best_lap_s": 90.0}
        for n, (code, name) in enumerate(
            [("AAA", "Driver <Alpha>"), ("BBB", "Driver Beta"), ("CCC", "Driver Gamma")], 1)
    ]


def test_podium_puts_the_winner_in_the_middle_and_escapes_names(tmp_path):
    """Check the podium is drawn second, first, third, with database text escaped"""
    html = widgets.podium_html(podium_cards(), folder=tmp_path, show=False)
    order = [html.index(name) for name in ("Driver Beta", "Driver &lt;Alpha&gt;", "Driver Gamma")]
    assert order == sorted(order)
    assert "<Alpha>" not in html
    assert html.count("f1-step") == 3


def test_podium_is_only_used_for_the_top_three_finishers():
    """Check any other set of drivers keeps the plain driver cards"""
    cards = podium_cards()
    assert widgets.is_podium(cards) is True
    assert widgets.is_podium(cards[:2]) is False
    cards[2]["finish"] = 7
    assert widgets.is_podium(cards) is False


def test_race_banner_shows_the_race_and_key_facts_escaped(tmp_path):
    """Check the banner title and fact tiles come from the data and are escaped"""
    header = {"season": 2026, "round": 3, "event_name": "Place <holder> GP",
              "circuit": "Placeville", "country": "Placeland", "race_date": "2026-03-01",
              "laps": 50, "drivers": 20}
    facts = {"winner": "Driver <Alpha>", "fastest_lap": {"code": "AAA", "lap": 3, "time_s": 89.5},
             "safety_cars": 2, "virtual_safety_cars": 1, "red_flags": 0, "start_suspended": True}
    title = widgets.banner_title_html(header)
    assert "R3" in title and "Place &lt;holder&gt; GP" in title and "<holder>" not in title
    tiles = widgets.fact_tiles_html(header, facts)
    assert "Driver &lt;Alpha&gt;" in tiles and "1:29.500" in tiles
    assert "50" in tiles and "2 SC" in tiles and "1 VSC" in tiles
    assert "suspended" in tiles.lower()


def test_compact_classification_drops_the_team_column(tmp_path):
    """Check the half-width classification shows the team under the name instead of a column"""
    rows = [{"position": 1, "code": "AAA", "name": "Driver Alpha", "team": "Team A",
             "team_color": "#112233", "grid": 2, "status": "Finished", "points": 25, "laps": 50}]
    wide = widgets.classification_html(rows, folder=tmp_path, show=False)
    compact = widgets.classification_html(rows, folder=tmp_path, show=False, compact=True)
    assert ">Team<" in wide and ">Team<" not in compact
    assert "Team A" in compact


def test_banner_carries_the_app_name_and_summary_can_skip_repeated_facts():
    """Check the banner shows the app name, and the summary card can leave out banner facts"""
    header = {"season": 2026, "round": 3, "event_name": "Placeholder GP", "circuit": "Placeville",
              "country": None, "race_date": "2026-03-01", "laps": 50, "drivers": 20}
    assert "F1 Race Assistant" in widgets.banner_title_html(header, app_name="F1 Race Assistant")
    card = {"summary_md": "Text", "winner": "Driver Alpha", "fastest_lap": None,
            "safety_cars": 0, "virtual_safety_cars": 0, "red_flags": 0}
    assert "Winner" in widgets.race_summary_html(card)
    assert "Winner" not in widgets.race_summary_html(card, facts=False)


def test_classification_shows_car_numbers_and_times(tmp_path):
    """Check each row shows the driver's number and the result time, escaped"""
    rows = [{"position": 1, "code": "AAA", "name": "Driver Alpha", "team": "Team <A>",
             "team_color": "#112233", "grid": 2, "status": "Finished", "points": 25, "laps": 50,
             "number": 4, "time": "1:35:23.504"},
            {"position": 2, "code": "BBB", "name": "Driver Beta", "team": "Team B",
             "team_color": "#112233", "grid": 1, "status": "Finished", "points": 18, "laps": 50,
             "number": 81, "time": "+14.602s"}]
    for compact in (False, True):
        html = widgets.classification_html(rows, folder=tmp_path, show=False, compact=compact)
        assert "#4" in html and "#81" in html
        assert "1:35:23.504" in html and "+14.602s" in html and ">Time<" in html
        assert "Team &lt;A&gt;" in html


def test_banner_title_is_large_and_spelled_out():
    """Check the app name is the large first line of the banner"""
    header = {"season": 2026, "round": 3, "event_name": "Placeholder GP", "circuit": "Placeville",
              "country": None, "race_date": "2026-03-01", "laps": 50, "drivers": 20}
    html = widgets.banner_title_html(header, app_name="Formula 1 Race Assistant")
    assert "Formula 1 Race Assistant" in html
    assert html.index("Formula 1 Race Assistant") < html.index("Placeholder GP")
    assert "font:800 30px" in html


def save_logo(folder, season, team):
    """Save a tiny placeholder logo where the app looks for it"""
    from f1rag.ingest import team_logos

    path = team_logos.logo_path(folder, season, team)
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", (12, 6), (255, 255, 255, 255)).save(path)


def test_team_logo_shows_only_when_enabled_and_saved(tmp_path):
    """Check a logo appears only with the setting on and the file present, with alt text"""
    assert avatars.team_logo_html("Team A", 2026, folder=tmp_path, show=True) == ""
    save_logo(tmp_path, 2026, "Team A")
    assert avatars.team_logo_html("Team A", 2026, folder=tmp_path, show=False) == ""
    odd = avatars.team_logo_html("Team <A>", 2026, folder=tmp_path, show=True)
    assert 'alt="Team &lt;A&gt; logo"' in odd
    html = avatars.team_logo_html("Team A", 2026, folder=tmp_path, show=True)
    assert 'src="data:image/png;base64,' in html and 'alt="Team A logo"' in html
    assert avatars.team_logo_html("Team A", "../x", folder=tmp_path, show=True) == ""


def test_show_team_logos_setting_is_read_from_the_environment(monkeypatch):
    """Check SHOW_TEAM_LOGOS=true turns logos on and anything else leaves them off"""
    monkeypatch.setenv("SHOW_TEAM_LOGOS", "true")
    assert avatars.team_logos_enabled() is True
    monkeypatch.setenv("SHOW_TEAM_LOGOS", "no")
    assert avatars.team_logos_enabled() is False


def test_tables_cards_and_podium_put_the_logo_before_the_team(tmp_path):
    """Check every place that names a team shows its logo first when logos are on"""
    save_logo(tmp_path, 2026, "Team A")
    rows = [{"position": 1, "code": "AAA", "name": "Driver Alpha", "team": "Team A",
             "team_color": "#112233", "grid": 2, "status": "Finished", "points": 25, "laps": 50,
             "number": 4, "time": "1:35:23.504", "season": 2026}]
    for compact in (False, True):
        html = widgets.classification_html(rows, folder=tmp_path, show=False,
                                           compact=compact, logos=True, logo_folder=tmp_path)
        assert html.index('alt="Team A logo"') < html.rindex("Team A")
    cards = [{**c, "season": 2026, "team": "Team A"} for c in podium_cards()]
    assert 'alt="Team A logo"' in widgets.podium_html(cards, folder=tmp_path, show=False,
                                                       logos=True, logo_folder=tmp_path)
    assert 'alt="Team A logo"' in widgets.driver_cards_html(
        cards[:1], folder=tmp_path, show=False, logos=True, logo_folder=tmp_path)
    plain = widgets.classification_html(rows, folder=tmp_path, show=False, logos=False,
                                        logo_folder=tmp_path)
    assert "logo" not in plain


PERIODS = [{"kind": "SC", "start": 2, "end": 3}, {"kind": "VSC", "start": 1, "end": 1},
           {"kind": "RED", "start": 3, "end": 3}, {"kind": "SC", "start": 40, "end": 42}]


def strip(fig):
    """Return the track strip blocks of a figure, drawn on the second y axis"""
    return [t for t in fig.data if t.yaxis == "y2"]


def test_charts_show_safety_cars_and_red_flags_in_a_track_strip_above_the_laps():
    """Check one strip block per period inside the laps shown, a hover note and one small key"""
    data = {"laps": [{"code": "AAA", "lap": n, "lap_time_s": 90 + n} for n in (1, 2, 3, 4)],
            "pit_laps": {}}
    fig = widgets.lap_time_figure(data, DRIVERS, periods=PERIODS)
    blocks = strip(fig)
    assert len(blocks) == 3
    sc = blocks[0]
    assert sc.base[0] == 1.5 and sc.x[0] == 2
    assert "Safety car, laps 2–3" in sc.hovertemplate
    assert "Red flag, lap 3" in blocks[2].hovertemplate
    # The strip sits above the chart, with one key above it and nothing written on the plot
    assert fig.layout.yaxis2.domain[0] > fig.layout.yaxis.domain[1]
    (key,) = fig.layout.annotations
    assert "Safety car" in key.text and "VSC" in key.text and "Red flag" in key.text
    assert key.y >= 1 and fig.layout.margin.t > 12
    rows = [{"code": "AAA", "lap": n, "position": 1} for n in (1, 2, 3, 4)]
    assert len(strip(widgets.position_figure(rows, DRIVERS, periods=PERIODS))) == 3


def test_tyre_strip_follows_the_bars_and_leaves_them_unshaded():
    """Check tyre strip blocks cover lap n from n-1 to n and nothing is drawn over the stints"""
    stints = [{"code": "AAA", "stint": 1, "compound": "SOFT", "start_lap": 1, "end_lap": 4}]
    tyre = widgets.tyre_strategy_figure(stints, periods=PERIODS)
    sc = strip(tyre)[0]
    assert sc.base[0] == 1 and sc.x[0] == 2
    # The key names what the strip shows
    assert "Safety car" in tyre.layout.annotations[0].text
    assert not tyre.layout.shapes


def test_line_charts_keep_only_a_faint_band_for_safety_car_laps():
    """Check lap and position charts shade safety car and VSC laps very lightly, behind the lines"""
    data = {"laps": [{"code": "AAA", "lap": n, "lap_time_s": 90 + n} for n in (1, 2, 3, 4)],
            "pit_laps": {}}
    shapes = widgets.lap_time_figure(data, DRIVERS, periods=PERIODS).layout.shapes
    assert len(shapes) == 2
    assert all(s.layer == "below" and s.opacity <= 0.1 for s in shapes)


def test_charts_without_periods_have_no_strip():
    """Check a race with no safety car or red flag draws no strip and no shapes"""
    data = {"laps": [{"code": "AAA", "lap": 1, "lap_time_s": 90}], "pit_laps": {}}
    fig = widgets.lap_time_figure(data, DRIVERS)
    assert not strip(fig) and not fig.layout.shapes and not fig.layout.annotations
    assert fig.layout.margin.t == 12


def test_pit_table_names_red_flag_waits_and_long_stops_in_minutes():
    """Check a red-flag wait and a long stop are named in words, with minutes, not compared"""
    stops = [{"code": "CCC", "stop": 2, "lap": 32, "pit_lane_time_s": 1415.3, "vs_median_s": None,
              "kind": "red_flag"},
             {"code": "AAA", "stop": 1, "lap": 34, "pit_lane_time_s": 777.7, "vs_median_s": None,
              "kind": "long"}]
    html = widgets.pit_stops_html({"stops": stops, "median_s": 23.0}, DRIVERS)
    assert "Red-flag wait" in html and "23.6 min" in html
    assert "Long stop" in html and "13.0 min" in html
    assert "1415.3 s" not in html
