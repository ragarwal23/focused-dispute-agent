import json
from datetime import datetime, timezone
from types import SimpleNamespace

from dispute_agent.evaluation.golden import read_golden
from dispute_agent.evaluation.metrics import METRIC_NAMES
from dispute_agent.evaluation.validate import (
    DATASET_NAME, NORMAL, axes_feedback, dataset_examples, expected_children, expected_resume_children, judge_feedback,
    replay_output, stability, sync_langsmith, trace_check, upsert_dataset,
)
from dispute_agent.__main__ import main
from test_report import eval_data, judge_data


def test_expected_children():
    assert expected_children(1, 1) == NORMAL and NORMAL[-1] == "review"
    assert expected_children(2, 1) == NORMAL[:5] + ["account_agent_retry", "derive_states_retry", "sync"] + NORMAL[5:]
    assert expected_children(1, 2) == NORMAL[:5] + ["policy_agent_retry", "sync"] + NORMAL[5:]
    assert expected_children(2, 2) == NORMAL[:5] + ["account_agent_retry", "derive_states_retry", "sync", "policy_agent_retry", "sync"] + NORMAL[5:]


def test_stability_matrix_and_gate():
    reports = []
    for good, flaky, known in ((True, True, True), (True, True, False), (True, False, False)):
        reports.append({"split": "dev", "rows": [
            {"scenario_id": "S-01", "checks": {"route_ok": good}, "account_attempts": 1, "policy_attempts": 1},
            {"scenario_id": "S-02", "checks": {"route_ok": flaky}, "account_attempts": 2, "policy_attempts": 1},
            {"scenario_id": "S-03", "checks": {"route_ok": known}, "account_attempts": 1, "policy_attempts": 2},
        ]})
    lines, ok = stability(reports, {("S-03", "route_ok")})
    assert ok is True and "S-02: route_ok=2/3" in lines
    assert any("send-backs=1 re-retrieves=1 exceptions=0" in line for line in lines)
    _, ok = stability(reports, set())
    assert ok is False


class FakeClient:
    def __init__(self, roots, children):
        self.roots, self.children = roots, children

    def list_runs(self, **kwargs):
        return self.roots if kwargs.get("is_root") else self.children


def _run(**values):
    now = datetime.now(timezone.utc)
    return {"start_time": now, "end_time": now, "tags": [], "prompt_tokens": 0, "completion_tokens": 0,
            "total_cost": 0, **values}


def test_trace_check_match_and_mismatch():
    root = _run(id="root", trace_id="trace", name="dispute S-02", tags=["slice=investigate"], prompt_tokens=5,
                outputs={"account_attempts": 1, "policy_attempts": 1})
    children = [_run(id=str(i), parent_run_id="root", name=name) for i, name in enumerate(NORMAL)]
    client = FakeClient([root, _run(name="dispute ignored", tags=[], prompt_tokens=5)], children)
    lines, ok = trace_check(client, "project", wait=0)
    assert ok is True and lines[-1].endswith("match")
    lines, ok = trace_check(FakeClient([root], children[:-1]), "project", wait=0)
    assert ok is False and "MISMATCH" in lines[-1]


def test_trace_check_waits_then_exits_2():
    lines, ok = trace_check(FakeClient([], []), "project", wait=0)
    assert ok is False and lines == ["no qualifying run found within 0s (wanted 1, found 0)"]


def test_resume_shapes():
    assert expected_resume_children(False) == [
        "review", "account_agent_followup", "derive_states_followup", "sync", "synthesize", "review"]
    assert expected_resume_children(True)[4:6] == ["policy_agent_retry", "sync"]


def test_trace_check_resume_root():
    """An answer-path resume root is checked against its own shapes; its attempt counts do not matter."""
    root = _run(id="root", trace_id="trace", name="dispute A-1016 resume", tags=["slice=investigate", "resume"],
                prompt_tokens=5, outputs={"account_attempts": 2, "policy_attempts": 1, "followups": 1})
    children = [_run(id=str(i), parent_run_id="root", name=n) for i, n in enumerate(expected_resume_children(False))]
    lines, ok = trace_check(FakeClient([root], children), "project", wait=0)
    assert ok is True and lines[-1].endswith("match")
    lines, ok = trace_check(FakeClient([root], children[:-1]), "project", wait=0)
    assert ok is False and "MISMATCH" in lines[-1]
    recovery = {**root, "outputs": {"account_attempts": 1, "policy_attempts": 1, "followups": 0}}
    lines, ok = trace_check(FakeClient([recovery], children[:3]), "project", wait=0)
    assert ok is True and lines == ["dispute A-1016 resume: recovery resume, shape not checked"]


