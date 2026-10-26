"""CLI for the investigation agents.

    python -m dispute_agent show [A-1002]
    python -m dispute_agent ask A-1002 "cust got hit w a late fee ... conf # ends in 8812"
    python -m dispute_agent eval [--split dev] [--scenario S-01] [--min-disputed-acc 0.75]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from .account_agent import build_account_agent, classify, derive_states, initial_state
from .data import Dataset, Scenario, load_dataset
from .policy_agent import load_policies, run_policy_agent


def run_one(ds: Dataset, sections, agent: Any, account_id: str, rep_message: str, tags: list[str], label: str):
    category = classify(rep_message, config={"run_name": f"classify {label}", "tags": tags})
    out = agent.invoke(initial_state(account_id, category, rep_message), config={"run_name": f"account_agent {label}", "tags": tags})
    derived = derive_states(out["findings"], category, account_id, ds)
    policy = run_policy_agent(sections, category, rep_message, out["findings"], derived,
                              config={"run_name": f"policy_agent {label}", "tags": tags})
    return category, out, derived, policy


def print_tool_calls(out: dict[str, Any], quiet: bool) -> None:
    results = {m.tool_call_id: m for m in out["messages"] if isinstance(m, ToolMessage)}
    for m in out["messages"]:
        if not isinstance(m, AIMessage):
            continue
        for call in m.tool_calls:
            args = ", ".join(f"{k}={v}" for k, v in call["args"].items())
            print(f"-> {call['name']}({args})")
            r = results.get(call["id"])
            if r is not None and not quiet:
                text = r.content.replace("\n", " ")
                print(f"   {text[:300]}{'...' if len(text) > 300 else ''}")


def derived_json(derived) -> dict[str, Any]:
    d = derived.model_dump(mode="json")
    d["evidence_events"] = [e["event_id"] for e in d["evidence_events"]]
    return d


def _ts(dt: datetime | None) -> str:
    return dt.strftime("%Y-%m-%d %H:%M") if dt else "-"


def cmd_show(args: argparse.Namespace) -> int:
    """Print an account straight from the Excel files. No LLM."""
    ds = load_dataset(args.data)
    if not args.account_id:
        by_account = {s.account_id: s for s in ds.scenarios.values()}
        print(f"{'account':<8} {'customer':<20} {'scenario':<9} {'split':<8} {'category':<26} trap")
        for a in sorted(ds.accounts.values(), key=lambda a: a.account_id):
            s = by_account.get(a.account_id)
            print(f"{a.account_id:<8} {a.customer_name:<20} " + (
                f"{s.scenario_id:<9} {s.split:<8} {s.expected_category:<26} {s.trap}" if s else "(no scenario; look-alike account)"))
        return 0
    a = ds.accounts.get(args.account_id)
    if a is None:
        print(f"unknown account {args.account_id}; run `show` with no ID to list them")
        return 2
    print(f"{a.account_id}  {a.customer_name}  {a.vehicle or ''}")
    print(f"monthly ${a.monthly_payment:,.2f} · due day {a.due_day} · bank ****{a.bank_last4 or '----'}")
    if a.memo:
        print(f"memo: {a.memo}")
    events = ds.events_for_account(a.account_id)
    print(f"\nevents ({len(events)})")
    print(f"{'id':<7} {'occurred':<17} {'posted':<17} {'type':<18} {'amount':>9}  {'channel':<14}{'reference':<15}{'links to':<9}{'code'}")
    for e in events:
        amount = f"${e.amount:,.2f}" if e.amount is not None else ""
        print(f"{e.event_id:<7} {_ts(e.occurred_at):<17} {_ts(e.posted_at):<17} {e.type:<18} {amount:>9}  "
              f"{e.channel or '':<14}{e.reference or '':<15}{e.related_event_id or '':<9}{e.return_code or ''}")
        if e.memo:
            print(f"{'':<8}{'NOTE' if e.type == 'rep_note' else 'memo'}: {e.memo}")
    cases = ds.cases_for_account(a.account_id)
    print(f"\nreview cases ({len(cases)})")
    for c in cases:
        print(f"{c.case_id:<7} {c.status:<7} {c.category:<26} disputed {c.disputed_event_id or '-':<7} created {_ts(c.created_at)}")
        if c.summary:
            print(f"{'':<8}{c.summary}")
    return 0


def cmd_ask(args: argparse.Namespace) -> int:
    ds = load_dataset(args.data)
    if args.account_id not in ds.accounts:
        print(f"unknown account {args.account_id}; known: {', '.join(sorted(ds.accounts))}")
        return 2
    category, out, derived, policy = run_one(ds, load_policies(Path(args.data) / "policies"), build_account_agent(ds), args.account_id, args.message, ["slice=investigate", "cli"], args.account_id)
    print(f"category: {category}\n")
    print_tool_calls(out, args.quiet)
    print("\nfindings:")
    print(json.dumps(out["findings"].model_dump() if out["findings"] else None, indent=2))
    print("\nderived (code):")
    print(json.dumps(derived_json(derived), indent=2))
    print("\npolicy retrieved (ranked):")
    for r in policy.retrieved:
        print(f"{r.rank} {r.section.section_id} {r.score:.3f} {r.section.effective_from}→{r.section.effective_to or '-'}" + (f" via {r.via}" if r.via else ""))
    print("\npolicy rules:")
    for rule in policy.rules: print(f"{rule.section_id} {rule.outcome} ambiguous={rule.policy_ambiguous} {rule.why}")
    print(f"\npolicy errors={policy.errors} dropped={policy.dropped_section_ids}")
    print(f"\ntool_calls_made={out['tool_calls_made']} budget_hit={out['budget_hit']} tool_errors={out['tool_errors']}")
    return 0


HEADER = (f"{'scenario':<9}{'trap':<15}{'wording':<8}| {'cat':<4}{'disp':<5}{'req':<4}{'forb':<5}{'drop':<5}{'pay':<4}{'ref':<4}{'key':<4}"
          f"| {'gap':<4}{'amb':<4}{'oam':<4}| {'calls':<6}{'budget':<7}{'err':<4}| {'pret':<5}{'pcite':<6}{'pforb':<6}{'pamb':<5}")
CHECKS = ("category_ok", "disputed_ok", "required_found", "forbidden_clear", "payment_ok", "refund_ok", "states_ok_keycat",
          "gap_ok", "ambiguous_ok", "oam_ok")


def _mark(v: Any) -> str:
    return "ok" if v is True else "X" if v is False else str(v)


def expected_flags(s: Scenario) -> tuple[bool, bool, bool]:
    """(fact_gap, fact_ambiguous, other_account_match) as the trap implies them (docs/architecture.md, trap table)."""
    return s.trap in ("missing_record", "wrong_account"), s.trap == "ambiguous", s.trap == "wrong_account"


def policy_forbidden(ids) -> list[str]:
    return [i for i in ids if i.startswith(("LF.v2.", "EX.v1."))]


def score(s: Scenario, category: str, out: dict[str, Any], derived, ds: Dataset, policy=None) -> dict[str, Any]:
    f = out["findings"]
    ok = f is not None  # a failed finalize fails every model-side check, so a null key can't score it as right
    raw_cited = (set(f.relevant_event_ids) | set(f.candidate_event_ids) | ({f.disputed_event_id} - {None})) if f else set()
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
        "states_ok_keycat": ok and keycat.payment_state == s.expected_payment_state and keycat.refund_state == s.expected_refund_state,
        "gap_ok": ok and derived.fact_gap == gap,
        "ambiguous_ok": ok and derived.fact_ambiguous == amb,
        "oam_ok": ok and derived.other_account_match == oam,
        "tool_calls": out["tool_calls_made"],
        "budget_hit": out["budget_hit"],
        "errors": len(out["tool_errors"]),
    }
    if policy is not None:
        retrieved = {r.section.section_id for r in policy.retrieved}; cited = [r.section_id for r in policy.rules]
        checks.update(policy_retrieved_ok=set(s.expected_policy_ids) <= retrieved, policy_cited_ok=set(s.expected_policy_ids) <= set(cited),
                      policy_forbidden_clear=not policy_forbidden(cited), policy_ambiguous_count=sum(r.policy_ambiguous for r in policy.rules),
                      policy_dropped=len(policy.dropped_section_ids))
    return checks


def row_line(s: Scenario, c: dict[str, Any]) -> str:
    return (
        f"{s.scenario_id:<9}{s.trap:<15}{s.wording:<8}| {_mark(c['category_ok']):<4}{_mark(c['disputed_ok']):<5}"
        f"{_mark(c['required_found']):<4}{_mark(c['forbidden_clear']):<5}{c['dropped']:<5}{_mark(c['payment_ok']):<4}"
        f"{_mark(c['refund_ok']):<4}{_mark(c['states_ok_keycat']):<4}| {_mark(c['gap_ok']):<4}{_mark(c['ambiguous_ok']):<4}"
        f"{_mark(c['oam_ok']):<4}| {c['tool_calls']:<6}{'HIT' if c['budget_hit'] else 'no':<7}{c['errors']:<4}| "
        f"{_mark(c.get('policy_retrieved_ok')):<5}{_mark(c.get('policy_cited_ok')):<6}{_mark(c.get('policy_forbidden_clear')):<6}{c.get('policy_ambiguous_count', '-'):<5}"
    )


def cmd_eval(args: argparse.Namespace) -> int:
    ds = load_dataset(args.data)
    agent = build_account_agent(ds)
    sections = load_policies(Path(args.data) / "policies")
    scenarios = [s for s in ds.scenarios.values() if (s.scenario_id == args.scenario if args.scenario else s.split == args.split)]
    if os.getenv("LANGSMITH_TRACING", "").lower() == "true":
        print(f"tracing to LangSmith project: {os.getenv('LANGSMITH_PROJECT', 'default')}")
    print(HEADER)
    rows: list[dict[str, Any]] = []
    for s in scenarios:
        tags = [f"scenario={s.scenario_id}", f"category={s.expected_category}", f"trap={s.trap}", f"wording={s.wording}",
                f"note_driven={str(s.note_driven).lower()}", "slice=investigate"]
        try:
            category, out, derived, policy = run_one(ds, sections, agent, s.account_id, s.rep_message, tags, s.scenario_id)
        except Exception as exc:
            print(f"{s.scenario_id:<9}EXCEPTION {type(exc).__name__}: {exc}")
            rows.append({"scenario_id": s.scenario_id, "exception": f"{type(exc).__name__}: {exc}"})
            continue
        checks = score(s, category, out, derived, ds, policy)
        print(row_line(s, checks), flush=True)
        rows.append({
            "scenario_id": s.scenario_id, "split": s.split, "trap": s.trap, "wording": s.wording, "note_driven": s.note_driven,
            "category_pred": category, "category_expected": s.expected_category, "checks": checks,
            "findings": out["findings"].model_dump() if out["findings"] else None, "derived": derived_json(derived),
            "policy": policy.model_dump(mode="json"),
            "tool_errors": out["tool_errors"],
        })
    scored = [r for r in rows if "checks" in r]
    failures = len(rows) - len(scored) + sum(r["findings"] is None for r in scored)  # exceptions + failed finalizes
    totals = {k: sum(bool(r["checks"][k]) for r in scored) for k in CHECKS}
    print(f"\nscored {len(scored)}/{len(rows)}, {failures} failed; " + "  ".join(f"{k}={v}/{len(scored)}" for k, v in totals.items()))
    disputed_acc = totals["disputed_ok"] / len(scored) if scored else 0.0
    policy_recall = sum(bool(r["checks"]["policy_cited_ok"]) for r in scored) / len(scored) if scored else 0.0
    out_path = Path("runs") / f"eval_{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    out_path.parent.mkdir(exist_ok=True)
    out_path.write_text(json.dumps({"category_source": "classify", "split": args.split, "rows": rows, "totals": totals}, indent=2, default=str))
    print(f"disputed-event accuracy {disputed_acc:.2f} (min {args.min_disputed_acc}); policy citation recall {policy_recall:.2f} (min {args.min_policy_recall}); wrote {out_path}")
    return 1 if failures or disputed_acc < args.min_disputed_acc or policy_recall < args.min_policy_recall else 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m dispute_agent", description="Payment-dispute investigation CLI")
    p.add_argument("--data", default="data", help="folder with the four .xlsx files")
    sub = p.add_subparsers(dest="cmd", required=True)
    show = sub.add_parser("show", help="print an account's records (no LLM); no ID lists all accounts")
    show.add_argument("account_id", nargs="?")
    ask = sub.add_parser("ask", help="run the agent on one complaint")
    ask.add_argument("account_id")
    ask.add_argument("message")
    ask.add_argument("--quiet", action="store_true", help="hide tool results, show only the calls")
    ev = sub.add_parser("eval", help="run and score scenarios")
    ev.add_argument("--split", default="dev", choices=["dev", "heldout"])
    ev.add_argument("--scenario", help="one scenario ID, e.g. S-01")
    ev.add_argument("--min-disputed-acc", type=float, default=0.0)
    ev.add_argument("--min-policy-recall", type=float, default=0.0)
    args = p.parse_args(argv)
    return {"show": cmd_show, "ask": cmd_ask, "eval": cmd_eval}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
