"""create_review_case: the agent's only write, simulated. No LLM.

Runs after the gate on every run, rejection included: the rep still needs a case, filed under the forced route.
Idempotent: the gate already looked for an open case with the same account, category and disputed event
(`existing_case_id`). When there is one, that case is returned and nothing is created. Otherwise a new case ID
is minted from the dataset's case count. Nothing is persisted, so the same complaint run twice mints the same ID;
that is what "simulated" means here. The data tests it: C-001 is closed, so it must not block S-05; C-002 is open,
so it must block S-11; C-003 is open in another category, so it must not block S-13.
"""

from __future__ import annotations

from pydantic import BaseModel

from .data import Category, Dataset, Route
from .derive import DerivedFacts
from .gate import GateResult


class CaseResult(BaseModel):
    """What the write would have sent: the case, who it is for, and the queue it is filed under."""

    case_id: str
    created: bool  # False when an open duplicate already existed; its ID is returned instead
    account_id: str
    category: Category
    disputed_event_id: str | None
    route: Route  # the final route, after the gate


def create_review_case(
    ds: Dataset, account_id: str, category: Category, derived: DerivedFacts, verdict: GateResult
) -> CaseResult:
    """Return the open duplicate if the gate found one, else mint the next case ID."""
    existing = verdict.existing_case_id
    return CaseResult(
        case_id=existing or f"C-{len(ds.review_cases) + 1:03d}",
        created=existing is None,
        account_id=account_id,
        category=category,
        disputed_event_id=derived.disputed_event_id,
        route=verdict.route,
    )
