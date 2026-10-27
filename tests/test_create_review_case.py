"""create_review_case: a closed case does not block, an open one in the same category does, one in another category does not."""

import pytest

from dispute_agent.create_review_case import create_review_case
from dispute_agent.gate import GateResult, find_duplicate
from test_policy_agent import facts


def verdict(existing, route="explain_events"):
    return GateResult(route=route, gate_rejected=False, gate_reasons=[], check_results={}, section_checks=[], existing_case_id=existing)


@pytest.mark.parametrize("sid", ["S-05", "S-11", "S-13"])
def test_duplicates_follow_the_answer_key(ds, sid):
    s = ds.scenarios[sid]
    _, derived = facts(ds, s)
    dup = find_duplicate(ds, s.account_id, s.expected_category, derived.disputed_event_id)
    assert dup == s.expected_existing_case_id
    case = create_review_case(ds, s.account_id, s.expected_category, derived, verdict(dup))
    assert case.created is (dup is None) and case.case_id == (dup or "C-004")
    assert case.account_id == s.account_id and case.disputed_event_id == s.expected_disputed_event_id


def test_rejected_run_still_files_a_case(ds):
    s = ds.scenarios["S-07"]
    _, derived = facts(ds, s)
    case = create_review_case(ds, s.account_id, s.expected_category, derived, verdict(None, "manual_escalation"))
    assert case.created is True and case.route == "manual_escalation" and case.disputed_event_id is None


def test_override_param(ds):
    """The rep's route wins on the case; the gate's result keeps the gate's route."""
    s = ds.scenarios["S-02"]
    _, derived = facts(ds, s)
    g = verdict(None, "adjustment_review")
    case = create_review_case(ds, s.account_id, s.expected_category, derived, g, "manual_escalation")
    assert case.route == "manual_escalation" and g.route == "adjustment_review" and case.created is True