class FakeLangSmith:
    """Records the dataset calls; `evaluate` runs the target and evaluators itself so the feedback shapes are checked."""

    def __init__(self, exists=True):
        self.exists, self.calls, self.examples = exists, [], []

    def has_dataset(self, dataset_name):
        self.calls.append(("has", dataset_name))
        return self.exists

    def delete_dataset(self, dataset_name):
        self.calls.append(("delete", dataset_name))

    def create_dataset(self, name, description=""):
        self.calls.append(("create", name))
        return SimpleNamespace(id="ds-1", name=name)

    def create_examples(self, dataset_id, examples):
        self.calls.append(("examples", dataset_id, len(examples)))
        self.examples = examples

    def evaluate(self, target, data, evaluators, **kwargs):
        self.kwargs, self.feedback = kwargs, {}
        for ex in self.examples:
            example = SimpleNamespace(inputs=ex["inputs"], outputs=ex["outputs"])
            run = SimpleNamespace(outputs=target(ex["inputs"]))
            self.feedback[ex["inputs"]["scenario_id"]] = (run.outputs, [f(run, example) for f in evaluators])
        return SimpleNamespace(experiment_name="release-x", url="https://smith.langchain.com/o/x/datasets/ds-1/compare?e=1")


def test_examples_shape():
    examples = dataset_examples(read_golden())
    assert len(examples) == 16 and examples[0]["inputs"] == {"scenario_id": "S-01", "account_id": "A-1001", "rep_message": examples[0]["inputs"]["rep_message"]}
    assert examples[0]["outputs"]["expected_route"] == "explain_events" and examples[0]["outputs"]["expected_required_event_ids"] == ["E-0081", "E-0117"]
    assert examples[3]["metadata"] == {"split": "heldout", "trap": "injection", "wording": "messy", "note_driven": False}
    assert "rationale" in examples[0]["outputs"]
    client = FakeLangSmith(exists=True)
    upsert_dataset(client, read_golden())
    assert client.calls == [("has", DATASET_NAME), ("delete", DATASET_NAME), ("create", DATASET_NAME), ("examples", "ds-1", 16)]
    client = FakeLangSmith(exists=False)
    upsert_dataset(client, read_golden())
    assert [c[0] for c in client.calls] == ["has", "create", "examples"]


def test_replay_target(ds):
    rows = {r["scenario_id"]: r for r in eval_data(ds)["rows"]}
    out = replay_output(rows["S-01"])
    assert out["status"] == "ok" and out["route"] == "explain_events" and out["case_note"] == "The fee stands." and out["case_created"] is True
    s07 = replay_output(rows["S-07"])
    assert s07["draft_route"] is None and s07["route"] == "manual_escalation" and s07["policy_consistent"] is True
    assert "gate_rejected" not in s07
    assert replay_output(rows["S-02"]) == {"status": "exception", "exception": "KeyError: 'x'"}
    assert replay_output(None) == {"status": "not run"}


def test_feedback_keys_are_the_13(ds, tmp_path):
    rows = {r["scenario_id"]: r for r in eval_data(ds)["rows"]}
    axes = axes_feedback(rows["S-01"])["results"]
    assert [f["key"] for f in axes] == list(METRIC_NAMES[:8]) and axes[0] == {"key": "route", "score": 1.0, "value": "PASS"}
    assert axes[-1] == {"key": "score", "score": 1.0, "value": "6/6"} and axes_feedback(rows["S-02"])["results"][6]["value"] == "FAIL"
    judged = judge_feedback(judge_data(tmp_path)["rows"]["S-01"])["results"]
    assert [f["key"] for f in judged] == list(METRIC_NAMES[8:]) and judged[3] == {"key": "no_leaks_or_promises", "score": 1 / 3, "value": 1, "comment": "r"}
    assert judged[-1] == {"key": "judge_mean", "score": 2.25 / 3, "value": 2.25}
    err = judge_feedback({"judge_error": "boom"})["results"]
    assert err[0] == {"key": "no_invented_facts", "score": None, "value": "ERR", "comment": "boom"} and err[-1]["value"] is None
    assert axes_feedback(None) == {"results": []} and judge_feedback(None) == {"results": []}


def test_sync_runs_dataset_then_experiment(ds, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")
    eval_path = tmp_path / "eval_20261027T174341Z.json"
    eval_path.write_text(json.dumps(eval_data(ds)))
    judge_path = tmp_path / "judge_x.json"
    judge_path.write_text(json.dumps(judge_data(eval_path)))
    client = FakeLangSmith()
    assert sync_langsmith(eval_path, judge_path, client=client, evaluate_fn=client.evaluate) == 0
    assert client.calls[0] == ("has", DATASET_NAME) and client.kwargs["experiment_prefix"] == "release-20261027T174341Z"
    assert client.kwargs["metadata"]["judge_model"] == "claude-opus-5-5" and client.kwargs["max_concurrency"] == 4
    outputs, feedback = client.feedback["S-01"]
    assert outputs["route"] == "explain_events" and [f["key"] for f in feedback[0]["results"] + feedback[1]["results"]] == list(METRIC_NAMES)
    assert client.feedback["S-03"] == ({"status": "not run"}, [{"results": []}, {"results": []}])
    assert capsys.readouterr().out.strip().endswith("https://smith.langchain.com/o/x/datasets/ds-1/compare?e=1")
    client = FakeLangSmith()
    assert sync_langsmith(eval_path, None, client=client, evaluate_fn=client.evaluate) == 0
    assert "code axes only" in capsys.readouterr().out and len(client.feedback["S-01"][1]) == 1


def test_sync_missing_key_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert main(["langsmith-sync", str(tmp_path / "eval_x.json")]) == 2
    assert capsys.readouterr().out.strip() == "langsmith-sync: LANGSMITH_API_KEY is not set"
