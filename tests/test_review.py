"""The human pause after the gate: the payload, approve, override, the one answer, refusals, SQLite resume and recovery."""

import logging

import pytest
from pydantic import ValidationError

from conftest import DATA
from dispute_agent.__main__ import main
from dispute_agent.account_agent.agent import AccountFindings
from dispute_agent.orchestrator.classify import CategoryChoice
from dispute_agent.orchestrator.graph import MAX_FOLLOWUPS, build_graph, sqlite_checkpointer
from dispute_agent.orchestrator.run import Investigation, investigate, resume, start, thread_status
from dispute_agent.policy_agent.agent import PolicyReading
from dispute_agent.review.review import APPROVE, Pause, ReviewDecision, build_request, with_answer
from dispute_agent.sync.synthesize import Draft
from test_derive import oracle_findings
from test_graph import NORMAL, FakeAccountAgent, run
from test_policy_agent import FakeModel, rule
from test_sync import account_state

FOLLOWUP = ["account_agent_followup", "derive_states_followup", "sync", "synthesize", "gate", "review"]
ANSWER = ReviewDecision(action="answer", answer="customer means the $731.50 refund requested Oct 6")


def s15_agent(ds, *extra):
    """S-15's Account Agent: both refund requests as candidates first, then the $731.50 one once the rep has answered."""
    picked = AccountFindings(relevant_event_ids=["E-0128", "E-0138"], disputed_event_id="E-0138", notes_summary="", summary="")
    return FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-15"])), *(extra or [account_state(picked)]))


def graph_for(ds, sections, sid, agent, checkpointer=None, classify_model=None):
    s = ds.scenarios[sid]
    draft = Draft(review_route=s.expected_route, event_ids=s.expected_required_event_ids, policy_source_ids=s.expected_policy_ids,
                  confidence="medium", open_questions=["which refund?"], case_note="x")
    return build_graph(
        ds, sections, agent, classify_model or FakeModel(CategoryChoice(category=s.expected_category, reason="x")),
        FakeModel(PolicyReading(rules=[rule(i) for i in s.expected_policy_ids], summary="x")), FakeModel(draft), checkpointer,
    )


def test_decision_shapes():
    assert APPROVE.action == "approve"
    ReviewDecision(action="override", route="manual_escalation", reason="supervisor asked")
    for bad in ({"action": "override", "route": "manual_escalation"}, {"action": "override", "reason": "r"},
                {"action": "answer"}, {"action": "approve", "answer": "x"}, {"action": "approve", "route": "explain_events"}):
        with pytest.raises(ValidationError):
            ReviewDecision(**bad)


@pytest.mark.parametrize("route,followups,expected", [
    ("need_more_evidence", 0, True), ("need_more_evidence", 1, False), ("adjustment_review", 0, False), ("manual_escalation", 0, False),
])
def test_can_answer_truth_table(ds, sections, route, followups, expected):
    _, final, *_ = run(ds, sections, FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]))))
    state = {**final, "gate": final["gate"].model_copy(update={"route": route}), "followups": followups}
    assert build_request(state, "S-02", MAX_FOLLOWUPS).can_answer is expected


def test_with_answer():
    assert with_answer("where is my refund", "the $731.50 one") == "where is my refund\n\nRep follow-up (after review): the $731.50 one"


def test_pause_payload_s02(ds, sections):
    graph = graph_for(ds, sections, "S-02", FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]))))
    out = start(graph, "A-1002", ds.scenarios["S-02"].rep_message, "t-s02", ["t"], "S-02")
    assert isinstance(out, Pause) and out.thread_id == "t-s02"
    r = out.request
    assert r.thread_label == "S-02" and r.route == "adjustment_review" and r.can_answer is False and r.gate_rejected is False
    assert r.draft["review_route"] == "adjustment_review" and (r.account_attempts, r.policy_attempts, r.followups) == (1, 1, 0)
    assert thread_status(graph, "t-s02") == "paused"


