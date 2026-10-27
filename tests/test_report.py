"""The release workbook: sheets and titles, verdicts, matrix and slices, compare, judge panels, calibration, bad inputs."""

import json

import openpyxl
import pytest

from dispute_agent.evaluation.golden import build_cases
from dispute_agent.evaluation.metrics import AXES, JUDGE_AXES
from dispute_agent.evaluation.report import CASE_COLS, MATRIX_COLS, NO_DRAFT, case_row, consistent, load_eval, render, write_report
from dispute_agent.__main__ import main
from test_metrics import ALL_OK


def normal_row(s):
    return {
        "scenario_id": s.scenario_id, "split": s.split, "trap": s.trap, "wording": s.wording, "note_driven": s.note_driven,
        "category_pred": s.expected_category, "category_expected": s.expected_category, "route_expected": s.expected_route,
        "case": {"case_id": "C-004", "created": True, "route": s.expected_route},
        "checks": {**ALL_OK, "tool_calls": 3, "route_final": s.expected_route, "draft_route": s.expected_route},
        "findings": {"relevant_event_ids": ["E-0081", "E-0117", "E-0120"], "disputed_event_id": "E-0117",
                     "candidate_event_ids": [], "summary": "cancelled autopay, fee stands"},
        "derived": {"payment_state": "posted", "refund_state": "not_applicable"},
        "sync": {"issues": []},
        "draft": {"review_route": s.expected_route, "event_ids": ["E-0081", "E-0117"], "policy_source_ids": ["AP.v1.s4", "LF.v3.s4"],
                  "confidence": "high", "open_questions": [], "case_note": "The fee stands."},
        "tool_errors": [],
    }


def null_draft_row(s):
    row = normal_row(s)
    row.update(draft=None, case={"case_id": "C-005", "created": True, "route": "manual_escalation"},
               findings={"relevant_event_ids": ["E-0157", "E-0135"], "disputed_event_id": None, "candidate_event_ids": [], "summary": "x"})
    row["checks"].update(synth_errors=["synthesize failed: parse"], draft_route=None, route_final="manual_escalation", route_ok=True,
                         draft_events_ok=False, draft_policy_ok=False, confidence_ok=False)
    return row


def eval_data(ds):
    s01, s07 = ds.scenarios["S-01"], ds.scenarios["S-07"]
    return {"category_source": "classify", "split": "dev", "model_id": "claude-sonnet-5-5",
            "golden_path": "dispute_agent/evaluation/golden.xlsx",
            "rows": [normal_row(s01), null_draft_row(s07), {"scenario_id": "S-02", "exception": "KeyError: 'x'"}],
            "totals": {}}


def judge_data(json_path):
    return {"eval_path": str(json_path), "judge_model": "claude-opus-5-5", "judged_at": "x", "rows": {
        "S-01": {a: {"score": s, "reason": "r", "quote": "q"} for a, s in zip(JUDGE_AXES, (3, 2, 3, 1))},
        "S-07": {"judge_error": "RuntimeError: boom"},
    }}


@pytest.fixture
def json_path(ds, tmp_path):
    path = tmp_path / "eval_20261027T120000Z.json"
    path.write_text(json.dumps(eval_data(ds)))
    return path


@pytest.fixture
def workbook(ds, tmp_path, json_path):
    out = tmp_path / "release.xlsx"
    write_report(load_eval(json_path), json_path, build_cases(ds), out)
    return openpyxl.load_workbook(out)


def table(ws):
    return {r[0]: r[1:3] for r in ws.iter_rows(min_row=4, values_only=True) if r[0]}


def verdicts(ws):
    return {r[0]: r[3] for r in ws.iter_rows(min_row=4, values_only=True) if r[0]}


def trim(row):
    """A sheet row without its trailing empty cells."""
    row = list(row)
    while row and row[-1] is None:
        row.pop()
    return tuple(row)


def matrix(wb):
    rows = list(wb["Verdict Matrix"].iter_rows(values_only=True))
    assert list(rows[0]) == MATRIX_COLS
    return {r[0]: dict(zip(MATRIX_COLS, r)) for r in rows[1:] if r[0]}


