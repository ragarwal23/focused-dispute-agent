"""The eval: run every scenario in a split, score it against the answer key in scenarios.xlsx, print a table.

Table columns, left to right:
    cat    category right            disp   disputed event right      req   required events cited
    forb   no forbidden events cited  drop   off-account IDs dropped   pay   payment state right
    ref    refund state right         key    states right given the key's category (separates classify errors)
    gap / amb / oam                   fact_gap, fact_ambiguous, other_account_match flags right
    calls / budget / err              tool calls used, budget hit, tool errors
    pret / pcite / pforb / pamb       policy: required sections retrieved, cited, no forbidden cited, # ambiguous
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .account_agent import build_account_agent
from .data import Dataset, Scenario, load_dataset
from .derive import DerivedFacts, derive_states, derived_json
from .pipeline import investigate
from .policy_agent import PolicyFindings, load_policies

HEADER = (
    f"{'scenario':<9}{'trap':<15}{'wording':<8}"
    f"| {'cat':<4}{'disp':<5}{'req':<4}{'forb':<5}{'drop':<5}{'pay':<4}{'ref':<4}{'key':<4}"
    f"| {'gap':<4}{'amb':<4}{'oam':<4}| {'calls':<6}{'budget':<7}{'err':<4}"
    f"| {'pret':<5}{'pcite':<6}{'pforb':<6}{'pamb':<5}"
)
CHECKS = (  # the pass/fail checks summed in the totals line
    "category_ok", "disputed_ok", "required_found", "forbidden_clear", "payment_ok", "refund_ok", "states_ok_keycat",
    "gap_ok", "ambiguous_ok", "oam_ok",
)
FORBIDDEN_POLICY_PREFIXES = ("LF.v2.", "EX.v1.")  # superseded or out-of-scope policy; never a valid citation


def _mark(v: Any) -> str:
    return "ok" if v is True else "X" if v is False else str(v)


def expected_flags(s: Scenario) -> tuple[bool, bool, bool]:
    """(fact_gap, fact_ambiguous, other_account_match) as the scenario's trap implies them."""
    return s.trap in ("missing_record", "wrong_account"), s.trap == "ambiguous", s.trap == "wrong_account"


def score(
    s: Scenario,
    category: str,
    account: dict[str, Any],
    derived: DerivedFacts,
    ds: Dataset,
    policy: PolicyFindings | None = None,
) -> dict[str, Any]:
    f = account["findings"]
    ok = f is not None  # a failed finalize fails every model-side check, so a null key can't score it as right
    raw_cited = set()  # every event ID the model cited, before derive_states cleans them
    if f:
        raw_cited = set(f.relevant_event_ids) | set(f.candidate_event_ids) | ({f.disputed_event_id} - {None})
    keycat = derive_states(f, s.expected_category, s.account_id, ds)
    gap, amb, oam = expected_flags(s)
    checks = {
        "category_ok": category == s.expected_category,
        "disputed_ok": ok and f.disputed_event_id == s.expected_disputed_event_id,
        "required_found": ok and set(s.expected_required_event_ids) <= raw_cited,
        "forbidden_clear": ok and not (set(s.expected_forbidden_event_ids) & raw_cited),
        "dropped": len(derived.dropped_off_account_ids),
        "payment_ok": ok and derived.payment_state == s.expected_payment_state,
        "refund_ok": ok and derived.refund_state == s.expected_refund_state,
        "states_ok_keycat": ok and keycat.payment_state == s.expected_payment_state
        and keycat.refund_state == s.expected_refund_state,
        "gap_ok": ok and derived.fact_gap == gap,
        "ambiguous_ok": ok and derived.fact_ambiguous == amb,
        "oam_ok": ok and derived.other_account_match == oam,
        "tool_calls": account["tool_calls_made"],
        "budget_hit": account["budget_hit"],
        "errors": len(account["tool_errors"]),
    }
    if policy is not None:
        expected = set(s.expected_policy_ids)
        retrieved = {r.section.section_id for r in policy.retrieved}
        cited = [r.section_id for r in policy.rules]
        checks.update(
            policy_retrieved_ok=expected <= retrieved,
            policy_cited_ok=expected <= set(cited),
            policy_forbidden_clear=not [i for i in cited if i.startswith(FORBIDDEN_POLICY_PREFIXES)],
            policy_ambiguous_count=sum(r.policy_ambiguous for r in policy.rules),
            policy_dropped=len(policy.dropped_section_ids),
        )
    return checks


