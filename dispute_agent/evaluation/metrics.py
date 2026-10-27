"""Phase 2 metrics: the six axes over evaluate.py's checks, a grade and score per scenario, slices, before/after.

Pure functions over eval JSON rows. The checks are evaluate.py's; an axis is a named grouping of them with a
PASS / PARTIAL / FAIL verdict (the table in README.md). The four judge axes are scored 0-3 in judge.py and
turned into verdicts here.
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any, Literal

Verdict = Literal["PASS", "PARTIAL", "FAIL"]
AXES = ("route", "disputed_event", "evidence", "policy", "confidence", "guardrails")
JUDGE_AXES = ("no_invented_facts", "matches_rationale", "ready_to_file", "no_leaks_or_promises")
METRIC_NAMES = AXES + ("grade", "score") + JUDGE_AXES + ("judge_mean",)  # the 13; also the LangSmith feedback keys
SLICE_KEYS = ("wording", "note_driven", "trap", "category", "route")
SLICE_SOURCE = {"wording": "wording", "note_driven": "note_driven", "trap": "trap", "category": "category_expected", "route": "route_expected"}
DIRECTIONAL_MAX = 6  # a slice this small is directional only (the brief)
SCORE_VALUE = {"PASS": 1.0, "PARTIAL": 0.5, "FAIL": 0.0}


def _ok(checks: dict[str, Any], key: str) -> bool:
    return checks.get(key) is True


def axis_verdicts(checks: dict[str, Any] | None) -> dict[str, Verdict]:
    """The six verdicts for one row's `checks`; a row with no checks (an exception) fails every axis."""
    if not checks:
        return {axis: "FAIL" for axis in AXES}
    no_draft = checks.get("draft_route") is None
    # right only because there was no draft (the fallback escalation, not a decision) is PARTIAL
    route: Verdict = "FAIL" if not _ok(checks, "route_ok") else "PARTIAL" if no_draft else "PASS"
    req, forb, draft_ev = _ok(checks, "required_found"), _ok(checks, "forbidden_clear"), _ok(checks, "draft_events_ok")
    evidence: Verdict = "FAIL" if no_draft or not (req and forb) else "PASS" if draft_ev else "PARTIAL"
    pret, pcite, pforb, dpol = (_ok(checks, k) for k in ("policy_retrieved_ok", "policy_cited_ok", "policy_forbidden_clear", "draft_policy_ok"))
    policy: Verdict = "FAIL" if no_draft or not (pret and pcite and pforb) else "PASS" if dpol else "PARTIAL"
    # policy_consistent is absent from reports made before the gate was removed (v0.8); only an explicit False fails
    guard = all(_ok(checks, k) for k in ("category_ok", "action_ok", "trajectory_ok"))
    guard = guard and checks.get("policy_consistent") is not False
    return {
        "route": route, "disputed_event": "PASS" if _ok(checks, "disputed_ok") else "FAIL", "evidence": evidence,
        "policy": policy, "confidence": "PASS" if _ok(checks, "confidence_ok") else "FAIL",
        "guardrails": "PASS" if guard else "FAIL",
    }


def grade(verdicts: dict[str, Verdict]) -> Verdict:
    values = [verdicts[a] for a in AXES]
    return "FAIL" if "FAIL" in values else "PASS" if all(v == "PASS" for v in values) else "PARTIAL"


def score_value(verdicts: dict[str, Verdict]) -> float:
    return sum(SCORE_VALUE[verdicts[a]] for a in AXES)


def score_str(verdicts: dict[str, Verdict]) -> str:
    return f"{score_value(verdicts):g}/{len(AXES)}"


def row_verdicts(row: dict[str, Any]) -> dict[str, Verdict]:
    """Verdicts for a JSON row as `run_eval` wrote it (an `exception` row has no `checks`)."""
    return axis_verdicts(row.get("checks"))


def judge_verdict(score: int | None) -> str:
    """3 PASS, 2 PARTIAL, 0-1 FAIL; None (a judge error) is ERR."""
    return "ERR" if score is None else "PASS" if score == 3 else "PARTIAL" if score == 2 else "FAIL"


def judge_scores(judge_row: dict[str, Any] | None) -> dict[str, int | None]:
    """The four 0-3 scores from a judge sidecar row; None per axis when the row is missing or errored."""
    if not judge_row or "judge_error" in judge_row:
        return {axis: None for axis in JUDGE_AXES}
    return {axis: judge_row[axis]["score"] for axis in JUDGE_AXES}


def judge_mean(scores: dict[str, int | None]) -> float | None:
    present = [s for s in scores.values() if s is not None]
    return round(sum(present) / len(present), 2) if present else None


def slice_value(row: dict[str, Any], key: str) -> str:
    v = row.get(SLICE_SOURCE[key])
    return ("yes" if v else "no") if key == "note_driven" else str(v)


def slice_table(rows: list[dict[str, Any]]) -> dict[str, dict[str, dict[str, tuple[int, int]]]]:
    """{slice key: {value: {axis or "grade": (passes, n)}}}; exception rows count as all-FAIL."""
    out: dict[str, dict[str, dict[str, list[int]]]] = {k: defaultdict(lambda: defaultdict(lambda: [0, 0])) for k in SLICE_KEYS}
    for row in rows:
        verdicts = row_verdicts(row)
        cells = {**verdicts, "grade": grade(verdicts)}
        for key in SLICE_KEYS:
            bucket = out[key][slice_value(row, key)]
            for name, verdict in cells.items():
                bucket[name][0] += verdict == "PASS"
                bucket[name][1] += 1
    return {k: {v: {n: (p, t) for n, (p, t) in cells.items()} for v, cells in sorted(table.items())} for k, table in out.items()}


def directional(n: int) -> bool:
    return n <= DIRECTIONAL_MAX


def pass_counts(rows: list[dict[str, Any]]) -> dict[str, tuple[int, int]]:
    """Per axis (and grade) PASS count over the rows."""
    counts = {name: [0, 0] for name in AXES + ("grade",)}
    for row in rows:
        verdicts = row_verdicts(row)
        for name in AXES:
            counts[name][0] += verdicts[name] == "PASS"
            counts[name][1] += 1
        counts["grade"][0] += grade(verdicts) == "PASS"
        counts["grade"][1] += 1
    return {k: (p, n) for k, (p, n) in counts.items()}


def compare(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    """Before/after over the scenarios both runs contain: per-axis PASS counts, and every (scenario, axis) that changed."""
    b, a = {r["scenario_id"]: r for r in before}, {r["scenario_id"]: r for r in after}
    shared = sorted(b.keys() & a.keys())
    excluded = sorted(b.keys() ^ a.keys())
    diffs = []
    for sid in shared:
        vb, va = row_verdicts(b[sid]), row_verdicts(a[sid])
        for axis in AXES:
            if vb[axis] != va[axis]:
                delta = "improved" if SCORE_VALUE[va[axis]] > SCORE_VALUE[vb[axis]] else "regressed"
                diffs.append((sid, axis, vb[axis], va[axis], delta))
    return {
        "shared": shared, "excluded": excluded, "diffs": diffs,
        "before": pass_counts([b[s] for s in shared]), "after": pass_counts([a[s] for s in shared]),
    }
