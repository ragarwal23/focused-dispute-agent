"""One complaint, start to finish, as the LangGraph orchestrator graph.

    classify -> Account Agent -> derive_states ─┐
            └> Policy Agent ───────────────────┴> sync -> synthesize -> gate -> review -> create_review_case

Start reading here. LangGraph decides what runs next; the LLM is only called inside the nodes. After classify the
two agents fan out in parallel and meet at sync, a join node. Sync can send each agent back once
(docs/plans/orchestrator.md, O2): the Account Agent when it returned no findings, and the Policy Agent when a
different policy set was in force on the disputed event's date than on the complaint date it first read. The
retry paths are separate nodes with plain edges back into sync; re-entering the fan-out nodes would hit the join
barrier and end the run silently. Nothing else is ever retried. One `invoke` is one parent trace in LangSmith.

After the gate the graph pauses at `review` for a person (docs/plans/human-review.md). A checkpointer saves the
state after every node, so `resume` continues the same thread later, in another process. The rep approves, overrides
the route, or, on need_more_evidence, answers once: the Account Agent then looks again through its own follow-up
nodes and the run comes back to `review`. `investigate` approves every pause, so the eval never waits.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.serde.jsonplus import JsonPlusSerializer
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from pydantic import BaseModel, Field

from .account_agent import AccountFindings, OtherAccountMatch, initial_state
from .config import WORLD_TODAY, make_model
from .create_review_case import CaseResult, create_review_case
from .data import Category, Dataset
from .derive import DerivedFacts, derive_states
from .gate import Draft, GateResult, SectionCheck, gate
from .policy_agent import PolicyFindings, PolicySection, RetrievedSection, RuleReading, run_policy_agent
from .review import APPROVE, Pause, ReviewDecision, ReviewRequest, build_request, with_answer
from .sync import SyncResult, sync
from .synthesize import SynthesisResult, run_synthesize

MAX_ATTEMPTS = 2  # each agent runs at most twice: graph shape, not a tunable, so not in config.py
MAX_FOLLOWUPS = 1  # the rep can answer open questions once per complaint
# The checkpoint serializer may load our state models by name; LangGraph warns on (and will block) any other class.
SERDE = JsonPlusSerializer(allowed_msgpack_modules=(
    AccountFindings, OtherAccountMatch, DerivedFacts, PolicyFindings, PolicySection, RetrievedSection, RuleReading,
    SyncResult, SynthesisResult, Draft, GateResult, SectionCheck, CaseResult, ReviewDecision,
))
FAULTS = ("finalize_once", "policy_date_mismatch")
FAULT_POLICY_DATE = datetime(2026, 4, 15)  # LF.v2 was in force; LF.v3 begins 2026-05-01

CLASSIFY_SYSTEM = """Name the category of a customer's auto-loan payment complaint. Pick exactly one:
- late_fee_autopay: a late fee the customer disputes, usually because autopay was supposed to cover it
- missing_payment: the customer says they paid but the account does not show it
- returned_reversed_payment: a payment that was returned by the bank or reversed by the lender, or the fee for it
- refund_status: a refund the customer is waiting for or says never arrived
Judge by what the customer is asking about, not by keywords."""


class CategoryChoice(BaseModel):
    category: Category
    reason: str = Field(description="One sentence")


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
    gate: GateResult | None
    case: CaseResult | None
    account_attempts: int
    policy_attempts: int
    thread_label: str  # what `review` shows the rep: the account or scenario ID
    review: ReviewDecision | None  # the rep's last decision
    followups: int  # rep answers used, at most MAX_FOLLOWUPS
    rep_answer: str | None  # the rep's raw answer; rep_message carries it too


@dataclass
class Investigation:
    """Everything one run produced, read off the final graph state. A dataclass because `account` holds LangChain messages."""

    category: Category
    account: dict[str, Any]
    derived: DerivedFacts
    policy: PolicyFindings
    sync: SyncResult
    synthesis: SynthesisResult
    gate: GateResult
    case: CaseResult
    account_attempts: int = 1  # 2 when sync sent the Account Agent back
    policy_attempts: int = 1  # 2 when sync re-ran the Policy Agent on the disputed event's date
    review: ReviewDecision = field(default_factory=lambda: APPROVE)  # who filed the case: the rep's approval or override
    followups: int = 0  # 1 when the rep answered and the Account Agent looked again
    thread_id: str = ""

    @classmethod
    def from_state(cls, s: GraphState, thread_id: str = "") -> Investigation:
        return cls(
            s["category"], s["account"], s["derived"], s["policy"], s["sync"], s["synthesis"], s["gate"], s["case"],
            s["account_attempts"], s["policy_attempts"], s["review"], s["followups"], thread_id,
        )


def classify(rep_message: str, model: Any = None, config: dict | None = None) -> Category:
    """One LLM call: which of the four dispute categories is this complaint?"""
    model = model or make_model().with_structured_output(CategoryChoice, method="json_schema")
    choice = model.invoke([SystemMessage(CLASSIFY_SYSTEM), HumanMessage(rep_message)], config=config)
    return choice.category if isinstance(choice, CategoryChoice) else CategoryChoice.model_validate(choice).category


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

    def gate_node(s: GraphState) -> dict[str, Any]:
        return {"gate": gate(s["synthesis"].draft, s["account_id"], s["category"], s["derived"], s["policy"], sections, ds)}

    def review_node(s: GraphState) -> dict[str, Any]:
        """Pause for the rep. Re-runs from the top on resume, so nothing before interrupt() but reading state."""
        request = build_request(s, s["thread_label"], MAX_FOLLOWUPS)
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
        override = s["review"].route if s["review"].action == "override" else None
        return {"case": create_review_case(ds, s["account_id"], s["category"], s["derived"], s["gate"], override)}

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
    g.add_node("gate", gate_node)
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
    g.add_edge("synthesize", "gate")
    g.add_edge("gate", "review")
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


def new_thread_id(label: str) -> str:
    """`<label>-<UTC timestamp>`: readable, and two runs on one account never collide."""
    return f"{label}-{datetime.now(timezone.utc):%Y%m%dT%H%M%S%fZ}"


def thread_config(thread_id: str, tags: list[str] | None = None, run_name: str | None = None) -> dict[str, Any]:
    return {"configurable": {"thread_id": thread_id}, "tags": tags or [], "run_name": run_name}


def _outcome(final: dict[str, Any], thread_id: str) -> Investigation | Pause:
    if "__interrupt__" in final:
        return Pause(thread_id=thread_id, request=ReviewRequest.model_validate(final["__interrupt__"][0].value))
    return Investigation.from_state(final, thread_id)


def start(graph: Any, account_id: str, rep_message: str, thread_id: str, tags: list[str], label: str) -> Investigation | Pause:
    """Run a new complaint until it pauses at `review` (or, never in practice, finishes). Traced as `dispute <label>`."""
    final = graph.invoke(initial_graph_state(account_id, rep_message, label), thread_config(thread_id, tags, f"dispute {label}"))
    return _outcome(final, thread_id)


def resume(graph: Any, thread_id: str, decision: ReviewDecision, tags: list[str], label: str) -> Investigation | Pause:
    """Continue a paused thread with the rep's decision. Traced as `dispute <label> resume`."""
    config = thread_config(thread_id, [*tags, "resume"], f"dispute {label} resume")
    return _outcome(graph.invoke(Command(resume=decision.model_dump()), config), thread_id)


def thread_status(graph: Any, thread_id: str) -> Literal["paused", "finished", "unknown"]:
    snap = graph.get_state(thread_config(thread_id))
    return "unknown" if not snap.values else "paused" if snap.next else "finished"


def investigate(
    ds: Dataset,
    sections: dict[str, PolicySection],
    account_agent: Any,
    account_id: str,
    rep_message: str,
    tags: list[str],
    label: str,
    graph: Any = None,
) -> Investigation:
    """Unattended: run the graph for one complaint and approve every pause. What eval uses; no person in the loop."""
    graph = graph or build_graph(ds, sections, account_agent)
    thread_id = new_thread_id(label)
    out = start(graph, account_id, rep_message, thread_id, tags, label)
    while isinstance(out, Pause):
        out = resume(graph, thread_id, APPROVE, tags, label)
    return out
