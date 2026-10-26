"""The gate: the registry, the five checks pinned on every scenario, and what the gate rejects or leaves alone."""

from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from dispute_agent.account_agent import AccountFindings
from dispute_agent.data import load_dataset
from dispute_agent.derive import derive_states
from dispute_agent.gate import (
    CHECKS, REGISTRY, RULES_OUT, Draft, add_business_days, find_duplicate, gate, run_checks,
)
from dispute_agent.policy_agent import PolicyFindings, RuleReading
from test_derive import oracle_findings

ALL_IDS = sorted(load_dataset(Path(__file__).resolve().parent.parent / "data").scenarios)

# What the five checks must say on each scenario's answer key (plan §6, verified by prototype). Unlisted = unknown.
PINNED = {
    "S-01": {"late_fee_unwarranted": "false", "lender_side_failure": "false", "customer_cancelled_autopay": "true"},
    "S-02": {"late_fee_unwarranted": "true", "lender_side_failure": "true", "customer_cancelled_autopay": "false"},
    "S-03": {"late_fee_unwarranted": "false", "lender_side_failure": "false", "customer_cancelled_autopay": "true"},
    "S-04": {"late_fee_unwarranted": "false", "lender_side_failure": "true", "customer_cancelled_autopay": "false"},
    "S-05": {"late_fee_unwarranted": "true", "lender_side_failure": "true", "customer_cancelled_autopay": "false"},
    "S-06": {},
    "S-07": {"late_fee_unwarranted": "false", "lender_side_failure": "false", "customer_cancelled_autopay": "false"},
    "S-08": {},
    "S-09": {"return_fee_valid": "true"},
    "S-10": {},
    "S-11": {"return_fee_valid": "true"},
    "S-12": {"return_fee_valid": "true"},
    "S-13": {"refund_overdue": "true"},
    "S-14": {"refund_overdue": "false"},
    "S-15": {"refund_overdue": "true"},
    "S-16": {},
}


def oracle_derived(ds, s):
    return derive_states(oracle_findings(s), s.expected_category, s.account_id, ds)


def oracle_draft(s, **changes):
    draft = Draft(
        review_route=s.expected_route, event_ids=s.expected_required_event_ids,
        policy_source_ids=s.expected_policy_ids, confidence="medium", case_note="",
    )
    return draft.model_copy(update=changes)


def run(ds, sections, s, draft, policy=None):
    return gate(draft, s.account_id, s.expected_category, oracle_derived(ds, s), policy, sections, ds)


# --- registry and dates ---


def test_registry_matches_readme(sections):
    assert len(REGISTRY) == 28
    for s in sections.values():
        if s.status == "current" and not s.section_id.startswith("EX."):
            assert s.section_id in REGISTRY
    assert not [k for k in REGISTRY if k.startswith(("LF.v2.", "EX.v1."))]
    assert set(REGISTRY.values()) - {"procedural"} == set(RULES_OUT) == set(CHECKS)


def test_business_days():
    assert add_business_days(date(2026, 9, 3), 10) == date(2026, 9, 18)
    assert add_business_days(date(2026, 9, 29), 10) == date(2026, 10, 14)  # skips Columbus Day
    assert add_business_days(date(2026, 10, 6), 10) == date(2026, 10, 21)
    assert add_business_days(date(2026, 9, 24), 10) == date(2026, 10, 8)


# --- the checks ---


@pytest.mark.parametrize("sid", ALL_IDS)
def test_checks_pinned(ds, sid):
    s = ds.scenarios[sid]
    expected = {name: PINNED[sid].get(name, "unknown") for name in CHECKS}
    assert run_checks(ds, s.account_id, oracle_derived(ds, s)) == expected


def test_refund_overdue_scoped_to_disputed(ds):
    """A-1016 live run: the customer asked about the October request (E-0138, not yet overdue), not the older E-0128."""
    picked = AccountFindings(relevant_event_ids=["E-0128", "E-0138"], disputed_event_id="E-0138", notes_summary="", summary="")
    derived = derive_states(picked, "refund_status", "A-1016", ds)
    assert run_checks(ds, "A-1016", derived)["refund_overdue"] == "false"
    unsure = picked.model_copy(update={"disputed_event_id": None, "candidate_event_ids": ["E-0128", "E-0138"]})
    derived = derive_states(unsure, "refund_status", "A-1016", ds)
    assert run_checks(ds, "A-1016", derived)["refund_overdue"] == "true"
    no_request = picked.model_copy(update={"disputed_event_id": "E-0118", "relevant_event_ids": ["E-0118", "E-0128"]})
    derived = derive_states(no_request, "refund_status", "A-1016", ds)
    assert run_checks(ds, "A-1016", derived)["refund_overdue"] == "unknown"  # E-0118's chain has no request


def test_returned_payment_not_received(ds):
    """S-11's October payment arrived in grace but was returned, so it does not count (LF.v3.s4)."""
    s = ds.scenarios["S-11"]
    f = oracle_findings(s).model_copy(update={"disputed_event_id": "E-0159", "relevant_event_ids": ["E-0159"]})
    derived = derive_states(f, "late_fee_autopay", s.account_id, ds)
    assert run_checks(ds, s.account_id, derived)["late_fee_unwarranted"] == "false"


# --- the gate ---


@pytest.mark.parametrize("sid", ALL_IDS)
def test_oracle_draft_passes(ds, sections, sid):
    s = ds.scenarios[sid]
    g = run(ds, sections, s, oracle_draft(s))
    assert g.gate_rejected is False and g.gate_reasons == [], g.gate_reasons
    assert g.route == s.expected_route
    assert g.existing_case_id == s.expected_existing_case_id