def test_sheets_and_titles(workbook, ds):
    assert workbook.sheetnames == ["Run", "Cases", "Verdict Matrix", "Slices"] + [f"S-{i:02d}" for i in range(1, 17)]
    run = dict(workbook["Run"].iter_rows(values_only=True))
    assert run["captured_at"] == "2026-10-27 12:00:00Z" and run["model_id"] == "claude-sonnet-5-5" and run["producer"] == "agent"
    assert run["scenarios"] == 3 and run["exceptions"] == 1 and run["judge_path"] is None and run["before_path"] is None
    ws = workbook["S-01"]
    assert ws["A1"].value == f"S-01 — {ds.scenarios['S-01'].rep_message}"
    assert [c.value for c in ws[3]] == ["field", "golden", "actual", "verdict"]
    v = verdicts(ws)
    assert v["route"] == "PASS" and v["category"] == "PASS" and v["confidence"] == "PASS" and v["payment_state"] is None
    assert "Judge" not in [r[0] for r in ws.iter_rows(values_only=True)]
    t = table(ws)
    assert t["category"] == ("late_fee_autopay", "late_fee_autopay") and t["route"] == ("explain_events", "explain_events")
    assert t["draft_route"] == (None, "explain_events") and t["disputed_event_id"] == ("E-0117", "E-0117")
    assert t["required_event_ids / cited_event_ids"] == ("E-0081, E-0117", "E-0081, E-0117")
    assert t["existing_case_id / case_id"] == (None, "C-004 (created)") and t["confidence"] == (None, "high")
    assert t["rationale / case_note"] == (ds.scenarios["S-01"].rationale, "The fee stands.")
    assert t["tool_calls_made"] == (None, 3) and t["account_summary"] == (None, "cancelled autopay, fee stands")
    assert "thread_id" not in t  # the eval JSON never stores a thread ID (commit 372b170)
    cases = {r[1]: r for r in workbook["Cases"].iter_rows(min_row=2, values_only=True)}
    assert cases["S-01"][7:] == ("explain_events", "explain_events", "E-0117", "E-0117", "ok", "yes", "OK")
    assert cases["S-04"][2] == "heldout" and len(cases) == 16


def test_null_draft_row(workbook):
    t = table(workbook["S-07"])
    assert t["route"] == ("manual_escalation", "manual_escalation") and t["draft_route"] == (None, NO_DRAFT)
    assert t["rationale / case_note"][1] == NO_DRAFT and t["confidence"] == (None, None) and t["open_questions"] == (None, None)
    assert t["forbidden_event_ids / cited anywhere by the Account Agent"] == ("E-0135", "E-0135")
    assert t["synth_errors"] == (None, "synthesize failed: parse")
    v = verdicts(workbook["S-07"])
    assert v["route"] == "PARTIAL" and v["required_event_ids / cited_event_ids"] == "FAIL" and v["confidence"] == "FAIL"
    cases = {r[1]: r for r in workbook["Cases"].iter_rows(min_row=2, values_only=True)}
    assert cases["S-07"][8] == "manual_escalation" and cases["S-07"][11:] == ("ok", "yes", "OK")


def test_exception_row(workbook, ds):
    t = table(workbook["S-02"])
    assert t["route"] == ("adjustment_review", "EXCEPTION: KeyError: 'x'")
    assert t["rationale / case_note"] == (ds.scenarios["S-02"].rationale, "EXCEPTION: KeyError: 'x'")
    assert t["synth_errors"] == (None, None)
    assert set(verdicts(workbook["S-02"])[k] for k in ("route", "category", "confidence")) == {"FAIL"}
    cases = {r[1]: r for r in workbook["Cases"].iter_rows(min_row=2, values_only=True)}
    assert cases["S-02"][8] is None and cases["S-02"][-1] == "EXCEPTION"


def test_pending_scenario(workbook, ds):
    t = table(workbook["S-03"])
    assert t["route"] == ("manual_escalation", None) and t["rationale / case_note"] == (ds.scenarios["S-03"].rationale, None)
    assert all(v[1] is None for v in t.values()) and all(v is None for v in verdicts(workbook["S-03"]).values())
    cases = {r[1]: r for r in workbook["Cases"].iter_rows(min_row=2, values_only=True)}
    assert cases["S-03"][-1] == "PENDING" and cases["S-03"][8] is None


