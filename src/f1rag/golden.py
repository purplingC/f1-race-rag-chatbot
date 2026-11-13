"""Golden question set: load the questions, work out the true answers and score each answer"""

import math
import numbers
import re
import tomllib
import unicodedata
from dataclasses import dataclass, field

import pandas as pd

from f1rag.engine.llm import RETRY_WAITS_S

CATEGORIES = ("statistical", "narrative", "hybrid", "trap")
TOOLS = {"find_race", "run_sql", "search_docs", "get_data_coverage"}
# Routing is judged on the tools that fetch facts; find_race and coverage checks are steps
DATA_TOOLS = {"run_sql", "search_docs"}
# Widgets that show every driver of the race when the model names none
FIELD_WIDGETS = {"classification", "tyre_strategy", "pit_stops_table"}
FIELDS = {"id", "category", "question", "race", "tools", "truth", "ordered", "keywords",
          "forbid", "widgets", "dashboard_drivers"}
NUMBER_WORDS = {
    word: value for value, word in enumerate(
        "zero one two three four five six seven eight nine ten eleven twelve".split()
    )
}
# Finishing places are often written as words, as in "finished third"
NUMBER_WORDS |= {
    word: value for value, word in enumerate(
        "first second third fourth fifth sixth seventh eighth ninth tenth".split(), start=1
    )
}
CITATIONS = re.compile(r"\[E\d+(?:\s*,\s*E\d+)*\]")
# A lap time such as 1:32.456, or a plain number such as 23.4
NUMBER = re.compile(r"(?<![\w.])(?:(\d+):(\d{2}(?:\.\d+)?)|(\d+(?:\.\d+)?))(?![\d:])")
# A list number such as "1." at the start of a line or after a colon or citation
LIST_MARKER = re.compile(r"(?:^|(?<=[:\]]))\s*\d{1,2}\.(?=\s)", re.M)
WORD_NUMBER = re.compile(r"\b(" + "|".join(NUMBER_WORDS) + r")\b")
# Targets from Section 9.3 of the technical documentation
TARGETS = {
    "Numeric accuracy": 0.90, "Hybrid answers": 0.80, "Routing accuracy": 0.85,
    "Dashboard relevance": 0.85, "Evidence linkage": 1.0, "Refusal correctness": 1.0,
}
LATENCY_TARGETS = {"Latency p50": (0.50, 10.0), "Latency p95": (0.95, 20.0)}
# The same figures without the pauses forced by the free tier's rate limit
QUIET_LATENCY = {"Latency p50 without waits": (0.50, 10.0),
                 "Latency p95 without waits": (0.95, 20.0)}


class GoldenError(Exception):
    """Raised when the golden file is broken, with a message naming the problem"""


@dataclass
class Question:
    """One golden question with what a correct answer and dashboard must contain"""

    id: str
    category: str
    question: str
    race: tuple = None
    tools: list = None
    truth: list = field(default_factory=list)
    ordered: bool = False
    keywords: list = field(default_factory=list)
    forbid: list = field(default_factory=list)
    widgets: list = field(default_factory=list)
    dashboard_drivers: bool = False


@dataclass
class Score:
    """The result of every check on one answer; a check that does not apply is None"""

    question: Question
    bundle: object
    expected: list
    answer: list
    routing: list
    sources: list
    dashboard: list
    unsupported: list
    seconds: float
    waits: float = 0.0

    @property
    def checks(self):
        """Return the checks that apply, by name"""
        named = {"answer": self.answer, "routing": self.routing, "sources": self.sources,
                 "dashboard": self.dashboard}
        return {name: problems for name, problems in named.items() if problems is not None}

    @property
    def passed(self):
        """Tell whether every check that applies passed"""
        return all(not problems for problems in self.checks.values())


def load(path):
    """Read the golden file and return its questions and the name aliases"""
    with open(path, "rb") as handle:
        try:
            data = tomllib.load(handle)
        except tomllib.TOMLDecodeError as error:
            raise GoldenError(f"{path} is not valid TOML: {error}") from error
    groups = data.get("groups", {})
    questions, seen = [], set()
    for raw in data.get("question", []):
        q = make_question(raw, groups)
        if q.id in seen:
            raise GoldenError(f"question {q.id} is listed twice")
        seen.add(q.id)
        questions.append(q)
    return questions, data.get("aliases", {})


