"""One complaint, start to finish: classify -> Account Agent -> derive_states -> Policy Agent.

Start reading here. Each step's output feeds the next, in the order of the graph in docs/architecture.md.
This is a plain function for now; it becomes the LangGraph orchestrator when sync, synthesize and the gate
are built.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from .account_agent import initial_state
from .config import make_model
from .data import Category, Dataset
from .derive import DerivedFacts, derive_states
from .policy_agent import PolicyFindings, PolicySection, run_policy_agent

CLASSIFY_SYSTEM = """Name the category of a customer's auto-loan payment complaint. Pick exactly one:
- late_fee_autopay: a late fee the customer disputes, usually because autopay was supposed to cover it
- missing_payment: the customer says they paid but the account does not show it
- returned_reversed_payment: a payment that was returned by the bank or reversed by the lender, or the fee for it
- refund_status: a refund the customer is waiting for or says never arrived
Judge by what the customer is asking about, not by keywords."""


class CategoryChoice(BaseModel):
    category: Category
    reason: str = Field(description="One sentence")


def classify(rep_message: str, model: Any = None, config: dict | None = None) -> Category:
    """One LLM call: which of the four dispute categories is this complaint?"""
    model = model or make_model().with_structured_output(CategoryChoice, method="json_schema")
    choice = model.invoke([SystemMessage(CLASSIFY_SYSTEM), HumanMessage(rep_message)], config=config)
    return choice.category if isinstance(choice, CategoryChoice) else CategoryChoice.model_validate(choice).category


def investigate(
    ds: Dataset,
    sections: dict[str, PolicySection],
    account_agent: Any,
    account_id: str,
    rep_message: str,
    tags: list[str],
    label: str,
) -> tuple[Category, dict[str, Any], DerivedFacts, PolicyFindings]:
    """Run every step for one complaint. `label` names the LangSmith traces; `tags` are attached to all of them.

    Returns (category, the Account Agent's final state, the derived facts, the Policy Agent's findings).
    """
    category = classify(rep_message, config={"run_name": f"classify {label}", "tags": tags})
    account = account_agent.invoke(
        initial_state(account_id, category, rep_message),
        config={"run_name": f"account_agent {label}", "tags": tags},
    )
    derived = derive_states(account["findings"], category, account_id, ds)
    policy = run_policy_agent(
        sections, category, rep_message, account["findings"], derived,
        config={"run_name": f"policy_agent {label}", "tags": tags},
    )
    return category, account, derived, policy
