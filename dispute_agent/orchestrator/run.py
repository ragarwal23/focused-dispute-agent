"""Running the graph: start a complaint, resume a paused one, or run it unattended for the eval.

    start(...)        runs a new complaint until it pauses at `review`         -> Pause
    resume(...)       continues a paused thread with the rep's decision        -> Pause or Investigation
    investigate(...)  start, then approve every pause; what the eval uses     -> Investigation

A thread is one complaint's run, saved under its thread ID (`<label>-<UTC timestamp>`). Each `invoke` is one parent
trace in LangSmith: `dispute <label>`, then `dispute <label> resume` for each resume.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from langgraph.types import Command

from dispute_agent.account_agent.derive import DerivedFacts
from dispute_agent.data import Category, Dataset
from dispute_agent.orchestrator.graph import GraphState, build_graph, initial_graph_state
from dispute_agent.policy_agent.agent import PolicyFindings
from dispute_agent.policy_agent.retrieve import PolicySection
from dispute_agent.review.create_review_case import CaseResult
from dispute_agent.review.review import APPROVE, Pause, ReviewDecision, ReviewRequest
from dispute_agent.sync.sync import SyncResult
from dispute_agent.sync.synthesize import SynthesisResult


@dataclass
class Investigation:
    """Everything one run produced, read off the final graph state. A dataclass because `account` holds LangChain messages."""

    category: Category
    account: dict[str, Any]
    derived: DerivedFacts
    policy: PolicyFindings
    sync: SyncResult
    synthesis: SynthesisResult
    case: CaseResult
    review: ReviewDecision = field(default_factory=lambda: APPROVE)  # who filed the case: the rep's approval or override
    followups: int = 0  # 1 when the rep answered and the Account Agent looked again
    thread_id: str = ""

    @classmethod
    def from_state(cls, s: GraphState, thread_id: str = "") -> Investigation:
        return cls(
            s["category"], s["account"], s["derived"], s["policy"], s["sync"], s["synthesis"], s["case"],
            s["review"], s["followups"], thread_id,
        )


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
