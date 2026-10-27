"""sync: the code cross-check of both agents' results before the LLM writes the draft. No LLM.

Both agents have reported. This file confirms the pieces line up: the Account Agent's findings exist, the
Policy Agent returned rules, every kept rule's section was in force on the policy date, and some kept rule
covers the dispute category. Anything that does not line up becomes an issue in plain words plus a `lean`,
the cautious route the draft should lean to. The lean is a sentence shown to the LLM, never a route set by
code: the LLM still chooses, and the evals catch it when it chooses wrong.

Nothing is retried. The Policy Agent ran in parallel, so it read on the complaint date; when a different policy set
was in force on the disputed event's date (`policy_date_mismatch`), that is recorded and the draft leans to
manual_escalation, like any other gap.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from dispute_agent.account_agent.derive import DerivedFacts
from dispute_agent.data import Category, Route
from dispute_agent.policy_agent.agent import PolicyFindings, RuleReading
from dispute_agent.policy_agent.retrieve import PolicySection, candidates, in_force


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

    # keep only rules whose section exists and was in force on policy_date
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
    mismatch = f"policy read on {read_on}, but a different policy set was in force on {on_policy}"
    flags: list[tuple[bool, str, Route]] = [
        (policy_missing, f"policy findings missing: {detail}", "manual_escalation"),
        (not policy_missing and not category_covered, f"no kept rule covers {category}", "manual_escalation"),
        (policy_date_mismatch, mismatch, "manual_escalation"),
        (account_missing, "account findings missing", "need_more_evidence"),
        (budget_hit, "account agent hit its tool budget", "need_more_evidence"),
    ]
    return SyncResult(
        rules=kept,
        dropped_section_ids=dropped,
        account_missing=account_missing,
        policy_missing=policy_missing,
        policy_date_mismatch=policy_date_mismatch,
        budget_hit=budget_hit,
        category_covered=category_covered,
        issues=[issue for hit, issue, _ in flags if hit] + drop_issues,
        lean=next((route for hit, _, route in flags if hit), None),
    )
