"""One eval JSON plus golden.xlsx -> a release workbook: `Run`, a `Cases` index, one `S-xx` sheet per golden case.

Presentation only: every actual value is copied from the JSON row the way the run produced it; nothing is scored,
coloured or judged here (phase 2 adds a verdict column to each sheet). Reports are committed by hand under
dispute_agent/evaluation/reports/, one per release.

    python -m dispute_agent report runs/eval_<ts>.json --out dispute_agent/evaluation/reports/release_<date>.xlsx
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Font

from dispute_agent.evaluation.golden import GOLDEN_XLSX, HEADER_FILL, HEADER_FONT, WRAP, Case, cell, read_golden

RUN_KEYS = ("json_path", "captured_at", "model_id", "split", "scenarios", "exceptions", "golden_path", "langsmith_project")
CASE_COLS = [
    "#", "scenario_id", "split", "trap", "wording", "note_driven", "complaint", "golden_route", "actual_route",
    "golden_disputed", "actual_disputed", "gate_rejected", "case_created", "attempts", "status",
]
FORBIDDEN_CITED = "forbidden_event_ids / cited anywhere by the Account Agent"
NO_DRAFT = "(no draft)"


def load_eval(path: Path) -> dict[str, Any]:
    """The JSON an `eval` run wrote; anything else (an `ask` dump, a checkpoint) raises ValueError."""
    data = json.loads(path.read_text())
    rows = data.get("rows") if isinstance(data, dict) else None
    if not isinstance(rows, list) or "totals" not in data or not all("scenario_id" in r for r in rows):
        raise ValueError(f"{path} is not an eval report (expected `rows` with scenario_id and `totals`)")
    return data


def captured_at(path: Path) -> str:
    m = re.search(r"(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})Z", path.name)
    return f"{m[1]}-{m[2]}-{m[3]} {m[4]}:{m[5]}:{m[6]}Z" if m else ""


def yes(v: Any) -> str:
    return "yes" if v else "no"


def cited_by_account_agent(row: dict[str, Any]) -> set[str]:
    """Every event ID the Account Agent's findings mention (the JSON keeps no raw_cited; this is the same set)."""
    f = row.get("findings") or {}
    return set(f.get("relevant_event_ids") or []) | set(f.get("candidate_event_ids") or []) | ({f.get("disputed_event_id")} - {None})


def actual_fields(case: Case, row: dict[str, Any]) -> dict[str, Any]:
    """The `actual` column of a scenario sheet, keyed by the golden field it sits beside."""
    draft, gate, derived, filed = row.get("draft"), row.get("gate") or {}, row.get("derived") or {}, row.get("case") or {}
    forbidden_cited = sorted(set(case.expected_forbidden_event_ids) & cited_by_account_agent(row))
    return {
        "category": row.get("category_pred"),
        "route": gate.get("route"),
        "draft_route": draft["review_route"] if draft else NO_DRAFT,
        "disputed_event_id": (row.get("findings") or {}).get("disputed_event_id"),
        "required_event_ids / cited_event_ids": cell(draft["event_ids"]) if draft else NO_DRAFT,
        FORBIDDEN_CITED: cell(forbidden_cited),
        "payment_state": derived.get("payment_state"),
        "refund_state": derived.get("refund_state"),
        "existing_case_id / case_id": f"{filed['case_id']} ({'created' if filed.get('created') else 'existing'})" if filed else None,
        "policy_ids / policy_source_ids": cell(draft["policy_source_ids"]) if draft else NO_DRAFT,
        "confidence": draft["confidence"] if draft else None,
        "open_questions": "\n".join(draft.get("open_questions") or []) or None if draft else None,
        "rationale / case_note": draft["case_note"] if draft else NO_DRAFT,
    }


def golden_fields(case: Case) -> dict[str, Any]:
    return {
        "category": case.expected_category, "route": case.expected_route, "draft_route": None,
        "disputed_event_id": case.expected_disputed_event_id,
        "required_event_ids / cited_event_ids": cell(case.expected_required_event_ids),
        FORBIDDEN_CITED: cell(case.expected_forbidden_event_ids),
        "payment_state": case.expected_payment_state, "refund_state": case.expected_refund_state,
        "existing_case_id / case_id": case.expected_existing_case_id,
        "policy_ids / policy_source_ids": cell(case.expected_policy_ids),
        "confidence": None, "open_questions": None, "rationale / case_note": case.rationale,
    }