def make_question(raw, groups):
    """Check one question's fields and return it as a Question"""
    name = raw.get("id", "?")
    unknown = set(raw) - FIELDS
    if unknown:
        raise GoldenError(f"question {name} has unknown fields: {', '.join(sorted(unknown))}")
    for needed in ("id", "category", "question"):
        if needed not in raw:
            raise GoldenError(f"question {name} has no {needed}")
    if raw["category"] not in CATEGORIES:
        raise GoldenError(f"question {name} has an unknown category {raw['category']!r}")
    if raw["category"] in ("statistical", "hybrid") and not raw.get("truth"):
        raise GoldenError(f"question {name} needs a truth query")
    tools = raw.get("tools")
    if tools is not None and set(tools) - TOOLS:
        raise GoldenError(f"question {name} names unknown tools: {set(tools) - TOOLS}")
    truth = raw.get("truth", [])
    return Question(
        id=raw["id"], category=raw["category"], question=raw["question"],
        race=tuple(raw["race"]) if raw.get("race") else None, tools=tools,
        truth=[truth] if isinstance(truth, str) else list(truth),
        ordered=raw.get("ordered", False),
        keywords=[expand(item, groups, name) for item in raw.get("keywords", [])],
        forbid=list(raw.get("forbid", [])),
        widgets=[[w] if isinstance(w, str) else list(w) for w in raw.get("widgets", [])],
        dashboard_drivers=raw.get("dashboard_drivers", False),
    )


def expand(item, groups, name):
    """Turn one keyword entry into a list of alternatives, filling in @group references"""
    if isinstance(item, str) and item.startswith("@"):
        if item[1:] not in groups:
            raise GoldenError(f"question {name} uses an unknown group {item}")
        return list(groups[item[1:]])
    return [item] if isinstance(item, str) else list(item)


def fold(text):
    """Remove accents so Pérez and Perez read the same, keeping the text's length"""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(c for c in decomposed if not unicodedata.combining(c)).replace("’", "'")


def plain(text):
    """Remove accents and lower-case the text"""
    return fold(text).lower()


def driver_names(conn):
    """Map each driver code to the surnames the answer may use for that driver"""
    names = {}
    for code, full_name in conn.execute("SELECT DISTINCT driver_code, full_name FROM entries"):
        words = (full_name or "").split()
        # OpenF1 writes the surname in capitals, as in Lando NORRIS
        surname = " ".join(w for w in words if w.isupper()) or (words[-1] if words else code)
        names.setdefault(code, set()).add(plain(surname))
    return names


def numbers_in(text, words=True):
    """List the numbers in a text as (position, value, decimals), lap times in seconds"""
    text = without_citations(text)
    found = [(match.start(), *parse_number(match)) for match in NUMBER.finditer(text)]
    if words:
        for match in WORD_NUMBER.finditer(text.lower()):
            found.append((match.start(), NUMBER_WORDS[match.group(1)], 0))
    return sorted(found)


def without_citations(text):
    """Blank out citation ids such as [E1, E2], keeping the text's length"""
    return CITATIONS.sub(lambda m: " " * len(m.group()), text or "")


def parse_number(match):
    """Return the value and decimals of one number match, a lap time in seconds"""
    minutes, seconds, number = match.groups()
    digits = seconds if minutes else number
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    value = int(minutes) * 60 + float(seconds) if minutes else float(number)
    return round(value, decimals), decimals


def number_matches(truth, value, decimals):
    """Tell whether an answer's number is the true value rounded to the answer's precision"""
    truth = float(truth)
    if decimals == 0 and not truth.is_integer():
        return False
    return abs(value - truth) <= 0.5 * 10 ** -decimals + 1e-9


def find_value(value, answer, names, aliases):
    """Return where the answer first states a true value, or None if it does not"""
    if isinstance(value, numbers.Number) and not isinstance(value, bool):
        spots = [at for at, number, decimals in numbers_in(answer)
                 if number_matches(value, number, decimals)]
        return min(spots) if spots else None
    value = str(value)
    spots = []
    if value in names:
        # A driver code counts only in capitals; a surname in any case
        spots += [m.start() for m in re.finditer(rf"\b{re.escape(value)}\b", fold(answer))]
        alternatives = names[value]
    else:
        alternatives = [value, *aliases.get(value, [])]
    lowered = plain(answer)
    for alternative in alternatives:
        spots += [m.start() for m in re.finditer(rf"\b{re.escape(plain(alternative))}", lowered)]
    return min(spots) if spots else None


