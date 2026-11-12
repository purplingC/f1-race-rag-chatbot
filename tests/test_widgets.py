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
