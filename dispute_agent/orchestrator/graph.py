"""The orchestrator graph: which node runs after which. Start reading the code here.

    classify ─┬> account_agent -> derive_states ─┐
              └> policy_agent ───────────────────┴> sync -> synthesize -> review -> create_review_case

LangGraph decides what runs next; the LLM is only called inside the nodes. After classify the two agents fan out in
parallel and meet at sync, a join node. Sync can send each agent back once (docs/plans/orchestrator.md, O2): the
Account Agent when it returned no findings, and the Policy Agent when a different policy set was in force on the
disputed event's date than on the complaint date it first read. The retry paths are separate nodes with plain edges
back into sync; re-entering the fan-out nodes would hit the join barrier and end the run silently. Nothing else is
ever retried.

After synthesize the graph pauses at `review` for a person (docs/plans/human-review.md). A checkpointer saves the
state after every node, so the run can be resumed later, in another process (run.py). The rep approves, overrides
the route, or, on need_more_evidence, answers once: the Account Agent then looks again through its own follow-up
nodes and the run comes back to `review`.

Each node is a few lines that call into the folder named after it: account_agent/, policy_agent/, sync/, review/.
"""

from __future__ import annotations

import os
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from dispute_agent.account_agent.agent import AccountFindings, OtherAccountMatch, initial_state
from dispute_agent.account_agent.derive import DerivedFacts, derive_states
from dispute_agent.config import WORLD_TODAY
from dispute_agent.data import Category, Dataset
from dispute_agent.orchestrator.classify import classify
from dispute_agent.policy_agent.agent import PolicyFindings, RuleReading, run_policy_agent
from dispute_agent.policy_agent.retrieve import PolicySection, RetrievedSection
from dispute_agent.review.create_review_case import CaseResult, create_review_case, find_duplicate
from dispute_agent.review.review import ReviewDecision, build_request, with_answer
from dispute_agent.sync.sync import SyncResult, sync
from dispute_agent.sync.synthesize import Draft, SynthesisResult, run_synthesize

MAX_ATTEMPTS = 2  # each agent runs at most twice: graph shape, not a tunable, so not in config.py
MAX_FOLLOWUPS = 1  # the rep can answer open questions once per complaint
# The checkpoint serializer may load our state models by name; LangGraph warns on (and will block) any other class.
SERDE = JsonPlusSerializer(allowed_msgpack_modules=(
    AccountFindings, OtherAccountMatch, DerivedFacts, PolicyFindings, PolicySection, RetrievedSection, RuleReading,
    SyncResult, SynthesisResult, Draft, CaseResult, ReviewDecision,
))
FAULTS = ("finalize_once", "policy_date_mismatch")
FAULT_POLICY_DATE = datetime(2026, 4, 15)  # LF.v2 was in force; LF.v3 begins 2026-05-01


def _fault() -> str | None:
    """Return the validation-only fault switch, rejecting typos before model work starts."""
    value = os.environ.get("DISPUTE_FAULT") or None
    if value and value not in FAULTS:
        raise ValueError(f"DISPUTE_FAULT must be one of {FAULTS}, got {value!r}")
    return value


class GraphState(TypedDict, total=False):
    """The one object that travels through the graph. Each node adds its own fields."""

    rep_message: str
    account_id: str
    category: Category
    account: dict[str, Any] | None  # the Account Agent's final state: findings, messages, tool_calls_made, budget_hit, tool_errors
    derived: DerivedFacts | None
    policy: PolicyFindings | None
    sync: SyncResult | None
    synthesis: SynthesisResult | None
    case: CaseResult | None
    account_attempts: int
    policy_attempts: int
    thread_label: str  # what `review` shows the rep: the account or scenario ID
    review: ReviewDecision | None  # the rep's last decision
    followups: int  # rep answers used, at most MAX_FOLLOWUPS
    rep_answer: str | None  # the rep's raw answer; rep_message carries it too


def after_sync(s: GraphState) -> Literal["account_agent_retry", "policy_agent_retry", "synthesize"]:
    """The graph's one decision: clarify first, then re-retrieve, each at most once; otherwise go on."""
    if s["sync"].account_missing and s["account_attempts"] < MAX_ATTEMPTS:
        return "account_agent_retry"
    if s["sync"].policy_date_mismatch and s["policy_attempts"] < MAX_ATTEMPTS:
        return "policy_agent_retry"
    return "synthesize"


def after_review(s: GraphState) -> Literal["account_agent_followup", "create_review_case"]:
    """The second decision in the graph: the rep answered, so look again once; otherwise file the case."""
    return "account_agent_followup" if s["review"].action == "answer" else "create_review_case"