def test_rejects_non_report(tmp_path, capsys):
    bad = tmp_path / "account_agent_x.json"
    bad.write_text(json.dumps({"messages": [], "findings": None}))
    assert main(["report", str(bad), "--out", str(tmp_path / "out.xlsx")]) == 2
    assert capsys.readouterr().out.strip() == f"report: {bad} is not an eval report (expected `rows` with scenario_id and `totals`)"
    assert not (tmp_path / "out.xlsx").exists()
    missing = tmp_path / "nope.json"
    assert render(str(missing), str(tmp_path / "out.xlsx")) == 2


def test_matrix_and_slices(workbook):
    m = matrix(workbook)
    assert m["S-01"]["Grade"] == "PASS" and m["S-01"]["Score"] == "6/6" and m["S-01"]["P1-P7"] == "ok" and m["S-01"]["Note"] == "no"
    assert m["S-07"]["route"] == "PARTIAL" and m["S-07"]["Grade"] == "FAIL" and m["S-07"]["Score"] == "2.5/6" and m["S-07"]["P1-P7"] == "ok"
    assert m["S-02"]["Grade"] == "FAIL" and m["S-02"]["P1-P7"] == "—" and m["S-03"]["Grade"] == "PENDING" and m["S-03"]["route"] is None
    assert m["S-01"]["no_invented_facts"] is None and m["S-01"]["Judge mean"] is None
    assert m["total"]["route"] == "1/3" and m["total"]["Grade"] == "1/3" and m["total"]["guardrails"] == "2/3"
    ws = workbook["Verdict Matrix"]
    assert ws["E2"].fill.fgColor.rgb.endswith("C6EFCE") and ws["K3"].fill.fgColor.rgb.endswith("FFC7CE")
    rows = [r for r in ws.iter_rows(values_only=True) if r[0]]
    assert rows[-1][0] == "manual_escalation" and rows[-5][0] == "expected \\ final route"
    s = {}
    for r in workbook["Slices"].iter_rows(values_only=True):
        if r[0] in ("wording", "note_driven", "trap", "category", "route"):
            key = r[0]
        elif r[0]:
            s[(key, r[0])] = r[1:]
    assert s[("wording", "clean (directional)")][0] == "1/1" and s[("wording", "messy (directional)")][-1] == "0/2"
    assert s[("trap", "wrong_account (directional)")][0] == "0/1" and s[("route", "explain_events (directional)")][-1] == "1/1"


def test_compare_intersection(ds, tmp_path, json_path):
    before = {"split": "dev", "rows": [dict(normal_row(ds.scenarios["S-01"]), checks={**normal_row(ds.scenarios["S-01"])["checks"], "route_ok": False}),
                                       normal_row(ds.scenarios["S-09"])], "totals": {}}
    before_path = tmp_path / "eval_20261026T224024Z.json"
    before_path.write_text(json.dumps(before))
    out = tmp_path / "release.xlsx"
    assert render(str(json_path), str(out), before=str(before_path)) == 0
    wb = openpyxl.load_workbook(out)
    assert "Compare" in wb.sheetnames and dict(wb["Run"].iter_rows(values_only=True))["before_path"] == str(before_path)
    rows = [trim(r) for r in wb["Compare"].iter_rows(values_only=True) if r[0]]
    head = {r[0]: r[1] for r in rows[:4]}
    assert head["scenarios compared"] == 1 and head["excluded (in one run only)"] == "S-02, S-07, S-09"
    assert ("route", "0/1", "1/1") in rows and ("grade", "0/1", "1/1") in rows
    assert rows[-1] == ("S-01", "route", "FAIL", "PASS", "improved")


