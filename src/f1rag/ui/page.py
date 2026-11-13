"""Page helpers: the answer, its sources, race list, footer and which widgets to show"""

import html
import re

from f1rag.engine import dashboard_planner as dp
from f1rag.engine.evidence import fact_sentences
from f1rag.ui import theme

# Citations such as [E1, E2] are checked by the engine but never shown to users
CITATIONS = re.compile(r"\s*\[(E\d+(?:\s*,\s*E\d+)*)\]")
BOLD = re.compile(r"\*\*(.+?)\*\*")
# Rows of a race data result shown on its source card
PREVIEW_ROWS = 5
WIDGET_TITLES = {
    "driver_card": "Drivers",
    "classification": "Classification",
    "lap_time_chart": "Lap times",
    "tyre_strategy": "Tyre strategy",
    "pit_stops_table": "Pit stops",
    "position_chart": "Positions",
    "race_summary_card": "Race summary",
}
OVERVIEW = ["classification", "race_summary_card", "tyre_strategy"]
# Widgets small enough to share a row with another one
HALF_WIDTH = {"pit_stops_table"}
# The classification shows the top ten in one panel and the rest of the field beside it
CLASSIFICATION_TOP = 10
# The top ten score points, so the two panels are named after that
CLASSIFICATION_TITLES = ("Points finishers", "Outside the points")
# The kind of source each tool reads, for the "Answered from" line
SOURCE_KINDS = {"run_sql": "race data", "search_docs": "race reports"}
# What each kind of source is called on its card
SOURCE_LABELS = {
    "run_sql": "Race data", "search_docs": "Race reports", "find_race": "Race lookup",
    "get_data_coverage": "Loaded races",
}
CELL = "padding:2px 6px"
FOOTER_STYLE = (f"border-top:1px solid {theme.BORDER};margin-top:14px;padding-top:8px;"
                f"color:{theme.MUTED};font-size:11px")
SOURCES = ("OpenF1 (CC BY-NC-SA 4.0), Wikipedia (CC BY-SA 4.0) · Unofficial project, not "
           "affiliated with Formula 1, the FIA or any team.")


def esc(value):
    """HTML-escape any value for safe display"""
    return html.escape("" if value is None else str(value))


def inline(text):
    """Escape one line of model text, keep bold, and drop the citation ids"""
    bold = r"<strong>\1</strong>"
    return BOLD.sub(bold, esc(CITATIONS.sub("", text)))


def answered_from(items, cited):
    """Name the kinds of source an answer used, for example race data and race reports"""
    kinds = []
    for item in items:
        if item.id in cited:
            kind = SOURCE_KINDS.get(item.tool)
            if kind and kind not in kinds:
                kinds.append(kind)
    if not kinds:
        return ""
    joined = kinds[0] if len(kinds) == 1 else ", ".join(kinds[:-1]) + " and " + kinds[-1]
    return f"Answered from {joined}"


def compare_question(race, other_seasons):
    """Ask how the race compared with the latest other season of the same Grand Prix"""
    if not other_seasons:
        return None
    name = f"{race.get('season')} {race.get('event_name')}"
    return f"How did the {name} compare with the {max(other_seasons)} race?"


def sourced_text(answer, cited, uncited):
    """Say how many of the answer's statements cite a source, or nothing if none do"""
    if not cited:
        return ""
    sourced = sum(1 for _, has in fact_sentences(answer) if has)
    total = sourced + len(uncited)
    if total == 0:
        return ""
    if not uncited:
        if total == 1:
            return "✓ The statement cites its source"
        return f"✓ All {total} statements cite their source"
    return f"{sourced} of {total} statements cite a source"


def answer_note_html(bundle):
    """Return the line under an answer: how many statements cite a source, and which kinds"""
    sourced = sourced_text(bundle.answer_md, bundle.cited, bundle.uncited)
    kinds = answered_from(bundle.evidence, bundle.cited)
    if not sourced and not kinds:
        return ""
    color = theme.SOURCED if sourced.startswith("✓") else theme.MUTED
    badge = f'<span style="color:{color};font-weight:600">{esc(sourced)}</span>' if sourced else ""
    joined = " · ".join(part for part in (badge, esc(kinds)) if part)
    return f'<div style="color:{theme.MUTED};font-size:11px;margin:2px 0 0 4px">{joined}</div>'


