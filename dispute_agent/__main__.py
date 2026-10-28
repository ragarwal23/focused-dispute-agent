"""Command line: look at data, run complaints and evals, or validate their stability and traces.

    python -m dispute_agent show [A-1002]
    python -m dispute_agent ask A-1002 "cust got hit w a late fee ... conf # ends in 8812" [--approve]
    python -m dispute_agent resume A-1002-20261027T101500Z (--approve | --route R --reason "..." | --answer "...")
    python -m dispute_agent eval [--split dev|heldout|all] [--scenario S-01] [--min-disputed-acc 0.75] [--min-policy-recall 0.75] [--min-route-acc 0.75] [--experiment]
    python -m dispute_agent judge runs/eval_<ts>.json [--experiment]
    python -m dispute_agent report runs/eval_<ts>.json --out dispute_agent/evaluation/reports/release_<date>.xlsx
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, get_args

from langchain_core.messages import AIMessage, ToolMessage
from langsmith import Client, evaluate_existing

from dispute_agent.account_agent.agent import build_account_agent
from dispute_agent.account_agent.derive import derived_json
from dispute_agent.data import Route, load_dataset
from dispute_agent.evaluation.evaluate import run_eval
from dispute_agent.evaluation.experiment import NO_KEY, append_judge, experiment_line
from dispute_agent.evaluation.golden import read_golden
from dispute_agent.evaluation.report import render
from dispute_agent.evaluation.validate import stability, trace_check
from dispute_agent.orchestrator.graph import build_graph, sqlite_checkpointer
from dispute_agent.orchestrator.run import Investigation, new_thread_id, resume, start, thread_config, thread_status
from dispute_agent.policy_agent.retrieve import load_policies
from dispute_agent.review.review import APPROVE, Pause, ReviewDecision

CHECKPOINTS = Path("runs") / "checkpoints.sqlite"  # paused runs; delete it to forget them
TAGS = ["slice=investigate", "cli"]


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


def _print_sections(v: dict[str, Any], quiet: bool) -> None:
    """Print what each step produced, read from the graph's state (there is no Investigation yet at a pause)."""
    account, policy = v["account"], v["policy"]
    print(f"category: {v['category']}\n")
    _print_tool_calls(account, quiet)
    print("\nfindings:")
    print(json.dumps(account["findings"].model_dump() if account["findings"] else None, indent=2))
    print(
        f"tool_calls_made={account['tool_calls_made']} budget_hit={account['budget_hit']} "
        f"tool_errors={account['tool_errors']}"
    )
    print("\nderived (code):")
    print(json.dumps(derived_json(v["derived"]), indent=2))
    print(f"\npolicy read on {policy.policy_date:%Y-%m-%d} (complaint date); retrieved (ranked):")
    for r in policy.retrieved:
        s, via = r.section, f" via {r.via}" if r.via else ""
        print(f"{r.rank} {s.section_id} {r.score:.3f} {s.effective_from}→{s.effective_to or '-'}{via}")
    print("\npolicy rules:")
    for rule in policy.rules:
        print(f"{rule.section_id} {rule.outcome} ambiguous={rule.policy_ambiguous} {rule.why}")
    print(f"policy errors={policy.errors} dropped={policy.dropped_section_ids}")

    issues = "; ".join(v["sync"].issues) or "no issues"
    print(f"\nsync (code): {issues}  lean={v['sync'].lean}")
    print("\ndraft (LLM):")
    if v["synthesis"].draft:
        print(json.dumps(v["synthesis"].draft.model_dump(), indent=2))
    else:
        print(f"none: {v['synthesis'].errors}")
    print(f"recommended route: {v['synthesis'].route}")


def _print_outcome(out: Investigation | Pause) -> None:
    """The pause block (what the rep can do next), or the filed case and who decided it."""
    if isinstance(out, Investigation):
        r = out.review
        who = "approved" if r.action == "approve" else f"override to {r.route}: {r.reason}"
        print(f"\ncreate_review_case (code): {out.case.case_id} created={out.case.created} route={out.case.route}  decided by: rep ({who})")
        return
    r, t = out.request, out.thread_id
    used = f"  (answers used: {r.followups} of {r.followups + int(r.can_answer)})" if r.followups else ""
    draft = "" if r.draft else " (no draft)"
    existing = f"  open case {r.existing_case_id}: approving files nothing new" if r.existing_case_id else ""
    questions = f"  open questions: {' | '.join(r.open_questions)}" if r.open_questions else ""
    print(f"\npaused for review: thread {t}{used}\n  route {r.route}{draft}{existing}{questions}")
    print(f"  resume with:  resume {t} --approve\n{'':16}resume {t} --route <route> --reason \"...\"")
    if r.can_answer:
        print(f"{'':16}resume {t} --answer \"...\"")


