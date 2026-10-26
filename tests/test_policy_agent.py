from datetime import datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from dispute_agent.account_agent import AccountFindings, derive_states
from dispute_agent.data import Scenario, load_dataset
from dispute_agent.policy_agent import PolicyReading, RuleReading, candidates, check_citations, load_policies, run_policy_agent, search_policy

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="session")
def ds(): return load_dataset(DATA)


@pytest.fixture(scope="session")
def sections(): return load_policies(DATA / "policies")


def facts(ds, s):
    findings = AccountFindings(relevant_event_ids=s.expected_required_event_ids, disputed_event_id=s.expected_disputed_event_id, notes_summary="", summary="")
    return findings, derive_states(findings, s.expected_category, s.account_id, ds)


def test_store_and_dates(sections):
    assert len(sections) == 37 and sections["LF.v2.s1"].effective_to.isoformat() == "2026-04-30"
    assert sections["RF.v1.s1"].effective_to is None


def test_version_boundary(sections):
    assert "LF.v2.s1" in {s.section_id for s in candidates(sections, "late_fee_autopay", datetime(2026, 4, 30).date())}
    assert "LF.v2.s1" not in {s.section_id for s in candidates(sections, "late_fee_autopay", datetime(2026, 5, 1).date())}


@pytest.mark.parametrize("sid", [f"S-{i:02d}" for i in (1,2,3,5,6,7,9,10,11,13,14,15)])
def test_dev_retrieval_recall(ds, sections, sid):
    s = ds.scenarios[sid]; _, derived = facts(ds, s)
    got = search_policy(sections, s.expected_category, derived.policy_date, s.rep_message)
    assert set(s.expected_policy_ids) <= {r.section.section_id for r in got}


def test_cross_refs_and_determinism(sections):
    one = search_policy(sections, "returned_reversed_payment", datetime(2026, 10, 7), "reversal keyed in error late fee")
    two = search_policy(sections, "returned_reversed_payment", datetime(2026, 10, 7), "reversal keyed in error late fee")
    assert [r.section.section_id for r in one] == [r.section.section_id for r in two]
    assert next(r for r in one if r.section.section_id == "LF.v3.s5").via == "RR.v1.s7"


def test_answer_key_is_valid(ds, sections):
    assert all(s.expected_policy_ids and set(s.expected_policy_ids) <= set(sections) for s in ds.scenarios.values())
    with pytest.raises(ValidationError): Scenario.model_validate({"scenario_id":"x"})


def test_citation_check(sections):
    retrieved = search_policy(sections, "late_fee_autopay", datetime(2026, 10, 14), "late fee")
    reading = PolicyReading(rules=[
        RuleReading(section_id="LF.v3.s5", condition="x", outcome="fee_adjustment_review", why="x", policy_ambiguous=False),
        RuleReading(section_id="LF.v3.s5", condition="x", outcome="fee_adjustment_review", why="x", policy_ambiguous=False),
        RuleReading(section_id="LF.v2.s3", condition="x", outcome="procedural", why="x", policy_ambiguous=False),
    ], summary="x")
    rules, dropped = check_citations(reading, retrieved)
    assert [r.section_id for r in rules] == ["LF.v3.s5"] and dropped == ["LF.v2.s3"]


class Fake:
    def __init__(self, result): self.result, self.calls = result, 0
    def invoke(self, messages, config=None): self.calls += 1; return self.result


def test_run_and_errors(ds, sections):
    s = ds.scenarios["S-02"]; account, derived = facts(ds, s)
    good = PolicyReading(rules=[RuleReading(section_id="LF.v3.s2", condition="paid", outcome="explains_outcome", why="x", policy_ambiguous=False)], summary="x")
    fake = Fake(good); result = run_policy_agent(sections, s.expected_category, s.rep_message, account, derived, fake)
    assert fake.calls == 1 and result.errors == [] and result.rules[0].section_id == "LF.v3.s2"
    bad = Fake(RuntimeError("boom")); bad.invoke = lambda messages, config=None: (_ for _ in ()).throw(RuntimeError("boom"))
    result = run_policy_agent(sections, s.expected_category, s.rep_message, account, derived, bad)
    assert result.errors == ["interpret failed: RuntimeError: boom"]