def follow_up_questions(race, asked, count=3, other_seasons=()):
    """Offer a few more questions about the same race, leaving out ones already asked"""
    name = f"{race.get('season')} {race.get('event_name')}"
    options = [
        f"Who gained the most places in the {name}, and how?",
        f"How did safety cars or red flags shape the {name}?",
        f"Which tyre strategy worked best in the {name}?",
        compare_question(race, other_seasons),
        f"How did the pit stops compare in the {name}?",
        f"Who set the fastest lap in the {name}, and on which lap?",
        f"What were the key moments of the {name}?",
    ]
    return [q for q in options if q and q not in asked][:count]


def answer_html(text):
    """Turn the answer's Markdown into safe HTML: paragraphs, bullet lists and bold"""
    blocks = []
    for block in re.split(r"\n\s*\n", text or ""):
        lines = [line.strip() for line in block.strip().split("\n") if line.strip()]
        if not lines:
            continue
        if all(line[:2] in ("- ", "* ") for line in lines):
            items = "".join(f"<li>{inline(line[2:])}</li>" for line in lines)
            blocks.append(f'<ul style="margin:4px 0 8px 18px">{items}</ul>')
        else:
            joined = "<br>".join(inline(x) for x in lines)
            blocks.append(f'<p style="margin:0 0 8px">{joined}</p>')
    return "".join(blocks)


def safe_link(url, text):
    """Return a link only for http or https addresses, otherwise plain text"""
    if isinstance(url, str) and url.startswith(("https://", "http://")):
        target = 'target="_blank" rel="noopener"'
        return f'<a href="{esc(url)}" {target} style="color:{theme.TEXT}">{text}</a>'
    return text


def source_body(item):
    """Show what one source holds in plain words: rows, report sections or races"""
    result = item.result
    if item.tool == "run_sql":
        parts = []
        if result.get("ok"):
            columns = result.get("columns") or []
            rows = (result.get("rows") or [])[:PREVIEW_ROWS]
            names = [str(c).replace("_", " ") for c in columns]
            head = "".join(f"<th style='text-align:left;{CELL}'>{esc(c)}</th>" for c in names)
            body = "".join(
                "<tr>" + "".join(f"<td style='{CELL}'>{esc(v)}</td>" for v in row) + "</tr>"
                for row in rows
            )
            more = result.get("row_count", 0) - len(rows)
            note = f"<div style='color:{theme.MUTED}'>… {more} more rows</div>" if more > 0 else ""
            table = f"<tr>{head}</tr>{body}"
            parts.append(f"<table style='font-size:11px;margin-top:4px'>{table}</table>{note}")
        else:
            parts.append(f'<div style="color:{theme.MUTED}">This lookup did not work.</div>')
        return "".join(parts)
    if item.tool == "search_docs":
        groups = {}
        for p in result.get("passages", []):
            groups.setdefault((p.get("title"), p.get("section")), []).append(p)
        lines = []
        for (title, section), passages in groups.items():
            first = passages[0]
            count = f" ({len(passages)} passages)" if len(passages) > 1 else ""
            label = f"{esc(title)} › {esc(section)}{count}"
            date = esc((first.get("published_at") or "")[:10])
            lines.append(f"<li>{safe_link(first.get('url'), label)} · "
                         f"{esc(first.get('source_name'))} {date}</li>")
        return f'<ul style="margin:4px 0 0 16px;font-size:11px">{"".join(lines)}</ul>'
    if item.tool == "find_race":
        races = "; ".join(f"{esc(m['season'])} round {esc(m['round'])}: {esc(m['event_name'])}"
                          for m in result.get("matches", []))
        return f'<div style="font-size:11px;color:{theme.MUTED}">{races or "No match"}</div>'
    return ""


def friendly_summary(item):
    """Describe an evidence item in plain words, without tool names"""
    result = item.result
    if item.tool == "run_sql":
        if not result.get("ok"):
            return "the lookup failed"
        count = result.get("row_count", 0)
        return f"{count} row{'' if count == 1 else 's'}"
    if item.tool == "search_docs":
        count = len(result.get("passages", []))
        return f"{count} passage{'' if count == 1 else 's'}"
    if item.tool == "find_race":
        count = len(result.get("matches", []))
        return f"{count} race{'' if count == 1 else 's'} found"
    return item.summary


def source_card_html(item):
    """Return one source card in plain words: its kind, a short count and what it holds"""
    label = SOURCE_LABELS.get(item.tool, "Source")
    title = (f"<b>{esc(label)}</b> "
             f'<span style="color:{theme.MUTED}">· {esc(friendly_summary(item))}</span>')
    return (f'<div style="border:1px solid {theme.BORDER};border-radius:8px;padding:8px;'
            f'margin:6px 0;font-size:12px;background:rgba(21,21,30,.45)">'
            f"{title}{source_body(item)}</div>")


def visible_evidence(items, cited):
    """Return the sources to list: only those the answer actually used"""
    return [item for item in items if item.id in cited]