def cmd_ask(args: argparse.Namespace) -> int:
    """Run every step on one complaint, print what each step produced, and pause for the rep (or approve with --approve)."""
    ds = load_dataset(args.data)
    if args.account_id not in ds.accounts:
        print(f"unknown account {args.account_id}; known: {', '.join(sorted(ds.accounts))}")
        return 2
    sections = load_policies(Path(args.data) / "policies")
    graph = build_graph(ds, sections, build_account_agent(ds), checkpointer=sqlite_checkpointer(CHECKPOINTS))
    thread_id = new_thread_id(args.account_id)
    print(f"thread: {thread_id}  (if this run crashes, `resume {thread_id} --approve` continues it)\n")
    out = start(graph, args.account_id, args.message, thread_id, TAGS, args.account_id)
    _print_sections(graph.get_state(thread_config(thread_id)).values, args.quiet)
    if args.approve:
        while isinstance(out, Pause):
            out = resume(graph, thread_id, APPROVE, TAGS, args.account_id)
    _print_outcome(out)
    return 0


def cmd_resume(args: argparse.Namespace) -> int:
    """Continue a paused run with the rep's decision. Checks the thread before any model is built."""
    ds = load_dataset(args.data)
    sections = load_policies(Path(args.data) / "policies")
    config = thread_config(args.thread_id)
    probe = build_graph(ds, sections, None, checkpointer=sqlite_checkpointer(CHECKPOINTS))
    status = thread_status(probe, args.thread_id)
    if status == "unknown":
        print(f"no paused run for thread {args.thread_id}")
        return 2
    snap = probe.get_state(config)
    if status == "finished":
        print(f"thread {args.thread_id} already finished: case {snap.values['case'].case_id}")
        return 2
    pending = [i.value for task in snap.tasks for i in task.interrupts]
    if args.answer and pending and not pending[0]["can_answer"]:
        print("this pause takes --approve or --route only")
        return 2
    if not pending:
        print(f"no pause was waiting: the run stopped at {', '.join(snap.next)}; continuing it from there. This decision "
              "is not used: the run pauses at review again, or, past review, files under the decision made there")

    if args.approve:
        decision = APPROVE
    elif args.route:
        decision = ReviewDecision(action="override", route=args.route, reason=args.reason)
    else:
        decision = ReviewDecision(action="answer", answer=args.answer)
    graph = build_graph(ds, sections, build_account_agent(ds), checkpointer=sqlite_checkpointer(CHECKPOINTS))
    label, before = snap.values["thread_label"], snap.values["followups"]
    out = resume(graph, args.thread_id, decision, TAGS, label)
    after = graph.get_state(config).values
    if after["followups"] > before or not pending:  # the agents ran again: show what they found this time
        _print_sections(after, args.quiet)
    _print_outcome(out)
    return 0


def cmd_eval(args: argparse.Namespace) -> int:
    return run_eval(
        args.data, args.split, args.scenario, args.min_disputed_acc, args.min_policy_recall, args.min_route_acc,
        args.producer, args.experiment,
    )


def cmd_report(args: argparse.Namespace) -> int:
    return render(args.json_path, args.out, judge=args.judge, before=args.before, labels=args.labels)


def cmd_judge(args: argparse.Namespace) -> int:
    from dispute_agent.evaluation.judge import judge_report
    from dispute_agent.evaluation.report import load_eval

    try:
        data = load_eval(Path(args.json_path))
    except (ValueError, json.JSONDecodeError, OSError) as exc:
        print(f"judge: {exc}")
        return 2
    if args.experiment and not data.get("experiment_name"):  # both refusals come before the paid judge calls
        print(f"judge: {args.json_path} was not captured with --experiment")
        return 2
    if args.experiment and not os.getenv("LANGSMITH_API_KEY"):
        print(f"judge: {NO_KEY}")
        return 2
    sidecar = judge_report(Path(args.json_path), data, load_dataset(args.data), read_golden())
    if args.experiment:
        client, evaluate_existing = langsmith_client()
        name, url = experiment_line(append_judge(client, evaluate_existing, data["experiment_name"], json.loads(sidecar.read_text())))
        print(f"experiment {name}: {url or 'no URL returned'}")
    return 0


def langsmith_client() -> tuple[Any, Any]:
    return Client(), evaluate_existing


def cmd_calibrate(args: argparse.Namespace) -> int:
    from dispute_agent.evaluation.judge import calibrate_check
    from dispute_agent.evaluation.report import build_labels, load_eval

    if args.build:
        try:
            data = load_eval(Path(args.build))
        except (ValueError, json.JSONDecodeError, OSError) as exc:
            print(f"calibrate: {exc}")
            return 2
        n = build_labels(data, read_golden(), Path(args.out))
        print(f"wrote {args.out}: {n} dev notes to score")
        return 0
    if not args.labels:
        print("calibrate: --check needs --labels")
        return 2
    return calibrate_check(Path(args.check), Path(args.labels))


