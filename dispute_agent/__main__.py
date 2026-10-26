"""Command line: look at the data, run one complaint, or run the eval.

    python -m dispute_agent show [A-1002]
    python -m dispute_agent ask A-1002 "cust got hit w a late fee ... conf # ends in 8812"
    python -m dispute_agent eval [--split dev] [--scenario S-01] [--min-disputed-acc 0.75] [--min-policy-recall 0.75] [--min-route-acc 0.75]
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, ToolMessage

from .account_agent import build_account_agent
from .data import load_dataset
from .derive import derived_json
from .evaluate import run_eval
from .pipeline import investigate
from .policy_agent import load_policies


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
            detail = (
                f"{s.scenario_id:<9} {s.split:<8} {s.expected_category:<26} {s.trap}" if s
                else "(no scenario; look-alike account)"
            )
            print(f"{a.account_id:<8} {a.customer_name:<20} {detail}")
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
    print(
        f"{'id':<7} {'occurred':<17} {'posted':<17} {'type':<18} {'amount':>9}  "
        f"{'channel':<14}{'reference':<15}{'links to':<9}{'code'}"
    )
    for e in events:
        amount = f"${e.amount:,.2f}" if e.amount is not None else ""
        print(
            f"{e.event_id:<7} {_ts(e.occurred_at):<17} {_ts(e.posted_at):<17} {e.type:<18} {amount:>9}  "
            f"{e.channel or '':<14}{e.reference or '':<15}{e.related_event_id or '':<9}{e.return_code or ''}"
        )
        if e.memo:
            print(f"{'':<8}{'NOTE' if e.type == 'rep_note' else 'memo'}: {e.memo}")

    cases = ds.cases_for_account(a.account_id)
    print(f"\nreview cases ({len(cases)})")
    for c in cases:
        print(
            f"{c.case_id:<7} {c.status:<7} {c.category:<26} "
            f"disputed {c.disputed_event_id or '-':<7} created {_ts(c.created_at)}"
        )
        if c.summary:
            print(f"{'':<8}{c.summary}")
    return 0


def _print_tool_calls(account: dict[str, Any], quiet: bool) -> None:
    results = {m.tool_call_id: m for m in account["messages"] if isinstance(m, ToolMessage)}
    for m in account["messages"]:
        if not isinstance(m, AIMessage):
            continue
        for call in m.tool_calls:
            args = ", ".join(f"{k}={v}" for k, v in call["args"].items())
            print(f"-> {call['name']}({args})")
            r = results.get(call["id"])
            if r is not None and not quiet:
                text = r.content.replace("\n", " ")
                print(f"   {text[:300]}{'...' if len(text) > 300 else ''}")


def cmd_ask(args: argparse.Namespace) -> int:
    """Run every step on one complaint and print what each step produced."""
    ds = load_dataset(args.data)
    if args.account_id not in ds.accounts:
        print(f"unknown account {args.account_id}; known: {', '.join(sorted(ds.accounts))}")
        return 2
    sections = load_policies(Path(args.data) / "policies")
    inv = investigate(
        ds, sections, build_account_agent(ds), args.account_id, args.message,
        tags=["slice=investigate", "cli"], label=args.account_id,
    )
    account, policy, g = inv.account, inv.policy, inv.gate

    print(f"category: {inv.category}\n")
    _print_tool_calls(account, args.quiet)
    print("\nfindings:")
    print(json.dumps(account["findings"].model_dump() if account["findings"] else None, indent=2))
    print(
        f"tool_calls_made={account['tool_calls_made']} budget_hit={account['budget_hit']} "
        f"tool_errors={account['tool_errors']}"
    )
    print("\nderived (code):")
    print(json.dumps(derived_json(inv.derived), indent=2))
    print("\npolicy retrieved (ranked):")
    for r in policy.retrieved:
        s, via = r.section, f" via {r.via}" if r.via else ""
        print(f"{r.rank} {s.section_id} {r.score:.3f} {s.effective_from}→{s.effective_to or '-'}{via}")
    print("\npolicy rules:")
    for rule in policy.rules:
        print(f"{rule.section_id} {rule.outcome} ambiguous={rule.policy_ambiguous} {rule.why}")
    print(f"policy errors={policy.errors} dropped={policy.dropped_section_ids}")

    issues = "; ".join(inv.sync.issues) or "no issues"
    print(f"\nsync (code): {issues}  lean={inv.sync.lean}  account attempts: {inv.account_attempts}")
    print("\ndraft (LLM):")
    if inv.synthesis.draft:
        print(json.dumps(inv.synthesis.draft.model_dump(), indent=2))
    else:
        print(f"none: {inv.synthesis.errors}")

    print("\ngate (code):")
    print(f"route={g.route} gate_rejected={g.gate_rejected} reasons={g.gate_reasons} existing_case_id={g.existing_case_id}")
    print("checks:         " + "  ".join(f"{k}={v}" for k, v in g.check_results.items()))
    cited = []
    for c in g.section_checks:
        line = f"{c.section_id} {c.check}" + (f"={c.result}" if c.result else "")
        cited.append(line + (f" rules out {c.rules_out}" if c.rules_out else ""))
    print("cited sections: " + (" · ".join(cited) or "none"))
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    return run_eval(
        args.data, args.split, args.scenario, args.min_disputed_acc, args.min_policy_recall, args.min_route_acc
    )


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
    ev.add_argument("--min-route-acc", type=float, default=0.0)
    args = p.parse_args(argv)
    return {"show": cmd_show, "ask": cmd_ask, "eval": cmd_eval}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
