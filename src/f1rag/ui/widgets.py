"""Widgets: Plotly charts and escaped HTML for each dashboard widget, drawn from widget data"""

import html
import re

import plotly.graph_objects as go

from f1rag.ui import theme
from f1rag.ui.avatars import avatar_html, safe_color, team_logo_html

BOLD = re.compile(r"\*\*(.+?)\*\*")


def esc(value):
    """HTML-escape any value from the database for safe display"""
    return html.escape("" if value is None else str(value))


def lap_time_text(seconds):
    """Format seconds as a lap time such as 1:29.500"""
    if seconds is None:
        return "–"
    minutes, rest = divmod(float(seconds), 60)
    return f"{int(minutes)}:{rest:06.3f}"


def points_text(points):
    """Format points without needless decimals, such as 25 or 0.5"""
    if points is None:
        return "0"
    return f"{points:g}"


def position_text(position, status=None):
    """Format a position as P1, or the status when the driver was not classified"""
    return f"P{position}" if position else esc(status or "–")


def line_styles(codes, drivers):
    """Give each driver a line style and marker, so teammates sharing a colour differ"""
    seen, styles = {}, {}
    for code in codes:
        team = (drivers.get(code) or {}).get("team") or code
        index = seen.get(team, 0)
        seen[team] = index + 1
        styles[code] = (theme.LINE_STYLES[index % 3], theme.MARKERS[index % 3])
    return styles


def driver_color(drivers, code):
    """Return a driver's team colour from the driver map, or the neutral grey"""
    return safe_color((drivers.get(code) or {}).get("team_color"))


def team_text(item, logos, logo_folder):
    """Return a team name, escaped, with its logo in front when logos are on"""
    logo = team_logo_html(item.get("team"), item.get("season"), logo_folder, logos)
    return f"{logo}{esc(item.get('team'))}"


def driver_cards_html(cards, folder=None, show=None, logos=None, logo_folder=None):
    """Return a row of driver cards: avatar, name, team, grid to finish, points and best lap"""
    parts = []
    for card in cards:
        color = safe_color(card.get("team_color"))
        gained = card.get("gained")
        change = f" ({gained:+d})" if gained else ""
        finish = position_text(card.get("finish"), card.get("status"))
        parts.append(
            f'<div class="f1-card" style="border-left:4px solid {color};background:{theme.PANEL};'
            f'border-radius:10px;padding:10px 12px;display:flex;gap:10px;align-items:center;'
            f'min-width:220px">'
            f'{avatar_html(card.get("code"), card.get("name"), color, folder, show, size=48)}'
            f'<div><div style="font-weight:600">{esc(card.get("name"))}</div>'
            f'<div style="color:{theme.MUTED};font-size:12px">'
            f'{team_text(card, logos, logo_folder)} · '
            f'#{esc(card.get("number"))}</div>'
            f'<div style="font-size:12px">Grid P{esc(card.get("grid"))} → {finish}{change} · '
            f'{points_text(card.get("points"))} pts · '
            f'best {lap_time_text(card.get("best_lap_s"))}</div>'
            f"</div></div>"
        )
    return f'<div style="display:flex;flex-wrap:wrap;gap:10px">{"".join(parts)}</div>'


def table_html(headers, rows, caption=None):
    """Return a small dark HTML table from already-escaped cells"""
    head = "".join(f'<th style="text-align:left;padding:4px 8px;color:{theme.MUTED}">{h}</th>'
                   for h in headers)
    body = "".join(
        "<tr>" + "".join(f'<td style="padding:4px 8px;border-top:1px solid {theme.BORDER}">'
                         f"{cell}</td>" for cell in row) + "</tr>"
        for row in rows
    )
    note = (f'<caption style="caption-side:bottom;text-align:left;color:{theme.MUTED};'
            f'font-size:12px;padding-top:6px">{caption}</caption>') if caption else ""
    return (f'<table style="border-collapse:collapse;width:100%;font-size:13px">{note}'
            f"<thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")


