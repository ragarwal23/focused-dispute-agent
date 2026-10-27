"""create_review_case: the agent's only write, simulated. No LLM.

Runs after the rep's review on every run, gate rejection included: the rep still needs a case, filed under the
forced route, or under the rep's route when they overrode it (docs/plans/human-review.md, H8).
Idempotent: the gate already looked for an open case with the same account, category and disputed event
(`existing_case_id`). When there is one, that case is returned and nothing is created. Otherwise a new case ID
is minted from the dataset's case count. Nothing is persisted, so the same complaint run twice mints the same ID;
that is what "simulated" means here. The data tests it: C-001 is closed, so it must not block S-05; C-002 is open,
so it must block S-11; C-003 is open in another category, so it must not block S-13.
"""

from __future__ import annotations

from pydantic import BaseModel

from dispute_agent.account_agent.derive import DerivedFacts
from dispute_agent.data import Category, Dataset, Route
from dispute_agent.gate.gate import GateResult


class CaseResult(BaseModel):
    """What the write would have sent: the case, who it is for, and the queue it is filed under."""

    case_id: str
    created: bool  # False when an open duplicate already existed; its ID is returned instead
    account_id: str
    category: Category
    disputed_event_id: str | None
    route: Route  # the final route: the rep's override, else the gate's


def create_review_case(
    ds: Dataset, account_id: str, category: Category, derived: DerivedFacts, verdict: GateResult,
    route_override: Route | None = None,
) -> CaseResult:
    """Return the open duplicate if the gate found one, else mint the next case ID. The gate's result is left as is."""
    existing = verdict.existing_case_id
    return CaseResult(
        case_id=existing or f"C-{len(ds.review_cases) + 1:03d}",
        created=existing is None,
        account_id=account_id,
        category=category,
        disputed_event_id=derived.disputed_event_id,
        route=route_override or verdict.route,
    )
