"""Command line Q&A: python -m f1rag.ask "question" prints the answer, its evidence and the trace"""

import argparse
import contextlib
import sys
from pathlib import Path

from f1rag import config, vectors
from f1rag.engine import orchestrator, sql_guard
from f1rag.engine.llm import GeminiLLM, LLMUnavailableError

# How each kind of Gemini call is described in the trace
PURPOSES = {"tools": "choosing tools", "final": "writing the answer"}
TOOL_WORDS = {
    "find_race": ("race lookup", "race lookups"),
    "run_sql": ("query", "queries"),
    "search_docs": ("search", "searches"),
    "get_data_coverage": ("coverage check", "coverage checks"),
}


def parse_args(argv=None):
    """Read the command line arguments"""
    parser = argparse.ArgumentParser(
        prog="python -m f1rag.ask", description="Ask a question about the ingested F1 races"
    )
    parser.add_argument("question", help="the question, in quotes")
    parser.add_argument("--trace", action="store_true", help="also show every tool call in full")
    parser.add_argument(
        "--db", default=str(config.DB_PATH), help="database file, default data/f1_stats.db"
    )
    parser.add_argument(
        "--chroma", default=str(config.CHROMA_DIR), help="vector store folder, default data/chroma"
    )
    return parser.parse_args(argv)


def how_line(trace):
    """Summarise the tool calls in one line, for example run_sql (1 query)"""
    counts = {}
    for step in trace:
        counts[step["tool"]] = counts.get(step["tool"], 0) + 1
    if not counts:
        return "How I answered: no tools were called"
    parts = []
    for tool, n in counts.items():
        one, many = TOOL_WORDS.get(tool, ("call", "calls"))
        parts.append(f"{tool} ({n} {one if n == 1 else many})")
    return "How I answered: " + " · ".join(parts)


def evidence_lines(item):
    """Describe one evidence item in a few indented lines"""
    lines = [f"{item.id}  {item.tool}: {item.summary}"]
    result = item.result
    if item.tool == "run_sql":
        lines.append(f"    {result.get('query', '')}")
        if result.get("error"):
            lines.append(f"    error: {result['error']}")
    elif item.tool == "search_docs":
        # A long section is stored as several overlapping chunks, so show it once with a count
        groups = {}
        for p in result.get("passages", []):
            groups.setdefault((p.get("title"), p.get("section")), []).append(p)
        for (title, section), passages in groups.items():
            p = passages[0]
            count = f" ({len(passages)} passages)" if len(passages) > 1 else ""
            date = (p.get("published_at") or "")[:10]
            lines.append(
                f"    {title} > {section}{count}, {p.get('source_name')}, {date}, {p.get('url')}"
            )
    elif item.tool == "find_race":
        for m in result.get("matches", []):
            race = f"{m['season']} round {m['round']}: {m['event_name']}"
            lines.append(f"    {race}, {m['race_date']}")
    return lines


def render(bundle, show_trace=False):
    """Turn an answer bundle into the text printed in the terminal"""
    lines = [bundle.answer_md, ""]
    if bundle.uncited:
        n = len(bundle.uncited)
        verb = "sentence has" if n == 1 else "sentences have"
        lines.append(f"Warning: {n} {verb} no citation:")
        lines += [f"  - {sentence}" for sentence in bundle.uncited]
        lines.append("")
    lines.append(how_line(bundle.trace))
    if bundle.evidence:
        lines += ["", "Evidence:"]
        for item in bundle.evidence:
            mark = "" if item.id in bundle.cited else "  (not cited)"
            first, *rest = evidence_lines(item)
            lines += [f"  {first}{mark}", *(f"  {line}" for line in rest)]
    if show_trace:
        seconds = bundle.total_ms / 1000
        lines += [
            "",
            f"Trace ({len(bundle.model_calls)} model calls, model {bundle.model}, "
            f"{seconds:.1f} s in total):",
        ]
        lines += trace_lines(bundle)
    return "\n".join(lines)


def trace_lines(bundle):
    """List each Gemini call with its time and retries, and under it the tools it asked for"""
    lines = []
    for call in bundle.model_calls:
        what = PURPOSES.get(call["purpose"], call["purpose"])
        retries = call["retries"]
        retry_text = f", {retries} retr{'y' if retries == 1 else 'ies'}" if retries else ""
        lines.append(f"  {call['step']}. Gemini, {what}: {call['ms'] / 1000:.1f} s{retry_text}")
        for step in bundle.trace:
            if step["step"] != call["step"] or call["purpose"] != "tools":
                continue
            status = "ok" if step["ok"] else f"error: {step['error']}"
            lines.append(
                f"       {step['tool']}({step['args']}) -> {step['evidence']}, "
                f"{status}, {step['ms']} ms"
            )
    return lines


def say_wait(notice):
    """Show a Gemini wait notice on the error stream, so it stays apart from the answer"""
    print(notice, file=sys.stderr)


def main(argv=None, llm=None, out=print):
    """Answer one question from the command line and return the exit code"""
    args = parse_args(argv)
    db_path, chroma_path = Path(args.db), Path(args.chroma)
    # Checked first because opening a missing store would create it, and asking only reads
    for path, what in ((db_path, "database"), (chroma_path, "vector store")):
        if not path.exists():
            out(f"No {what} at {path}. Load races first: python -m f1rag.ingest --year 2026 --all")
            return 1
    try:
        llm = llm or GeminiLLM(on_wait=say_wait)
    except LLMUnavailableError as error:
        out(str(error))
        return 1
    collection = vectors.open_collection(chroma_path)
    with contextlib.closing(sql_guard.connect_read_only(db_path)) as conn:
        bundle = orchestrator.answer(args.question, llm, conn, collection)
    out(render(bundle, show_trace=args.trace))
    return 1 if bundle.error else 0


if __name__ == "__main__":
    sys.exit(main())
