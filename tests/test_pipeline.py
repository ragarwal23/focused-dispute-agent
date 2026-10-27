"""The orchestrator graph: the fan-out and join, and each bounded re-request firing once, never twice, never on anything else."""

from datetime import datetime

from dispute_agent.gate import Draft
import pytest
from langgraph.types import Command

from dispute_agent.pipeline import (
    FAULT_POLICY_DATE, MAX_ATTEMPTS, CategoryChoice, build_graph, initial_graph_state, investigate, thread_config,
)
from dispute_agent.policy_agent import PolicyReading
from dispute_agent.review import APPROVE
from test_derive import oracle_findings
from test_policy_agent import FakeModel, rule
from test_sync import account_state

NORMAL = ["classify", "account_agent", "policy_agent", "derive_states", "sync", "synthesize", "gate", "review", "create_review_case"]


class FakeAccountAgent:
    """Returns the states in `results` in order, repeating the last one; counts its runs."""

    def __init__(self, *results):
        self.results, self.calls = list(results), 0

    def invoke(self, state, config=None):
        self.calls += 1
        return self.results[min(self.calls, len(self.results)) - 1]


def run(ds, sections, agent, sid="S-02", decisions=(), checkpointer=None, synthesize_model=None):
    """Stream the graph with fakes for the three single-call models, answering each pause with the next decision
    (then approve); return the node order, the final state, the fakes and the graph."""
    s = ds.scenarios[sid]
    classify_model = FakeModel(CategoryChoice(category=s.expected_category, reason="x"))
    policy_model = FakeModel(PolicyReading(rules=[rule(i) for i in s.expected_policy_ids], summary="x"))
    draft = Draft(review_route=s.expected_route, event_ids=s.expected_required_event_ids, policy_source_ids=s.expected_policy_ids,
                  confidence="medium", open_questions=["x"], case_note="x")
    graph = build_graph(ds, sections, agent, classify_model, policy_model, synthesize_model or FakeModel(draft), checkpointer)
    order, final, decisions = [], None, list(decisions)
    step = initial_graph_state(s.account_id, s.rep_message, sid)
    while step is not None:
        paused = False
        for mode, chunk in graph.stream(step, thread_config(f"test-{sid}"), stream_mode=["updates", "values"]):
            if mode == "updates":
                paused |= "__interrupt__" in chunk
                order.extend(k for k in chunk if k != "__interrupt__")
            else:
                final = chunk
        step = Command(resume=(decisions.pop(0) if decisions else APPROVE).model_dump()) if paused else None
    return order, final, agent, policy_model, graph


def test_graph_clean(ds, sections):
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"])))
    order, final, agent, policy_model, graph = run(ds, sections, agent)
    assert order == NORMAL and agent.calls == 1 and policy_model.calls == 1
    assert final["account_attempts"] == 1 and final["policy_attempts"] == 1
    assert final["policy"].policy_date_basis == "complaint_date" and final["derived"].policy_date_basis == "disputed_event"
    assert final["sync"].issues == [] and final["gate"].gate_rejected is False and final["gate"].route == "adjustment_review"
    assert final["case"].created is True and final["case"].case_id == "C-004"

    inv = investigate(ds, sections, agent, "A-1002", ds.scenarios["S-02"].rep_message, ["t"], "S-02", graph=graph)
    assert inv.account_attempts == 1 and inv.policy_attempts == 1 and inv.case.route == "adjustment_review"


def test_clarify_once(ds, sections):
    """A failed finalize sends the Account Agent back once; the Policy Agent, which ran in parallel, is not rerun."""
    agent = FakeAccountAgent(account_state(None, errors=["finalize failed: x"]), account_state(oracle_findings(ds.scenarios["S-02"])))
    order, final, agent, policy_model, _ = run(ds, sections, agent)
    assert order == NORMAL[:5] + ["account_agent_retry", "derive_states_retry", "sync"] + NORMAL[5:]
    assert agent.calls == 2 and policy_model.calls == 1
    assert final["account_attempts"] == 2 and final["policy_attempts"] == 1
    assert final["account"]["findings"] is not None and final["sync"].account_missing is False and final["sync"].issues == []