def classification_html(rows, folder=None, show=None, compact=False, logos=None,
                        logo_folder=None):
    """Return the classification as a table with small avatars, narrower when compact"""
    if not rows:
        return f'<p style="color:{theme.MUTED}">No classification for this race.</p>'
    if compact:
        # Half-width panels have no room for a team column, so team and number go under the name
        cells = [
            [
                position_text(r["position"], r["status"]),
                avatar_html(r["code"], r["name"], r["team_color"], folder, show, size=22),
                f'<div style="white-space:nowrap">{esc(r["name"])}</div>'
                f'<div style="color:{theme.MUTED};font-size:11px;white-space:nowrap">'
                f'{team_text(r, logos, logo_folder)} · '
                f'{number_text(r.get("number"))}</div>',
                f'P{esc(r["grid"])}' if r.get("grid") else "–",
                time_cell(r.get("time")),
                points_text(r["points"]),
            ]
            for r in rows
        ]
        return table_html(["Pos", "", "Driver", "Grid", "Time", "Pts"], cells)
    cells = [
        [
            position_text(r["position"], r["status"]),
            avatar_html(r["code"], r["name"], r["team_color"], folder, show, size=24),
            f'{esc(r["name"])} <span style="color:{theme.MUTED}">{number_text(r.get("number"))}'
            "</span>",
            team_text(r, logos, logo_folder),
            f'P{esc(r["grid"])}' if r.get("grid") else "–",
            time_cell(r.get("time")),
            points_text(r["points"]),
        ]
        for r in rows
    ]
    return table_html(["Pos", "", "Driver", "Team", "Grid", "Time", "Pts"], cells)


def number_text(number):
    """Show a car number as a tag such as #4, or nothing when it is unknown"""
    return f"#{esc(number)}" if number is not None else ""


def time_cell(text):
    """Show a result time in a fixed-width font so the gaps line up"""
    return (f'<span style="font-variant-numeric:tabular-nums;white-space:nowrap">'
            f'{esc(text or "–")}</span>')


def pit_stops_html(data, drivers):
    """Return pit stops as a table with each stop against the race's median pit-lane time"""
    if not data["stops"]:
        return f'<p style="color:{theme.MUTED}">No pit stops for this race.</p>'
    cells = []
    for stop in data["stops"]:
        name = (drivers.get(stop["code"]) or {}).get("name") or stop["code"]
        diff, kind = stop.get("vs_median_s"), stop.get("kind")
        time_s = stop["pit_lane_time_s"]
        if kind:
            # Too long to compare, so the time is in minutes and the reason in words
            label = "Red-flag wait" if kind == "red_flag" else "Long stop"
            pit_lane = f"{time_s / 60:.1f} min"
            versus = f'<span style="color:{theme.MUTED}">{label}</span>'
        else:
            pit_lane = f"{time_s:.1f} s" if time_s is not None else "–"
            versus = f"{diff:+.1f} s" if diff is not None else "–"
        cells.append([
            f'{esc(name)} <span style="color:{theme.MUTED}">{esc(stop["code"])}</span>',
            esc(stop["stop"]), esc(stop["lap"]), pit_lane, versus,
        ])
    caption = (f"Pit-lane time from entry to exit, not stationary time. Race median "
               f'{data["median_s"]:.1f} s.' if data["median_s"] is not None else None)
    if caption and any(stop.get("kind") for stop in data["stops"]):
        caption += " Red-flag waits and long stops are left out of the median."
    return table_html(["Driver", "Stop", "Lap", "Pit lane", "vs median"], cells, caption)


def markdown_paragraphs(text):
    """Escape stored Markdown text and keep only paragraphs and bold"""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]
    # Built outside the f-string, because Python 3.11 allows no backslash inside one
    bold = r"<strong>\1</strong>"
    return "".join(f"<p>{BOLD.sub(bold, esc(p))}</p>" for p in paragraphs)


def race_summary_html(card, facts=True):
    """Return the race summary card: key facts unless shown elsewhere, then the stored text"""
    if not card.get("summary_md"):
        return f'<p style="color:{theme.MUTED}">No stored summary for this race.</p>'
    if not facts:
        # The page banner already shows the winner, fastest lap and interruptions
        return markdown_paragraphs(card["summary_md"])
    lines = [f"Winner: {esc(card.get('winner'))}"]
    fastest = card.get("fastest_lap")
    if fastest:
        lines.append(f"Fastest lap: {esc(fastest['code'])} {lap_time_text(fastest['time_s'])} "
                     f"(lap {esc(fastest['lap'])})")
    lines.append(f"Safety cars: {esc(card.get('safety_cars', 0))} · "
                 f"Virtual safety cars: {esc(card.get('virtual_safety_cars', 0))} · "
                 f"Red flags: {esc(card.get('red_flags', 0))}")
    if card.get("start_suspended"):
        lines.append("Start suspended before the race")
    fact_html = "".join(f"<li>{fact}</li>" for fact in lines)
    return (f'<ul style="color:{theme.MUTED};font-size:13px;margin:0 0 8px 16px">{fact_html}</ul>'
            f"{markdown_paragraphs(card['summary_md'])}")


