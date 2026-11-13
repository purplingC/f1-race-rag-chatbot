"""Evaluation command: python -m f1rag.eval asks the golden questions and scores the answers"""

import argparse
import contextlib
import sys
import time
from datetime import datetime
from pathlib import Path

from f1rag import config, golden, vectors
from f1rag.ask import PURPOSES
from f1rag.engine import orchestrator, sql_guard
from f1rag.engine.llm import GeminiLLM, LLMUnavailableError

# Seconds from the start of one question to the next: a question can take 5 or 6 Gemini calls,
# and the free limit is 15 requests a minute (20 s hit the limit in the first real run)
DEFAULT_GAP_S = 35
# Two failed answers in a row usually mean the daily quota is used up, so the run stops
MAX_ERRORS_IN_ROW = 2


def parse_args(argv=None):
    """Read the command line arguments"""
    parser = argparse.ArgumentParser(
        prog="python -m f1rag.eval",
        description="Ask the golden questions and score the answers against the loaded data",
    )
    parser.add_argument("--only", help="question ids to run, comma separated, e.g. S01,N03")
    parser.add_argument("--category", choices=golden.CATEGORIES, help="run one category")
    parser.add_argument("--check", action="store_true",
                        help="only run the truth queries and show the expected values; no Gemini")
    parser.add_argument("--gap", type=float, default=DEFAULT_GAP_S,
                        help=f"seconds between question starts, default {DEFAULT_GAP_S}")
    parser.add_argument("--golden", default=str(config.GOLDEN_PATH), help="golden question file")
    parser.add_argument("--db", default=str(config.DB_PATH), help="database file")
    parser.add_argument("--chroma", default=str(config.CHROMA_DIR), help="vector store folder")
    parser.add_argument("--out", default=str(config.EVAL_DIR), help="folder for the report")
    return parser.parse_args(argv)


def select(questions, only=None, category=None):
    """Keep the questions asked for by id or category, in file order"""
    wanted = {part.strip().upper() for part in only.split(",")} if only else None
    return [q for q in questions
            if (wanted is None or q.id.upper() in wanted)
            and (category is None or q.category == category)]


def check_truth(conn, questions, out):
    """Run every truth query, show the expected values and return the number of problems"""
    problems = 0
    for q in questions:
        if not q.truth:
            continue
        rows, found = golden.truth_rows(conn, q)
        problems += len(found)
        shown = "; ".join(", ".join(str(cell) for cell in row) for row in rows)
        out(f"{q.id}  expected: {shown}" if not found else f"{q.id}  problem: {found[0]}")
    return problems


def result_line(score):
    """Describe one scored answer in a single line"""
    q = score.question
    verdict = "pass" if score.passed else "FAIL"
    problems = [f"{name}: {', '.join(found)}" for name, found in score.checks.items() if found]
    tail = f"  {'; '.join(problems)}" if problems else ""
    return f"{q.id:<4} {q.category:<12} {verdict:<5} {score.seconds:5.1f} s{tail}"


def metrics_lines(rows):
    """Lay the metrics out as an aligned table"""
    lines = [f"{'Metric':<26}{'Result':<16}{'Target':<20}Met"]
    for row in rows:
        met = {True: "yes", False: "NO", None: "-"}[row["met"]]
        lines.append(f"{row['metric']:<26}{row['result']:<16}{row['target']:<20}{met}")
    return lines


def report_md(scores, rows, model, started):
    """Write the run as Markdown: metrics, a line per question, then each answer for review"""
    lines = [
        f"# Evaluation run {started:%Y-%m-%d %H:%M}", "",
        f"Model: {model}. Questions: {len(scores)}. "
        f"Passed: {sum(s.passed for s in scores)}.", "",
        "| Metric | Result | Target | Met |", "|---|---|---|---|",
    ]
    for row in rows:
        met = {True: "yes", False: "no", None: "-"}[row["met"]]
        lines.append(f"| {row['metric']} | {row['result']} | {row['target']} | {met} |")
    lines += ["", "| Id | Category | Result | Seconds | Problems |", "|---|---|---|---|---|"]
    for s in scores:
        problems = "; ".join(f"{name}: {', '.join(found)}"
                             for name, found in s.checks.items() if found)
        verdict = "pass" if s.passed else "fail"
        lines.append(f"| {s.question.id} | {s.question.category} | {verdict} | {s.seconds} | "
                     f"{problems.replace('|', '/')} |")
    lines += ["", "## Answers for review", "",
              "Check that every number below comes from the evidence listed under it."]
    for s in scores:
        lines += ["", f"### {s.question.id}: {s.question.question}", ""]
        if s.expected:
            expected = "; ".join(", ".join(str(c) for c in row) for row in s.expected)
            lines += [f"Expected values: {expected}", ""]
        lines += [s.bundle.error or s.bundle.answer_md, ""]
        tools = ", ".join(step["tool"] for step in s.bundle.trace) or "none"
        lines.append(f"Tools: {tools}")
        if s.bundle.model_calls:
            lines.append("Gemini calls: " + ", ".join(call_text(c) for c in s.bundle.model_calls))
        if s.unsupported:
            lines.append(f"Numbers not found in tool output: {', '.join(s.unsupported)}")
        for item in s.bundle.evidence:
            mark = "" if item.id in s.bundle.cited else " (not cited)"
            detail = f": `{item.result.get('query', '')}`" if item.tool == "run_sql" else ""
            lines.append(f"- {item.id} {item.summary}{mark}{detail}")
    return "\n".join(lines) + "\n"