def cmd_langsmith_sync(args: argparse.Namespace) -> int:
    from dispute_agent.evaluation.experiment import sync_langsmith

    return sync_langsmith(Path(args.json_path), Path(args.judge) if args.judge else None)


def cmd_stability(args: argparse.Namespace) -> int:
    reports = [json.loads(Path(path).read_text()) for path in args.reports]
    known = {tuple(value.split(":", 1)) for value in args.known}
    lines, ok = stability(reports, known)
    print("\n".join(lines))
    return 0 if ok else 1


def cmd_trace(args: argparse.Namespace) -> int:
    from langsmith import Client

    lines, ok = trace_check(Client(), os.getenv("LANGSMITH_PROJECT", "default"), args.last)
    print("\n".join(lines))
    return 0 if ok else 2 if lines[0].startswith("no qualifying run") else 1


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
    ask.add_argument("--approve", action="store_true", help="approve at the review pause and file the case now")
    res = sub.add_parser("resume", help="continue a paused run with the rep's decision")
    res.add_argument("thread_id")
    res.add_argument("--quiet", action="store_true", help="hide tool results, show only the calls")
    decide = res.add_mutually_exclusive_group(required=True)
    decide.add_argument("--approve", action="store_true", help="file the case under the recommended route")
    decide.add_argument("--route", choices=get_args(Route), help="file it under this route instead (needs --reason)")
    decide.add_argument("--answer", help="answer the open questions; the Account Agent looks again once")
    res.add_argument("--reason", help="why the route was overridden")
    ev = sub.add_parser("eval", help="run and score scenarios")
    ev.add_argument("--split", default="dev", choices=["dev", "heldout", "all"])
    ev.add_argument("--scenario", help="one scenario ID, e.g. S-01")
    ev.add_argument("--min-disputed-acc", type=float, default=0.0)
    ev.add_argument("--min-policy-recall", type=float, default=0.0)
    ev.add_argument("--min-route-acc", type=float, default=0.0)
    ev.add_argument("--producer", default="agent", choices=["agent", "golden_echo", "empty_stub"],
                    help="what answers: the live agent, the golden key itself, or an empty answer (both no LLM)")
    ev.add_argument("--experiment", action="store_true",
                    help="run as a LangSmith experiment on the golden dataset (each row carries its trace)")
    rep = sub.add_parser("report", help="render one eval JSON next to golden.xlsx as a release workbook")
    rep.add_argument("json_path", metavar="eval.json")
    rep.add_argument("--out", required=True, metavar="release.xlsx", help="a .md path writes the Verdict Matrix as a page with trace links")
    rep.add_argument("--judge", metavar="judge.json", help="the judge sidecar for this JSON: judge panels and judge columns")
    rep.add_argument("--before", metavar="eval.json", help="an older eval JSON: adds the Compare sheet")
    rep.add_argument("--labels", metavar="labels.xlsx", help="hand labels (needs --judge): adds the Calibration sheet")
    jd = sub.add_parser("judge", help="score every case note in an eval JSON with the Opus judge (LLM)")
    jd.add_argument("json_path", metavar="eval.json")
    jd.add_argument("--experiment", action="store_true", help="also append the scores to the experiment named in the eval JSON")
    cal = sub.add_parser("calibrate", help="build the hand-label workbook, or check the judge against it")
    cal_mode = cal.add_mutually_exclusive_group(required=True)
    cal_mode.add_argument("--build", metavar="eval.json", help="write the labels workbook for this run's dev notes")
    cal_mode.add_argument("--check", metavar="judge.json", help="agreement of this judge sidecar with --labels")
    cal.add_argument("--labels", metavar="labels.xlsx", default="dispute_agent/evaluation/labels.xlsx")
    cal.add_argument("--out", metavar="labels.xlsx", default="dispute_agent/evaluation/labels.xlsx")
    ls = sub.add_parser("langsmith-sync", help="upload golden.xlsx as a LangSmith dataset if missing, and replay an eval JSON as an experiment")
    ls.add_argument("json_path", metavar="eval.json")
    ls.add_argument("--judge", metavar="judge.json", help="also upload the judge scores as feedback")
    stable = sub.add_parser("stability", help="check repeated eval JSON reports")
    stable.add_argument("reports", nargs="+")
    stable.add_argument("--known", action="append", default=[], metavar="SCENARIO:CHECK")
    trace = sub.add_parser("trace", help="verify recent LangSmith graph trace shapes")
    trace.add_argument("--last", type=int, default=1, metavar="N")
    args = p.parse_args(argv)
    if args.cmd == "resume" and bool(args.route) != bool(args.reason):
        p.error("--route and --reason go together")
    commands = {
        "show": cmd_show, "ask": cmd_ask, "resume": cmd_resume, "eval": cmd_eval, "report": cmd_report, "judge": cmd_judge,
        "calibrate": cmd_calibrate, "langsmith-sync": cmd_langsmith_sync, "stability": cmd_stability, "trace": cmd_trace,
    }
    return commands[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
