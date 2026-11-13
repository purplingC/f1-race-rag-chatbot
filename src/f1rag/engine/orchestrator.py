"""Question loop: the model calls tools, code runs them as evidence, then a final JSON answer"""

import time

from f1rag.engine import dashboard_planner, prompts, tools
from f1rag.engine.evidence import AnswerBundle, EvidenceLog
from f1rag.engine.llm import ATTEMPTS, LLMUnavailableError, parse_json

# Most model turns with tool calls before the final answer is forced
MAX_STEPS = 8
# One failed query may be fixed by the model; after the second failure run_sql is refused
MAX_SQL_ERRORS = 2
NO_DATA = "I don't have that in the ingested data."
DRAFT_NOTE = (
    "Gemini was busy for the final step, so this is its draft answer "
    "(citations checked, dashboard chosen by rules)"
)
# Shown when the model answered without looking anything up: facts must come from tools,
# so such an answer is replaced, whatever the model wrote (off-topic questions, greetings)
OFF_TOPIC = (
    "I can only answer questions about the Formula 1 races loaded here. Try asking who won a "
    "race, how a driver's race went, or how the tyre strategies compared."
)
# What the user sees while each tool runs
STEP_WORDS = {
    "find_race": "Finding the race",
    "run_sql": "Looking up the race data",
    "search_docs": "Reading the race reports",
    "get_data_coverage": "Checking which races are loaded",
}
FINAL_STEP = "Writing the answer"
# Arguments each tool accepts, read from the descriptions given to the model
ALLOWED_ARGS = {t["name"]: set(t["parameters"]["properties"]) for t in tools.DECLARATIONS}


def run_tool(name, args, conn, collection):
    """Run one tool by name and return its result, or an error the model can read"""
    handlers = {
        "find_race": lambda a: tools.find_race(conn, **a),
        "run_sql": lambda a: tools.run_sql(conn, **a),
        "search_docs": lambda a: tools.search_docs(collection, **a),
        "get_data_coverage": lambda a: tools.get_data_coverage(conn),
    }
    if name not in handlers:
        return {"ok": False, "error": f"Unknown tool {name}"}
    # A made-up argument is dropped and reported, so the call still runs without a retry
    ignored = sorted(set(args) - ALLOWED_ARGS[name])
    known = {key: value for key, value in args.items() if key not in ignored}
    try:
        result = handlers[name](known)
    except TypeError as error:
        return {"ok": False, "error": f"Wrong arguments for {name}: {error}"}
    if ignored:
        result["ignored_arguments"] = ignored
    return result


def tool_failed(result):
    """Tell whether a tool result reports an error"""
    return result.get("ok") is False


class Session:
    """State of one question: the conversation, the evidence and the trace"""

    def __init__(self, llm, conn, collection):
        """Start with no evidence and no trace"""
        self.llm, self.conn, self.collection = llm, conn, collection
        self.evidence = EvidenceLog()
        self.trace = []
        self.sql_errors = 0
        self.model_calls = []

    def generate(self, contents, purpose, **kwargs):
        """Call the model once and record how long it took and how often it retried"""
        started = time.monotonic()
        try:
            reply = self.llm.generate(contents, prompts.system_prompt(), **kwargs)
        except LLMUnavailableError:
            # A call that gave up still shows in the trace, with the time it cost
            self.model_calls.append({
                "step": len(self.model_calls) + 1, "purpose": purpose,
                "ms": round((time.monotonic() - started) * 1000),
                "retries": ATTEMPTS - 1, "failed": True,
            })
            raise
        self.model_calls.append({
            "step": len(self.model_calls) + 1, "purpose": purpose,
            "ms": getattr(reply, "ms", 0), "retries": getattr(reply, "retries", 0),
        })
        return reply

    def handle(self, step, call):
        """Run one tool call, store it as evidence and return the result the model will see"""
        started = time.monotonic()
        if call.name == "run_sql" and self.sql_errors >= MAX_SQL_ERRORS:
            result = {
                "ok": False,
                "query": call.args.get("query", ""),
                "error": "The query failed twice. Do not retry: tell the user it failed.",
            }
        else:
            result = run_tool(call.name, call.args, self.conn, self.collection)
        if call.name == "run_sql" and tool_failed(result):
            self.sql_errors += 1
        evidence_id = self.evidence.add(call.name, call.args, result)
        item = self.evidence.get(evidence_id)
        self.trace.append({
            "step": step, "tool": call.name, "args": call.args, "evidence": evidence_id,
            "summary": item.summary, "ok": not tool_failed(result),
            "error": result.get("error"), "ms": round((time.monotonic() - started) * 1000),
        })
        return {"evidence_id": evidence_id, **result}


def answer(question, llm, conn, collection, history=None, max_steps=MAX_STEPS, default_race=None,
           on_progress=None):
    """Answer one question with tools and return the answer, evidence, trace and dashboard"""
    started = time.monotonic()
    said = []

    def progress(words):
        """Tell the caller what is happening, once per change, in plain words"""
        if on_progress and (not said or said[-1] != words):
            said.append(words)
            on_progress(words)

    session = Session(llm, conn, collection)
    contents = []
    for role, text in history or []:
        contents.append(llm.user_turn(text) if role == "user" else llm.model_turn(text))
    contents.append(llm.user_turn(question))
    draft = ""
    try:
        for step in range(1, max_steps + 1):
            reply = session.generate(contents, "tools", tools=tools.DECLARATIONS)
            if reply.turn is not None:
                contents.append(reply.turn)
            draft = reply.text or draft
            if not reply.calls:
                break
            results = []
            for call in reply.calls:
                progress(STEP_WORDS.get(call.name, "Looking things up"))
                results.append((call.name, session.handle(step, call)))
            contents.append(llm.tool_results_turn(results))
    except LLMUnavailableError as error:
        return bundle(question, session, llm, str(error), started, error=str(error))
    if not session.evidence.items:
        # Nothing was looked up, so nothing in an answer could be grounded
        return bundle(question, session, llm, OFF_TOPIC, started)
    progress(FINAL_STEP)
    contents.append(llm.user_turn(prompts.FINAL_INSTRUCTION))
    try:
        final = session.generate(contents, "final", json_schema=prompts.ANSWER_SCHEMA)
    except LLMUnavailableError as error:
        if not draft.strip():
            return bundle(question, session, llm, str(error), started, error=str(error))
        # The facts and a cited draft already exist, so only the formatting step is lost
        board = dashboard_planner.plan(question, None, session.evidence, conn, default_race)
        return bundle(question, session, llm, draft.strip(), started, dashboard=board,
                      warning=DRAFT_NOTE)
    parsed = parse_json(final.text) or {}
    text = (parsed.get("answer_md") or draft or NO_DATA).strip()
    # The model only picks widgets; the planner checks them and the widgets fetch the numbers
    board = dashboard_planner.plan(
        question, parsed.get("dashboard"), session.evidence, conn, default_race
    )
    return bundle(question, session, llm, text, started, dashboard=board)


def bundle(question, session, llm, text, started, error=None, dashboard=None, warning=None):
    """Check the citations and pack the answer, evidence and trace into an AnswerBundle"""
    clean, cited = session.evidence.validate_citations(text)
    return AnswerBundle(
        question=question, answer_md=clean, evidence=session.evidence.items, cited=cited,
        trace=session.trace, model=getattr(llm, "model", ""), error=error,
        model_calls=session.model_calls,
        total_ms=round((time.monotonic() - started) * 1000),
        uncited=[] if error else session.evidence.uncited_sentences(clean),
        dashboard=dashboard,
        warning=warning,
    )
