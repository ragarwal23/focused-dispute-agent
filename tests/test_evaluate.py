"""The eval scorer: a failed finalize, the final route vs the draft's route, confidence consistency, the matrix."""

import json
from pathlib import Path

import pytest

from dispute_agent.evaluation.evaluate import CHECKS, HEADER, confusion_lines, row_line, run_eval, score
from dispute_agent.evaluation.golden import investigation
from dispute_agent.sync.synthesize import Draft
from test_derive import oracle_findings
from test_experiment import FakeLangSmith
from test_review import graph_for, s15_agent

DATA = str(Path(__file__).resolve().parent.parent / "data")


def test_score_failed_finalize_is_not_right(ds, sections):
    s = ds.scenarios["S-06"]
    c = score(s, investigation(ds, sections, s, None, None), ds)
    model_checks = ("disputed_ok", "required_found", "forbidden_clear", "payment_ok", "refund_ok", "gap_ok", "oam_ok")
    assert not any(c[k] for k in model_checks)
    assert c["draft_route"] is None and c["route_final"] == "manual_escalation" and c["policy_consistent"] is True
    assert c["route_ok"] is (s.expected_route == "manual_escalation") and c["confidence_ok"] is False
    assert c["draft_events_ok"] is False and c["draft_policy_ok"] is False and c["synth_errors"] == ["no draft"]
    assert len(row_line(s, c)) == len(HEADER)


def test_score_route_and_confidence(ds, sections):
    s = ds.scenarios["S-15"]
    draft = Draft(review_route="need_more_evidence", event_ids=s.expected_required_event_ids,
                  policy_source_ids=s.expected_policy_ids, confidence="high", open_questions=["which refund?"], case_note="x")
    c = score(s, investigation(ds, sections, s, oracle_findings(s), draft), ds)
    assert c["route_ok"] is True and c["confidence_ok"] is False
    assert c["draft_events_ok"] is True and c["draft_policy_ok"] is True and c["policy_consistent"] is True
    assert c["trajectory_ok"] is True and c["action_ok"] is True
    assert all(c[k] for k in CHECKS if k != "confidence_ok")


def test_score_action_against_existing_case(ds, sections):
    """S-11 has an open duplicate (C-002): the right action is to return it, not create; S-05's closed C-001 must not block."""
    for sid, created, case_id in (("S-11", False, "C-002"), ("S-05", True, "C-004")):
        s = ds.scenarios[sid]
        draft = Draft(review_route=s.expected_route, event_ids=s.expected_required_event_ids,
                      policy_source_ids=s.expected_policy_ids, confidence="medium", open_questions=["x"], case_note="x")
        inv = investigation(ds, sections, s, oracle_findings(s), draft)
        assert inv.case.created is created and inv.case.case_id == case_id and score(s, inv, ds)["action_ok"] is True


def test_score_policy_consistent(ds, sections):
    """S-02: a received-in-grace payment rules out explain_events, so that route is scored inconsistent, and wrong."""
    s = ds.scenarios["S-02"]
    draft = Draft(review_route="explain_events", event_ids=s.expected_required_event_ids, policy_source_ids=s.expected_policy_ids,
                  confidence="medium", case_note="x")
    c = score(s, investigation(ds, sections, s, oracle_findings(s), draft), ds)
    assert c["route_final"] == c["draft_route"] == "explain_events"  # nothing overrides the draft at runtime
    assert c["route_ok"] is False and c["policy_consistent"] is False
    assert "gate_rejected" not in c and "gate_reasons" not in c


def test_confusion_matrix():
    rows = [
        {"route_expected": "adjustment_review", "checks": {"route_final": "adjustment_review"}},
        {"route_expected": "adjustment_review", "checks": {"route_final": "manual_escalation"}},
        {"route_expected": "need_more_evidence", "checks": {"route_final": "need_more_evidence"}},
    ]
    lines = confusion_lines(rows)
    assert len(lines) == 5 and lines[1].split() == ["adjustment_review", "1", "0", "0", "1"]
    assert lines[3].split() == ["need_more_evidence", "0", "0", "1", "0"]


