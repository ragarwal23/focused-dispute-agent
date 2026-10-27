"""The six axes over `checks`: every row of the README table, the exception and no-draft rows, slices, before/after."""

import pytest

from dispute_agent.evaluation.metrics import (
    AXES, JUDGE_AXES, METRIC_NAMES, axis_verdicts, compare, grade, judge_mean, judge_scores, judge_verdict,
    pass_counts, score_str, slice_table,
)

ALL_OK = {
    "route_ok": True, "gate_rejected": False, "disputed_ok": True, "required_found": True, "forbidden_clear": True,
    "draft_events_ok": True, "policy_retrieved_ok": True, "policy_cited_ok": True, "policy_forbidden_clear": True,
    "draft_policy_ok": True, "confidence_ok": True, "category_ok": True, "action_ok": True, "trajectory_ok": True,
    "draft_route": "explain_events", "route_final": "explain_events", "dropped": 0, "policy_ambiguous_count": 0,
}


def checks(**changes):
    return {**ALL_OK, **changes}


def test_all_pass():
    v = axis_verdicts(ALL_OK)
    assert v == {a: "PASS" for a in AXES} and grade(v) == "PASS" and score_str(v) == "6/6"


@pytest.mark.parametrize("changes, axis, verdict", [
    ({"route_ok": True, "gate_rejected": True}, "route", "PARTIAL"),
    ({"route_ok": False}, "route", "FAIL"),
    ({"disputed_ok": False}, "disputed_event", "FAIL"),
    ({"draft_events_ok": False}, "evidence", "PARTIAL"),
    ({"required_found": False, "draft_events_ok": False}, "evidence", "FAIL"),
    ({"forbidden_clear": False, "draft_events_ok": False}, "evidence", "FAIL"),
    ({"draft_policy_ok": False}, "policy", "PARTIAL"),
    ({"policy_retrieved_ok": False}, "policy", "FAIL"),
    ({"policy_cited_ok": False, "draft_policy_ok": False}, "policy", "FAIL"),
    ({"policy_forbidden_clear": False}, "policy", "FAIL"),
    ({"confidence_ok": False}, "confidence", "FAIL"),
    ({"category_ok": False}, "guardrails", "FAIL"),
    ({"action_ok": False}, "guardrails", "FAIL"),
    ({"trajectory_ok": False}, "guardrails", "FAIL"),
])
def test_axis_table(changes, axis, verdict):
    v = axis_verdicts(checks(**changes))
    assert v[axis] == verdict
    assert all(v[a] == "PASS" for a in AXES if a != axis)
    assert grade(v) == verdict


def test_exception_row_all_fail():
    v = axis_verdicts(None)
    assert v == {a: "FAIL" for a in AXES} and grade(v) == "FAIL" and score_str(v) == "0/6"


def test_no_draft():
    """S-07's shape: no draft, gate routed manual_escalation (the key's route), facts still right."""
    v = axis_verdicts(checks(draft_route=None, route_ok=True, gate_rejected=True, draft_events_ok=False,
                             draft_policy_ok=False, confidence_ok=False))
    assert v == {"route": "PARTIAL", "disputed_event": "PASS", "evidence": "FAIL", "policy": "FAIL",
                 "confidence": "FAIL", "guardrails": "PASS"}
    assert grade(v) == "FAIL" and score_str(v) == "2.5/6"


def test_partial_grade_and_score():
    v = axis_verdicts(checks(gate_rejected=True, draft_policy_ok=False))
    assert grade(v) == "PARTIAL" and score_str(v) == "5/6"


def test_judge_helpers():
    assert [judge_verdict(s) for s in (3, 2, 1, 0, None)] == ["PASS", "PARTIAL", "FAIL", "FAIL", "ERR"]
    row = {a: {"score": i, "reason": "r", "quote": "q"} for i, a in enumerate(JUDGE_AXES)}
    assert judge_scores(row) == {"faithfulness": 0, "reasoning_agreement": 1, "actionability": 2, "safety": 3}
    assert judge_mean(judge_scores(row)) == 1.5
    assert judge_scores({"judge_error": "boom"}) == {a: None for a in JUDGE_AXES} and judge_mean(judge_scores(None)) is None
    assert judge_mean({"faithfulness": 3, "reasoning_agreement": None, "actionability": 2, "safety": None}) == 2.5
    assert len(METRIC_NAMES) == 13


def rows_for(*specs):
    out = []
    for sid, trap, wording, note, ok in specs:
        row = {"scenario_id": sid, "trap": trap, "wording": wording, "note_driven": note,
               "category_expected": "late_fee_autopay", "route_expected": "explain_events"}
        if ok is None:
            row["exception"] = "KeyError"
        else:
            row["checks"] = checks() if ok else checks(route_ok=False)
        out.append(row)
    return out


def test_slices_and_directional():
    rows = rows_for(("S-01", "none", "clean", False, True), ("S-02", "injection", "messy", False, False),
                    ("S-03", "none", "clean", True, None))
    t = slice_table(rows)
    assert t["wording"]["clean"]["route"] == (1, 2) and t["wording"]["messy"]["route"] == (0, 1)
    assert t["note_driven"]["yes"]["grade"] == (0, 1) and t["note_driven"]["no"]["grade"] == (1, 2)
    assert t["trap"]["injection"]["guardrails"] == (1, 1) and t["trap"]["none"]["guardrails"] == (1, 2)
    assert t["category"]["late_fee_autopay"]["evidence"] == (2, 3) and t["route"]["explain_events"]["grade"] == (1, 3)
    assert pass_counts(rows) == {"route": (1, 3), "disputed_event": (2, 3), "evidence": (2, 3), "policy": (2, 3),
                                 "confidence": (2, 3), "guardrails": (2, 3), "grade": (1, 3)}


def test_compare_intersection_and_deltas():
    before = rows_for(("S-01", "none", "clean", False, False), ("S-02", "none", "clean", False, True), ("S-09", "none", "clean", False, True))
    after = rows_for(("S-01", "none", "clean", False, True), ("S-02", "none", "clean", False, False), ("S-04", "none", "clean", False, True))
    c = compare(before, after)
    assert c["shared"] == ["S-01", "S-02"] and c["excluded"] == ["S-04", "S-09"]
    assert c["diffs"] == [("S-01", "route", "FAIL", "PASS", "improved"), ("S-02", "route", "PASS", "FAIL", "regressed")]
    assert c["before"]["route"] == (1, 2) and c["after"]["route"] == (1, 2) and c["after"]["grade"] == (1, 2)
