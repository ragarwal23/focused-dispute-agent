"""The LangSmith side of a release: the golden dataset, an experiment over it, and the judge's scores on that experiment.

`eval --experiment` runs the graph on every golden scenario *as* a LangSmith experiment (`run_experiment` with a live
target), so each row's trace is the real `dispute S-xx` run; `judge --experiment` appends the four Opus scores to that
same experiment (`append_judge`); `langsmith-sync` replays an older eval JSON as an experiment with no model call. The
dataset `dispute-agent-golden` is created when missing and verified when present, never deleted: deleting a dataset
deletes every experiment attached to it. Feedback keys are exactly the 13 metric names from metrics.py.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any, Callable

from dispute_agent.evaluation.golden import Case, read_golden
from dispute_agent.evaluation.metrics import (
    AXES, JUDGE_AXES, SCORE_VALUE, grade, judge_mean, judge_scores, row_verdicts, score_str, score_value,
)

DATASET_NAME = "dispute-agent-golden"
DATASET_DESCRIPTION = "The dispute agent's golden scenarios (dispute_agent/evaluation/golden.xlsx)"
GOLDEN_OUTPUTS = (
    "expected_category", "expected_route", "expected_disputed_event_id", "expected_required_event_ids",
    "expected_forbidden_event_ids", "expected_payment_state", "expected_refund_state", "expected_existing_case_id",
    "expected_policy_ids", "rationale",
)
NO_KEY = "LANGSMITH_API_KEY is not set"


def dataset_examples(cases: list[Case]) -> list[dict[str, Any]]:
    """One LangSmith example per golden case: the complaint as inputs, every expected field as outputs, tags as metadata."""
    return [{
        "inputs": {"scenario_id": c.scenario_id, "account_id": c.account_id, "rep_message": c.rep_message},
        "outputs": {k: getattr(c, k) for k in GOLDEN_OUTPUTS},
        "metadata": {"split": c.split, "trap": c.trap, "wording": c.wording, "note_driven": c.note_driven},
    } for c in cases]


def _example_ids(client: Any) -> list[str]:
    return sorted(ex.inputs["scenario_id"] for ex in client.list_examples(dataset_name=DATASET_NAME))


def ensure_dataset(client: Any, cases: list[Case]) -> None:
    """Create the golden dataset if it is missing; if it exists, require the same scenario IDs. Never delete it."""
    if not client.has_dataset(dataset_name=DATASET_NAME):
        dataset = client.create_dataset(DATASET_NAME, description=DATASET_DESCRIPTION)
        client.create_examples(dataset_id=dataset.id, examples=dataset_examples(cases))
        return
    found, wanted = _example_ids(client), sorted(c.scenario_id for c in cases)
    if found != wanted:  # a list, so a duplicated example fails too
        raise RuntimeError(
            f"LangSmith dataset {DATASET_NAME} holds {found} but golden.xlsx has {wanted}; "
            "delete the dataset in the LangSmith UI (this deletes its experiments too) and rerun"
        )


def select_examples(client: Any, scenario_ids: set[str]) -> list[Any]:
    """The dataset's examples for these scenarios, in scenario order (list_examples does not promise an order)."""
    examples = [ex for ex in client.list_examples(dataset_name=DATASET_NAME) if ex.inputs["scenario_id"] in scenario_ids]
    return sorted(examples, key=lambda ex: ex.inputs["scenario_id"])


def replay_output(row: dict[str, Any] | None) -> dict[str, Any]:
    """What a run produced for one scenario, as the experiment's output (the agent's own fields, nothing new)."""
    if row is None:
        return {"status": "not run"}
    if "exception" in row:
        return {"status": "exception", "exception": row["exception"]}
    draft, checks, filed = row.get("draft") or {}, row.get("checks") or {}, row.get("case") or {}
    return {
        "status": "ok", "route": checks.get("route_final"), "draft_route": draft.get("review_route"),
        "disputed_event_id": (row.get("findings") or {}).get("disputed_event_id"), "event_ids": draft.get("event_ids"),
        "policy_source_ids": draft.get("policy_source_ids"), "confidence": draft.get("confidence"),
        "open_questions": draft.get("open_questions"), "case_note": draft.get("case_note"),
        "policy_consistent": checks.get("policy_consistent"), "case_created": filed.get("created"),
    }


def axes_feedback(row: dict[str, Any] | None) -> dict[str, Any]:
    """The six axis verdicts, grade and score as LangSmith feedback (score 1 / 0.5 / 0, value PASS / PARTIAL / FAIL)."""
    if row is None:
        return {"results": []}
    verdicts = row_verdicts(row)
    results = [{"key": axis, "score": SCORE_VALUE[verdicts[axis]], "value": verdicts[axis]} for axis in AXES]
    g = grade(verdicts)
    results.append({"key": "grade", "score": SCORE_VALUE[g], "value": g})
    results.append({"key": "score", "score": score_value(verdicts) / len(AXES), "value": score_str(verdicts)})
    return {"results": results}


