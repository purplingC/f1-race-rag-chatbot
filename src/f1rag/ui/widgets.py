"""Widgets: Plotly charts and escaped HTML for each dashboard widget, drawn from widget data"""

import html
import re

import plotly.graph_objects as go

from f1rag.ui import theme
from f1rag.ui.avatars import avatar_html, safe_color

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


def driver_cards_html(cards, folder=None, show=None):
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
            f'<div style="color:{theme.MUTED};font-size:12px">{esc(card.get("team"))} · '
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


def classification_html(rows, folder=None, show=None):
    """Return the classification as a table with small avatars"""
    if not rows:
        return f'<p style="color:{theme.MUTED}">No classification for this race.</p>'
    cells = [
        [
            position_text(r["position"], r["status"]),
            avatar_html(r["code"], r["name"], r["team_color"], folder, show, size=24),
            f'{esc(r["name"])} <span style="color:{theme.MUTED}">{esc(r["code"])}</span>',
            esc(r["team"]),
            f'P{esc(r["grid"])}' if r.get("grid") else "–",
            points_text(r["points"]),
        ]
        for r in rows
    ]
    return table_html(["Pos", "", "Driver", "Team", "Grid", "Pts"], cells)


def pit_stops_html(data, drivers):
    """Return pit stops as a table with each stop against the race's median pit-lane time"""
    if not data["stops"]:
        return f'<p style="color:{theme.MUTED}">No pit stops for this race.</p>'
    cells = []
    for stop in data["stops"]:
        name = (drivers.get(stop["code"]) or {}).get("name") or stop["code"]
        diff = stop.get("vs_median_s")
        cells.append([
            f'{esc(name)} <span style="color:{theme.MUTED}">{esc(stop["code"])}</span>',
            esc(stop["stop"]), esc(stop["lap"]),
            f'{stop["pit_lane_time_s"]:.1f} s' if stop["pit_lane_time_s"] is not None else "–",
            f"{diff:+.1f} s" if diff is not None else "–",
        ])
    caption = (f"Pit-lane time from entry to exit, not stationary time. Race median "
               f'{data["median_s"]:.1f} s.' if data["median_s"] is not None else None)
    return table_html(["Driver", "Stop", "Lap", "Pit lane", "vs median"], cells, caption)


def markdown_paragraphs(text):
    """Escape stored Markdown text and keep only paragraphs and bold"""
    paragraphs = [part.strip() for part in re.split(r"\n\s*\n", text or "") if part.strip()]
    # Built outside the f-string, because Python 3.11 allows no backslash inside one
    bold = r"<strong>\1</strong>"
    return "".join(f"<p>{BOLD.sub(bold, esc(p))}</p>" for p in paragraphs)


def race_summary_html(card):
    """Return the race summary card: key facts, then the stored summary text"""
    if not card.get("summary_md"):
        return f'<p style="color:{theme.MUTED}">No stored summary for this race.</p>'
    facts = [f"Winner: {esc(card.get('winner'))}"]
    fastest = card.get("fastest_lap")
    if fastest:
        facts.append(f"Fastest lap: {esc(fastest['code'])} {lap_time_text(fastest['time_s'])} "
                     f"(lap {esc(fastest['lap'])})")
    facts.append(f"Safety cars: {esc(card.get('safety_cars', 0))} · "
                 f"Virtual safety cars: {esc(card.get('virtual_safety_cars', 0))} · "
                 f"Red flags: {esc(card.get('red_flags', 0))}")
    if card.get("start_suspended"):
        facts.append("Start suspended before the race")
    fact_html = "".join(f"<li>{fact}</li>" for fact in facts)
    return (f'<ul style="color:{theme.MUTED};font-size:13px;margin:0 0 8px 16px">{fact_html}</ul>'
            f"{markdown_paragraphs(card['summary_md'])}")


def lap_time_figure(data, drivers):
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
    fig.update_xaxes(title="Lap")
    fig.update_yaxes(title="Lap time (s)")
    return theme.style_figure(fig)


def tyre_strategy_figure(rows):
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
    fig.update_yaxes(categoryorder="array", categoryarray=[labels[c] for c in reversed(order)])
    fig.update_xaxes(title="Lap")
    fig.update_layout(barmode="overlay", bargap=0.35)
    if incomplete:
        fig.add_annotation(text="* tyre data incomplete for this driver (OpenF1)",
                           showarrow=False, x=0, y=-0.22, xref="paper", yref="paper",
                           xanchor="left", font={"size": 11, "color": theme.MUTED})
    return theme.style_figure(fig, height=max(200, 26 * len(order) + 80))


def position_figure(rows, drivers):
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
    deepest = max(row["position"] for row in rows)
    # P1 at the top and no P0 on the axis
    fig.update_yaxes(range=[deepest + 0.5, 0.5], dtick=1, title="Position (approximate)")
    fig.update_xaxes(title="Lap")
    return theme.style_figure(fig)