def test_pause_payload_s15(ds, sections):
    out = start(graph_for(ds, sections, "S-15", s15_agent(ds)), "A-1016", ds.scenarios["S-15"].rep_message, "t-s15", [], "S-15")
    assert out.request.route == "need_more_evidence" and out.request.can_answer is True
    assert out.request.open_questions == ["which refund?"]


def test_approve_files_gate_route(ds, sections):
    order, final, *_ = run(ds, sections, FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]))))
    assert order == NORMAL and final["review"].action == "approve"
    assert final["case"].route == final["gate"].route == "adjustment_review"


def test_override_files_rep_route(ds, sections):
    override = ReviewDecision(action="override", route="manual_escalation", reason="customer disputes the call record")
    order, final, *_ = run(ds, sections, FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]))), decisions=[override])
    assert order == NORMAL and final["case"].route == "manual_escalation"
    assert final["gate"].route == "adjustment_review" and final["review"].reason == "customer disputes the call record"


def test_answer_reruns_account_agent_once(ds, sections):
    agent = s15_agent(ds)
    order, final, agent, policy_model, _ = run(ds, sections, agent, "S-15", decisions=[ANSWER])
    assert order == NORMAL[:-1] + FOLLOWUP + ["create_review_case"]
    assert agent.calls == 2 and policy_model.calls == 1
    assert final["followups"] == 1 and final["account_attempts"] == 2 and final["review"].action == "approve"
    assert final["rep_message"].endswith("Rep follow-up (after review): " + ANSWER.answer) and final["rep_answer"] == ANSWER.answer
    assert final["derived"].disputed_event_id == "E-0138" and final["derived"].fact_ambiguous is False


def test_second_pause_refuses_answer(ds, sections):
    graph = graph_for(ds, sections, "S-15", s15_agent(ds))
    start(graph, "A-1016", ds.scenarios["S-15"].rep_message, "t", [], "S-15")
    second = resume(graph, "t", ANSWER, [], "S-15")
    assert isinstance(second, Pause) and second.request.can_answer is False and second.request.followups == 1


def test_answer_refused_when_not_allowed(ds, sections):
    graph = graph_for(ds, sections, "S-02", FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]))))
    start(graph, "A-1002", ds.scenarios["S-02"].rep_message, "t", [], "S-02")
    again = resume(graph, "t", ANSWER, [], "S-02")  # an answer the pause does not take: the same pause comes back
    assert isinstance(again, Pause) and again.request.can_answer is False and thread_status(graph, "t") == "paused"
    out = resume(graph, "t", APPROVE, [], "S-02")
    assert isinstance(out, Investigation) and out.case.route == "adjustment_review"


def test_answer_then_clarify_bound(ds, sections):
    """The follow-up's finalize fails: attempts are already at the bound, so sync does not send it back; the run reaches review."""
    agent = s15_agent(ds, account_state(None, errors=["finalize failed: x"]))
    order, final, agent, *_ = run(ds, sections, agent, "S-15", decisions=[ANSWER])
    assert order == NORMAL[:-1] + FOLLOWUP + ["create_review_case"] and agent.calls == 2
    assert final["sync"].account_missing is True and "account_agent_retry" not in order


def test_sqlite_resume_across_savers(ds, sections, tmp_path, caplog):
    """Pause in one 'process', resume in another: state (pydantic models, messages, datetimes) round-trips the file."""
    path, s = tmp_path / "checkpoints.sqlite", ds.scenarios["S-02"]
    agent = FakeAccountAgent(account_state(oracle_findings(s)))
    first = graph_for(ds, sections, "S-02", agent, sqlite_checkpointer(path))
    assert isinstance(start(first, "A-1002", s.rep_message, "t", [], "S-02"), Pause)
    del first
    second = graph_for(ds, sections, "S-02", agent, sqlite_checkpointer(path))
    with caplog.at_level(logging.WARNING):
        out = resume(second, "t", APPROVE, [], "S-02")
    assert isinstance(out, Investigation) and out.thread_id == "t" and out.case.case_id == "C-004"
    assert out.derived.policy_date.year == 2026 and out.account["findings"].disputed_event_id == s.expected_disputed_event_id
    assert not [r for r in caplog.records if "unregistered type" in r.getMessage() or "Blocked" in r.getMessage()]
    assert thread_status(second, "t") == "finished"