def truth_rows(conn, q):
    """Run the question's truth queries with pandas and return all rows and any problems"""
    rows, problems = [], []
    for sql in q.truth:
        try:
            frame = pd.read_sql_query(sql, conn)
        except Exception as error:  # noqa: BLE001 - any failure is a golden-set problem
            problems.append(f"truth query failed ({error}): {sql}")
            continue
        if frame.empty:
            problems.append(f"truth query returned no rows: {sql}")
        rows += [[cell.item() if hasattr(cell, "item") else cell for cell in row]
                 for row in frame.itertuples(index=False)]
    return rows, problems


def check_answer(q, bundle, rows, names, aliases):
    """List what the answer gets wrong: missing true values, order, keywords or banned words"""
    if bundle.error:
        return [f"no answer: {bundle.error}"]
    text = bundle.answer_md
    problems = []
    values = [cell for row in rows for cell in row if not is_missing(cell)]
    for value in dict.fromkeys(values):
        if find_value(value, text, names, aliases) is None:
            problems.append(f"missing {value}")
    if q.ordered:
        # Order is judged on the values the answer does name; missing ones are listed above
        spots = [find_value(row[0], text, names, aliases) for row in rows]
        spots = [spot for spot in spots if spot is not None]
        if spots != sorted(spots):
            problems.append("wrong order")
    lowered = plain(text)
    for group in q.keywords:
        if not any(plain(word) in lowered for word in group):
            problems.append("missing one of: " + " / ".join(group))
    problems += [f"says {word}" for word in q.forbid if plain(word) in lowered]
    return problems


def is_missing(cell):
    """Tell whether a truth cell is empty"""
    return cell is None or (isinstance(cell, float) and math.isnan(cell))


def check_routing(q, bundle):
    """List the expected tools not called and the data tools called without being expected"""
    if q.tools is None:
        return None
    called = {step["tool"] for step in bundle.trace}
    problems = [f"did not call {tool}" for tool in sorted(set(q.tools) - called)]
    problems += [f"also called {tool}" for tool in sorted((called & DATA_TOOLS) - set(q.tools))]
    return problems


def check_sources(q, bundle):
    """List the expected data tools with no cited, working result about the question's race"""
    if q.tools is None or not set(q.tools) & DATA_TOOLS:
        return None
    cited = [item for item in bundle.evidence
             if item.id in bundle.cited and item.result.get("ok") is not False]
    problems = []
    for tool in sorted(set(q.tools) & DATA_TOOLS):
        items = [item for item in cited if item.tool == tool]
        if not items:
            problems.append(f"no cited {tool} result")
        elif tool == "search_docs" and q.race:
            about = [p for item in items for p in item.result.get("passages", [])
                     if (p.get("season"), p.get("round")) == tuple(q.race)]
            if not about:
                problems.append(f"no cited passage about {q.race[0]} round {q.race[1]}")
    return problems


def check_dashboard(q, bundle, rows, names):
    """List what the dashboard lacks: the right race, a widget of each group, the true drivers"""
    if not (q.race or q.widgets or q.dashboard_drivers):
        return None
    plan = bundle.dashboard
    if plan is None:
        return ["no dashboard"]
    spec, problems = plan.spec, []
    if q.race and (spec.race.season, spec.race.round) != tuple(q.race):
        problems.append(f"race {spec.race.season} round {spec.race.round}, "
                        f"expected {q.race[0]} round {q.race[1]}")
    types = {widget.type for widget in spec.widgets}
    problems += ["no widget of: " + " / ".join(group)
                 for group in q.widgets if not types & set(group)]
    if q.dashboard_drivers:
        whole_field = any(w.type in FIELD_WIDGETS and not w.drivers for w in spec.widgets)
        shown = {code for widget in spec.widgets for code in widget.drivers}
        codes = [cell for row in rows for cell in row if isinstance(cell, str) and cell in names]
        problems += [f"{code} not shown" for code in dict.fromkeys(codes)
                     if code not in shown and not whole_field]
    return problems


def evidence_numbers(value):
    """Collect every number in a tool result, including numbers written inside its text"""
    if isinstance(value, bool):
        return []
    if isinstance(value, numbers.Number):
        return [float(value)]
    if isinstance(value, str):
        return [number for _, number, _ in numbers_in(value, words=False)]
    if isinstance(value, dict):
        value = list(value.values())
    if isinstance(value, (list, tuple)):
        return [number for item in value for number in evidence_numbers(item)]
    return []


