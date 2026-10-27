"""P1-P7 in the eval: the five checks pinned on every answer key, the routes they rule out, and the golden audit."""

import subprocess
import sys
from datetime import date
from pathlib import Path

import pytest

from dispute_agent.account_agent.agent import AccountFindings
from dispute_agent.account_agent.derive import derive_states
from dispute_agent.data import load_dataset
from dispute_agent.evaluation.policy_checks import CHECKS, RULES_OUT, add_business_days, audit, ruled_out, run_checks
from test_derive import oracle_derived, oracle_findings

ROOT = Path(__file__).resolve().parent.parent
ALL_IDS = sorted(load_dataset(ROOT / "data").scenarios)

# What the five checks must say on each scenario's answer key (verified by prototype). Unlisted = unknown.
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


def test_rules_out_covers_every_check():
    assert set(RULES_OUT) == set(CHECKS)
    assert set(RULES_OUT.values()) == {"explain_events", "adjustment_review"}  # the cautious routes are never ruled out


def test_business_days():
    assert add_business_days(date(2026, 9, 3), 10) == date(2026, 9, 18)
    assert add_business_days(date(2026, 9, 29), 10) == date(2026, 10, 14)  # skips Columbus Day
    assert add_business_days(date(2026, 10, 6), 10) == date(2026, 10, 21)
    assert add_business_days(date(2026, 9, 24), 10) == date(2026, 10, 8)


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


@pytest.mark.parametrize("sid, route", [
    ("S-01", "adjustment_review"), ("S-02", "explain_events"), ("S-04", "explain_events"),
    ("S-05", "explain_events"), ("S-09", "adjustment_review"), ("S-13", "explain_events"),
])
def test_ruled_out_wrong_route(ds, sid, route):
    s = ds.scenarios[sid]
    assert route in ruled_out(ds, s.account_id, oracle_derived(ds, s))


@pytest.mark.parametrize("sid, route", [("S-03", "explain_events"), ("S-10", "explain_events"), ("S-14", "adjustment_review")])
def test_note_driven_limit(ds, sid, route):
    """The documented limit: when the deciding evidence is only in a note, code-only checks cannot catch it."""
    s = ds.scenarios[sid]
    assert route not in ruled_out(ds, s.account_id, oracle_derived(ds, s))


@pytest.mark.parametrize("sid", ALL_IDS)
def test_cautious_routes_never_ruled_out(ds, sid):
    s = ds.scenarios[sid]
    assert not ruled_out(ds, s.account_id, oracle_derived(ds, s)) & {"need_more_evidence", "manual_escalation"}


def test_no_findings_rules_out_nothing(ds):
    """No findings at all: every check is unknown, so nothing is ruled out."""
    s = ds.scenarios["S-02"]
    derived = derive_states(None, s.expected_category, s.account_id, ds)
    assert set(run_checks(ds, s.account_id, derived).values()) == {"unknown"} and ruled_out(ds, s.account_id, derived) == set()


def test_golden_audit(ds):
    lines, ok = audit(ds)
    assert ok is True and len(lines) == 16 and all(line.endswith(" ok") for line in lines)


def test_audit_command_exit_0():
    out = subprocess.run([sys.executable, "-m", "dispute_agent.evaluation.policy_checks"], cwd=ROOT, capture_output=True, text=True)
    assert out.returncode == 0 and len(out.stdout.splitlines()) == 16, out.stderr
