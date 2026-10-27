"""Small, code-only checks for repeated evals and LangSmith graph traces, and the LangSmith upload of a release.

`langsmith-sync` puts golden.xlsx up as the dataset `dispute-agent-golden` (recreated each time; 16 rows) and one eval
JSON up as an experiment whose target replays the stored rows, so no model is called: the experiment describes the same
run the release workbook shows. Feedback keys are exactly the 13 metric names from metrics.py.
"""

from __future__ import annotations

import json
import os
import time
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from dispute_agent.evaluation.evaluate import CHECKS
from dispute_agent.evaluation.golden import Case, read_golden
from dispute_agent.evaluation.metrics import (
    AXES, JUDGE_AXES, SCORE_VALUE, grade, judge_mean, judge_scores, row_verdicts, score_str, score_value,
)

DATASET_NAME = "dispute-agent-golden"
GOLDEN_OUTPUTS = (
    "expected_category", "expected_route", "expected_disputed_event_id", "expected_required_event_ids",
    "expected_forbidden_event_ids", "expected_payment_state", "expected_refund_state", "expected_existing_case_id",
    "expected_policy_ids", "rationale",
)

NORMAL = ["classify", "account_agent", "policy_agent", "derive_states", "sync", "synthesize", "gate", "review"]  # a run pauses at review
RESUME_HEAD = ["review", "account_agent_followup", "derive_states_followup", "sync"]  # a resume where the rep answered
RESUME_TAIL = ["synthesize", "gate", "review"]


def expected_children(account_attempts: int, policy_attempts: int) -> list[str]:
    """The child-node sequence implied by the final graph state."""
    head, tail = NORMAL[:5], NORMAL[5:]
    if account_attempts == 2:
        head += ["account_agent_retry", "derive_states_retry", "sync"]
    if policy_attempts == 2:
        head += ["policy_agent_retry", "sync"]
    return head + tail


def expected_resume_children(policy_retry: bool) -> list[str]:
    """An answer-path resume: the Account Agent looks again, maybe a re-retrieve, then back to the pause."""
    return RESUME_HEAD + (["policy_agent_retry", "sync"] if policy_retry else []) + RESUME_TAIL


def stability(reports: list[dict], known: set[tuple[str, str]]) -> tuple[list[str], bool]:
    """Print each scenario/check pass count; tolerate one miss per input report."""
    rows = [r for report in reports for r in report.get("rows", []) if "checks" in r]
    # CHECKS is the eval's explicit pass/fail contract; adjacent diagnostics are omitted.
    keys = [key for key in CHECKS if any(key in row["checks"] for row in rows)]
    scenario_ids = sorted({row["scenario_id"] for row in rows})
    n = len(reports)
    lines = [f"stability: {n} report(s)"]
    ok = bool(n)
    for sid in scenario_ids:
        checks = {key: sum(bool(row["checks"].get(key)) for row in rows if row["scenario_id"] == sid) for key in keys}
        cells = []
        for key, passed in checks.items():
            ignored = (sid, key) in known
            cells.append(f"{key}={passed}/{n}" + (" (known)" if ignored else ""))
            ok &= ignored or passed >= n - 1
        lines.append(f"{sid}: " + "  ".join(cells))
    for report in reports:
        scored = [row for row in report.get("rows", []) if "checks" in row]
        sends = sum(row.get("account_attempts", 0) > 1 for row in scored)
        retries = sum(row.get("policy_attempts", 0) > 1 for row in scored)
        exceptions = sum("exception" in row for row in report.get("rows", []))
        lines.append(f"{report.get('split', '?')}: send-backs={sends} re-retrieves={retries} exceptions={exceptions}")
    return lines, ok


def _value(run: Any, key: str, default: Any = None) -> Any:
    return run.get(key, default) if isinstance(run, dict) else getattr(run, key, default)


def _children_match(actual: list[str], expected: list[str]) -> bool:
    """The parallel Account/Policy nodes may swap by a few milliseconds."""
    if len(actual) != len(expected) or actual[:1] != expected[:1]:
        return False
    return set(actual[1:3]) == {"account_agent", "policy_agent"} and actual[3:] == expected[3:]


def _runs(client: Any, **kwargs: Any) -> list[Any]:
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", category=DeprecationWarning)
        return list(client.list_runs(**kwargs))