def suggested_questions(race, other_seasons=()):
    """Return starter questions that ask how and why about the race, with no facts in them"""
    name = f"{race.get('season')} {race.get('event_name')}"
    return [
        f"Who gained the most places in the {name}, and how?",
        f"How did safety cars or red flags shape the {name}?",
        f"Which tyre strategy worked best in the {name}?",
        compare_question(race, other_seasons) or f"What were the key moments of the {name}?",
    ]


def same_event_seasons(conn, race):
    """List the other loaded seasons of the same Grand Prix, latest first"""
    return [row[0] for row in conn.execute(
        "SELECT DISTINCT season FROM events WHERE event_name = ? AND season != ? "
        "ORDER BY season DESC",
        (race.get("event_name"), race.get("season")),
    )]


def split_classification(rows, top=CLASSIFICATION_TOP):
    """Split the classification into the top ten and the rest of the field, with no repeats"""
    return rows[:top], rows[top:]


def layout_rows(widgets):
    """Group widgets into rows: two small widgets side by side, charts on their own"""
    grouped, row = [], []
    for widget in widgets:
        if widget.type in HALF_WIDTH:
            row.append(widget)
            if len(row) == 2:
                grouped.append(row)
                row = []
            continue
        if row:
            grouped.append(row)
            row = []
        grouped.append([widget])
    if row:
        grouped.append(row)
    return grouped


def user_bubble_html(question):
    """Return the user's question as a right-aligned bubble, escaped"""
    return (f'<div style="display:flex;justify-content:flex-end"><div style="max-width:88%;'
            f'background:linear-gradient(135deg,rgba(225,6,0,.30),rgba(225,6,0,.14));'
            f'border:1px solid rgba(225,6,0,.55);border-radius:14px 14px 4px 14px;'
            f'padding:8px 12px;font-size:14px">{esc(question)}</div></div>')


def answer_bubble_html(inner):
    """Wrap an already-safe answer in a left-aligned bubble"""
    return (f'<div style="max-width:96%;background:rgba(31,31,43,.85);'
            f'border:1px solid {theme.BORDER};border-radius:14px 14px 14px 4px;'
            f'padding:10px 12px;font-size:14px">{inner}</div>')


def race_options(conn):
    """List the loaded races, latest first, for the race picker"""
    cursor = conn.execute(
        "SELECT season, round, event_name, circuit FROM events ORDER BY race_date DESC"
    )
    keys = ("season", "round", "event_name", "circuit")
    return [dict(zip(keys, row)) for row in cursor]


def race_label(option):
    """Label a race for the picker, for example R16 · Bahrain Grand Prix (Kuala Lumpur)"""
    circuit = f" ({option['circuit']})" if option.get("circuit") else ""
    return f"R{option['round']} · {option['event_name']}{circuit}"


def footer_html():
    """Return the footer: the data sources with their licences, and the disclaimer"""
    # The licences require crediting the sources wherever their data is shown
    return f'<footer style="{FOOTER_STYLE}">Data: {SOURCES}</footer>'


def podium(conn, season, round_no):
    """Return the top three finishers of a race"""
    return [row[0] for row in conn.execute(
        "SELECT driver_code FROM results WHERE season = ? AND round = ? "
        "AND finish_pos IS NOT NULL ORDER BY finish_pos LIMIT 3",
        (season, round_no),
    )]


def dashboard_view(plan, conn, race):
    """Choose what the dashboard shows: the answer's checked plan, or the race overview"""
    if plan is not None:
        spec = plan.spec
        return {
            "race": {"season": spec.race.season, "round": spec.race.round},
            "widgets": list(spec.widgets), "evidence": list(spec.evidence),
            "source": plan.source, "problems": list(plan.problems),
        }
    widgets = []
    top = podium(conn, race["season"], race["round"])
    if top:
        widgets.append(dp.Widget(type="driver_card", drivers=top))
    widgets += [dp.Widget(type=widget_type) for widget_type in OVERVIEW]
    return {"race": dict(race), "widgets": widgets, "evidence": [], "source": "overview",
            "problems": []}


def panel_header(title):
    """Return a small uppercase panel title with a slanted red marker"""
    # A slanted red marker before each title, like a timing-screen sector flag
    marker = (f'<span style="display:inline-block;width:14px;height:10px;background:{theme.ACCENT};'
              f'transform:skewX(-20deg);margin-right:8px"></span>')
    return (f'<div style="color:{theme.TEXT};font:700 12px sans-serif;text-transform:uppercase;'
            f'letter-spacing:.08em;margin-bottom:6px">{marker}{esc(title)}</div>')