def add_track_periods(fig, periods, low, high, shift=0.0, height=320, bands=True):
    """Draw safety cars, VSCs and red flags as a strip above the chart, between laps low and high"""
    shown = [p for p in periods or [] if max(p["start"], low) <= min(p["end"], high)]
    if not shown:
        return
    for period in shown:
        start, end = max(period["start"], low), min(period["end"], high)
        style = theme.TRACK_STYLE[period["kind"]]
        laps = f"lap {start}" if start == end else f"laps {start}–{end}"
        # Points sit on whole laps, so a block runs half a lap either side
        fig.add_trace(go.Bar(
            y=["Track"], x=[end - start + 1], base=[start - 0.5 + shift], orientation="h",
            yaxis="y2", width=[0.8],
            marker={"color": style["fill"], "line": {"color": style["line"], "width": 1}},
            hovertemplate=f"{style['name']}, {laps}<extra></extra>", showlegend=False,
        ))
        if bands and period["kind"] != "RED":
            fig.add_vrect(x0=start - 0.5 + shift, x1=end + 0.5 + shift, fillcolor=style["line"],
                          opacity=theme.TRACK_BAND_OPACITY, layer="below", line_width=0)
    # One small key above the strip names what it shows; the laps are in the hover note
    kinds = [kind for kind in theme.TRACK_STYLE if any(p["kind"] == kind for p in shown)]
    key = "   ".join(f'<span style="color:{theme.TRACK_STYLE[kind]["line"]}">'
                     f'{theme.TRACK_STYLE[kind]["mark"]}</span> {theme.TRACK_STYLE[kind]["key"]}'
                     for kind in kinds)
    fig.add_annotation(text=key, x=1, y=1, xref="paper", yref="paper", xanchor="right",
                       yanchor="bottom", showarrow=False, font={"size": 11, "color": theme.MUTED})
    # The strip takes a fixed height at the top, whatever the chart's height
    top = 1 - theme.TRACK_STRIP_PX / height
    fig.update_layout(
        barmode="overlay",
        yaxis={"domain": [0, top - 0.03]},
        yaxis2={"domain": [top, 1], "showgrid": False, "zeroline": False, "fixedrange": True,
                "tickfont": {"size": 10, "color": theme.MUTED}},
    )


def lap_time_figure(data, drivers, periods=None):
    """Draw lap times per driver in team colours, teammates in other styles, pit laps marked"""
    if not data["laps"]:
        return theme.empty_figure()
    codes = list(dict.fromkeys(row["code"] for row in data["laps"]))
    styles = line_styles(codes, drivers)
    fig = go.Figure()
    for code in codes:
        laps = [row for row in data["laps"] if row["code"] == code]
        dash, marker = styles[code]
        color = driver_color(drivers, code)
        fig.add_trace(go.Scatter(
            x=[row["lap"] for row in laps], y=[row["lap_time_s"] for row in laps],
            name=code, mode="lines+markers", line={"color": color, "dash": dash, "width": 2},
            marker={"symbol": marker, "size": 5, "color": color},
            customdata=[lap_time_text(row["lap_time_s"]) for row in laps],
            hovertemplate=f"{code} lap %{{x}}: %{{customdata}}<extra></extra>",
        ))
        times = {row["lap"]: row["lap_time_s"] for row in laps}
        pit = [lap for lap in data["pit_laps"].get(code, []) if lap in times]
        if pit:
            fig.add_trace(go.Scatter(
                x=pit, y=[times[lap] for lap in pit], name=f"{code} pit", mode="markers",
                marker={"symbol": "triangle-down", "size": 11, "color": color,
                        "line": {"color": theme.TEXT, "width": 1}},
                hovertemplate=f"{code} pit stop, lap %{{x}}<extra></extra>", showlegend=False,
            ))
    laps = [row["lap"] for row in data["laps"]]
    add_track_periods(fig, periods, min(laps), max(laps))
    fig.update_xaxes(title="Lap")
    fig.update_yaxes(title="Lap time (s)")
    return theme.style_figure(fig)