def judge_feedback(judge_row: dict[str, Any] | None) -> dict[str, Any]:
    """The four judge scores (0-3, uploaded as score/3) with the judge's reason as the comment, plus the mean."""
    if judge_row is None:
        return {"results": []}
    scores = judge_scores(judge_row)
    results = []
    for axis in JUDGE_AXES:
        s = scores[axis]
        comment = judge_row.get("judge_error") if s is None else judge_row[axis]["reason"]
        results.append({"key": axis, "score": None if s is None else s / 3, "value": "ERR" if s is None else s, "comment": comment})
    mean = judge_mean(scores)
    results.append({"key": "judge_mean", "score": None if mean is None else mean / 3, "value": mean})
    return {"results": results}


def _judged(judge: dict[str, Any]) -> Callable[[Any, Any], dict[str, Any]]:
    def judged(run: Any, example: Any) -> dict[str, Any]:
        return judge_feedback(judge["rows"].get(example.inputs["scenario_id"]))
    return judged


def run_experiment(
    client: Any, evaluate_fn: Any, target: Callable[[dict[str, Any]], dict[str, Any]], rows: dict[str, dict[str, Any]],
    scenario_ids: set[str], *, prefix: str, description: str, metadata: dict[str, Any],
    judge: dict[str, Any] | None = None, max_concurrency: int = 0,
) -> Any:
    """One experiment over the golden dataset. `rows` is read by the evaluator after the target has filled it (live) or
    holds a loaded eval JSON (replay); the evaluators read only those local rows, so their tracing is off."""
    ensure_dataset(client, read_golden())

    def axes(run: Any, example: Any) -> dict[str, Any]:
        return axes_feedback(rows.get(example.inputs["scenario_id"]))

    return evaluate_fn(
        target, data=select_examples(client, scenario_ids), evaluators=[axes] + ([_judged(judge)] if judge else []),
        experiment_prefix=prefix, max_concurrency=max_concurrency, client=client, description=description,
        metadata=metadata, disable_evaluator_tracing=True,
    )


def append_judge(client: Any, evaluate_existing_fn: Any, experiment_name: str, judge: dict[str, Any]) -> Any:
    """Add the judge sidecar's scores as feedback on an experiment that already has its rows."""
    return evaluate_existing_fn(
        experiment_name, evaluators=[_judged(judge)], client=client, max_concurrency=0, disable_evaluator_tracing=True,
    )


def git_sha() -> str:
    """The short commit the code ran at, for the experiment's metadata; blank outside a git checkout."""
    try:
        return subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    except OSError:
        return ""


def experiment_line(results: Any) -> tuple[str, str | None]:
    """(name, url) of an experiment as the SDK reports them; the url is None when the SDK gives none."""
    return getattr(results, "experiment_name", ""), getattr(results, "url", None)


def sync_langsmith(eval_path: Path, judge_path: Path | None = None, client: Any = None, evaluate_fn: Any = None) -> int:
    """The `langsmith-sync` command: replay an eval JSON as an experiment (no model call); prints its URL. Exit 2 without a key."""
    if not os.getenv("LANGSMITH_API_KEY"):
        print(f"langsmith-sync: {NO_KEY}")
        return 2
    data = json.loads(eval_path.read_text())
    rows = {r["scenario_id"]: r for r in data["rows"]}
    judge = json.loads(judge_path.read_text()) if judge_path else None
    if judge is None:
        print("langsmith-sync: no --judge sidecar, uploading the six code axes only")
    if client is None or evaluate_fn is None:
        from langsmith import Client, evaluate

        client, evaluate_fn = client or Client(), evaluate_fn or evaluate

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return replay_output(rows.get(inputs["scenario_id"]))

    stamp = eval_path.stem.replace("eval_", "")
    results = run_experiment(
        client, evaluate_fn, target, rows, set(rows), prefix=f"release-{stamp}", judge=judge, max_concurrency=4,
        description="Replay of a stored eval JSON scored by the six code axes" + (" and the Opus judge" if judge else ""),
        metadata={"eval_path": str(eval_path), "judge_path": str(judge_path) if judge_path else None,
                  "model_id": data.get("model_id"), "judge_model": judge["judge_model"] if judge else None},
    )
    name, url = experiment_line(results)
    print(f"experiment {name}: {url or 'no URL returned'}")
    return 0 if url else 1