def test_row_has_review(ds, sections, tmp_path, monkeypatch):
    """The eval approves every pause: each row records the approval and no follow-up."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("dispute_agent.evaluation.evaluate.build_graph", lambda ds_, sections_, *a, **k: graph_for(ds, sections, "S-15", s15_agent(ds)))
    assert run_eval(DATA, "dev", "S-15", 0, 0, 0) == 0
    row = json.loads(next((tmp_path / "runs").glob("eval_*.json")).read_text())["rows"][0]
    assert row["review"]["action"] == "approve" and row["followups"] == 0


def test_producer_exit_codes(tmp_path, monkeypatch, capsys):
    """golden_echo passes every scenario with no LLM and no graph; empty_stub fails; both write a JSON with the producer."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("dispute_agent.evaluation.evaluate.build_graph", lambda *a, **k: pytest.fail("graph built"))
    assert run_eval(DATA, "all", None, 1.0, 1.0, 1.0, producer="golden_echo") == 0
    out = capsys.readouterr().out
    path = max((tmp_path / "runs").glob("eval_*.json"))
    assert f"wrote runs/{path.name}" in out and "grade PASS 16/16" in out  # the stamp is fixed before the run
    report = json.loads(path.read_text())
    assert report["producer"] == "golden_echo" and len(report["rows"]) == 16 and "experiment_name" not in report
    assert run_eval(DATA, "dev", None, 0, 0, 0, producer="empty_stub") == 1
    assert "grade PASS 0/12" in capsys.readouterr().out


def test_eval_experiment_golden_echo(tmp_path, monkeypatch, capsys):
    """--experiment runs the same rows through a LangSmith experiment: JSON and experiment share the stamp, the split
    picks the examples, rows run one at a time; without a key it stops before any graph is built."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("dispute_agent.evaluation.evaluate.build_graph", lambda *a, **k: pytest.fail("graph built"))
    client = FakeLangSmith()
    monkeypatch.setattr("dispute_agent.evaluation.evaluate.Client", lambda: client)
    monkeypatch.setattr("dispute_agent.evaluation.evaluate.langsmith_evaluate", client.evaluate)
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")
    assert run_eval(DATA, "all", None, 1.0, 1.0, 1.0, producer="golden_echo", experiment=True) == 0
    path = next((tmp_path / "runs").glob("eval_*.json"))
    report = json.loads(path.read_text())
    assert len(report["rows"]) == 16 and report["experiment_name"] == "release-x" and report["experiment_url"].startswith("https://")
    assert client.kwargs["experiment_prefix"] == "golden_echo-" + path.stem.removeprefix("eval_")
    assert client.kwargs["max_concurrency"] == 0 and client.kwargs["metadata"]["eval_path"] == f"runs/{path.name}"
    assert all(fb[0]["results"][6]["value"] == "PASS" for _, fb in client.feedback.values())  # every grade PASS
    assert capsys.readouterr().out.strip().endswith("experiment release-x: https://smith.langchain.com/o/x/datasets/ds-1/compare?e=1")
    client = FakeLangSmith()
    monkeypatch.setattr("dispute_agent.evaluation.evaluate.langsmith_evaluate", client.evaluate)
    assert run_eval(DATA, "dev", None, 0, 0, 0, producer="golden_echo", experiment=True) == 0
    assert len(client.feedback) == 12 and not {"S-04", "S-08", "S-12", "S-16"} & set(client.feedback)
    capsys.readouterr()
    monkeypatch.delenv("LANGSMITH_API_KEY")
    monkeypatch.setattr("dispute_agent.evaluation.evaluate.load_dataset", lambda *a: pytest.fail("ran without a key"))
    assert run_eval(DATA, "all", None, 0, 0, 0, experiment=True) == 2
    assert capsys.readouterr().out.strip() == "eval: LANGSMITH_API_KEY is not set"
