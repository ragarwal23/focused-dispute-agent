"""One complaint, start to finish, as the LangGraph orchestrator graph.

    classify -> Account Agent -> derive_states ─┐
            └> Policy Agent ───────────────────┴> sync -> synthesize -> gate -> create_review_case

Start reading here. LangGraph decides what runs next; the LLM is only called inside the nodes. After classify the
two agents fan out in parallel and meet at sync, a join node. Sync can send each agent back once
(docs/plans/orchestrator.md, O2): the Account Agent when it returned no findings, and the Policy Agent when a
different policy set was in force on the disputed event's date than on the complaint date it first read. The
retry paths are separate nodes with plain edges back into sync; re-entering the fan-out nodes would hit the join
barrier and end the run silently. Nothing else is ever retried. One `invoke` is one parent trace in LangSmith.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal, TypedDict

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .account_agent import initial_state
from .config import WORLD_TODAY, make_model
from .create_review_case import CaseResult, create_review_case
from .data import Category, Dataset
from .derive import DerivedFacts, derive_states
from .gate import GateResult, gate
from .policy_agent import PolicyFindings, PolicySection, run_policy_agent
from .sync import SyncResult, sync
from .synthesize import SynthesisResult, run_synthesize

MAX_ATTEMPTS = 2  # each agent runs at most twice: graph shape, not a tunable, so not in config.py
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

    @classmethod
    def from_state(cls, s: GraphState) -> Investigation:
        return cls(
            s["category"], s["account"], s["derived"], s["policy"], s["sync"], s["synthesis"], s["gate"], s["case"],
            s["account_attempts"], s["policy_attempts"],
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


def build_graph(
    ds: Dataset,
    sections: dict[str, PolicySection],
    account_agent: Any,
    classify_model: Any = None,
    policy_model: Any = None,
    synthesize_model: Any = None,
):
    """Compile the orchestrator. The model arguments default to the real models; tests pass fakes with .invoke()."""
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

    def case_node(s: GraphState) -> dict[str, Any]:
        return {"case": create_review_case(ds, s["account_id"], s["category"], s["derived"], s["gate"])}

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
    g.add_edge("gate", "create_review_case")
    g.add_edge("create_review_case", END)
    return g.compile()


def initial_graph_state(account_id: str, rep_message: str) -> GraphState:
    return {"rep_message": rep_message, "account_id": account_id, "account_attempts": 0, "policy_attempts": 0}


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
    """Run the graph once for one complaint. The run is traced as `dispute <label>` with `tags`; every node is a child."""
    graph = graph or build_graph(ds, sections, account_agent)
    final = graph.invoke(initial_graph_state(account_id, rep_message), config={"run_name": f"dispute {label}", "tags": tags})
    return Investigation.from_state(final)
