"""create_review_case: the agent's only write, simulated. No LLM.

Runs after the rep's review on every run, a missing draft included: the rep still needs a case, filed under the
recommended route, or under the rep's route when they overrode it (docs/history/human-review.md, H8).
Idempotent: `find_duplicate` looks for an open case with the same account, category and disputed event. When there
is one, that case is returned and nothing is created; the review pause shows it to the rep first. Otherwise the next
case ID is minted: one past the highest in the dataset and in `filed`, the IDs earlier runs already minted (the graph
reads them from its checkpoints). Nothing is written back to the dataset; that is what "simulated" means here.
The data tests it: C-001 is closed, so it must not block S-05; C-002 is open, so it must block S-11; C-003 is open
in another category, so it must not block S-13.
"""

from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel

from dispute_agent.account_agent.derive import DerivedFacts
from dispute_agent.data import Category, Dataset, Route


class CaseResult(BaseModel):
    """What the write would have sent: the case, who it is for, and the queue it is filed under."""

    case_id: str
    created: bool  # False when an open duplicate already existed; its ID is returned instead
    account_id: str
    category: Category
    disputed_event_id: str | None
    route: Route  # the final route: the rep's override, else the recommended route


def find_duplicate(ds: Dataset, account_id: str, category: Category, disputed_event_id: str | None) -> str | None:
    """An open case with the same account, category and disputed event, else None."""
    if disputed_event_id is None:
        return None
    for c in ds.cases_for_account(account_id):
        if c.status == "open" and c.category == category and c.disputed_event_id == disputed_event_id:
            return c.case_id
    return None


def next_case_id(ds: Dataset, filed: Iterable[str] = ()) -> str:
    """One past the highest case number in the dataset and in `filed`."""
    numbers = [int(i[2:]) for i in [*ds.review_cases, *filed] if i[2:].isdigit()]
    return f"C-{max(numbers, default=0) + 1:03d}"


def create_review_case(
    ds: Dataset, account_id: str, category: Category, derived: DerivedFacts, route: Route, filed: Iterable[str] = (),
) -> CaseResult:
    """Return the open duplicate if there is one, else mint the next case ID. `route` is final: overrides already applied."""
    existing = find_duplicate(ds, account_id, category, derived.disputed_event_id)
    return CaseResult(
        case_id=existing or next_case_id(ds, filed),
        created=existing is None,
        account_id=account_id,
        category=category,
        disputed_event_id=derived.disputed_event_id,
        route=route,
    )
