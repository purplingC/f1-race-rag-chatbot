"""Race summary generator: a fact sheet from SQL plus report passages, written up by Gemini"""

import json

from f1rag import vectors
from f1rag.engine.llm import parse_json
from f1rag.ingest import chunking

PASSAGES = 6
PASSAGE_QUERY = "race report: the start, key moments, strategy, incidents and the result"
SOURCE_NAME = "Generated summary"
LICENCE = "Derived from OpenF1 (CC BY-NC-SA 4.0) and Wikipedia (CC BY-SA 4.0)"

SYSTEM = (
    "You write short race summaries for Formula 1 fans. Use only the fact sheet and the report "
    "passages you are given; never add facts from memory. If the fact sheet and a passage "
    "disagree, use the fact sheet, which comes from official timing data. Never state why "
    "something happened unless a passage says so: two things on the same lap are not cause "
    "and effect. Pit times are pit-lane times, not stationary times."
)
INSTRUCTION = (
    "Write a summary of this race in Markdown, 150 to 250 words, in two or three paragraphs: "
    "who won and the podium, how the race unfolded (key moments, safety cars, strategy) and "
    "notable retirements or penalties. Use full driver names and the team names given. "
    "Return JSON with one field, summary_md."
)
SUMMARY_SCHEMA = {
    "type": "object",
    "properties": {"summary_md": {"type": "string"}},
    "required": ["summary_md"],
}


def rows(conn, sql, params):
    """Run a query and return its rows as dictionaries"""
    cursor = conn.execute(sql, params)
    names = [d[0] for d in cursor.description]
    return [dict(zip(names, row)) for row in cursor]


def fact_sheet(conn, season, round_no):
    """Collect the race's key facts from SQLite: result, fastest lap, strategy and incidents"""
    key = {"s": season, "r": round_no}
    classification = rows(
        conn,
        "SELECT r.driver_code, e.full_name, e.team, r.grid_pos, r.finish_pos, r.status, "
        "r.points, r.laps_completed FROM results r LEFT JOIN entries e "
        "ON e.season = r.season AND e.round = r.round AND e.driver_code = r.driver_code "
        "WHERE r.season = :s AND r.round = :r ORDER BY r.finish_pos IS NULL, r.finish_pos",
        key,
    )
    if not classification:
        raise LookupError(f"No race data for {season} round {round_no}, load race data first")
    events = rows(
        conn,
        "SELECT event_name, circuit, country, race_date FROM events "
        "WHERE season = :s AND round = :r",
        key,
    )
    fastest = rows(
        conn,
        "SELECT driver_code, lap_number, lap_time_s FROM laps WHERE season = :s AND round = :r "
        "AND lap_time_s IS NOT NULL ORDER BY lap_time_s LIMIT 1",
        key,
    )
    stints = rows(
        conn,
        "SELECT driver_code, stint_no, compound, start_lap, end_lap FROM stints "
        "WHERE season = :s AND round = :r ORDER BY driver_code, stint_no",
        key,
    )
    stops = rows(
        conn,
        "SELECT driver_code, stop_no, lap, pit_lane_time_s FROM pit_stops "
        "WHERE season = :s AND round = :r ORDER BY lap, driver_code",
        key,
    )
    # Flags such as blue flags are routine, only race-changing messages are kept
    control = rows(
        conn,
        "SELECT lap_number, category, message FROM race_control "
        "WHERE season = :s AND round = :r AND (category = 'SafetyCar' "
        "OR message LIKE 'RED FLAG%' OR message LIKE '%PENALTY%') ORDER BY event_no",
        key,
    )
    return {
        "event": events[0] if events else {"season": season, "round": round_no},
        "classification": classification,
        "fastest_lap": fastest[0] if fastest else None,
        "stints": stints,
        "pit_stops": stops,
        "race_control": control,
    }


def report_passages(collection, season, round_no):
    """Return the race report passages most related to how the race went"""
    found = collection.query(
        query_texts=[PASSAGE_QUERY], n_results=PASSAGES,
        where={"$and": [{"season": season}, {"round": round_no}, {"source_type": "wiki"}]},
        include=["documents"],
    )
    return found["documents"][0]


def build_prompt(facts, passages):
    """Put the fact sheet and the passages into one prompt for the model"""
    text = "\n\n".join(passages) if passages else "(no report passages loaded)"
    return (
        f"{INSTRUCTION}\n\nFact sheet (JSON, times in seconds):\n"
        f"{json.dumps(facts, ensure_ascii=False)}\n\nReport passages:\n{text}"
    )


def summary_chunks(summary_md, facts, season, round_no, generated_at, model):
    """Split the summary into chunks with metadata like the wiki chunks"""
    name = facts["event"].get("event_name") or f"{season} round {round_no}"
    title = f"{name} {season} summary"
    paragraphs = [line.strip() for line in summary_md.split("\n") if line.strip()]
    return [
        {
            "id": chunking.chunk_id(f"summary:{season}:{round_no}", index),
            "document": f"{title} - Summary\n{body}",
            "metadata": {
                "season": season, "round": round_no, "source_type": "summary",
                "source_name": SOURCE_NAME, "title": title, "url": "", "section": "Summary",
                "chunk_idx": index, "licence": LICENCE, "published_at": generated_at,
                "ingested_at": generated_at, "model": model,
            },
        }
        for index, body in enumerate(chunking.section_chunks(paragraphs))
    ]


def ingest_race_summary(conn, collection, llm, season, round_no, generated_at):
    """Generate one race's summary, store it in SQLite and Chroma, and return the chunk count"""
    facts = fact_sheet(conn, season, round_no)
    prompt = build_prompt(facts, report_passages(collection, season, round_no))
    reply = llm.generate([llm.user_turn(prompt)], SYSTEM, json_schema=SUMMARY_SCHEMA)
    summary_md = ((parse_json(reply.text) or {}).get("summary_md") or "").strip()
    if not summary_md:
        raise ValueError("The model returned an empty summary")
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO race_summaries "
            "(season, round, summary_md, generated_at, model) VALUES (?, ?, ?, ?, ?)",
            (season, round_no, summary_md, generated_at, llm.model),
        )
    chunks = summary_chunks(summary_md, facts, season, round_no, generated_at, llm.model)
    return vectors.replace_race_docs(collection, season, round_no, "summary", chunks)
