"""derive_states: plain code that turns the Account Agent's findings into checked facts. No LLM.

The model proposes which event is disputed. This file confirms that event is real and on this account,
then works out the payment and refund state from the event's chain, the date whose policy applies,
and the fact flags (gap, ambiguous, other-account match). Language goes to the LLM; rules go to code.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import BaseModel

from dispute_agent.account_agent.agent import AccountFindings
from dispute_agent.config import WORLD_TODAY
from dispute_agent.data import PAYMENT_TYPES, REFUND_TYPES, Category, Dataset, Event, PaymentState, RefundState


class DerivedFacts(BaseModel):
    """Code-derived facts. Nothing in here was written by the model."""

    disputed_event_id: str | None
    candidate_event_ids: list[str]
    relevant_event_ids: list[str]
    dropped_off_account_ids: list[str]
    evidence_events: list[Event]
    payment_state: PaymentState
    refund_state: RefundState
    policy_date: datetime
    policy_date_basis: Literal["disputed_event", "complaint_date"]
    fact_gap: bool
    fact_ambiguous: bool
    other_account_match: bool
    reasons: list[str]


def derived_json(derived: DerivedFacts) -> dict[str, Any]:
    """For printing: evidence events shown as IDs only."""
    d = derived.model_dump(mode="json")
    d["evidence_events"] = [e["event_id"] for e in d["evidence_events"]]
    return d


def _chain_state(ds: Dataset, event_id: str) -> PaymentState:
    types = {e.type for e in ds.chain_of(event_id)}
    for t, state in (("payment_returned", "returned"), ("payment_reversed", "reversed"), ("payment_posted", "posted")):
        if t in types:
            return state
    return "pending"


def _payment_for_late_fee(ds: Dataset, fee: Event) -> Event | None:
    """The payment in question for an unlinked late fee: earliest payment chain head in the billing window."""
    due_day = ds.accounts[fee.account_id].due_day
    anchor = fee.occurred_at - timedelta(days=10)
    due = datetime(anchor.year, anchor.month, due_day)
    if due > anchor:  # due day of this month has not passed yet; use last month's
        first = anchor.replace(day=1) - timedelta(days=1)
        due = datetime(first.year, first.month, due_day)
    lo, hi = due - timedelta(days=10), due + timedelta(days=35)
    heads = [
        e for e in ds.events_for_account(fee.account_id)
        if e.type in PAYMENT_TYPES and e.related_event_id is None and lo <= e.occurred_at <= hi
    ]
    return heads[0] if heads else None


def derive_states(findings: AccountFindings | None, category: Category, account_id: str, ds: Dataset) -> DerivedFacts:
    reasons: list[str] = []
    f = findings or AccountFindings(relevant_event_ids=[], disputed_event_id=None, notes_summary="", summary="")

    # 1. cited set, cleaned: keep only real events on this account
    disputed = [f.disputed_event_id] if f.disputed_event_id else []
    cited = list(dict.fromkeys([*f.relevant_event_ids, *disputed, *f.candidate_event_ids]))
    on_account = [i for i in cited if i in ds.events and ds.events[i].account_id == account_id]
    dropped = [i for i in cited if i not in on_account]
    fact_gap = bool(dropped)
    if dropped:
        reasons.append(f"dropped citations not on {account_id}: {dropped}")
    candidates = [i for i in f.candidate_event_ids if i in on_account]
    evidence = sorted((ds.events[i] for i in on_account), key=lambda e: (e.occurred_at, e.event_id))

    # 2. confirm the disputed event
    d: Event | None = None
    if f.disputed_event_id:
        if f.disputed_event_id in on_account:
            d = ds.events[f.disputed_event_id]
        else:
            reasons.append(f"proposed disputed event {f.disputed_event_id} is unknown or not on this account")

    # 3. flags
    fact_ambiguous = d is None and len(candidates) > 1
    # an other-account match counts only if the event exists and really is on another account
    off_account = [
        m.event_id for m in f.other_account_matches
        if m.event_id in ds.events and ds.events[m.event_id].account_id != account_id
    ]
    if rejected := [m.event_id for m in f.other_account_matches if m.event_id not in off_account]:
        reasons.append(f"ignored other-account matches that are unknown or on {account_id}: {rejected}")
    other_account_match = bool(off_account)
    if off_account:
        reasons.append(f"other-account matches: {off_account}")
    if d is None and f.claimed_but_not_found:
        fact_gap = True
        reasons.append(f"claimed but not found: {f.claimed_but_not_found}")
    if findings is None:
        fact_gap = True
        reasons.append("no findings from the agent")
    if fact_ambiguous:
        reasons.append(f"more than one candidate: {candidates}")

    # 4. policy date: the disputed event's date if we have one, else the day of the complaint
    if d is not None:
        policy_date, basis = d.occurred_at, "disputed_event"
    else:
        policy_date, basis = WORLD_TODAY, "complaint_date"

    # 5. payment state
    cand_events = [ds.events[i] for i in candidates]
    if d is None:
        if cand_events and all(e.type in REFUND_TYPES for e in cand_events):
            payment_state: PaymentState = "not_applicable"
        else:
            payment_state = "not_found"
    elif d.type == "payment_returned":
        payment_state = "returned"
    elif d.type == "payment_reversed":
        payment_state = "reversed"
    elif d.type in PAYMENT_TYPES:
        payment_state = _chain_state(ds, d.event_id)
    elif d.type == "fee_assessed" and d.related_event_id:
        payment_state = _chain_state(ds, d.related_event_id)
        reasons.append(f"fee {d.event_id} is linked to {d.related_event_id}; state follows that chain")
    elif d.type == "fee_assessed":
        pay = _payment_for_late_fee(ds, d)
        payment_state = _chain_state(ds, pay.event_id) if pay else "not_found"
        reasons.append(f"late fee {d.event_id}: payment in question is {pay.event_id if pay else 'none'}")
    else:
        payment_state = "not_applicable"

    # 6. refund state
    in_play = ([d] if d else []) + cand_events
    chain = ds.chain_of(d.event_id) if d else []
    refund_state: RefundState = (
        "not_applicable" if category != "refund_status"
        else "issued" if any(e.type == "refund_issued" for e in in_play + chain)
        else "requested_not_issued" if any(e.type == "refund_requested" for e in in_play)
        else "no_record"
    )

    return DerivedFacts(
        disputed_event_id=d.event_id if d else None,
        candidate_event_ids=candidates,
        relevant_event_ids=on_account,
        dropped_off_account_ids=dropped,
        evidence_events=evidence,
        payment_state=payment_state,
        refund_state=refund_state,
        policy_date=policy_date,
        policy_date_basis=basis,
        fact_gap=fact_gap,
        fact_ambiguous=fact_ambiguous,
        other_account_match=other_account_match,
        reasons=reasons,
    )