def test_judge_panel_and_err(ds, tmp_path, json_path):
    judge_path = tmp_path / "judge_x.json"
    judge_path.write_text(json.dumps(judge_data(json_path)))
    out = tmp_path / "release.xlsx"
    assert render(str(json_path), str(out), judge=str(judge_path)) == 0
    wb = openpyxl.load_workbook(out)
    panel = {r[0]: r[1:] for r in wb["S-01"].iter_rows(values_only=True) if r[0] in JUDGE_AXES}
    assert panel["no_leaks_or_promises"] == (1, "r", "q") and panel["no_invented_facts"][0] == 3
    s07 = [r for r in wb["S-07"].iter_rows(values_only=True) if r[0] == "ERR"]
    assert s07 == [("ERR", None, "RuntimeError: boom", None)]
    assert [r for r in wb["S-02"].iter_rows(values_only=True) if r[0] == "not judged"]
    m = matrix(wb)
    assert m["S-01"]["no_leaks_or_promises"] == 1 and m["S-01"]["Judge mean"] == 2.25 and m["S-07"]["no_leaks_or_promises"] == "ERR" and m["S-07"]["Judge mean"] is None
    assert m["total"]["no_leaks_or_promises"] == 1.0 and m["total"]["Judge mean"] == 2.25
    assert "Calibration" not in wb.sheetnames and dict(wb["Run"].iter_rows(values_only=True))["judge_path"] == "claude-opus-5-5 via judge sidecar"


def test_sidecar_mismatch_exit_2(tmp_path, json_path, capsys):
    other = tmp_path / "judge_other.json"
    other.write_text(json.dumps({**judge_data(tmp_path / "eval_other.json")}))
    assert render(str(json_path), str(tmp_path / "out.xlsx"), judge=str(other)) == 2
    assert "judged" in capsys.readouterr().out and not (tmp_path / "out.xlsx").exists()
    assert render(str(json_path), str(tmp_path / "out.xlsx"), labels=str(tmp_path / "labels.xlsx")) == 2
    assert "--labels needs --judge" in capsys.readouterr().out


def test_calibration_sheet(ds, tmp_path, json_path, capsys):
    judge_path = tmp_path / "judge_x.json"
    judge_path.write_text(json.dumps(judge_data(json_path)))
    labels_path = tmp_path / "labels.xlsx"
    assert main(["calibrate", "--build", str(json_path), "--out", str(labels_path)]) == 0
    assert "3 dev notes to score" in capsys.readouterr().out
    wb = openpyxl.load_workbook(labels_path)
    assert wb.sheetnames == ["Labels", "How to score"]
    rows = list(wb["Labels"].iter_rows(values_only=True))
    assert rows[0][:2] == ("scenario_id", "trap") and [r[0] for r in rows[1:]] == ["S-01", "S-02", "S-07"]
    assert rows[3][6] == NO_DRAFT and all(v is None for v in rows[1][8:12])
    out = tmp_path / "release.xlsx"
    assert render(str(json_path), str(out), judge=str(judge_path), labels=str(labels_path)) == 0
    assert list(openpyxl.load_workbook(out)["Calibration"].iter_rows(values_only=True)) == [("no labels yet",)]
    ws = wb["Labels"]
    for col in range(9, 13):
        ws.cell(row=2, column=col).value = 3
    wb.save(labels_path)
    assert render(str(json_path), str(out), judge=str(judge_path), labels=str(labels_path)) == 0
    cal = [trim(r) for r in openpyxl.load_workbook(out)["Calibration"].iter_rows(values_only=True) if r[0]]
    assert cal[0] == ("axis", "n", "exact", "within-1", "judge mean", "human mean") and cal[1] == ("no_invented_facts", 1, 1.0, 1.0, 3.0, 3.0)
    assert ("S-01", "no_leaks_or_promises", 1, 3) in cal and ("S-01", "matches_rationale", 2, 3) in cal


def test_old_format_row(ds):
    """A report made before v0.8 (a `gate` key, no policy_consistent) still renders: the route comes from checks."""
    old = normal_row(ds.scenarios["S-01"])
    old["gate"] = {"route": "explain_events", "gate_rejected": False, "gate_reasons": []}
    del old["checks"]["policy_consistent"]
    assert consistent(old) is None and consistent(normal_row(ds.scenarios["S-01"])) == "ok"
    assert case_row(1, ds.scenarios["S-01"], old)[8:12] == ["explain_events", "E-0117", "E-0117", None]
    assert "policy_consistent" in CASE_COLS and "gate_rejected" not in CASE_COLS
