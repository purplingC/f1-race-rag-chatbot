"""The four tools the model can call: find_race, run_sql, search_docs and get_data_coverage"""

from f1rag.engine import sql_guard

MAX_RACES = 5
DEFAULT_K = 5
MAX_K = 10

# Tool descriptions given to the model (Section 6.1); llm.py turns them into Gemini's format
DECLARATIONS = [
    {
        "name": "find_race",
        "description": (
            "Find ingested races by season, name, country or exact date. Use it when the race "
            "is not clearly in the loaded race list, for example a date or a description. "
            "Returns up to 5 matches, latest first, with an ambiguous flag; an empty list means "
            "the race is not in the data."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "season": {"type": "integer", "description": "Season year, for example 2026"},
                "name": {
                    "type": "string",
                    "description": "Part of the race name, circuit or country, for example Monaco",
                },
                "country": {"type": "string", "description": "Country of the race"},
                "date": {"type": "string", "description": "Race day as YYYY-MM-DD"},
            },
        },
    },
    {
        "name": "run_sql",
        "description": (
            "Run one read-only SQLite SELECT query on the race database and return the rows "
            "(at most 200). Use it for numbers, rankings, counts, comparisons and driver-team "
            "lookups. Aggregate and use LIMIT. On an error the message explains what to fix."
        ),
        "parameters": {
            "type": "object",
            "properties": {"query": {"type": "string", "description": "A single SELECT statement"}},
            "required": ["query"],
        },
    },
    {
        "name": "search_docs",
        "description": (
            "Search race reports (Wikipedia) by meaning and return the most relevant passages "
            "with title, section, URL and date. Use it for reasons, incidents, strategy, quotes "
            "and penalties. Filter by season and round whenever the race is known."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "What to look for, in plain words"},
                "season": {"type": "integer", "description": "Season year of the race"},
                "round": {"type": "integer", "description": "Round number of the race"},
                "source_type": {"type": "string", "description": "wiki or summary"},
                "k": {"type": "integer", "description": "Passages to return, default 5, max 10"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "get_data_coverage",
        "description": (
            "List every ingested race (season, round, name, circuit, date, and whether race data, "
            "text and a summary are loaded) and the latest race. Use it when a question is "
            "ambiguous or may be outside the data."
        ),
        "parameters": {"type": "object", "properties": {}},
    },
]


def find_race(conn, season=None, name=None, country=None, date=None):
    """Return up to five ingested races matching the arguments, latest first, flagging ambiguity"""
    if season is None and not name and not country and not date:
        return {
            "matches": [],
            "ambiguous": False,
            "message": "Give at least one of season, name, country or date",
        }
    sql = (
        "SELECT season, round, event_name, circuit, country, race_date FROM events "
        "WHERE (:season IS NULL OR season = :season) "
        "AND (:date IS NULL OR race_date = :date) "
        "AND (:country IS NULL OR LOWER(country) LIKE :country) "
        "AND (:name IS NULL OR LOWER(event_name) LIKE :name OR LOWER(circuit) LIKE :name "
        "OR LOWER(country) LIKE :name) "
        "ORDER BY race_date DESC LIMIT :limit"
    )
    params = {
        "season": season,
        "date": date or None,
        "country": f"%{country.lower()}%" if country else None,
        "name": f"%{name.lower()}%" if name else None,
        "limit": MAX_RACES,
    }
    columns = ["season", "round", "event_name", "circuit", "country", "race_date"]
    matches = [dict(zip(columns, row)) for row in conn.execute(sql, params)]
    result = {"matches": matches, "ambiguous": len(matches) > 1}
    if not matches:
        result["message"] = (
            "No ingested race matches. Call get_data_coverage to see which races are available."
        )
    elif result["ambiguous"]:
        result["message"] = (
            "Several races match: use the latest and state the assumption, or ask the user."
        )
    return result


def run_sql(conn, query):
    """Run one guarded SELECT and return its rows, or the error message for the model to fix"""
    try:
        return {"ok": True, "query": query, **sql_guard.execute_safe(conn, query)}
    except sql_guard.SqlError as error:
        return {"ok": False, "query": query, "error": str(error)}


def get_data_coverage(conn):
    """List the ingested races with the sources loaded for each, and the latest race"""
    sql = (
        "SELECT e.season, e.round, e.event_name, e.circuit, e.country, e.race_date, "
        "EXISTS (SELECT 1 FROM ingest_log l WHERE l.season = e.season AND l.round = e.round "
        "AND l.source = 'wikipedia' AND l.status = 'ok'), "
        "EXISTS (SELECT 1 FROM race_summaries s WHERE s.season = e.season AND s.round = e.round) "
        "FROM events e ORDER BY e.season, e.round"
    )
    races = [
        {
            "season": s, "round": r, "event_name": name, "circuit": circuit,
            "country": country, "race_date": day,
            "race_data": True, "text": bool(text), "summary": bool(summary),
        }
        for s, r, name, circuit, country, day, text, summary in conn.execute(sql)
    ]
    latest = max(races, key=lambda race: race["race_date"] or "") if races else None
    return {
        "races": races,
        "latest": latest,
        "note": (
            "Only races that were held and loaded are listed; cancelled races are not stored. "
            "Rounds are numbered among held races."
        ),
    }


def search_docs(collection, query, season=None, round=None, source_type=None, k=DEFAULT_K):
    """Return the passages closest in meaning to the query, filtered by race and source type"""
    filters = [
        {key: value}
        for key, value in (("season", season), ("round", round), ("source_type", source_type))
        if value is not None
    ]
    where = None if not filters else filters[0] if len(filters) == 1 else {"$and": filters}
    k = max(1, min(int(k or DEFAULT_K), MAX_K))
    found = collection.query(
        query_texts=[query], n_results=k, where=where,
        include=["documents", "metadatas", "distances"],
    )
    passages, seen = [], set()
    rows = zip(found["documents"][0], found["metadatas"][0], found["distances"][0])
    for doc, meta, distance in rows:
        body = doc.split("\n", 1)[1] if "\n" in doc else doc
        if body in seen:
            continue
        seen.add(body)
        passages.append({
            "season": meta.get("season"), "round": meta.get("round"),
            "title": meta.get("title"), "section": meta.get("section"),
            "source_name": meta.get("source_name"), "url": meta.get("url"),
            "published_at": meta.get("published_at"), "distance": round_(distance),
            "text": body,
        })
    result = {"passages": passages, "count": len(passages)}
    if not passages:
        result["message"] = "No passages match these filters."
    return result


def round_(value):
    """Round a distance to three decimals for compact tool output"""
    return round(float(value), 3)
