"""The LangSmith upload: dataset created once and never deleted, live and replay experiments, judge scores appended."""

import json
from types import SimpleNamespace

import pytest

from dispute_agent.evaluation.experiment import (
    DATASET_NAME, append_judge, axes_feedback, dataset_examples, ensure_dataset, judge_feedback, public_links, replay_output,
    run_experiment, select_examples, sync_langsmith,
)
from dispute_agent.evaluation.golden import read_golden
from dispute_agent.evaluation.metrics import AXES, METRIC_NAMES
from dispute_agent.__main__ import main
from test_report import eval_data, judge_data


class FakeLangSmith:
    """Records the dataset calls; `evaluate` runs the target and evaluators itself so the feedback shapes are checked."""

    def __init__(self, exists=True, examples=None):
        self.exists, self.calls = exists, []
        self.examples = dataset_examples(read_golden()) if examples is None and exists else (examples or [])

    def has_dataset(self, dataset_name):
        self.calls.append(("has", dataset_name))
        return self.exists

    def delete_dataset(self, dataset_name):
        raise AssertionError("never delete: it deletes the experiments too")

    def create_dataset(self, name, description=""):
        self.calls.append(("create", name))
        return SimpleNamespace(id="ds-1", name=name)

    def create_examples(self, dataset_id, examples):
        self.calls.append(("examples", dataset_id, len(examples)))
        self.examples = examples

    def list_examples(self, dataset_name):
        self.calls.append(("list", dataset_name))
        return [SimpleNamespace(inputs=ex["inputs"], outputs=ex["outputs"]) for ex in reversed(self.examples)]

    def evaluate(self, target, data, evaluators, **kwargs):
        self.kwargs, self.feedback, self.runs = kwargs, {}, []
        for example in data:
            run = SimpleNamespace(outputs=target(example.inputs))
            self.runs.append((run, example))
            self.feedback[example.inputs["scenario_id"]] = (run.outputs, [f(run, example) for f in evaluators])
        return SimpleNamespace(experiment_name="release-x", url="https://smith.langchain.com/o/x/datasets/ds-1/compare?e=1")

    def evaluate_existing(self, experiment, evaluators, **kwargs):
        self.existing_kwargs, self.appended = {"experiment": experiment, **kwargs}, {}
        for run, example in self.runs:
            self.appended[example.inputs["scenario_id"]] = [f(run, example) for f in evaluators]
        return SimpleNamespace(experiment_name=experiment, url="https://smith.langchain.com/o/x/datasets/ds-1/compare?e=1")


def test_examples_shape_and_ensure_dataset():
    examples = dataset_examples(read_golden())
    assert len(examples) == 16 and examples[0]["inputs"] == {"scenario_id": "S-01", "account_id": "A-1001", "rep_message": examples[0]["inputs"]["rep_message"]}
    assert examples[0]["outputs"]["expected_route"] == "explain_events" and examples[0]["outputs"]["expected_required_event_ids"] == ["E-0081", "E-0117"]
    assert examples[3]["metadata"] == {"split": "heldout", "trap": "injection", "wording": "messy", "note_driven": False}
    assert "rationale" in examples[0]["outputs"]
    client = FakeLangSmith(exists=True)
    ensure_dataset(client, read_golden())
    assert client.calls == [("has", DATASET_NAME), ("list", DATASET_NAME)]  # verified, not recreated
    client = FakeLangSmith(exists=False)
    ensure_dataset(client, read_golden())
    assert client.calls == [("has", DATASET_NAME), ("create", DATASET_NAME), ("examples", "ds-1", 16)]
    with pytest.raises(RuntimeError, match="delete the dataset in the LangSmith UI"):
        ensure_dataset(FakeLangSmith(exists=True, examples=examples[:15]), read_golden())
    with pytest.raises(RuntimeError):
        ensure_dataset(FakeLangSmith(exists=True, examples=examples + examples[:1]), read_golden())


def test_select_examples_filters_and_sorts():
    picked = select_examples(FakeLangSmith(), {"S-03", "S-01", "S-12"})
    assert [ex.inputs["scenario_id"] for ex in picked] == ["S-01", "S-03", "S-12"]


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


def test_run_experiment_live_shape(ds):
    """The live target fills the rows the evaluator reads; an exception row still exists and fails every axis."""
    stored = {r["scenario_id"]: r for r in eval_data(ds)["rows"]}
    rows: dict = {}

    def target(inputs):
        rows[inputs["scenario_id"]] = stored[inputs["scenario_id"]]
        return replay_output(rows[inputs["scenario_id"]])

    client = FakeLangSmith()
    results = run_experiment(client, client.evaluate, target, rows, {"S-01", "S-02", "S-07"}, prefix="release-t",
                             description="d", metadata={"model_id": "m"})
    assert results.experiment_name == "release-x" and set(rows) == {"S-01", "S-02", "S-07"}
    assert client.kwargs["max_concurrency"] == 0 and client.kwargs["disable_evaluator_tracing"] is True
    assert client.kwargs["experiment_prefix"] == "release-t" and client.kwargs["metadata"] == {"model_id": "m"}
    outputs, feedback = client.feedback["S-02"]
    assert outputs["status"] == "exception" and [f["value"] for f in feedback[0]["results"][:len(AXES)]] == ["FAIL"] * len(AXES)
    assert [f["key"] for f in client.feedback["S-01"][1][0]["results"]] == list(METRIC_NAMES[:8])


