"""The orchestrator graph: the fan-out and join, and sync recording what does not line up without sending an agent back."""

from datetime import datetime

from dispute_agent.sync.synthesize import Draft
from langgraph.types import Command

from dispute_agent.orchestrator.classify import CategoryChoice
from dispute_agent.config import WORLD_TODAY
from dispute_agent.orchestrator.graph import build_graph, initial_graph_state
from dispute_agent.orchestrator.run import investigate, thread_config
from dispute_agent.policy_agent.agent import PolicyReading
from dispute_agent.review.review import APPROVE
from test_derive import oracle_findings
from test_policy_agent import FakeModel, rule
from test_sync import account_state

NORMAL = ["classify", "account_agent", "policy_agent", "derive_states", "sync", "synthesize", "review", "create_review_case"]


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
    assert final["policy"].policy_date == WORLD_TODAY and final["derived"].policy_date_basis == "disputed_event"
    assert final["sync"].issues == [] and final["synthesis"].route == "adjustment_review" and "gate" not in final
    assert final["case"].created is True and final["case"].case_id == "C-004"

    inv = investigate(ds, sections, agent, "A-1002", ds.scenarios["S-02"].rep_message, ["t"], "S-02", graph=graph)
    assert inv.case.route == "adjustment_review"


def test_account_missing_goes_on(ds, sections):
    """A failed finalize is recorded, not retried: the Account Agent runs once and the run reaches the end, escalated."""
    agent = FakeAccountAgent(account_state(None, errors=["finalize failed: x"]))
    order, final, agent, policy_model, _ = run(ds, sections, agent)
    assert order == NORMAL and agent.calls == 1 and policy_model.calls == 1
    assert final["sync"].account_missing is True and final["sync"].lean == "need_more_evidence"
    assert final["derived"].policy_date_basis == "complaint_date" and final["derived"].fact_gap is True
    # no evidence was shown, so the fake draft's event IDs are rejected; the run still reaches the end, escalated
    assert final["synthesis"].draft is None and final["synthesis"].route == "manual_escalation"
    assert final["case"].created is True and final["case"].route == "manual_escalation"


def test_policy_date_mismatch_is_recorded(ds, sections, monkeypatch):
    """The parallel read happened on a date with a different policy set: recorded and escalated, not re-read."""
    monkeypatch.setattr("dispute_agent.orchestrator.graph.WORLD_TODAY", datetime(2026, 4, 15))  # LF.v2 was in force then
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"])))
    order, final, agent, policy_model, _ = run(ds, sections, agent)
    assert order == NORMAL and agent.calls == 1 and policy_model.calls == 1
    assert final["sync"].policy_date_mismatch is True and final["sync"].lean == "manual_escalation"
    assert "policy read on 2026-04-15, but a different policy set was in force on 2026-10-14" in final["sync"].issues


def test_budget_hit_is_recorded(ds, sections):
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]), budget_hit=True))
    order, final, agent, policy_model, _ = run(ds, sections, agent)
    assert order == NORMAL and agent.calls == 1 and policy_model.calls == 1
    assert final["sync"].budget_hit is True and final["sync"].lean == "need_more_evidence"
