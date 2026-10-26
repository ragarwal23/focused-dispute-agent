"""The eval scorer: a failed finalize, the final route vs the draft's route, confidence consistency, the matrix."""

from dispute_agent.derive import derive_states
from dispute_agent.evaluate import CHECKS, HEADER, confusion_lines, row_line, score
from dispute_agent.gate import Draft, gate
from dispute_agent.pipeline import Investigation
from dispute_agent.sync import sync
from dispute_agent.synthesize import SynthesisResult
from test_derive import oracle_findings
from test_policy_agent import rule
from test_sync import account_state, policy_with


def investigation(ds, sections, s, findings, draft, attempts=1):
    """An Investigation built from the answer key, with the given findings and draft."""
    account = account_state(findings)
    derived = derive_states(findings, s.expected_category, s.account_id, ds)
    policy = policy_with(derived, s.expected_category, *(rule(i) for i in s.expected_policy_ids))
    synced = sync(account, policy, derived, s.expected_category, sections)
    synthesis = SynthesisResult(draft=draft) if draft else SynthesisResult(errors=["x"])
    g = gate(draft, s.account_id, s.expected_category, derived, policy, sections, ds)
    return Investigation(s.expected_category, account, derived, policy, synced, synthesis, g, attempts)


def test_score_failed_finalize_is_not_right(ds, sections):
    s = ds.scenarios["S-06"]
    c = score(s, investigation(ds, sections, s, None, None, attempts=2), ds)
    model_checks = ("disputed_ok", "required_found", "forbidden_clear", "payment_ok", "refund_ok", "gap_ok", "oam_ok")
    assert not any(c[k] for k in model_checks)
    assert c["draft_route"] is None and c["route_final"] == "manual_escalation" and c["gate_rejected"] is True
    assert c["route_ok"] is (s.expected_route == "manual_escalation") and c["confidence_ok"] is False
    assert c["draft_events_ok"] is False and c["draft_policy_ok"] is False and c["synth_errors"] == ["x"]
    assert len(row_line(s, c)) == len(HEADER)


def test_score_route_and_confidence(ds, sections):
    s = ds.scenarios["S-15"]
    draft = Draft(review_route="need_more_evidence", event_ids=s.expected_required_event_ids,
                  policy_source_ids=s.expected_policy_ids, confidence="high", open_questions=["which refund?"], case_note="x")
    c = score(s, investigation(ds, sections, s, oracle_findings(s), draft), ds)
    assert c["route_ok"] is True and c["confidence_ok"] is False
    assert c["draft_events_ok"] is True and c["draft_policy_ok"] is True and c["gate_rejected"] is False
    assert all(c[k] for k in CHECKS if k != "confidence_ok")


def test_score_gate_rescue(ds, sections):
    s = ds.scenarios["S-07"]
    draft = Draft(review_route="adjustment_review", event_ids=["E-0157", "E-0135"], policy_source_ids=s.expected_policy_ids,
                  confidence="medium", case_note="x")
    c = score(s, investigation(ds, sections, s, oracle_findings(s), draft), ds)
    assert c["gate_rejected"] is True and c["route_final"] == "manual_escalation"
    assert c["route_ok"] is True and c["draft_route"] == "adjustment_review"
    assert c["draft_events_ok"] is False  # E-0135 is forbidden
    assert c["gate_reasons"] == ["event E-0135 is on A-1008, not A-1007"]


def test_confusion_matrix():
    rows = [
        {"route_expected": "adjustment_review", "checks": {"route_final": "adjustment_review"}},
        {"route_expected": "adjustment_review", "checks": {"route_final": "manual_escalation"}},
        {"route_expected": "need_more_evidence", "checks": {"route_final": "need_more_evidence"}},
    ]
    lines = confusion_lines(rows)
    assert len(lines) == 5 and lines[1].split() == ["adjustment_review", "1", "0", "0", "1"]
    assert lines[3].split() == ["need_more_evidence", "0", "0", "1", "0"]