def test_sqlite_fan_out(ds, sections, tmp_path):
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"])))
    order, final, *_ = run(ds, sections, agent, checkpointer=sqlite_checkpointer(tmp_path / "c.sqlite"))
    assert order == NORMAL and final["case"].created is True


class FlakyModel(FakeModel):
    """Raises on the first call (an API error), then answers."""

    def invoke(self, messages, config=None):
        if self.calls == 0:
            self.calls += 1
            raise RuntimeError("API down")
        return super().invoke(messages, config)


def test_recovery_resumes_from_checkpoint(ds, sections):
    """A node that raises leaves the thread at that node; resume re-runs it and goes on to the review pause."""
    s = ds.scenarios["S-02"]
    flaky = FlakyModel(CategoryChoice(category=s.expected_category, reason="x"))
    graph = graph_for(ds, sections, "S-02", FakeAccountAgent(account_state(oracle_findings(s))), classify_model=flaky)
    with pytest.raises(RuntimeError, match="API down"):
        start(graph, "A-1002", s.rep_message, "t", [], "S-02")
    assert thread_status(graph, "t") == "paused"
    out = resume(graph, "t", APPROVE, [], "S-02")  # no pause was waiting: the run continues to review, nothing approved
    assert isinstance(out, Pause) and out.request.route == "adjustment_review"
    assert isinstance(resume(graph, "t", APPROVE, [], "S-02"), Investigation)


def test_thread_status_unknown_finished(ds, sections):
    graph = graph_for(ds, sections, "S-02", FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]))))
    assert thread_status(graph, "no-such-thread") == "unknown"
    start(graph, "A-1002", ds.scenarios["S-02"].rep_message, "t", [], "S-02")
    resume(graph, "t", APPROVE, [], "S-02")
    assert thread_status(graph, "t") == "finished"


def test_investigate_auto_approves(ds, sections):
    s = ds.scenarios["S-15"]
    graph = graph_for(ds, sections, "S-15", s15_agent(ds))
    inv = investigate(ds, sections, None, s.account_id, s.rep_message, ["t"], "S-15", graph=graph)
    assert inv.review.action == "approve" and inv.followups == 0 and inv.thread_id.startswith("S-15-")
    assert inv.case.route == "need_more_evidence"


def test_cli_resume_prechecks(ds, sections, tmp_path, monkeypatch, capsys):
    """`resume` refuses before any model is built: an unknown thread, an answer this pause does not take, a finished thread."""
    path = tmp_path / "c.sqlite"
    monkeypatch.setattr("dispute_agent.__main__.CHECKPOINTS", path)
    monkeypatch.setattr("dispute_agent.__main__.build_account_agent", lambda ds: pytest.fail("a model was built"))
    graph = graph_for(ds, sections, "S-02", FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]))), sqlite_checkpointer(path))
    start(graph, "A-1002", ds.scenarios["S-02"].rep_message, "t", [], "S-02")
    assert main(["--data", str(DATA), "resume", "nope", "--approve"]) == 2
    assert main(["--data", str(DATA), "resume", "t", "--answer", "x"]) == 2
    resume(graph, "t", APPROVE, [], "S-02")
    assert main(["--data", str(DATA), "resume", "t", "--approve"]) == 2
    out = capsys.readouterr().out
    assert "no paused run for thread nope" in out and "takes --approve or --route only" in out and "already finished: case C-004" in out