def tyre_strategy_figure(rows, periods=None):
    """Draw each driver's stints as bars in compound colours with compound letters"""
    if not rows:
        return theme.empty_figure()
    order = list(dict.fromkeys(row["code"] for row in rows))
    incomplete = {row["code"] for row in rows if not row.get("complete", True)}
    labels = {code: f"{code}*" if code in incomplete else code for code in order}
    fig = go.Figure()
    for row in rows:
        compound = row["compound"] if row["compound"] in theme.TYRE_COLORS else "UNKNOWN"
        fig.add_trace(go.Bar(
            y=[labels[row["code"]]], x=[row["end_lap"] - row["start_lap"] + 1],
            base=[row["start_lap"] - 1], orientation="h",
            marker={"color": theme.TYRE_COLORS[compound], "line": {"color": theme.BACKGROUND,
                                                                  "width": 1}},
            text=[theme.TYRE_LETTERS[compound]], textposition="inside", insidetextanchor="middle",
            textangle=0,
            textfont={"color": theme.TYRE_TEXT.get(compound, theme.TEXT)},
            hovertemplate=(f"{row['code']} stint {row['stint']}: {compound.title()}, laps "
                           f"{row['start_lap']}-{row['end_lap']}<extra></extra>"),
            showlegend=False,
        ))
    height = max(200, 26 * len(order) + 80)
    # A stint bar covers lap n from n-1 to n, so the strip moves half a lap left
    add_track_periods(fig, periods, min(row["start_lap"] for row in rows),
                      max(row["end_lap"] for row in rows), shift=-0.5, height=height,
                      bands=False)
    fig.update_yaxes(categoryorder="array", categoryarray=[labels[c] for c in reversed(order)])
    fig.update_xaxes(title="Lap")
    fig.update_layout(barmode="overlay", bargap=0.35)
    if incomplete:
        fig.add_annotation(text="* tyre data incomplete for this driver (OpenF1)",
                           showarrow=False, x=0, y=-0.22, xref="paper", yref="paper",
                           xanchor="left", font={"size": 11, "color": theme.MUTED})
    return theme.style_figure(fig, height=height)


def position_figure(rows, drivers, periods=None):
    """Draw the running position per lap, P1 at the top, teammates in other styles"""
    if not rows:
        return theme.empty_figure()
    codes = list(dict.fromkeys(row["code"] for row in rows))
    styles = line_styles(codes, drivers)
    fig = go.Figure()
    for code in codes:
        laps = [row for row in rows if row["code"] == code]
        dash, marker = styles[code]
        color = driver_color(drivers, code)
        fig.add_trace(go.Scatter(
            x=[row["lap"] for row in laps], y=[row["position"] for row in laps], name=code,
            mode="lines", line={"color": color, "dash": dash, "width": 2, "shape": "hv"},
            hovertemplate=f"{code} lap %{{x}}: P%{{y}}<extra></extra>",
        ))
    laps = [row["lap"] for row in rows]
    add_track_periods(fig, periods, min(laps), max(laps))
    deepest = max(row["position"] for row in rows)
    # P1 at the top and no P0 on the axis
    fig.update_yaxes(range=[deepest + 0.5, 0.5], dtick=1, title="Position (approximate)")
    fig.update_xaxes(title="Lap")
    return theme.style_figure(fig)


PODIUM_ORDER = (2, 1, 3)
STEP_HEIGHTS = {1: 74, 2: 52, 3: 36}
AVATAR_SIZES = {1: 88, 2: 68, 3: 68}


def is_podium(cards):
    """Tell whether the cards are exactly the first, second and third finishers"""
    return sorted(card.get("finish") or 0 for card in cards) == [1, 2, 3]