def run_detail(row: dict[str, Any]) -> list[tuple[str, Any]]:
    checks, gate, sync = row.get("checks") or {}, row.get("gate") or {}, row.get("sync") or {}
    return [
        ("gate_reasons", cell(gate.get("gate_reasons"))), ("sync_issues", cell(sync.get("issues"))),
        ("account_summary", (row.get("findings") or {}).get("summary")), ("tool_calls_made", checks.get("tool_calls")),
        ("tool_errors", cell(row.get("tool_errors"))), ("account_attempts", row.get("account_attempts")),
        ("policy_attempts", row.get("policy_attempts")), ("thread_id", row.get("thread_id")),
    ]


def _header(ws, row: int) -> None:
    for c in ws[row]:
        c.font, c.fill = HEADER_FONT, HEADER_FILL


def write_scenario_sheet(wb: Workbook, case: Case, row: dict[str, Any] | None) -> None:
    ws = wb.create_sheet(title=case.scenario_id)
    ws.append([f"{case.scenario_id} — {case.rep_message}"])
    ws["A1"].font = Font(bold=True, size=12)
    ws.append([])
    ws.append(["field", "golden", "actual"])
    _header(ws, 3)
    golden = golden_fields(case)
    if row is None:
        actual: dict[str, Any] = {}
    elif "exception" in row:
        actual = {k: f"EXCEPTION: {row['exception']}" for k in golden}
    else:
        actual = actual_fields(case, row)
    for name, g in golden.items():
        if name == "rationale / case_note":
            ws.append([])
        ws.append([name, g, actual.get(name)])
    ws.append([])
    ws.append(["Run detail"])
    _header(ws, ws.max_row)
    for name, value in run_detail(row or {}):
        ws.append([name, None, value if row and "exception" not in row else None])
    for col, width in zip("ABC", (28, 60, 60)):
        ws.column_dimensions[col].width = width
    for r in ws.iter_rows(min_row=3):
        for c in r:
            c.alignment = WRAP
    ws.freeze_panes = "A4"


def case_row(n: int, case: Case, row: dict[str, Any] | None) -> list[Any]:
    base = [n, case.scenario_id, case.split, case.trap, case.wording, case.note_driven, case.rep_message[:80], case.expected_route]
    if row is None:
        return base + [None, case.expected_disputed_event_id, None, None, None, None, "PENDING"]
    if "exception" in row:
        return base + [None, case.expected_disputed_event_id, None, None, None, None, "EXCEPTION"]
    gate, filed = row.get("gate") or {}, row.get("case") or {}
    return base + [
        gate.get("route"), case.expected_disputed_event_id, (row.get("findings") or {}).get("disputed_event_id"),
        yes(gate.get("gate_rejected")), yes(filed.get("created")),
        f"a{row.get('account_attempts', '?')}/p{row.get('policy_attempts', '?')}", "OK",
    ]


def write_report(data: dict[str, Any], json_path: Path, cases: list[Case], out: Path) -> None:
    rows = {r["scenario_id"]: r for r in data["rows"]}
    wb = Workbook()
    run = wb.active
    run.title = "Run"
    values = {
        "json_path": str(json_path), "captured_at": captured_at(json_path), "model_id": data.get("model_id", ""),
        "split": data.get("split", ""), "scenarios": len(rows), "exceptions": sum("exception" in r for r in rows.values()),
        "golden_path": data.get("golden_path", ""), "langsmith_project": data.get("langsmith_project", ""),
    }
    for key in RUN_KEYS:
        run.append([key, values[key]])
        run[run.max_row][0].font = HEADER_FONT
    run.column_dimensions["A"].width, run.column_dimensions["B"].width = 20, 70
    idx = wb.create_sheet(title="Cases")
    idx.append(CASE_COLS)
    _header(idx, 1)
    for n, case in enumerate(cases, 1):
        idx.append(case_row(n, case, rows.get(case.scenario_id)))
        write_scenario_sheet(wb, case, rows.get(case.scenario_id))
    idx.freeze_panes = "A2"
    out.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out)


def render(json_path: str, out: str, golden_path: Path = GOLDEN_XLSX) -> int:
    """The `report` command: exit 0 on success, 2 when the input is not an eval report."""
    try:
        data = load_eval(Path(json_path))
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"report: {exc}")
        return 2
    cases = read_golden(golden_path)
    write_report(data, Path(json_path), cases, Path(out))
    print(f"wrote {out}: {len(cases)} scenario sheets, {len(data['rows'])} with a run")
    return 0