def build_graph(
    ds: Dataset,
    sections: dict[str, PolicySection],
    account_agent: Any,
    classify_model: Any = None,
    policy_model: Any = None,
    synthesize_model: Any = None,
    checkpointer: Any = None,
):
    """Compile the orchestrator. The model arguments default to the real models; tests pass fakes with .invoke().

    `review` needs a checkpointer; the default keeps checkpoints in memory (tests, eval), the CLI passes SQLite."""
    fault = _fault()  # validate it before the graph can make a model call

    def classify_node(s: GraphState) -> dict[str, Any]:
        return {"category": classify(s["rep_message"], classify_model)}

    def account_node(s: GraphState) -> dict[str, Any]:
        out = account_agent.invoke(initial_state(s["account_id"], s["category"], s["rep_message"]))
        if s["account_attempts"] and "fault injected: finalize_once" in s.get("account", {}).get("tool_errors", []):
            out = dict(out)
            out["tool_errors"] = [*out["tool_errors"], "fault injected: finalize_once"]
        if fault == "finalize_once" and s["account_attempts"] == 0:
            out = dict(out)
            out["findings"] = None
            out["tool_errors"] = [*out["tool_errors"], "fault injected: finalize_once"]
        return {"account": out, "account_attempts": s["account_attempts"] + 1}

    def derive_node(s: GraphState) -> dict[str, Any]:
        return {"derived": derive_states(s["account"]["findings"], s["category"], s["account_id"], ds)}

    def policy_node(s: GraphState) -> dict[str, Any]:
        """The parallel first run: the complaint date, no facts yet."""
        read_on = FAULT_POLICY_DATE if fault == "policy_date_mismatch" else WORLD_TODAY
        policy = run_policy_agent(sections, s["category"], s["rep_message"], read_on, "complaint_date", model=policy_model)
        return {"policy": policy, "policy_attempts": s["policy_attempts"] + 1}

    def policy_retry_node(s: GraphState) -> dict[str, Any]:
        """The re-retrieve: the disputed event's date, with the facts."""
        d = s["derived"]
        policy = run_policy_agent(
            sections, s["category"], s["rep_message"], d.policy_date, d.policy_date_basis,
            s["account"]["findings"], d, model=policy_model,
        )
        return {"policy": policy, "policy_attempts": s["policy_attempts"] + 1}

    def sync_node(s: GraphState) -> dict[str, Any]:
        return {"sync": sync(s["account"], s["policy"], s["derived"], s["category"], sections)}

    def synthesize_node(s: GraphState) -> dict[str, Any]:
        return {"synthesis": run_synthesize(
            s["category"], s["rep_message"], s["account"], s["derived"], s["policy"], s["sync"], model=synthesize_model,
        )}

    def review_node(s: GraphState) -> dict[str, Any]:
        """Pause for the rep. Re-runs from the top on resume, so nothing before interrupt() but reading state."""
        existing = find_duplicate(ds, s["account_id"], s["category"], s["derived"].disputed_event_id)
        request = build_request(s, s["thread_label"], MAX_FOLLOWUPS, existing)
        decision = ReviewDecision.model_validate(interrupt(request.model_dump(mode="json")))
        while decision.action == "answer" and not request.can_answer:  # refused: pause again (raising would replay it)
            decision = ReviewDecision.model_validate(interrupt(request.model_dump(mode="json")))
        return {"review": decision}

    def account_followup_node(s: GraphState) -> dict[str, Any]:
        """The rep answered: the Account Agent looks again with the answer folded into the complaint."""
        message = with_answer(s["rep_message"], s["review"].answer)
        out = account_node({**s, "rep_message": message})
        return {**out, "rep_message": message, "rep_answer": s["review"].answer, "followups": s["followups"] + 1}

    def case_node(s: GraphState) -> dict[str, Any]:
        route = s["review"].route if s["review"].action == "override" else s["synthesis"].route
        return {"case": create_review_case(ds, s["account_id"], s["category"], s["derived"], route)}

    g = StateGraph(GraphState)
    g.add_node("classify", classify_node)
    g.add_node("account_agent", account_node)
    g.add_node("derive_states", derive_node)
    g.add_node("policy_agent", policy_node)
    g.add_node("sync", sync_node)
    g.add_node("account_agent_retry", account_node)
    g.add_node("derive_states_retry", derive_node)
    g.add_node("policy_agent_retry", policy_retry_node)
    g.add_node("synthesize", synthesize_node)
    g.add_node("review", review_node)
    g.add_node("account_agent_followup", account_followup_node)
    g.add_node("derive_states_followup", derive_node)
    g.add_node("create_review_case", case_node)

    g.add_edge(START, "classify")
    g.add_edge("classify", "account_agent")  # fan out
    g.add_edge("classify", "policy_agent")
    g.add_edge("account_agent", "derive_states")
    g.add_edge(["derive_states", "policy_agent"], "sync")  # the join: sync waits for both branches
    g.add_conditional_edges("sync", after_sync, ["account_agent_retry", "policy_agent_retry", "synthesize"])
    g.add_edge("account_agent_retry", "derive_states_retry")  # the two bounded re-requests, plain edges back in
    g.add_edge("derive_states_retry", "sync")
    g.add_edge("policy_agent_retry", "sync")
    g.add_edge("synthesize", "review")
    g.add_conditional_edges("review", after_review, ["account_agent_followup", "create_review_case"])
    g.add_edge("account_agent_followup", "derive_states_followup")  # same join rule as the retries: own nodes
    g.add_edge("derive_states_followup", "sync")
    g.add_edge("create_review_case", END)
    return g.compile(checkpointer=checkpointer or InMemorySaver(serde=SERDE))


def sqlite_checkpointer(path: Path) -> SqliteSaver:
    """Checkpoints in one SQLite file, so a paused run can be resumed by a later process."""
    path.parent.mkdir(parents=True, exist_ok=True)
    return SqliteSaver(sqlite3.connect(path, check_same_thread=False), serde=SERDE)


def initial_graph_state(account_id: str, rep_message: str, label: str = "") -> GraphState:
    return {
        "rep_message": rep_message, "account_id": account_id, "account_attempts": 0, "policy_attempts": 0,
        "thread_label": label or account_id, "review": None, "followups": 0, "rep_answer": None,
    }