@pytest.mark.parametrize("sid, route, check", [
    ("S-01", "adjustment_review", "customer_cancelled_autopay"),
    ("S-02", "explain_events", "late_fee_unwarranted"),
    ("S-04", "explain_events", "lender_side_failure"),
    ("S-05", "explain_events", "lender_side_failure"),
    ("S-09", "adjustment_review", "return_fee_valid"),
    ("S-13", "explain_events", "refund_overdue"),
])
def test_wrong_route_caught(ds, sections, sid, route, check):
    g = run(ds, sections, ds.scenarios[sid], oracle_draft(ds.scenarios[sid], review_route=route))
    assert g.gate_rejected is True and g.route == "manual_escalation"
    assert any(r.startswith(f"{check} is true") and r.endswith(f"rules out {route}") for r in g.gate_reasons), g.gate_reasons


@pytest.mark.parametrize("sid, route", [("S-03", "explain_events"), ("S-10", "explain_events"), ("S-14", "adjustment_review")])
def test_note_driven_limit(ds, sections, sid, route):
    """The documented limit: when the deciding evidence is only in a note, code-only checks cannot catch it."""
    g = run(ds, sections, ds.scenarios[sid], oracle_draft(ds.scenarios[sid], review_route=route))
    assert g.gate_rejected is False and g.route == route


@pytest.mark.parametrize("route", ["need_more_evidence", "manual_escalation"])
def test_cautious_routes_never_ruled_out(ds, sections, route):
    s = ds.scenarios["S-02"]
    g = run(ds, sections, s, oracle_draft(s, review_route=route))
    assert g.gate_rejected is False and g.route == route
    assert {c.rules_out for c in g.section_checks} == {"explain_events"}


def test_off_account_citation(ds, sections):
    s = ds.scenarios["S-07"]
    g = run(ds, sections, s, oracle_draft(s, event_ids=["E-0157", "E-0135"]))
    assert g.gate_rejected is True and g.route == "manual_escalation"
    assert g.gate_reasons == ["event E-0135 is on A-1008, not A-1007"]
    s6 = ds.scenarios["S-06"]
    assert run(ds, sections, s6, oracle_draft(s6, event_ids=["E-0109", "E-0158"])).gate_rejected is True


def test_unknown_event(ds, sections):
    s = ds.scenarios["S-01"]
    g = run(ds, sections, s, oracle_draft(s, event_ids=[*s.expected_required_event_ids, "E-9999"]))
    assert g.gate_rejected is True and g.gate_reasons == ["event E-9999 is unknown"]


@pytest.mark.parametrize("sid, reason", [
    ("XX.v9.s9", "policy XX.v9.s9 is unknown"),
    ("LF.v2.s3", "policy LF.v2.s3 was not in force on 2026-10-14"),
    ("EX.v1.s1", "policy EX.v1.s1 is not registered to a check"),
])
def test_policy_id_problems(ds, sections, sid, reason):
    s = ds.scenarios["S-02"]
    g = run(ds, sections, s, oracle_draft(s, policy_source_ids=[*s.expected_policy_ids, sid]))
    assert g.gate_rejected is True and g.route == "manual_escalation" and g.gate_reasons == [reason]


def test_policy_ambiguity_escalates(ds, sections):
    s = ds.scenarios["S-02"]
    derived = oracle_derived(ds, s)
    flagged = RuleReading(
        section_id="LF.v3.s5", condition="x", outcome="fee_adjustment_review", why="x",
        policy_ambiguous=True, ambiguity_reason="the text does not say who caused the delay",
    )
    policy = PolicyFindings(policy_date=derived.policy_date, category=s.expected_category, retrieved=[], rules=[flagged])
    g = run(ds, sections, s, oracle_draft(s), policy)
    assert g.gate_rejected is True and g.route == "manual_escalation"
    assert g.gate_reasons == ["policy LF.v3.s5 reading is ambiguous: the text does not say who caused the delay"]


def test_fact_ambiguity_left_alone(ds, sections):
    s = ds.scenarios["S-15"]
    assert oracle_derived(ds, s).fact_ambiguous is True
    g = run(ds, sections, s, oracle_draft(s))
    assert g.gate_rejected is False and g.route == "need_more_evidence"


def test_no_draft(ds, sections):
    g = run(ds, sections, ds.scenarios["S-01"], None)
    assert g.gate_rejected is True and g.route == "manual_escalation"
    assert g.gate_reasons == ["no draft from synthesize"]
    assert g.check_results["customer_cancelled_autopay"] == "true" and g.section_checks == []


def test_bad_route_rejected_by_model():
    with pytest.raises(ValidationError):
        Draft(review_route="waive_fee", event_ids=[], policy_source_ids=[], confidence="high", case_note="")


def test_duplicate_rule(ds):
    assert find_duplicate(ds, "A-1005", "missing_payment", "E-0131") is None  # C-001 is closed
    assert find_duplicate(ds, "A-1012", "returned_reversed_payment", "E-0152") == "C-002"
    assert find_duplicate(ds, "A-1014", "refund_status", "E-0093") is None  # C-003 is another category
    assert find_duplicate(ds, "A-1012", "returned_reversed_payment", None) is None


def test_all_reasons_collected(ds, sections):
    s = ds.scenarios["S-07"]
    g = run(ds, sections, s, oracle_draft(s, event_ids=["E-0135"], policy_source_ids=[*s.expected_policy_ids, "XX.v9.s9"]))
    assert len(g.gate_reasons) == 2