def call_text(call):
    """Describe one Gemini call with its time, purpose and retries, as in 2.4 s choosing tools"""
    text = f"{call['ms'] / 1000:.1f} s {PURPOSES.get(call['purpose'], call['purpose'])}"
    if call.get("retries"):
        text += f" ({call['retries']} retr{'y' if call['retries'] == 1 else 'ies'})"
    return text + (" (failed)" if call.get("failed") else "")


def say_wait(notice):
    """Show a Gemini wait notice on the error stream, so it stays apart from the results"""
    print(notice, file=sys.stderr)


def main(argv=None, llm=None, out=print, sleep=time.sleep, now=datetime.now):
    """Run the golden questions, print each result and the metrics, and save a report"""
    args = parse_args(argv)
    try:
        questions, aliases = golden.load(Path(args.golden))
    except (OSError, golden.GoldenError) as error:
        out(f"Cannot read the golden set: {error}")
        return 1
    questions = select(questions, args.only, args.category)
    if not questions:
        out("No questions match --only or --category.")
        return 1
    db_path, chroma_path = Path(args.db), Path(args.chroma)
    # Checked first because opening a missing store would create it, and the eval only reads
    for path, what in ((db_path, "database"), (chroma_path, "vector store")):
        if not path.exists():
            out(f"No {what} at {path}. Load races first: python -m f1rag.ingest --year 2026 --all")
            return 1
    with contextlib.closing(sql_guard.connect_read_only(db_path)) as conn:
        # The truth queries are checked before any Gemini call, so a broken set costs no quota
        problems = check_truth(conn, questions, out if args.check else lambda line: None)
        if problems or args.check:
            if problems:
                out(f"{problems} truth problem(s); fix eval/golden.toml before running.")
            return 1 if problems else 0
        try:
            llm = llm or GeminiLLM(on_wait=say_wait)
        except LLMUnavailableError as error:
            out(str(error))
            return 1
        scores = run(questions, llm, conn, vectors.open_collection(chroma_path), aliases,
                     args.gap, out, sleep)
    started = now()
    rows = golden.metrics(scores)
    out("")
    for line in metrics_lines(rows):
        out(line)
    report = Path(args.out) / f"eval-{started:%Y-%m-%d-%H%M}.md"
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(report_md(scores, rows, getattr(llm, "model", ""), started),
                      encoding="utf-8")
    out(f"\nReport with every answer for review: {report}")
    stopped = len(scores) < len(questions)
    if stopped:
        out(f"Stopped after {len(scores)} of {len(questions)} questions: Gemini kept failing.")
    return 1 if stopped else 0


def run(questions, llm, conn, collection, aliases, gap, out, sleep):
    """Ask each question in turn, pausing between them, and return the scores"""
    names = golden.driver_names(conn)
    scores, errors_in_row, last_start = [], 0, None
    out(f"Asking {len(questions)} questions, about {gap:.0f} s apart.")
    for q in questions:
        if last_start is not None:
            sleep(max(0.0, gap - (time.monotonic() - last_start)))
        last_start = time.monotonic()
        bundle = orchestrator.answer(q.question, llm, conn, collection)
        score = golden.score(q, bundle, conn, names, aliases)
        scores.append(score)
        out(result_line(score))
        errors_in_row = errors_in_row + 1 if bundle.error else 0
        if errors_in_row >= MAX_ERRORS_IN_ROW:
            break
    return scores


if __name__ == "__main__":
    sys.exit(main())
