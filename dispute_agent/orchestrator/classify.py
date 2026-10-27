"""classify: the orchestrator's first LLM call. Which of the four dispute categories is this complaint?

The category decides which tools the Account Agent leans on and which policy documents the Policy Agent searches.
"""

from __future__ import annotations

from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, Field

from dispute_agent.config import make_model
from dispute_agent.data import Category

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
