"""Streamlit app: race banner on top, dashboard on the left, chat on the right"""

# Run with: streamlit run src/f1rag/ui/app.py

import contextlib
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from f1rag import config, vectors
from f1rag.engine import orchestrator, sql_guard
from f1rag.engine.llm import GeminiLLM, LLMUnavailableError
from f1rag.ui import avatars, page, theme
from f1rag.ui import widget_data as wd
from f1rag.ui import widgets as w

TITLE = "Formula 1 Race Assistant"
# Earlier questions and answers sent with a new question, so follow-ups have context
HISTORY_TURNS = 3
# Starting height of the chat log; the stylesheet stretches it to the window height
CHAT_HEIGHT = 520
PLOT_CONFIG = {"displayModeBar": False}
# Charts with laps along the bottom, which show safety cars and red flags
CHARTS = {"lap_time_chart", "tyre_strategy", "position_chart"}


def setting(name, default):
    """Read a value tests can inject through session state, or use the default"""
    return st.session_state.get(name, default)


def logos_on():
    """Tell whether team logos may be shown: SHOW_TEAM_LOGOS, or a value tests inject"""
    return setting("show_team_logos", avatars.team_logos_enabled())


def get_llm():
    """Return the Gemini model once per session, or None with an error shown"""
    if "llm" not in st.session_state:
        try:
            st.session_state["llm"] = GeminiLLM(on_wait=lambda notice: None)
        except LLMUnavailableError as error:
            st.error(str(error))
            return None
    return st.session_state["llm"]


def get_collection():
    """Open the vector store once per session"""
    if "collection" not in st.session_state:
        st.session_state["collection"] = vectors.open_collection(
            Path(setting("chroma_path", config.query_paths()[1])),
            embedding_function=setting("embedding_function", None),
        )
    return st.session_state["collection"]


def queue_question(question):
    """Put a question in the chat straight away; its answer is made on this run"""
    if question and question.strip():
        st.session_state["turns"].append({"question": question.strip(), "bundle": None})


def submit_typed():
    """Put a typed question in the chat straight away, before the answer is made"""
    queue_question(st.session_state.get("question_box"))


def clear_plan():
    """Go back to the race overview when another race is picked"""
    st.session_state.pop("plan", None)
    st.session_state.pop("no_new_widgets", None)


def banner(conn):
    """Draw the race banner with the pickers and key facts, and return the chosen race"""
    # Pickers can only be moved before they are drawn, so an answer's race waits until here
    pending = st.session_state.pop("pending_race", None)
    if pending:
        st.session_state["season"], st.session_state["race"] = pending[0], pending
    options = page.race_options(conn)
    seasons = sorted({o["season"] for o in options}, reverse=True)
    with st.container(key="hero"):
        title, season_col, race_col = st.columns([6, 1.2, 2.8], vertical_alignment="center")
        season = season_col.selectbox("Season", seasons, key="season")
        races = [o for o in options if o["season"] == season]
        labels = {(o["season"], o["round"]): page.race_label(o) for o in races}
        if st.session_state.get("race") not in labels:
            st.session_state["race"] = next(iter(labels))
        chosen = race_col.selectbox("Grand Prix", list(labels), format_func=labels.get,
                                    key="race", on_change=clear_plan)
        race = {"season": chosen[0], "round": chosen[1]}
        header = wd.race_header(conn, race["season"], race["round"])
        title.html(w.banner_title_html(header, app_name=TITLE))
        st.html(w.fact_tiles_html(header, wd.race_summary(conn, race["season"], race["round"])))
    race = {**race, "event_name": header["event_name"]}
    return {**race, "other_seasons": page.same_event_seasons(conn, race)}


def draw_widget(conn, race, widget, drivers, show, slot):
    """Draw one widget in its own panel, with its data fetched from the database"""
    season, round_no = race["season"], race["round"]
    data = wd.fetch(conn, season, round_no, widget)
    if widget.type == "classification":
        draw_classification(data, show, slot)
        return
    with st.container(key=f"w-{widget.type}-{slot}"):
        key = f"{widget.type}-{season}-{round_no}-{'-'.join(widget.drivers)}"
        if widget.type == "driver_card":
            podium = w.is_podium(data)
            st.html(page.panel_header("Podium" if podium else "Drivers"))
            st.html(w.podium_html(data, show=show, logos=logos_on()) if podium
                    else w.driver_cards_html(data, show=show, logos=logos_on()))
            return
        st.html(page.panel_header(page.WIDGET_TITLES[widget.type]))
        periods = wd.track_periods(conn, season, round_no) if widget.type in CHARTS else None
        if widget.type == "race_summary_card":
            st.html(w.race_summary_html(data, facts=False))
        elif widget.type == "pit_stops_table":
            st.html(w.pit_stops_html(data, drivers))
        elif widget.type == "lap_time_chart":
            st.plotly_chart(w.lap_time_figure(data, drivers, periods), key=key, config=PLOT_CONFIG)
        elif widget.type == "tyre_strategy":
            st.plotly_chart(w.tyre_strategy_figure(data, periods), key=key, config=PLOT_CONFIG)
        elif widget.type == "position_chart":
            st.plotly_chart(w.position_figure(data, drivers, periods), key=key, config=PLOT_CONFIG)


def draw_classification(rows, show, slot):
    """Draw the top ten and, beside it, the rest of the field, so no driver is shown twice"""
    top, rest = page.split_classification(rows)
    panels = st.columns(2, gap="medium") if rest else [st.container()]
    with panels[0], st.container(key=f"w-classification-{slot}"):
        title = page.CLASSIFICATION_TITLES[0] if rest else "Classification"
        st.html(page.panel_header(title))
        st.html(w.classification_html(top, show=show, compact=bool(rest), logos=logos_on()))
    if rest:
        with panels[1], st.container(key=f"w-classification-rest-{slot}"):
            st.html(page.panel_header(page.CLASSIFICATION_TITLES[1]))
            st.html(w.classification_html(rest, show=show, compact=True, logos=logos_on()))


