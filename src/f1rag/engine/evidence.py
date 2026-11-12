"""Evidence items (E1, E2, ...) for every tool result, citation checks and the answer bundle"""

import re
from dataclasses import dataclass, field

CITATION_GROUP = re.compile(r"\s*\[(E\d+(?:\s*,\s*E\d+)*)\]")
CITED = re.compile(r"\[E\d+")
SENTENCE_END = re.compile(r"(?<=[.!?])\s+")
# Sentences that state no fact, so they need no citation
NO_FACT = ("i don't have that",)
# A bold label at the start of a list item, such as **Race start:**
LABEL = re.compile(r"^\*\*[^*]+:\*\*\s*")


@dataclass
class EvidenceItem:
    """One tool call and its result, with the id the answer cites"""

    id: str
    tool: str
    args: dict
    result: dict
    summary: str


@dataclass
class AnswerBundle:
    """The answer, the evidence it rests on and the trace of how it was made"""

    question: str
    answer_md: str
    evidence: list = field(default_factory=list)
    cited: list = field(default_factory=list)
    trace: list = field(default_factory=list)
    model: str = ""
    error: str = None
    # A note shown with an answer that is usable but not complete, such as a draft
    warning: str = None
    model_calls: list = field(default_factory=list)
    total_ms: int = 0
    uncited: list = field(default_factory=list)
    # The checked dashboard plan (dashboard_planner.PlanResult), or None for no new widgets
    dashboard: object = None


def summarize(tool, result):
    """Describe a tool result in a few words for the evidence list"""
    if tool == "run_sql":
        if not result.get("ok"):
            return "SQL error"
        n = result.get("row_count", 0)
        more = ", cut at the row cap" if result.get("truncated") else ""
        return f"SQL result ({n} row{'' if n == 1 else 's'}{more})"
    if tool == "search_docs":
        passages = result.get("passages", [])
        if not passages:
            return "no passages"
        titles = sorted({p.get("title") or "untitled" for p in passages})
        n = len(passages)
        return f"{n} passage{'' if n == 1 else 's'}: {'; '.join(titles)}"
    if tool == "find_race":
        n = len(result.get("matches", []))
        return f"{n} matching race{'' if n == 1 else 's'}"
    if tool == "get_data_coverage":
        return f"coverage list ({len(result.get('races', []))} races)"
    return tool


class EvidenceLog:
    """Collects evidence items in order and checks the answer's citations against them"""

    def __init__(self):
        """Start with no evidence"""
        self.items = []

    def add(self, tool, args, result):
        """Store one tool result as the next evidence item and return its id"""
        item_id = f"E{len(self.items) + 1}"
        self.items.append(EvidenceItem(item_id, tool, dict(args), result, summarize(tool, result)))
        return item_id

    def get(self, item_id):
        """Return the evidence item with this id, or None"""
        return next((item for item in self.items if item.id == item_id), None)

    def validate_citations(self, text):
        """Drop citations to ids that do not exist and return the text with the valid ids cited"""
        cited = []

        def keep_valid(match):
            """Rebuild one citation group with only the ids that exist"""
            valid = [i.strip() for i in match.group(1).split(",") if self.get(i.strip())]
            for item_id in valid:
                if item_id not in cited:
                    cited.append(item_id)
            return f" [{', '.join(valid)}]" if valid else ""

        return CITATION_GROUP.sub(keep_valid, text), cited

    def uncited_sentences(self, text):
        """List the answer's sentences that state something but cite no evidence"""
        found = []
        for line in text.split("\n"):
            line = LABEL.sub("", line.strip().lstrip("-#> ").strip())
            # Headings such as **What happened:** and table rows are not sentences
            if not line or line.endswith(":") or line.endswith(":**") or line.startswith("|"):
                continue
            for sentence in SENTENCE_END.split(line):
                bare = sentence.strip("*_() ")
                if not bare or CITED.search(sentence) or bare.lower().startswith(NO_FACT):
                    continue
                found.append(bare)
        return found
