"""The gate: the last check on the LLM's draft before a person sees it. No LLM.

The draft (route, cited events, cited policy sections) arrives from synthesize. Code confirms every cited ID is
real and on this account and every cited section was in force on the policy date and is registered to a check,
then runs our P1-P7 check (checks.py) for each cited section against the account's events. A `true` check rules
out a route; if the draft chose that route, the gate forces `manual_escalation` and keeps the draft as written, with
the reasons. The ID and date checks repeat what derive_states and the Policy Agent did to the LLM's inputs on
purpose: the draft is a fresh LLM output written after both, and nothing else checks what goes to the human.
"""

from __future__ import annotations

from pydantic import BaseModel

from dispute_agent.account_agent.derive import DerivedFacts
from dispute_agent.data import Category, Dataset, Route
from dispute_agent.gate.checks import REGISTRY, RULES_OUT, Verdict, run_checks
from dispute_agent.policy_agent.agent import PolicyFindings
from dispute_agent.policy_agent.retrieve import PolicySection, in_force
from dispute_agent.sync.synthesize import Draft


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