def podium_html(cards, folder=None, show=None, logos=None, logo_folder=None):
    """Draw the top three on podium steps: second, first, third, in team colours"""
    by_place = {card["finish"]: card for card in cards}
    columns = []
    for place in PODIUM_ORDER:
        card = by_place[place]
        color = safe_color(card.get("team_color"))
        avatar = avatar_html(card.get("code"), card.get("name"), color, folder, show,
                             size=AVATAR_SIZES[place])
        gained = card.get("gained")
        change = f" ({gained:+d})" if gained else ""
        columns.append(
            f'<div style="flex:1;display:flex;flex-direction:column;align-items:center;'
            f'justify-content:flex-end;text-align:center;min-width:0">{avatar}'
            f'<div style="font-weight:700;margin-top:6px;font-size:{15 if place == 1 else 13}px">'
            f'{esc(card.get("name"))}</div>'
            f'<div style="color:{theme.MUTED};font-size:11px">'
            f'{team_text(card, logos, logo_folder)} · '
            f'#{esc(card.get("number"))}</div>'
            f'<div style="font-size:11px;margin-bottom:6px">Grid P{esc(card.get("grid"))}'
            f'{change} · {points_text(card.get("points"))} pts · '
            f'{lap_time_text(card.get("best_lap_s"))}</div>'
            f'<div class="f1-step" style="width:100%;height:{STEP_HEIGHTS[place]}px;'
            f'border-top:3px solid {color};border-radius:8px 8px 0 0;'
            f'background:linear-gradient(180deg,{color}55,{color}0D);display:flex;'
            f'align-items:center;justify-content:center;font:italic 800 26px sans-serif;'
            f'color:{theme.TEXT}">{place}</div></div>'
        )
    return f'<div style="display:flex;gap:10px;align-items:flex-end">{"".join(columns)}</div>'


def banner_title_html(header, app_name=None):
    """Return the banner title: app name, round badge, race name, circuit and date"""
    brand = (f'<div style="font:800 30px sans-serif;letter-spacing:.01em;color:#fff;'
             f'line-height:1.1;margin-bottom:10px">{esc(app_name)}</div>'
             if app_name else "")
    place = " · ".join(esc(part) for part in (header.get("circuit"), header.get("country"),
                                              header.get("race_date")) if part)
    return (
        f'{brand}<div style="display:flex;align-items:center;gap:14px">'
        f'<div style="font:italic 800 34px sans-serif;line-height:1;color:transparent;'
        f'-webkit-text-stroke:1px rgba(255,255,255,.9)">'
        f'R{esc(header.get("round"))}</div>'
        f'<div><div style="font:700 20px sans-serif;letter-spacing:.01em;color:#fff">'
        f'{esc(header.get("event_name") or "No race loaded")}</div>'
        f'<div style="color:rgba(255,255,255,.75);font-size:12px">{esc(header.get("season"))} · '
        f"{place}</div></div></div>"
    )


def fact_tile(label, value, note=""):
    """Return one key-fact tile with a small label, a value and an optional note"""
    extra = f'<div style="color:{theme.MUTED};font-size:11px">{note}</div>' if note else ""
    return (
        f'<div style="flex:1;min-width:130px;background:rgba(21,21,30,.55);'
        f'border:1px solid rgba(255,255,255,.10);border-radius:10px;padding:8px 12px">'
        f'<div style="color:{theme.MUTED};font:600 10px sans-serif;text-transform:uppercase;'
        f'letter-spacing:.08em">{label}</div>'
        f'<div style="font:700 16px sans-serif;margin-top:2px">{value}</div>{extra}</div>'
    )


def fact_tiles_html(header, facts):
    """Return the row of key facts under the banner: winner, fastest lap, laps, interruptions"""
    fastest = facts.get("fastest_lap")
    fast_value = f"{lap_time_text(fastest['time_s'])}" if fastest else "–"
    fast_note = f"{esc(fastest['code'])} · lap {esc(fastest['lap'])}" if fastest else ""
    stops = (f"{esc(facts.get('safety_cars', 0))} SC · "
             f"{esc(facts.get('virtual_safety_cars', 0))} VSC")
    flags = "Start suspended" if facts.get("start_suspended") else ""
    tiles = [
        fact_tile("Winner", esc(facts.get("winner") or "–")),
        fact_tile("Fastest lap", fast_value, fast_note),
        fact_tile("Race distance", f"{esc(header.get('laps') or '–')} laps",
                  f"{esc(header.get('drivers') or 0)} drivers"),
        fact_tile("Safety cars", stops),
        fact_tile("Red flags", esc(facts.get("red_flags", 0)), flags),
    ]
    return f'<div style="display:flex;flex-wrap:wrap;gap:10px">{"".join(tiles)}</div>'
