"""derive_states: on every dev scenario's answer key, plus the edge cases."""

from pathlib import Path

import pytest

from dispute_agent.account_agent.agent import AccountFindings, OtherAccountMatch
from dispute_agent.account_agent.derive import derive_states
from dispute_agent.data import Scenario, load_dataset

DEV_IDS = [
    s.scenario_id for s in load_dataset(Path(__file__).resolve().parent.parent / "data").scenarios.values()
    if s.split == "dev"
]


def oracle_findings(s: Scenario) -> AccountFindings:
    """What a perfect agent would return for this scenario, built from the answer key."""
    other_account = OtherAccountMatch(event_id="E-0135", account_id="A-1008", reference="PHN-0048213", why="keyed to another customer")
    return AccountFindings(
        relevant_event_ids=s.expected_required_event_ids,
        disputed_event_id=s.expected_disputed_event_id,
        candidate_event_ids=s.expected_required_event_ids if s.trap == "ambiguous" else [],
        other_account_matches=[other_account] if s.scenario_id == "S-07" else [],
        claimed_but_not_found=["per key"] if s.trap in ("missing_record", "wrong_account") else [],
        notes_summary="",
        summary="",
    )


def oracle_derived(ds, s: Scenario):
    return derive_states(oracle_findings(s), s.expected_category, s.account_id, ds)


@pytest.mark.parametrize("sid", DEV_IDS)
def test_derive_matches_key(ds, sid):
    s = ds.scenarios[sid]
    d = derive_states(oracle_findings(s), s.expected_category, s.account_id, ds)
    assert d.payment_state == s.expected_payment_state, d.reasons
    assert d.refund_state == s.expected_refund_state, d.reasons
    assert d.disputed_event_id == s.expected_disputed_event_id
    assert d.fact_ambiguous is (s.trap == "ambiguous")
    assert d.other_account_match is (s.trap == "wrong_account")
    assert d.fact_gap is (s.trap in ("missing_record", "wrong_account"))
    assert d.policy_date_basis == ("disputed_event" if s.expected_disputed_event_id else "complaint_date")
    assert d.dropped_off_account_ids == []
    assert [e.event_id for e in d.evidence_events] and all(e.account_id == s.account_id for e in d.evidence_events)


def test_derive_drops_off_account_citation(ds):
    f = AccountFindings(relevant_event_ids=["E-0157", "E-0135"], disputed_event_id="E-0135", notes_summary="", summary="")
    d = derive_states(f, "missing_payment", "A-1007", ds)
    assert d.dropped_off_account_ids == ["E-0135"] and d.disputed_event_id is None and d.fact_gap is True
    assert d.relevant_event_ids == ["E-0157"] and d.payment_state == "not_found"


def test_derive_linked_return_fee(ds):
    f = AccountFindings(relevant_event_ids=["E-0108"], disputed_event_id="E-0108", notes_summary="", summary="")
    assert derive_states(f, "returned_reversed_payment", "A-1010", ds).payment_state == "returned"


def test_derive_no_findings(ds):
    d = derive_states(None, "missing_payment", "A-1006", ds)
    assert d.fact_gap is True and d.payment_state == "not_found" and d.policy_date_basis == "complaint_date"


def test_derive_other_account_match_must_be_off_account(ds):
    def oam(*ids):
        matches = [OtherAccountMatch(event_id=i, account_id="A-1008", why="x") for i in ids]
        return AccountFindings(
            relevant_event_ids=[], disputed_event_id=None, notes_summary="", summary="", other_account_matches=matches
        )

    assert derive_states(oam("E-0109", "E-9999"), "missing_payment", "A-1006", ds).other_account_match is False  # own, unknown
    assert derive_states(oam("E-0135"), "missing_payment", "A-1007", ds).other_account_match is True


def test_derive_noisy_claims_set_gap_without_a_disputed_event(ds):
    """Why the claimed_but_not_found spec matters: any entry, however vague, sets fact_gap when nothing is disputed."""
    s = ds.scenarios["S-15"]
    f = oracle_findings(s).model_copy(update={"claimed_but_not_found": ["a rep note of the call"]})
    assert derive_states(f, s.expected_category, s.account_id, ds).fact_gap is True