def test_clarify_gives_up_after_two(ds, sections):
    agent = FakeAccountAgent(account_state(None, errors=["finalize failed: x"]))
    order, final, agent, _, _ = run(ds, sections, agent)
    assert agent.calls == MAX_ATTEMPTS == 2 and order.count("sync") == 2 and "synthesize" in order
    assert final["sync"].account_missing is True and final["sync"].lean == "need_more_evidence"
    assert final["derived"].policy_date_basis == "complaint_date" and final["derived"].fact_gap is True
    assert final["synthesis"].draft is not None and final["case"].created is True  # the run still reaches the end


def test_re_retrieve_once(ds, sections, monkeypatch):
    """The first read happened on a date with a different policy set: the Policy Agent runs once more on the disputed event's date."""
    monkeypatch.setattr("dispute_agent.pipeline.WORLD_TODAY", datetime(2026, 4, 15))  # LF.v2 was in force then
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"])))
    order, final, agent, policy_model, _ = run(ds, sections, agent)
    assert order == NORMAL[:5] + ["policy_agent_retry", "sync"] + NORMAL[5:]
    assert agent.calls == 1 and policy_model.calls == 2
    assert final["account_attempts"] == 1 and final["policy_attempts"] == 2
    assert final["policy"].policy_date_basis == "disputed_event" and final["policy"].policy_date.date().isoformat() == "2026-10-14"
    assert final["sync"].policy_date_mismatch is False and final["sync"].issues == []
    assert [r.section_id for r in final["sync"].rules] == ["LF.v3.s2", "LF.v3.s5"]


def test_both_re_requests_then_stop(ds, sections, monkeypatch):
    monkeypatch.setattr("dispute_agent.pipeline.WORLD_TODAY", datetime(2026, 4, 15))
    agent = FakeAccountAgent(account_state(None, errors=["finalize failed: x"]), account_state(oracle_findings(ds.scenarios["S-02"])))
    order, final, agent, policy_model, _ = run(ds, sections, agent)
    assert order == NORMAL[:5] + ["account_agent_retry", "derive_states_retry", "sync", "policy_agent_retry", "sync"] + NORMAL[5:]
    assert agent.calls == 2 and policy_model.calls == 2 and order.count("sync") == 3
    assert final["account_attempts"] == 2 and final["policy_attempts"] == 2 and final["sync"].issues == []


def test_no_re_request_on_budget_hit(ds, sections):
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]), budget_hit=True))
    order, final, agent, policy_model, _ = run(ds, sections, agent)
    assert order == NORMAL and agent.calls == 1 and policy_model.calls == 1
    assert final["sync"].budget_hit is True and final["sync"].lean == "need_more_evidence"


def test_fault_unset_is_identical(ds, sections, monkeypatch):
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"])))
    monkeypatch.delenv("DISPUTE_FAULT", raising=False)
    _, absent, *_ = run(ds, sections, agent)
    monkeypatch.setenv("DISPUTE_FAULT", "")
    _, empty, *_ = run(ds, sections, FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]))))
    assert absent["account_attempts"] == empty["account_attempts"] == 1
    assert absent["policy_attempts"] == empty["policy_attempts"] == 1


def test_fault_finalize_once(ds, sections, monkeypatch):
    monkeypatch.setenv("DISPUTE_FAULT", "finalize_once")
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"])))
    order, final, *_ = run(ds, sections, agent)
    assert order == NORMAL[:5] + ["account_agent_retry", "derive_states_retry", "sync"] + NORMAL[5:]
    assert final["account_attempts"] == 2 and final["account"]["findings"] is not None
    assert final["account"]["tool_errors"] == ["fault injected: finalize_once"]


def test_fault_policy_date_mismatch(ds, sections, monkeypatch):
    monkeypatch.setenv("DISPUTE_FAULT", "policy_date_mismatch")
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"])))
    order, final, _, policy_model, _ = run(ds, sections, agent)
    assert order == NORMAL[:5] + ["policy_agent_retry", "sync"] + NORMAL[5:]
    assert final["policy_attempts"] == policy_model.calls == 2
    assert final["policy"].policy_date_basis == "disputed_event"
    assert FAULT_POLICY_DATE.date().isoformat() == "2026-04-15"


def test_fault_unknown_value_raises(ds, sections, monkeypatch):
    monkeypatch.setenv("DISPUTE_FAULT", "not-a-fault")
    with pytest.raises(ValueError, match="finalize_once.*policy_date_mismatch"):
        build_graph(ds, sections, FakeAccountAgent(account_state(None)))
