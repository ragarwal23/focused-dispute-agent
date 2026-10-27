"""Our own policy checks, P1-P7, in Python. No LLM. The eval runs them; the agent never does.

Each check reads structured event fields only, never notes or the LLM's reading of the policy, and returns "true",
"false" or "unknown". A "true" result rules out one route (RULES_OUT). `audit` checks the golden labels: no expected
route may be ruled out. `ruled_out` feeds the eval's `policy_consistent` check, which needs no golden answer, so it
can score live cases too. The checks cannot read notes, so a wrong route decided by a note alone (S-03, S-10, S-14)
passes them. `python -m dispute_agent.evaluation.policy_checks` prints the audit and exits 1 on any conflict.
"""

from __future__ import annotations

import sys
from datetime import date, datetime, time, timedelta
from typing import Literal

from dispute_agent.account_agent.derive import DerivedFacts, derive_states
from dispute_agent.config import WORLD_TODAY
from dispute_agent.data import PAYMENT_TYPES, Dataset, Event, Route, load_dataset
from dispute_agent.evaluation.golden import key_findings

# --- policy constants (data/README.md, P1-P7) -----------------------------------------------------

GRACE_DAYS = 10  # P1: grace ends 23:59 on due_day + 10
LATE_FEE = 25.0  # P3
RETURN_FEE = 15.0  # P4
REFUND_BUSINESS_DAYS = 10  # P7
HOLIDAYS = {date(2026, 9, 7), date(2026, 10, 12)}  # Labor Day, Columbus Day: inside the data window

Verdict = Literal["true", "false", "unknown"]
RULES_OUT: dict[str, Route] = {
    "late_fee_unwarranted": "explain_events",
    "lender_side_failure": "explain_events",
    "customer_cancelled_autopay": "adjustment_review",
    "return_fee_valid": "adjustment_review",
    "refund_overdue": "explain_events",
}


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


def ruled_out(ds: Dataset, account_id: str, derived: DerivedFacts) -> set[Route]:
    """The routes P1-P7 rule out on these facts. The two cautious routes never appear."""
    return {RULES_OUT[name] for name, verdict in run_checks(ds, account_id, derived).items() if verdict == "true"}


def audit(ds: Dataset) -> tuple[list[str], bool]:
    """Every scenario's answer key through P1-P7: one line each, ok unless the expected route is ruled out."""
    lines, ok = [], True
    for s in ds.scenarios.values():
        derived = derive_states(key_findings(s), s.expected_category, s.account_id, ds)
        true = [name for name, verdict in run_checks(ds, s.account_id, derived).items() if verdict == "true"]
        conflict = s.expected_route in ruled_out(ds, s.account_id, derived)
        ok &= not conflict
        lines.append(f"{s.scenario_id} {s.expected_route:<19} true: {', '.join(true) or '-':<50} {'CONFLICT' if conflict else 'ok'}")
    return lines, ok


if __name__ == "__main__":
    lines, ok = audit(load_dataset("data"))
    print("\n".join(lines))
    sys.exit(0 if ok else 1)
