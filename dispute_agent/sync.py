"""sync: the code cross-check of both agents' results before the LLM writes the draft. No LLM.

Both agents have reported. This file confirms the pieces line up: the Account Agent's findings exist, the
Policy Agent returned rules, every kept rule's section was in force on the policy date, and some kept rule
covers the dispute category. Anything that does not line up becomes an issue in plain words plus a `lean`,
the cautious route the draft should lean to. The lean is a sentence shown to the LLM, never a route set by
code: the LLM still chooses, and the gate and the evals catch it when it chooses wrong.

Sync raises two flags the graph may act on, each at most once (docs/plans/orchestrator.md, O2): `account_missing`
(the Account Agent's finalize failed) sends the Account Agent back for one more run; `policy_date_mismatch` (the
Policy Agent read on the complaint date, but a different policy set was in force on the disputed event's date)
re-runs the Policy Agent on that date. Nothing else is retried: a budget hit, missing policy or a dropped rule
would come back the same.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from .data import Category, Route
from .derive import DerivedFacts
from .policy_agent import PolicyFindings, PolicySection, RuleReading, candidates, in_force


class SyncResult(BaseModel):
    rules: list[RuleReading]  # kept: the section is known and was in force on policy_date
    dropped_section_ids: list[str]
    account_missing: bool  # the Account Agent returned no findings (finalize failed)
    policy_missing: bool  # the Policy Agent returned None, errors, or no rules at all
    policy_date_mismatch: bool  # the policy was read on a date with a different in-force set than policy_date
    budget_hit: bool
    category_covered: bool  # some kept rule's section lists the dispute category
    issues: list[str]
    lean: Route | None  # a hint for the prompt; code never applies it


def sync(
    account: dict[str, Any] | None,
    policy: PolicyFindings | None,
    derived: DerivedFacts,
    category: Category,
    sections: dict[str, PolicySection],
) -> SyncResult:
    """Check both results against each other and the policy store. Pure."""
    account_missing = account is None or account["findings"] is None
    budget_hit = bool(account and account["budget_hit"])
    policy_missing = policy is None or not policy.rules

    # the Policy Agent ran in parallel, on the complaint date; a mismatch means a different policy set applied
    on_policy = derived.policy_date.date()
    read_on = policy.policy_date.date() if policy else on_policy
    policy_date_mismatch = read_on != on_policy and (
        {s.section_id for s in candidates(sections, category, read_on)}
        != {s.section_id for s in candidates(sections, category, on_policy)}
    )

    # keep only rules whose section exists and was in force on policy_date; after a re-retrieve, a drop here is a bug
    kept: list[RuleReading] = []
    dropped: list[str] = []
    drop_issues: list[str] = []
    for r in policy.rules if policy else []:
        section = sections.get(r.section_id)
        if section is None:
            dropped.append(r.section_id)
            drop_issues.append(f"rule {r.section_id} dropped: unknown section")
        elif not in_force(section, derived.policy_date.date()):
            dropped.append(r.section_id)
            drop_issues.append(f"rule {r.section_id} dropped: not in force on {derived.policy_date:%Y-%m-%d}")
        else:
            kept.append(r)
    category_covered = any(category in sections[r.section_id].categories for r in kept)

    # issues in priority order; the lean is the first that applies, so missing policy wins over a budget hit
    detail = "; ".join(policy.errors) if policy and policy.errors else "no rules"
    flags: list[tuple[bool, str, Route]] = [
        (policy_missing, f"policy findings missing: {detail}", "manual_escalation"),
        (not policy_missing and not category_covered, f"no kept rule covers {category}", "manual_escalation"),
        (account_missing, "account findings missing", "need_more_evidence"),
        (budget_hit, "account agent hit its tool budget", "need_more_evidence"),
    ]
    mismatch_issues = (
        [f"policy read on {read_on}, but a different policy set was in force on {on_policy}"] if policy_date_mismatch else []
    )
    return SyncResult(
        rules=kept,
        dropped_section_ids=dropped,
        account_missing=account_missing,
        policy_missing=policy_missing,
        policy_date_mismatch=policy_date_mismatch,
        budget_hit=budget_hit,
        category_covered=category_covered,
        issues=[issue for hit, issue, _ in flags if hit] + mismatch_issues + drop_issues,
        lean=next((route for hit, _, route in flags if hit), None),
    )
