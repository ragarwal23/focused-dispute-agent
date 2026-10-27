"""sync: what is kept, what is dropped, which issue and lean each failure produces, and the date-mismatch flag."""

from datetime import datetime

from dispute_agent.config import WORLD_TODAY
from dispute_agent.policy_agent.agent import PolicyFindings
from dispute_agent.sync.sync import sync
from test_policy_agent import facts, rule


def account_state(findings, budget_hit=False, errors=()):
    """The minimal Account Agent final state."""
    return {"findings": findings, "budget_hit": budget_hit, "tool_errors": list(errors), "tool_calls_made": 2, "messages": []}


def policy_with(derived, category, *rules, errors=(), policy_date=None):
    """Policy findings read on `policy_date` (default: the derived date)."""
    return PolicyFindings(
        policy_date=policy_date or derived.policy_date, category=category, retrieved=[], rules=list(rules), errors=list(errors)
    )


def test_sync_clean(ds, sections):
    s = ds.scenarios["S-02"]
    findings, derived = facts(ds, s)
    policy = policy_with(derived, s.expected_category, rule("LF.v3.s2"), rule("LF.v3.s5"))
    r = sync(account_state(findings), policy, derived, s.expected_category, sections)
    assert [x.section_id for x in r.rules] == ["LF.v3.s2", "LF.v3.s5"] and r.dropped_section_ids == []
    assert r.issues == [] and r.lean is None and r.category_covered is True
    assert not (r.account_missing or r.policy_missing or r.budget_hit or r.policy_date_mismatch)


def test_sync_policy_date_mismatch(ds, sections):
    """Read on the complaint date (the parallel run): no mismatch when the same policy set applies, a mismatch otherwise."""
    s = ds.scenarios["S-02"]
    findings, derived = facts(ds, s)
    assert derived.policy_date.date().isoformat() == "2026-10-14"
    same_set = policy_with(derived, s.expected_category, rule("LF.v3.s2"), policy_date=WORLD_TODAY)
    r = sync(account_state(findings), same_set, derived, s.expected_category, sections)
    assert r.policy_date_mismatch is False and r.issues == [] and r.lean is None

    # a disputed event from before LF.v3 took effect: the read on Oct 20 saw a different policy set
    old = derived.model_copy(update={"policy_date": datetime(2026, 4, 15)})
    r = sync(account_state(findings), same_set, old, s.expected_category, sections)
    # recorded, not re-read; here every rule is also dropped, so nothing covers the category either
    assert r.policy_date_mismatch is True and r.rules == [] and r.category_covered is False
    assert r.issues == [
        "no kept rule covers late_fee_autopay",
        "policy read on 2026-10-20, but a different policy set was in force on 2026-04-15",
        "rule LF.v3.s2 dropped: not in force on 2026-04-15",
    ]
    assert r.lean == "manual_escalation"


def test_sync_drops_unknown_and_stale(ds, sections):
    s = ds.scenarios["S-02"]
    findings, derived = facts(ds, s)
    assert derived.policy_date.date().isoformat() == "2026-10-14"
    policy = policy_with(derived, s.expected_category, rule("LF.v3.s2"), rule("XX.v9.s9"), rule("LF.v2.s3"))
    r = sync(account_state(findings), policy, derived, s.expected_category, sections)
    assert r.dropped_section_ids == ["XX.v9.s9", "LF.v2.s3"] and [x.section_id for x in r.rules] == ["LF.v3.s2"]
    assert r.issues == ["rule XX.v9.s9 dropped: unknown section", "rule LF.v2.s3 dropped: not in force on 2026-10-14"]
    assert r.lean is None


def test_sync_account_missing(ds, sections):
    s = ds.scenarios["S-02"]
    _, derived = facts(ds, s)
    policy = policy_with(derived, s.expected_category, rule("LF.v3.s2"))
    for account in (None, account_state(None)):
        r = sync(account, policy, derived, s.expected_category, sections)
        assert r.account_missing is True and r.issues == ["account findings missing"]
        assert r.lean == "need_more_evidence"


def test_sync_budget_hit(ds, sections):
    s = ds.scenarios["S-02"]
    findings, derived = facts(ds, s)
    policy = policy_with(derived, s.expected_category, rule("LF.v3.s2"))
    r = sync(account_state(findings, budget_hit=True), policy, derived, s.expected_category, sections)
    assert r.budget_hit is True and r.issues == ["account agent hit its tool budget"] and r.lean == "need_more_evidence"


def test_sync_policy_missing(ds, sections):
    s = ds.scenarios["S-02"]
    findings, derived = facts(ds, s)
    r = sync(account_state(findings), None, derived, s.expected_category, sections)
    assert r.policy_missing is True and r.issues == ["policy findings missing: no rules"] and r.lean == "manual_escalation"
    errored = policy_with(derived, s.expected_category, errors=["interpret failed: x"])
    r = sync(account_state(findings), errored, derived, s.expected_category, sections)
    assert r.policy_missing is True and r.issues == ["policy findings missing: interpret failed: x"]
    assert r.lean == "manual_escalation" and r.category_covered is False


def test_sync_category_not_covered(ds, sections):
    s = ds.scenarios["S-10"]
    findings, derived = facts(ds, s)
    only_cross_ref = policy_with(derived, s.expected_category, rule("LF.v3.s5"))
    r = sync(account_state(findings), only_cross_ref, derived, s.expected_category, sections)
    assert r.category_covered is False and r.issues == [f"no kept rule covers {s.expected_category}"]
    assert r.lean == "manual_escalation"
    covered = policy_with(derived, s.expected_category, rule("LF.v3.s5"), rule("RR.v1.s7"))
    r = sync(account_state(findings), covered, derived, s.expected_category, sections)
    assert r.category_covered is True and r.issues == [] and r.lean is None


def test_sync_lean_priority(ds, sections):
    s = ds.scenarios["S-02"]
    findings, derived = facts(ds, s)
    r = sync(account_state(findings, budget_hit=True), None, derived, s.expected_category, sections)
    assert r.lean == "manual_escalation"
    assert r.issues == ["policy findings missing: no rules", "account agent hit its tool budget"]
