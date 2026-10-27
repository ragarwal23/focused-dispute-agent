"""Small, code-only checks for repeated evals and LangSmith graph traces. The LangSmith upload is in experiment.py."""

from __future__ import annotations

import time
import warnings
from datetime import datetime, timedelta, timezone
from typing import Any

from dispute_agent.evaluation.evaluate import CHECKS

NORMAL = ["classify", "account_agent", "policy_agent", "derive_states", "sync", "synthesize", "review"]  # a run pauses at review
RESUME = ["review", "account_agent_followup", "derive_states_followup", "sync", "synthesize", "review"]  # the rep answered


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
        exceptions = sum("exception" in row for row in report.get("rows", []))
        lines.append(f"{report.get('split', '?')}: exceptions={exceptions}")
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
        if not isinstance(outputs, dict) or "followups" not in outputs:
            lines.append(f"{_value(root, 'name')}: no outputs")
            ok = False
            continue
        if str(_value(root, "name")).endswith(" resume"):  # approve/override resumes have no tokens and never get here
            if not outputs.get("followups"):  # continued a crashed run: its shape depends on where it crashed
                lines.append(f"{_value(root, 'name')}: recovery resume, shape not checked")
                continue
            expected = RESUME
            match = actual == expected
        else:
            expected = NORMAL
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
