"""create_review_case: a closed case does not block, an open one in the same category does, one in another category does not."""

import pytest

from dispute_agent.review.create_review_case import create_review_case, find_duplicate
from test_policy_agent import facts


def test_duplicate_rule(ds):
    assert find_duplicate(ds, "A-1005", "missing_payment", "E-0131") is None  # C-001 is closed
    assert find_duplicate(ds, "A-1012", "returned_reversed_payment", "E-0152") == "C-002"
    assert find_duplicate(ds, "A-1014", "refund_status", "E-0093") is None  # C-003 is another category
    assert find_duplicate(ds, "A-1012", "returned_reversed_payment", None) is None


@pytest.mark.parametrize("sid", ["S-05", "S-11", "S-13"])
def test_duplicates_follow_the_answer_key(ds, sid):
    s = ds.scenarios[sid]
    _, derived = facts(ds, s)
    dup = find_duplicate(ds, s.account_id, s.expected_category, derived.disputed_event_id)
    assert dup == s.expected_existing_case_id
    case = create_review_case(ds, s.account_id, s.expected_category, derived, "explain_events")
    assert case.created is (dup is None) and case.case_id == (dup or "C-004")
    assert case.account_id == s.account_id and case.disputed_event_id == s.expected_disputed_event_id


def test_no_draft_still_files_a_case(ds):
    s = ds.scenarios["S-07"]
    _, derived = facts(ds, s)
    case = create_review_case(ds, s.account_id, s.expected_category, derived, "manual_escalation")
    assert case.created is True and case.route == "manual_escalation" and case.disputed_event_id is None


def test_route_is_filed_as_given(ds):
    """The caller applies the rep's override; the case is filed under whatever route it is handed."""
    s = ds.scenarios["S-02"]
    _, derived = facts(ds, s)
    case = create_review_case(ds, s.account_id, s.expected_category, derived, "manual_escalation")
    assert case.route == "manual_escalation" and case.created is True