def trace_check(client: Any, project: str, last: int = 1, wait: float = 30.0) -> tuple[list[str], bool]:
    """Fetch recent graph roots and verify that their LangSmith child shape is complete."""
    deadline = time.monotonic() + wait
    roots: list[Any] = []
    while True:
        roots = [
            run for run in _runs(client, project_name=project, is_root=True,
                                  start_time=datetime.now(timezone.utc) - timedelta(hours=6))
            if str(_value(run, "name", "")).startswith("dispute ")
            and _value(run, "prompt_tokens", 0) > 0
            and "slice=investigate" in (_value(run, "tags", []) or [])
        ]
        roots.sort(key=lambda run: _value(run, "start_time") or datetime.min.replace(tzinfo=timezone.utc), reverse=True)
        if len(roots) >= last or time.monotonic() >= deadline:
            break
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
    roots = roots[:last]
    if len(roots) < last:
        return [f"no qualifying run found within {wait:g}s (wanted {last}, found {len(roots)})"], False

    lines, ok = [], True
    for root in roots:
        children = _runs(client, project_name=project, trace_id=_value(root, "trace_id"))
        children = [r for r in children if _value(r, "parent_run_id") == _value(root, "id")]
        children.sort(key=lambda run: _value(run, "start_time") or datetime.min.replace(tzinfo=timezone.utc))
        actual = [_value(run, "name", "?") for run in children]
        outputs = _value(root, "outputs") or {}
        if not isinstance(outputs, dict) or "account_attempts" not in outputs or "policy_attempts" not in outputs:
            lines.append(f"{_value(root, 'name')}: no outputs")
            ok = False
            continue
        if str(_value(root, "name")).endswith(" resume"):  # approve/override resumes have no tokens and never get here
            if not outputs.get("followups"):  # continued a crashed run: its shape depends on where it crashed
                lines.append(f"{_value(root, 'name')}: recovery resume, shape not checked")
                continue
            options = [expected_resume_children(False), expected_resume_children(True)]
            match = actual in options
            expected = options[0]
        else:
            expected = expected_children(int(outputs["account_attempts"]), int(outputs["policy_attempts"]))
            match = _children_match(actual, expected)
        duration = _value(root, "end_time") and _value(root, "start_time")
        seconds = (_value(root, "end_time") - _value(root, "start_time")).total_seconds() if duration else 0
        lines.append(
            f"{_value(root, 'name')} tags={_value(root, 'tags', [])} "
            f"in={_value(root, 'prompt_tokens', 0)} out={_value(root, 'completion_tokens', 0)} "
            f"${_value(root, 'total_cost', 0) or 0:.4f} {seconds:.1f}s\n"
            f"children: {' > '.join(actual)}\nexpected: {'match' if match else 'MISMATCH ' + ' > '.join(expected)}"
        )
        ok &= match
    return lines, ok


def dataset_examples(cases: list[Case]) -> list[dict[str, Any]]:
    """One LangSmith example per golden case: the complaint as inputs, every expected field as outputs, tags as metadata."""
    return [{
        "inputs": {"scenario_id": c.scenario_id, "account_id": c.account_id, "rep_message": c.rep_message},
        "outputs": {k: getattr(c, k) for k in GOLDEN_OUTPUTS},
        "metadata": {"split": c.split, "trap": c.trap, "wording": c.wording, "note_driven": c.note_driven},
    } for c in cases]


def upsert_dataset(client: Any, cases: list[Case]) -> Any:
    """Recreate the golden dataset (16 rows, no example bookkeeping) and return it."""
    if client.has_dataset(dataset_name=DATASET_NAME):
        client.delete_dataset(dataset_name=DATASET_NAME)
    dataset = client.create_dataset(DATASET_NAME, description="The dispute agent's golden scenarios (dispute_agent/evaluation/golden.xlsx)")
    client.create_examples(dataset_id=dataset.id, examples=dataset_examples(cases))
    return dataset


def replay_output(row: dict[str, Any] | None) -> dict[str, Any]:
    """What the stored run produced for one scenario, as the experiment's output (no model call)."""
    if row is None:
        return {"status": "not run"}
    if "exception" in row:
        return {"status": "exception", "exception": row["exception"]}
    draft, gate, filed = row.get("draft") or {}, row.get("gate") or {}, row.get("case") or {}
    return {
        "status": "ok", "route": gate.get("route"), "draft_route": draft.get("review_route"),
        "disputed_event_id": (row.get("findings") or {}).get("disputed_event_id"), "event_ids": draft.get("event_ids"),
        "policy_source_ids": draft.get("policy_source_ids"), "confidence": draft.get("confidence"),
        "open_questions": draft.get("open_questions"), "case_note": draft.get("case_note"),
        "gate_rejected": gate.get("gate_rejected"), "case_created": filed.get("created"),
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


def sync_langsmith(eval_path: Path, judge_path: Path | None = None, client: Any = None, evaluate_fn: Any = None) -> int:
    """The `langsmith-sync` command: dataset + experiment, prints the experiment URL. Exit 2 without an API key."""
    if not os.getenv("LANGSMITH_API_KEY"):
        print("langsmith-sync: LANGSMITH_API_KEY is not set")
        return 2
    data = json.loads(eval_path.read_text())
    rows = {r["scenario_id"]: r for r in data["rows"]}
    judge = json.loads(judge_path.read_text()) if judge_path else None
    if judge is None:
        print("langsmith-sync: no --judge sidecar, uploading the six code axes only")
    if client is None or evaluate_fn is None:
        from langsmith import Client, evaluate

        client, evaluate_fn = client or Client(), evaluate_fn or evaluate
    cases = read_golden()
    upsert_dataset(client, cases)

    def target(inputs: dict[str, Any]) -> dict[str, Any]:
        return replay_output(rows.get(inputs["scenario_id"]))

    def axes(run: Any, example: Any) -> dict[str, Any]:
        return axes_feedback(rows.get(example.inputs["scenario_id"]))

    def judged(run: Any, example: Any) -> dict[str, Any]:
        return judge_feedback(judge["rows"].get(example.inputs["scenario_id"]))

    stamp = eval_path.stem.replace("eval_", "")
    results = evaluate_fn(
        target, data=DATASET_NAME, evaluators=[axes] + ([judged] if judge else []),
        experiment_prefix=f"release-{stamp}", max_concurrency=4, client=client,
        description="Replay of a stored eval JSON scored by the six code axes" + (" and the Opus judge" if judge else ""),
        metadata={"eval_path": str(eval_path), "judge_path": str(judge_path) if judge_path else None,
                  "model_id": data.get("model_id"), "judge_model": judge["judge_model"] if judge else None},
    )
    url = getattr(results, "url", None)
    print(f"experiment {getattr(results, 'experiment_name', '')}: {url or 'no URL returned'}")
    return 0 if url else 1
