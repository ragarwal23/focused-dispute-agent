"""The records (accounts, events, review cases, scenarios) and the Excel loader.

The shapes follow docs/data_contract.md exactly; a workbook with a wrong header fails to load.
The agent never reads this directly: it goes through the tools in account_agent/tools.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import openpyxl
from pydantic import BaseModel, Field, field_validator

# --- fixed value lists (data contract, "Conventions") ---------------------------------

Category = Literal["late_fee_autopay", "missing_payment", "returned_reversed_payment", "refund_status"]
Route = Literal["adjustment_review", "explain_events", "need_more_evidence", "manual_escalation"]
EventType = Literal[
    "payment_scheduled", "payment_attempted", "payment_posted", "payment_returned", "payment_reversed",
    "fee_assessed", "fee_waived", "refund_requested", "refund_issued",
    "autopay_enrolled", "autopay_cancelled", "rep_note",
]
Channel = Literal["autopay", "online", "phone", "bank_bill_pay", "check", "ach"]
CaseStatus = Literal["open", "closed"]
Split = Literal["dev", "heldout"]
Wording = Literal["clean", "messy"]
Trap = Literal[
    "none", "missing_record", "out_of_order", "wrong_account", "duplicate", "injection", "ambiguous", "stale_policy",
]
PaymentState = Literal["posted", "pending", "returned", "reversed", "not_found", "not_applicable"]
RefundState = Literal["issued", "requested_not_issued", "no_record", "not_applicable"]

PAYMENT_TYPES = {"payment_scheduled", "payment_attempted", "payment_posted"}
REFUND_TYPES = {"refund_requested", "refund_issued"}


def _split_ids(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [p.strip() for p in str(v).split(",") if p.strip()]


# --- records -----------------------------------------------------------------------------


class Account(BaseModel):
    account_id: str
    customer_name: str
    vehicle: str | None = None
    monthly_payment: float
    due_day: int
    bank_last4: str | None = None
    memo: str | None = None


class Event(BaseModel):
    event_id: str
    account_id: str
    type: EventType
    occurred_at: datetime
    posted_at: datetime | None = None
    amount: float | None = None
    channel: Channel | None = None
    reference: str | None = None
    related_event_id: str | None = None
    return_code: str | None = None
    memo: str | None = None

    @field_validator("amount")
    @classmethod
    def _round_money(cls, v: float | None) -> float | None:
        return None if v is None else round(v, 2)


class ReviewCase(BaseModel):
    case_id: str
    account_id: str
    category: Category
    disputed_event_id: str | None = None
    status: CaseStatus
    created_at: datetime
    summary: str | None = None


class Scenario(BaseModel):
    scenario_id: str
    split: Split
    account_id: str
    rep_message: str
    wording: Wording
    note_driven: bool
    trap: Trap
    expected_category: Category
    expected_route: Route
    expected_required_event_ids: list[str]
    expected_forbidden_event_ids: list[str] = []
    expected_disputed_event_id: str | None = None
    expected_payment_state: PaymentState
    expected_refund_state: RefundState
    expected_existing_case_id: str | None = None
    expected_policy_ids: list[str] = Field(min_length=1)
    rationale: str

    @field_validator(
        "expected_required_event_ids", "expected_forbidden_event_ids", "expected_policy_ids", mode="before"
    )
    @classmethod
    def _lists(cls, v: Any) -> list[str]:
        return _split_ids(v)


FILES: dict[str, type[BaseModel]] = {
    "accounts": Account,
    "events": Event,
    "review_cases": ReviewCase,
    "scenarios": Scenario,
}


# --- loader --------------------------------------------------------------------------------


def _coerce(v: Any) -> Any:
    """Excel cell -> plain value. Blank -> None, text stripped, numbers and datetimes pass."""
    if v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        return s or None
    return v


def _read_rows(path: Path, expected_columns: set[str]) -> list[dict[str, Any]]:
    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        rows = wb.worksheets[0].iter_rows(values_only=True)
        header = [(_coerce(h) if h is None else str(h).strip()) for h in next(rows)]
        columns = {h for h in header if h}
        if columns != expected_columns:
            raise ValueError(
                f"{path.name}: columns {sorted(columns ^ expected_columns)} are extra or missing"
            )
        out = []
        for n, raw in enumerate(rows, start=2):
            values = [_coerce(v) for v in raw]
            if all(v is None for v in values):
                continue
            row = {h: v for h, v in zip(header, values) if h}
            row["_row"] = n
            out.append(row)
        return out
    finally:
        wb.close()


@dataclass
class Dataset:
    accounts: dict[str, Account] = field(default_factory=dict)
    events: dict[str, Event] = field(default_factory=dict)
    review_cases: dict[str, ReviewCase] = field(default_factory=dict)
    scenarios: dict[str, Scenario] = field(default_factory=dict)

    def events_for_account(self, account_id: str) -> list[Event]:
        return sorted(
            (e for e in self.events.values() if e.account_id == account_id),
            key=lambda e: (e.occurred_at, e.event_id),
        )

    def cases_for_account(self, account_id: str) -> list[ReviewCase]:
        return sorted(
            (c for c in self.review_cases.values() if c.account_id == account_id),
            key=lambda c: c.created_at,
        )

    def children_of(self, event_id: str) -> list[Event]:
        return [e for e in self.events.values() if e.related_event_id == event_id]

    def chain_of(self, event_id: str) -> list[Event]:
        """The event's head (following related_event_id up) plus every descendant, sorted."""
        if event_id not in self.events:
            return []
        seen: set[str] = set()
        head = self.events[event_id]
        while head.related_event_id in self.events and head.event_id not in seen:
            seen.add(head.event_id)
            head = self.events[head.related_event_id]
        out: list[Event] = []
        stack, seen = [head], set()
        while stack:
            e = stack.pop()
            if e.event_id in seen:
                continue
            seen.add(e.event_id)
            out.append(e)
            stack.extend(self.children_of(e.event_id))
        return sorted(out, key=lambda e: (e.occurred_at, e.event_id))


def load_dataset(data_dir: str | Path = "data") -> Dataset:
    data_dir = Path(data_dir)
    ds = Dataset()
    for name, model in FILES.items():
        path = data_dir / f"{name}.xlsx"
        table: dict[str, BaseModel] = getattr(ds, name)
        key = next(iter(model.model_fields))  # first field is the ID column
        for row in _read_rows(path, set(model.model_fields)):
            n = row.pop("_row")
            try:
                rec = model(**row)
            except Exception as exc:  # pydantic error, re-raised with location
                raise ValueError(f"{path.name} row {n}: {exc}") from exc
            rid = getattr(rec, key)
            if rid in table:
                raise ValueError(f"{path.name} row {n}: duplicate id {rid}")
            table[rid] = rec
    for e in ds.events.values():
        if e.related_event_id:
            rel = ds.events.get(e.related_event_id)
            if rel is None or rel.account_id != e.account_id:
                raise ValueError(
                    f"events.xlsx {e.event_id}: related_event_id {e.related_event_id} missing or on another account"
                )
    return ds