def row_line(s: Scenario, c: dict[str, Any]) -> str:
    return (
        f"{s.scenario_id:<9}{s.trap:<15}{s.wording:<8}| {_mark(c['category_ok']):<4}{_mark(c['disputed_ok']):<5}"
        f"{_mark(c['required_found']):<4}{_mark(c['forbidden_clear']):<5}{c['dropped']:<5}{_mark(c['payment_ok']):<4}"
        f"{_mark(c['refund_ok']):<4}{_mark(c['states_ok_keycat']):<4}"
        f"| {_mark(c['gap_ok']):<4}{_mark(c['ambiguous_ok']):<4}{_mark(c['oam_ok']):<4}"
        f"| {c['tool_calls']:<6}{'HIT' if c['budget_hit'] else 'no':<7}{c['errors']:<4}| "
        f"{_mark(c.get('policy_retrieved_ok')):<5}{_mark(c.get('policy_cited_ok')):<6}"
        f"{_mark(c.get('policy_forbidden_clear')):<6}{c.get('policy_ambiguous_count', '-'):<5}"
    )


def run_eval(
    data_dir: str, split: str, scenario_id: str | None, min_disputed_acc: float, min_policy_recall: float
) -> int:
    """Score the split (or one scenario), write runs/eval_<timestamp>.json, return the exit code."""
    ds = load_dataset(data_dir)
    agent = build_account_agent(ds)
    sections = load_policies(Path(data_dir) / "policies")
    if scenario_id:
        scenarios = [s for s in ds.scenarios.values() if s.scenario_id == scenario_id]
    else:
        scenarios = [s for s in ds.scenarios.values() if s.split == split]
    if os.getenv("LANGSMITH_TRACING", "").lower() == "true":
        print(f"tracing to LangSmith project: {os.getenv('LANGSMITH_PROJECT', 'default')}")
    print(HEADER)

    rows: list[dict[str, Any]] = []
    for s in scenarios:
        tags = [
            f"scenario={s.scenario_id}", f"category={s.expected_category}", f"trap={s.trap}", f"wording={s.wording}",
            f"note_driven={str(s.note_driven).lower()}", "slice=investigate",
        ]
        try:
            category, account, derived, policy = investigate(
                ds, sections, agent, s.account_id, s.rep_message, tags, s.scenario_id
            )
        except Exception as exc:
            print(f"{s.scenario_id:<9}EXCEPTION {type(exc).__name__}: {exc}")
            rows.append({"scenario_id": s.scenario_id, "exception": f"{type(exc).__name__}: {exc}"})
            continue
        checks = score(s, category, account, derived, ds, policy)
        print(row_line(s, checks), flush=True)
        rows.append({
            "scenario_id": s.scenario_id, "split": s.split, "trap": s.trap, "wording": s.wording,
            "note_driven": s.note_driven, "category_pred": category, "category_expected": s.expected_category,
            "checks": checks,
            "findings": account["findings"].model_dump() if account["findings"] else None,
            "derived": derived_json(derived),
            "policy": policy.model_dump(mode="json"),
            "tool_errors": account["tool_errors"],
        })

    scored = [r for r in rows if "checks" in r]
    failures = len(rows) - len(scored) + sum(r["findings"] is None for r in scored)  # exceptions + failed finalizes
    totals = {k: sum(bool(r["checks"][k]) for r in scored) for k in CHECKS}
    per_check = "  ".join(f"{k}={v}/{len(scored)}" for k, v in totals.items())
    print(f"\nscored {len(scored)}/{len(rows)}, {failures} failed; {per_check}")
    disputed_acc = totals["disputed_ok"] / len(scored) if scored else 0.0
    policy_recall = sum(bool(r["checks"]["policy_cited_ok"]) for r in scored) / len(scored) if scored else 0.0

    out_path = Path("runs") / f"eval_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out_path.parent.mkdir(exist_ok=True)
    report = {"category_source": "classify", "split": split, "rows": rows, "totals": totals}
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(
        f"disputed-event accuracy {disputed_acc:.2f} (min {min_disputed_acc}); "
        f"policy citation recall {policy_recall:.2f} (min {min_policy_recall}); wrote {out_path}"
    )
    return 1 if failures or disputed_acc < min_disputed_acc or policy_recall < min_policy_recall else 0