def unsupported_numbers(bundle):
    """List the numbers in the answer that appear in no tool result, for a person to review"""
    # Numbers the user wrote, such as a year in the question, need no source
    known = evidence_numbers([item.result for item in bundle.evidence])
    known += [number for _, number, _ in numbers_in(bundle.question, words=False)]
    text = LIST_MARKER.sub(lambda m: " " * len(m.group()), bundle.answer_md or "")
    text = without_citations(text)
    unsupported = []
    for match in NUMBER.finditer(text):
        value, decimals = parse_number(match)
        if not any(number_matches(number, value, decimals) for number in known):
            unsupported.append(match.group())
    return unsupported


def wait_seconds(bundle):
    """Add up the seconds the answer spent waiting on Gemini's rate limit before retrying"""
    return sum(sum(RETRY_WAITS_S[:call.get("retries", 0)]) for call in bundle.model_calls)


def score(q, bundle, conn, names, aliases):
    """Run every check on one answer and return its Score"""
    rows, problems = truth_rows(conn, q) if q.truth else ([], [])
    answer = problems or check_answer(q, bundle, rows, names, aliases)
    return Score(
        question=q, bundle=bundle, expected=rows, answer=answer,
        routing=check_routing(q, bundle), sources=check_sources(q, bundle),
        dashboard=check_dashboard(q, bundle, rows, names),
        unsupported=unsupported_numbers(bundle), seconds=round(bundle.total_ms / 1000, 1),
        waits=wait_seconds(bundle),
    )


def share_row(metric, passed, total, target=None):
    """Describe one metric as passed out of total, with its target and whether it was met"""
    if not total:
        return {"metric": metric, "result": "not run", "target": target_text(target), "met": None}
    ratio = passed / total
    return {"metric": metric, "result": f"{passed}/{total} ({ratio:.0%})",
            "target": target_text(target), "met": None if target is None else ratio >= target}


def target_text(target):
    """Write a target share such as 0.9 as ≥ 90%"""
    if target is None:
        return "-"
    return "100%" if target == 1.0 else f"≥ {target:.0%}"


def percentile(values, share):
    """Return the nearest-rank percentile of a list of numbers"""
    ordered = sorted(values)
    return ordered[max(0, math.ceil(share * len(ordered)) - 1)]


def metrics(scores):
    """Work out the Section 9.3 metrics for a run and compare each with its target"""
    def of(category):
        """Return the scores of one category"""
        return [s for s in scores if s.question.category == category]

    def answered(group, with_sources=False):
        """Count the scores whose answer check passed, and their sources too if asked"""
        return sum(not s.answer and not (with_sources and s.sources) for s in group)

    routed = [s for s in scores if s.routing is not None]
    boards = [s for s in scores if s.dashboard is not None]
    cited = [(item_id, s.bundle) for s in scores for item_id in s.bundle.cited]
    linked = sum(any(item.id == item_id for item in bundle.evidence) for item_id, bundle in cited)
    rows = [
        share_row("Numeric accuracy", answered(of("statistical")), len(of("statistical")),
                  TARGETS["Numeric accuracy"]),
        share_row("Narrative answers", answered(of("narrative"), True), len(of("narrative"))),
        share_row("Hybrid answers", answered(of("hybrid"), True), len(of("hybrid")),
                  TARGETS["Hybrid answers"]),
        share_row("Routing accuracy", sum(not s.routing for s in routed), len(routed),
                  TARGETS["Routing accuracy"]),
        share_row("Dashboard relevance", sum(not s.dashboard for s in boards), len(boards),
                  TARGETS["Dashboard relevance"]),
        share_row("Evidence linkage", linked, len(cited), TARGETS["Evidence linkage"]),
        share_row("Refusal correctness", answered(of("trap")), len(of("trap")),
                  TARGETS["Refusal correctness"]),
        {**share_row("Groundedness (automatic)", sum(not s.unsupported for s in scores),
                     len(scores)), "target": "100% after review", "met": None},
    ]
    rows += latency_rows([s.seconds for s in scores], LATENCY_TARGETS)
    rows += latency_rows([max(0.0, s.seconds - s.waits) for s in scores], QUIET_LATENCY)
    return rows


def latency_rows(seconds, targets):
    """Describe the latency percentiles of a run against their targets"""
    rows = []
    for metric, (share, limit) in targets.items():
        if not seconds:
            rows.append({"metric": metric, "result": "not run", "target": f"≤ {limit:.0f} s",
                         "met": None})
            continue
        value = percentile(seconds, share)
        rows.append({"metric": metric, "result": f"{value:.1f} s", "target": f"≤ {limit:.0f} s",
                     "met": value <= limit})
    return rows