def dashboard(conn, race, show):
    """Draw the dashboard grid: the last answer's checked widgets, or the race overview"""
    view = page.dashboard_view(st.session_state.get("plan"), conn, race)
    if st.session_state.get("no_new_widgets"):
        st.info("This answer used no race data, so the dashboard shows the race overview.")
    drivers = wd.race_drivers(conn, view["race"]["season"], view["race"]["round"])
    slot = 0
    for row in page.layout_rows(view["widgets"]):
        columns = st.columns(len(row), gap="medium") if len(row) > 1 else [st.container()]
        for column, widget in zip(columns, row):
            with column:
                draw_widget(conn, view["race"], widget, drivers, show, slot=slot)
            slot += 1


def draw_turn(index, turn, latest, race):
    """Draw one question and its answer in plain words, where it came from, and follow-ups"""
    bundle = turn["bundle"]
    st.html(page.user_bubble_html(turn["question"]))
    if bundle.error:
        st.error(bundle.error)
    else:
        if bundle.warning:
            st.info(bundle.warning)
        st.html(page.answer_bubble_html(page.answer_html(bundle.answer_md)))
        note = page.answer_note_html(bundle)
        if note:
            st.html(note)
    shown = page.visible_evidence(bundle.evidence, bundle.cited)
    if shown:
        with st.expander(f"Sources ({len(shown)})"):
            st.html("".join(page.source_card_html(item) for item in shown))
    if latest and not bundle.error:
        asked = [t["question"] for t in st.session_state["turns"]]
        follow = page.follow_up_questions(race, asked, other_seasons=race.get("other_seasons", ()))
        for number, question in enumerate(follow):
            st.button(question, key=f"follow-{index}-{number}", width="stretch",
                      on_click=queue_question, args=(question,))


def answer_pending(turn, conn, race):
    """Make the answer for a question already shown in the chat, then redraw the page"""
    llm = get_llm()
    if llm is None:
        st.session_state["turns"].remove(turn)
        return
    history = []
    done = [t for t in st.session_state["turns"] if t["bundle"] is not None]
    for earlier in done[-HISTORY_TURNS:]:
        if not earlier["bundle"].error:
            history += [("user", earlier["question"]), ("model", earlier["bundle"].answer_md)]
    default = {"season": race["season"], "round": race["round"]}
    with st.status("Thinking…") as status:
        # Each step is shown in plain words, under the question, while the answer is made
        bundle = orchestrator.answer(
            turn["question"], llm, conn, get_collection(), history=history,
            default_race=default, on_progress=lambda words: status.update(label=f"{words}…"),
        )
        status.update(label="Done", state="complete")
    turn["bundle"] = bundle
    st.session_state["no_new_widgets"] = bundle.dashboard is None and not bundle.error
    if bundle.dashboard is not None:
        st.session_state["plan"] = bundle.dashboard
        spec_race = bundle.dashboard.spec.race
        st.session_state["pending_race"] = (spec_race.season, spec_race.round)
    st.rerun()


def chat(conn, race):
    """Draw the chat panel: starter questions or the conversation, and the question box"""
    turns = st.session_state["turns"]
    with st.container(key="chat"):
        st.html(page.panel_header("Ask about the race"))
        if not turns:
            # Before the first question the panel is only as tall as its suggestions
            st.html(f'<div style="color:{theme.MUTED};font-size:13px;margin-bottom:4px">'
                    "Every answer comes from the loaded race data and reports, with its "
                    "sources. Try one of these, or type your own question.</div>")
            starters = page.suggested_questions(race, other_seasons=race.get("other_seasons", ()))
            for number, question in enumerate(starters):
                st.button(question, key=f"suggest-{number}", width="stretch",
                          on_click=queue_question, args=(question,))
        else:
            with st.container(height=CHAT_HEIGHT, border=False, key="chatlog"):
                for index, turn in enumerate(turns):
                    if turn["bundle"] is None:
                        # The question shows at once; its answer is made right under it
                        st.html(page.user_bubble_html(turn["question"]))
                        answer_pending(turn, conn, race)
                    else:
                        draw_turn(index, turn, latest=index == len(turns) - 1, race=race)
        st.chat_input("Ask about a race…", key="question_box", on_submit=submit_typed)


def footer():
    """Draw the footer with the sources and the disclaimer"""
    st.html(page.footer_html())


def main():
    """Draw the whole page"""
    st.set_page_config(page_title=TITLE, layout="wide")
    load_dotenv()
    st.html(theme.page_css())
    st.session_state.setdefault("turns", [])
    default_db, _, is_sample = config.query_paths()
    db_path = Path(setting("db_path", default_db))
    if not db_path.exists():
        st.error(f"No database at {db_path}. Load races first: python -m f1rag.ingest "
                 "--year 2026 --all")
        st.stop()
    show = setting("show_headshots", avatars.headshots_enabled())
    with contextlib.closing(sql_guard.connect_read_only(db_path)) as conn:
        race = banner(conn)
        if setting("sample", is_sample):
            st.caption("Showing the bundled sample data: three races. Load the full seasons with "
                       "python -m f1rag.ingest --year 2026 --all")
        left, right = st.columns([11, 8], gap="medium")
        with left:
            dashboard(conn, race, show)
        with right:
            chat(conn, race)
        footer()


main()
