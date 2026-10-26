"""One complaint, start to finish: classify -> Account Agent -> derive_states -> Policy Agent -> sync -> synthesize -> gate.

Start reading here. Each step's output feeds the next, in the order of the graph in docs/architecture.md.
`run_agents` holds the pipeline's one loop: when sync finds the Account Agent returned no findings (its finalize
failed), the Account Agent -> derive -> Policy Agent -> sync chain runs once more, then the run continues
regardless (S14 in docs/plans/sync-synthesize.md). Nothing else is ever retried.
This is a plain function for now; it becomes the LangGraph orchestrator when create_review_case is built.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .account_agent import initial_state
from .config import make_model
from .data import Category, Dataset
from .derive import DerivedFacts, derive_states
from .gate import GateResult, gate
from .policy_agent import PolicyFindings, PolicySection, run_policy_agent
from .sync import SyncResult, sync
from .synthesize import SynthesisResult, run_synthesize

MAX_ACCOUNT_ATTEMPTS = 2  # the one send-back (S14): graph shape, not a tunable, so not in config.py

CLASSIFY_SYSTEM = """Name the category of a customer's auto-loan payment complaint. Pick exactly one:
- late_fee_autopay: a late fee the customer disputes, usually because autopay was supposed to cover it
- missing_payment: the customer says they paid but the account does not show it
- returned_reversed_payment: a payment that was returned by the bank or reversed by the lender, or the fee for it
- refund_status: a refund the customer is waiting for or says never arrived
Judge by what the customer is asking about, not by keywords."""


class CategoryChoice(BaseModel):
    category: Category
    reason: str = Field(description="One sentence")


@dataclass
class Investigation:
    """Everything one run produced: the proto graph state. A dataclass because `account` holds LangChain messages."""

    category: Category
    account: dict[str, Any]  # the Account Agent's final state: findings, messages, tool_calls_made, budget_hit, tool_errors
    derived: DerivedFacts
    policy: PolicyFindings
    sync: SyncResult
    synthesis: SynthesisResult
    gate: GateResult
    account_attempts: int = 1  # 2 when the S14 send-back fired


def classify(rep_message: str, model: Any = None, config: dict | None = None) -> Category:
    """One LLM call: which of the four dispute categories is this complaint?"""
    model = model or make_model().with_structured_output(CategoryChoice, method="json_schema")
    choice = model.invoke([SystemMessage(CLASSIFY_SYSTEM), HumanMessage(rep_message)], config=config)
    return choice.category if isinstance(choice, CategoryChoice) else CategoryChoice.model_validate(choice).category


def run_agents(
    ds: Dataset,
    sections: dict[str, PolicySection],
    account_agent: Any,
    account_id: str,
    category: Category,
    rep_message: str,
    tags: list[str],
    label: str,
    policy_model: Any = None,
) -> tuple[dict[str, Any], DerivedFacts, PolicyFindings, SyncResult, int]:
    """Account Agent -> derive_states -> Policy Agent -> sync, with the one send-back (S14).

    Runs the chain again only when sync reports the account findings missing, and at most twice. The second
    attempt's run names end in " retry"; its state replaces the first's. `policy_model` is for offline tests only.
    """
    for attempt in range(1, MAX_ACCOUNT_ATTEMPTS + 1):
        suffix = "" if attempt == 1 else " retry"
        account = account_agent.invoke(
            initial_state(account_id, category, rep_message),
            config={"run_name": f"account_agent {label}{suffix}", "tags": tags},
        )
        derived = derive_states(account["findings"], category, account_id, ds)
        policy = run_policy_agent(
            sections, category, rep_message, account["findings"], derived,
            model=policy_model, config={"run_name": f"policy_agent {label}{suffix}", "tags": tags},
        )
        synced = sync(account, policy, derived, category, sections)
        if not synced.account_missing:
            break
    return account, derived, policy, synced, attempt


def investigate(
    ds: Dataset,
    sections: dict[str, PolicySection],
    account_agent: Any,
    account_id: str,
    rep_message: str,
    tags: list[str],
    label: str,
) -> Investigation:
    """Run every step for one complaint. `label` names the LangSmith traces; `tags` are attached to all of them."""
    category = classify(rep_message, config={"run_name": f"classify {label}", "tags": tags})
    account, derived, policy, synced, attempts = run_agents(
        ds, sections, account_agent, account_id, category, rep_message, tags, label
    )
    synthesis = run_synthesize(
        category, rep_message, account, derived, policy, synced,
        config={"run_name": f"synthesize {label}", "tags": tags},
    )
    verdict = gate(synthesis.draft, account_id, category, derived, policy, sections, ds)
    return Investigation(category, account, derived, policy, synced, synthesis, verdict, attempts)
