"""The gate: our own P1-P7 checks over event fields, keyed by policy section ID. No LLM.

The LLM's draft (route, cited events, cited policy sections) arrives here last. Code confirms every cited ID is
real and on this account and every cited section was in force on the policy date and is registered to a check,
then runs the check for each cited section against the account's events. A `true` check rules out a route; if
the draft chose that route, the gate forces `manual_escalation` and keeps the draft as written, with the reasons.
The ID and date checks repeat what derive_states and the Policy Agent did to the LLM's inputs on purpose: the
draft is a fresh LLM output written after both, and nothing else checks what goes to the human.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Literal

from pydantic import BaseModel, Field

from .config import WORLD_TODAY
from .data import PAYMENT_TYPES, Category, Dataset, Event, Route
from .derive import DerivedFacts
from .policy_agent import PolicyFindings, PolicySection, in_force

# --- policy constants (data/README.md, P1-P7) -----------------------------------------------------

GRACE_DAYS = 10  # P1: grace ends 23:59 on due_day + 10
LATE_FEE = 25.0  # P3
RETURN_FEE = 15.0  # P4
REFUND_BUSINESS_DAYS = 10  # P7
HOLIDAYS = {date(2026, 9, 7), date(2026, 10, 12)}  # Labor Day, Columbus Day: inside the data window

Verdict = Literal["true", "false", "unknown"]
Confidence = Literal["high", "medium", "low"]

# --- registry: section ID -> our check, or "procedural" (mirrors data/policies/README.md) ------------

RULES_OUT: dict[str, Route] = {
    "late_fee_unwarranted": "explain_events",
    "lender_side_failure": "explain_events",
    "customer_cancelled_autopay": "adjustment_review",
    "return_fee_valid": "adjustment_review",
    "refund_overdue": "explain_events",
}
_REGISTERED = {
    "late_fee_unwarranted": "LF.v3.s1 LF.v3.s2 LF.v3.s4",
    "lender_side_failure": "LF.v3.s3 LF.v3.s5 AP.v1.s5 RR.v1.s7",
    "customer_cancelled_autopay": "AP.v1.s4",
    "return_fee_valid": "RR.v1.s2 RR.v1.s3",
    "refund_overdue": "RF.v1.s2 RF.v1.s4",
    "procedural": "LF.v3.s6 LF.v3.s7 LF.v3.s8 AP.v1.s1 AP.v1.s2 AP.v1.s3 AP.v1.s6 "
                  "RR.v1.s1 RR.v1.s4 RR.v1.s5 RR.v1.s6 RR.v1.s8 RF.v1.s1 RF.v1.s3 RF.v1.s5 RF.v1.s6",
}
REGISTRY: dict[str, str] = {s: check for check, ids in _REGISTERED.items() for s in ids.split()}


# --- the five checks -------------------------------------------------------------------------------


def add_business_days(day: date, n: int) -> date:
    while n:
        day += timedelta(days=1)
        if day.weekday() < 5 and day not in HOLIDAYS:
            n -= 1
    return day


def _verdict(v: bool) -> Verdict:
    return "true" if v else "false"


def _is_late_fee(e: Event) -> bool:
    return e.type == "fee_assessed" and e.related_event_id is None and e.amount == LATE_FEE


def _late_fee_in_play(ds: Dataset, derived: DerivedFacts) -> Event | None:
    """The disputed event if it is a late fee, else the latest late fee among the evidence."""
    d = ds.events.get(derived.disputed_event_id) if derived.disputed_event_id else None
    if d is not None and _is_late_fee(d):
        return d
    fees = [e for e in derived.evidence_events if _is_late_fee(e)]
    return fees[-1] if fees else None


def _grace_window(fee: Event) -> tuple[datetime, datetime, datetime]:
    """(window_lo, due, grace_end) from the fee alone: it is assessed the day after grace ends (P3)."""
    grace_end = datetime.combine(fee.occurred_at.date() - timedelta(days=1), time.max)
    due = datetime.combine(grace_end.date() - timedelta(days=GRACE_DAYS), time.min)
    return due - timedelta(days=10), due, grace_end


def _received(e: Event) -> bool:
    return e.type in ("payment_attempted", "payment_posted")


def _clean(ds: Dataset, e: Event) -> bool:
    """Not returned or reversed anywhere in its chain (LF.v3.s4: a returned payment does not count)."""
    return not any(c.type in ("payment_returned", "payment_reversed") for c in ds.chain_of(e.event_id))


def _received_in_grace(ds: Dataset, account_id: str, lo: datetime, grace_end: datetime) -> list[Event]:
    events = ds.events_for_account(account_id)
    return [e for e in events if _received(e) and lo <= e.occurred_at <= grace_end and _clean(ds, e)]


def _autopay_status(ds: Dataset, account_id: str, at: datetime) -> str | None:
    """The type of the latest autopay_enrolled / autopay_cancelled event at or before `at`, or None."""
    events = ds.events_for_account(account_id)
    status = [e for e in events if e.type in ("autopay_enrolled", "autopay_cancelled") and e.occurred_at <= at]
    return status[-1].type if status else None


def late_fee_unwarranted(ds: Dataset, account_id: str, derived: DerivedFacts) -> Verdict:
    """P1-P3: a late fee was assessed, yet a payment was received by the end of grace."""
    fee = _late_fee_in_play(ds, derived)
    if fee is None:
        return "unknown"
    lo, _, grace_end = _grace_window(fee)
    return _verdict(bool(_received_in_grace(ds, account_id, lo, grace_end)))


def lender_side_failure(ds: Dataset, account_id: str, derived: DerivedFacts) -> Verdict:
    """P5: a draft that never ran while autopay was on, a received payment never posted, or posted after the fee."""
    d = ds.events.get(derived.disputed_event_id) if derived.disputed_event_id else None
    if d is not None and d.type in ("payment_returned", "payment_reversed"):
        return "unknown"  # a keying error is recorded only in a note; the LLM has to read it
    fee = _late_fee_in_play(ds, derived)
    if fee is None:
        return "unknown"
    lo, _, grace_end = _grace_window(fee)
    events = ds.events_for_account(account_id)
    heads = [e for e in events if e.type in PAYMENT_TYPES and not e.related_event_id and lo <= e.occurred_at <= grace_end]
    for head in heads:
        chain = ds.chain_of(head.event_id)
        posted = any(c.type == "payment_posted" for c in chain)
        if head.type == "payment_scheduled" and head.channel == "autopay" and not posted:
            if _autopay_status(ds, account_id, head.occurred_at) == "autopay_enrolled":
                return "true"
        if head.type == "payment_attempted" and not posted:
            return "true"
        if any(_received(c) and c.occurred_at <= grace_end and c.posted_at and c.posted_at > fee.occurred_at for c in chain):
            return "true"
    return "false"


def customer_cancelled_autopay(ds: Dataset, account_id: str, derived: DerivedFacts) -> Verdict:
    """P6: autopay was cancelled before the due date and nothing was received in grace."""
    fee = _late_fee_in_play(ds, derived)
    if fee is None:
        return "unknown"
    lo, due, grace_end = _grace_window(fee)
    cancelled = _autopay_status(ds, account_id, due) == "autopay_cancelled"
    return _verdict(cancelled and not _received_in_grace(ds, account_id, lo, grace_end))


def return_fee_valid(ds: Dataset, account_id: str, derived: DerivedFacts) -> Verdict:
    """P4: the $15 fee in the disputed chain follows a posted return."""
    if not derived.disputed_event_id:
        return "unknown"
    fees = [c for c in ds.chain_of(derived.disputed_event_id) if c.type == "fee_assessed" and c.related_event_id]
    if not fees:
        return "unknown"
    parent = ds.events[fees[0].related_event_id]
    return _verdict(parent.type == "payment_returned" and fees[0].amount == RETURN_FEE)


def refund_overdue(ds: Dataset, account_id: str, derived: DerivedFacts) -> Verdict:
    """P7: the refund request in question was made more than 10 business days ago and nothing was issued.

    With a disputed event, only requests in its chain count (the customer asked about that one); without one, every
    request in the evidence counts (fact-ambiguous cases like S-15).
    """
    pool = ds.chain_of(derived.disputed_event_id) if derived.disputed_event_id else derived.evidence_events
    requests = [e for e in pool if e.type == "refund_requested"]
    if not requests:
        return "unknown"
    for r in requests:
        issued = any(c.type == "refund_issued" for c in ds.chain_of(r.event_id))
        if not issued and WORLD_TODAY.date() > add_business_days(r.occurred_at.date(), REFUND_BUSINESS_DAYS):
            return "true"
    return "false"


CHECKS = {
    "late_fee_unwarranted": late_fee_unwarranted,
    "lender_side_failure": lender_side_failure,
    "customer_cancelled_autopay": customer_cancelled_autopay,
    "return_fee_valid": return_fee_valid,
    "refund_overdue": refund_overdue,
}


def run_checks(ds: Dataset, account_id: str, derived: DerivedFacts) -> dict[str, Verdict]:
    return {name: check(ds, account_id, derived) for name, check in CHECKS.items()}


# --- the draft (what the LLM writes; synthesize imports this) ----------------------------------------


class Draft(BaseModel):  # no docstring: pydantic would send it to the LLM as the schema description
    review_route: Route = Field(description="Which human queue gets the case")
    event_ids: list[str] = Field(description="Event IDs from the evidence shown that support the route, rep notes included")
    policy_source_ids: list[str] = Field(description="Section IDs from the policy rules shown that support the route")
    confidence: Confidence = Field(description="high, medium or low; never high with need_more_evidence or manual_escalation")
    open_questions: list[str] = Field(default=[], description="What is still unknown; required for need_more_evidence, else empty")
    case_note: str = Field(description="Three to five plain sentences a rep can edit and file")


# --- the gate --------------------------------------------------------------------------------------


class SectionCheck(BaseModel):
    section_id: str
    check: str  # a check name or "procedural"
    result: Verdict | None = None
    rules_out: Route | None = None


class GateResult(BaseModel):
    route: Route
    gate_rejected: bool
    gate_reasons: list[str]
    check_results: dict[str, Verdict]
    section_checks: list[SectionCheck]
    existing_case_id: str | None


def find_duplicate(ds: Dataset, account_id: str, category: Category, disputed_event_id: str | None) -> str | None:
    """An open case with the same account, category and disputed event, else None."""
    if disputed_event_id is None:
        return None
    for c in ds.cases_for_account(account_id):
        if c.status == "open" and c.category == category and c.disputed_event_id == disputed_event_id:
            return c.case_id
    return None


def gate(
    draft: Draft | None,
    account_id: str,
    category: Category,
    derived: DerivedFacts,
    policy: PolicyFindings | None,
    sections: dict[str, PolicySection],
    ds: Dataset,
) -> GateResult:
    """Every check runs; every failure is a reason. Any reason forces manual_escalation."""
    reasons: list[str] = []
    section_checks: list[SectionCheck] = []
    check_results = run_checks(ds, account_id, derived)
    if draft is None:
        reasons.append("no draft from synthesize")
    else:
        for eid in draft.event_ids:
            e = ds.events.get(eid)
            if e is None:
                reasons.append(f"event {eid} is unknown")
            elif e.account_id != account_id:
                reasons.append(f"event {eid} is on {e.account_id}, not {account_id}")
        flagged = {r.section_id: r for r in (policy.rules if policy else []) if r.policy_ambiguous}
        for sid in draft.policy_source_ids:
            s = sections.get(sid)
            if s is None:
                reasons.append(f"policy {sid} is unknown")
                continue
            if not in_force(s, derived.policy_date.date()):
                reasons.append(f"policy {sid} was not in force on {derived.policy_date:%Y-%m-%d}")
                continue
            check = REGISTRY.get(sid)
            if check is None:
                reasons.append(f"policy {sid} is not registered to a check")
                continue
            if sid in flagged:
                reasons.append(f"policy {sid} reading is ambiguous: {flagged[sid].ambiguity_reason}")
            if check == "procedural":
                section_checks.append(SectionCheck(section_id=sid, check=check))
                continue
            result = check_results[check]
            rules_out = RULES_OUT[check] if result == "true" else None
            section_checks.append(SectionCheck(section_id=sid, check=check, result=result, rules_out=rules_out))
            if rules_out == draft.review_route:
                reasons.append(f"{check} is true ({sid}), which rules out {rules_out}")
    rejected = bool(reasons)
    return GateResult(
        route="manual_escalation" if rejected else draft.review_route,
        gate_rejected=rejected,
        gate_reasons=reasons,
        check_results=check_results,
        section_checks=section_checks,
        existing_case_id=find_duplicate(ds, account_id, category, derived.disputed_event_id),
    )