def test_append_judge(ds, tmp_path):
    rows = {r["scenario_id"]: r for r in eval_data(ds)["rows"]}
    client = FakeLangSmith()
    run_experiment(client, client.evaluate, lambda i: replay_output(rows.get(i["scenario_id"])), rows, set(rows),
                   prefix="p", description="d", metadata={})
    append_judge(client, client.evaluate_existing, "release-x", judge_data(tmp_path))
    assert client.existing_kwargs["experiment"] == "release-x" and client.existing_kwargs["disable_evaluator_tracing"] is True
    assert [f["key"] for f in client.appended["S-01"][0]["results"]] == list(METRIC_NAMES[8:])
    assert client.appended["S-07"][0]["results"][0]["value"] == "ERR" and client.appended["S-02"] == [{"results": []}]


def test_sync_runs_dataset_then_experiment(ds, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")
    eval_path = tmp_path / "eval_20261027T174341Z.json"
    eval_path.write_text(json.dumps(eval_data(ds)))
    judge_path = tmp_path / "judge_x.json"
    judge_path.write_text(json.dumps(judge_data(eval_path)))
    client = FakeLangSmith()
    assert sync_langsmith(eval_path, judge_path, client=client, evaluate_fn=client.evaluate) == 0
    assert client.calls[:2] == [("has", DATASET_NAME), ("list", DATASET_NAME)] and client.kwargs["experiment_prefix"] == "release-20261027T174341Z"
    assert client.kwargs["metadata"]["judge_model"] == "claude-opus-5-5" and client.kwargs["max_concurrency"] == 4
    outputs, feedback = client.feedback["S-01"]
    assert outputs["route"] == "explain_events" and [f["key"] for f in feedback[0]["results"] + feedback[1]["results"]] == list(METRIC_NAMES)
    assert set(client.feedback) == {"S-01", "S-02", "S-07"}  # only the scenarios the JSON ran
    assert capsys.readouterr().out.strip().endswith("https://smith.langchain.com/o/x/datasets/ds-1/compare?e=1")
    client = FakeLangSmith()
    assert sync_langsmith(eval_path, None, client=client, evaluate_fn=client.evaluate) == 0
    assert "code axes only" in capsys.readouterr().out and len(client.feedback["S-01"][1]) == 1


def test_sync_missing_key_exit_2(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)
    assert main(["langsmith-sync", str(tmp_path / "eval_x.json")]) == 2
    assert capsys.readouterr().out.strip() == "langsmith-sync: LANGSMITH_API_KEY is not set"


def test_judge_experiment_cli(ds, tmp_path, monkeypatch, capsys):
    """`judge --experiment` needs a JSON captured with --experiment, then appends the sidecar to that experiment."""
    monkeypatch.setenv("LANGSMITH_API_KEY", "k")
    sidecar = tmp_path / "judge_x.json"
    sidecar.write_text(json.dumps(judge_data(tmp_path)))
    monkeypatch.setattr("dispute_agent.evaluation.judge.judge_report", lambda *a, **k: sidecar)
    plain = tmp_path / "eval_20261027T120000Z.json"
    plain.write_text(json.dumps(eval_data(ds)))
    assert main(["judge", str(plain), "--experiment"]) == 2
    assert capsys.readouterr().out.strip().endswith("was not captured with --experiment")
    captured = tmp_path / "eval_20261027T130000Z.json"
    captured.write_text(json.dumps({**eval_data(ds), "experiment_name": "release-20261027T130000Z-abc"}))
    seen = {}
    monkeypatch.setattr("dispute_agent.__main__.append_judge",
                        lambda client, fn, name, judge: seen.update(name=name, rows=set(judge["rows"])) or SimpleNamespace(experiment_name=name, url="u"))
    monkeypatch.setattr("dispute_agent.__main__.langsmith_client", lambda: (None, None))
    assert main(["judge", str(captured), "--experiment"]) == 0
    assert seen == {"name": "release-20261027T130000Z-abc", "rows": {"S-01", "S-07"}}
    assert capsys.readouterr().out.strip().endswith("experiment release-20261027T130000Z-abc: u")
    monkeypatch.delenv("LANGSMITH_API_KEY")
    assert main(["judge", str(captured), "--experiment"]) == 2
    assert capsys.readouterr().out.strip() == "judge: LANGSMITH_API_KEY is not set"


def test_public_links():
    """The experiment's URL on the shared dataset, and one trace link per scenario that opens that trace."""
    client = SimpleNamespace(
        read_dataset_shared_schema=lambda dataset_name: {"url": "https://smith.langchain.com/public/tok/d"},
        read_project=lambda project_name: SimpleNamespace(id="sess"),
        list_runs=lambda project_id, is_root: [SimpleNamespace(inputs={"scenario_id": "S-01"}, trace_id="t1")],
    )
    url, traces = public_links(client, "release-x")
    assert url == "https://smith.langchain.com/public/tok/d/compare?selectedSessions=sess"
    assert traces == {"S-01": url + "&trace=t1&peeked_trace_id=t1"}
