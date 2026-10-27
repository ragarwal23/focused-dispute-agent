"""The Policy Agent: loading, date-aware search, the answer key, the citation check, and a run with fake models."""

from datetime import datetime

import pytest
from pydantic import ValidationError

from dispute_agent.account_agent.agent import AccountFindings
from dispute_agent.account_agent.derive import derive_states
from dispute_agent.config import WORLD_TODAY
from dispute_agent.data import Scenario
from dispute_agent.policy_agent.agent import (
    NO_FACTS_YET, POLICY_SYSTEM, PolicyReading, RuleReading, check_citations, render_seed, run_policy_agent,
)
from dispute_agent.policy_agent.retrieve import candidates, search_policy
from test_derive import DEV_IDS


def facts(ds, s):
    """The account findings and derived facts a perfect Account Agent would hand over for scenario `s`."""
    findings = AccountFindings(
        relevant_event_ids=s.expected_required_event_ids, disputed_event_id=s.expected_disputed_event_id,
        notes_summary="", summary="",
    )
    return findings, derive_states(findings, s.expected_category, s.account_id, ds)


def rule(section_id, outcome="fee_adjustment_review"):
    return RuleReading(section_id=section_id, condition="x", outcome=outcome, why="x", policy_ambiguous=False)


class FakeModel:
    def __init__(self, result):
        self.result, self.calls = result, 0

    def invoke(self, messages, config=None):
        self.calls += 1
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def test_store_and_dates(sections):
    assert len(sections) == 37
    assert sections["LF.v2.s1"].effective_to.isoformat() == "2026-04-30"
    assert sections["RF.v1.s1"].effective_to is None


def test_version_boundary(sections):
    def ids_on(day):
        return {s.section_id for s in candidates(sections, "late_fee_autopay", day.date())}

    assert "LF.v2.s1" in ids_on(datetime(2026, 4, 30))
    assert "LF.v2.s1" not in ids_on(datetime(2026, 5, 1))


@pytest.mark.parametrize("sid", DEV_IDS)
def test_dev_retrieval_recall(ds, sections, sid):
    s = ds.scenarios[sid]
    _, derived = facts(ds, s)
    got = search_policy(sections, s.expected_category, derived.policy_date, s.rep_message)
    assert set(s.expected_policy_ids) <= {r.section.section_id for r in got}


def test_cross_refs_and_determinism(sections):
    def search():
        return search_policy(sections, "returned_reversed_payment", datetime(2026, 10, 7), "reversal keyed in error late fee")

    one, two = search(), search()
    assert [r.section.section_id for r in one] == [r.section.section_id for r in two]
    assert next(r for r in one if r.section.section_id == "LF.v3.s5").via == "RR.v1.s7"


def test_answer_key_is_valid(ds, sections):
    assert all(s.expected_policy_ids and set(s.expected_policy_ids) <= set(sections) for s in ds.scenarios.values())
    with pytest.raises(ValidationError):
        Scenario.model_validate({"scenario_id": "x"})


def test_citation_check(sections):
    retrieved = search_policy(sections, "late_fee_autopay", datetime(2026, 10, 14), "late fee")
    reading = PolicyReading(
        rules=[rule("LF.v3.s5"), rule("LF.v3.s5"), rule("LF.v3.s5", "refer_to_supervisor"), rule("LF.v2.s3", "procedural")],
        summary="x",
    )
    rules, dropped = check_citations(reading, retrieved)
    # a section may state two rules with different outcomes (LF.v3.s6 does); an exact repeat is dropped silently
    assert [(r.section_id, r.outcome) for r in rules] == [("LF.v3.s5", "fee_adjustment_review"), ("LF.v3.s5", "refer_to_supervisor")]
    assert dropped == ["LF.v2.s3"]


def test_run_and_errors(ds, sections):
    """It runs in parallel with the Account Agent: the complaint date, no account findings, no derived facts."""
    s = ds.scenarios["S-02"]
    good = FakeModel(PolicyReading(rules=[rule("LF.v3.s2", "explains_outcome")], summary="x"))
    result = run_policy_agent(sections, s.expected_category, s.rep_message, WORLD_TODAY, model=good)
    assert good.calls == 1 and result.errors == [] and result.rules[0].section_id == "LF.v3.s2"
    assert result.policy_date == WORLD_TODAY and set(s.expected_policy_ids) <= {r.section.section_id for r in result.retrieved}

    seed = render_seed(s.expected_category, s.rep_message, result.retrieved, WORLD_TODAY)
    assert f"Derived facts: {NO_FACTS_YET}" in seed and f"Evidence events: {NO_FACTS_YET}" in seed
    assert "Account summary: none" in seed and "Policy date: 2026-10-20 (complaint_date)" in seed

    bad = FakeModel(RuntimeError("boom"))
    result = run_policy_agent(sections, s.expected_category, s.rep_message, WORLD_TODAY, model=bad)
    assert result.errors == ["interpret failed: RuntimeError: boom"]


def test_ambiguity_rule_is_narrow():
    """Live runs flagged sections that merely did not cover the case, or business-day counts the gate computes."""
    assert "can be read two ways that lead to different outcomes" in POLICY_SYSTEM
    assert "does not cover this situation is not ambiguous" in POLICY_SYSTEM
    assert "drops a condition the text states" in POLICY_SYSTEM
    assert "date arithmetic" in POLICY_SYSTEM and "never ambiguity" in POLICY_SYSTEM
