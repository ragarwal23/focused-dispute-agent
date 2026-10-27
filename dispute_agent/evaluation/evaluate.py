"""The eval: run every scenario in a split, score it against the answer key in scenarios.xlsx, print a table.

Table columns, left to right:
    cat    category right            disp   disputed event right      req   required events cited
    forb   no forbidden events cited  drop   off-account IDs dropped   pay   payment state right
    ref    refund state right         key    states right given the key's category (separates classify errors)
    gap / amb / oam                   fact_gap, fact_ambiguous, other_account_match flags right
    calls / budget / err              tool calls used, budget hit, tool errors
    pret / pcite / pforb / pamb       policy: required sections retrieved, cited, no forbidden cited, # ambiguous
    route                             final route (what the rep was asked to approve) matches the key
    cons                              policy-consistent: P1-P7 do not rule out the final route (needs no answer key)
    conf                              confidence consistent: never high on a cautious route; no draft fails
    devt / dpol                       the draft cites the required events and none forbidden / the required sections
    traj                              trajectory: the Account Agent's tool calls stayed within budget
    act                               action: a case was created, or the key's existing open case was returned instead
After the totals: route accuracy and a confusion matrix of expected route (rows) by final route (columns).
"""

from __future__ import annotations

import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langsmith import Client
from langsmith import evaluate as langsmith_evaluate

from dispute_agent.account_agent.agent import build_account_agent
from dispute_agent.account_agent.derive import derive_states, derived_json
from dispute_agent.config import MODEL_ID, TOOL_BUDGET
from dispute_agent.data import Dataset, Route, Scenario, load_dataset
from dispute_agent.evaluation.experiment import NO_KEY, experiment_line, git_sha, replay_output, run_experiment
from dispute_agent.evaluation.golden import GOLDEN_PATH, PRODUCERS
from dispute_agent.evaluation.metrics import grade, row_verdicts
from dispute_agent.evaluation.policy_checks import ruled_out
from dispute_agent.orchestrator.graph import build_graph
from dispute_agent.orchestrator.run import Investigation, investigate
from dispute_agent.policy_agent.retrieve import PolicySection, load_policies

HEADER = (
    f"{'scenario':<9}{'trap':<15}{'wording':<8}"
    f"| {'cat':<4}{'disp':<5}{'req':<4}{'forb':<5}{'drop':<5}{'pay':<4}{'ref':<4}{'key':<4}"
    f"| {'gap':<4}{'amb':<4}{'oam':<4}| {'calls':<6}{'budget':<7}{'err':<4}"
    f"| {'pret':<5}{'pcite':<6}{'pforb':<6}{'pamb':<5}| {'route':<6}{'cons':<5}{'conf':<5}{'devt':<5}{'dpol':<5}"
    f"| {'traj':<5}{'act':<4}"
)
CHECKS = (  # the pass/fail checks summed in the totals line
    "category_ok", "disputed_ok", "required_found", "forbidden_clear", "payment_ok", "refund_ok", "states_ok_keycat",
    "gap_ok", "ambiguous_ok", "oam_ok", "route_ok", "confidence_ok", "draft_events_ok", "draft_policy_ok",
    "trajectory_ok", "action_ok", "policy_consistent",
)
FORBIDDEN_POLICY_PREFIXES = ("LF.v2.", "EX.v1.")  # superseded or out-of-scope policy; never a valid citation
CAUTIOUS_ROUTES = ("need_more_evidence", "manual_escalation")  # confidence must not be high on these
ROUTES: tuple[Route, ...] = ("adjustment_review", "explain_events", "need_more_evidence", "manual_escalation")


def _mark(v: Any) -> str:
    return "ok" if v is True else "X" if v is False else str(v)


def expected_flags(s: Scenario) -> tuple[bool, bool, bool]:
    """(fact_gap, fact_ambiguous, other_account_match) as the scenario's trap implies them."""
    return s.trap in ("missing_record", "wrong_account"), s.trap == "ambiguous", s.trap == "wrong_account"


def score(s: Scenario, inv: Investigation, ds: Dataset) -> dict[str, Any]:
    account, derived, policy, draft, route = inv.account, inv.derived, inv.policy, inv.synthesis.draft, inv.synthesis.route
    f = account["findings"]
    ok = f is not None  # a failed finalize fails every model-side check, so a null key can't score it as right
    raw_cited = set()  # every event ID the model cited, before derive_states cleans them
    if f:
        raw_cited = set(f.relevant_event_ids) | set(f.candidate_event_ids) | ({f.disputed_event_id} - {None})
    keycat = derive_states(f, s.expected_category, s.account_id, ds)
    gap, amb, oam = expected_flags(s)
    expected_policy, cited = set(s.expected_policy_ids), [r.section_id for r in policy.rules]
    required, forbidden = set(s.expected_required_event_ids), set(s.expected_forbidden_event_ids)
    return {
        "category_ok": inv.category == s.expected_category,
        "disputed_ok": ok and f.disputed_event_id == s.expected_disputed_event_id,
        "required_found": ok and required <= raw_cited,
        "forbidden_clear": ok and not (forbidden & raw_cited),
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
        "policy_retrieved_ok": expected_policy <= {r.section.section_id for r in policy.retrieved},
        "policy_cited_ok": expected_policy <= set(cited),
        "policy_forbidden_clear": not [i for i in cited if i.startswith(FORBIDDEN_POLICY_PREFIXES)],
        "policy_ambiguous_count": sum(r.policy_ambiguous for r in policy.rules),
        "policy_dropped": len(policy.dropped_section_ids),
        # the draft: the final route is what the rep sees (manual_escalation without a draft), so that is what is scored
        "draft_route": draft.review_route if draft else None,
        "route_final": route,
        "route_ok": route == s.expected_route,
        "policy_consistent": route not in ruled_out(ds, s.account_id, derived),
        "confidence_ok": draft is not None and not (draft.confidence == "high" and draft.review_route in CAUTIOUS_ROUTES),
        "draft_events_ok": draft is not None and required <= set(draft.event_ids) and not (forbidden & set(draft.event_ids)),
        "draft_policy_ok": draft is not None and expected_policy <= set(draft.policy_source_ids),
        "synth_errors": inv.synthesis.errors,
        # the graph's shape and its one write
        "trajectory_ok": account["tool_calls_made"] <= TOOL_BUDGET,
        "action_ok": inv.case.created is (s.expected_existing_case_id is None)
        and (s.expected_existing_case_id is None or inv.case.case_id == s.expected_existing_case_id),
    }


def row_line(s: Scenario, c: dict[str, Any]) -> str:
    return (
        f"{s.scenario_id:<9}{s.trap:<15}{s.wording:<8}| {_mark(c['category_ok']):<4}{_mark(c['disputed_ok']):<5}"
        f"{_mark(c['required_found']):<4}{_mark(c['forbidden_clear']):<5}{c['dropped']:<5}{_mark(c['payment_ok']):<4}"
        f"{_mark(c['refund_ok']):<4}{_mark(c['states_ok_keycat']):<4}"
        f"| {_mark(c['gap_ok']):<4}{_mark(c['ambiguous_ok']):<4}{_mark(c['oam_ok']):<4}"
        f"| {c['tool_calls']:<6}{'HIT' if c['budget_hit'] else 'no':<7}{c['errors']:<4}| "
        f"{_mark(c['policy_retrieved_ok']):<5}{_mark(c['policy_cited_ok']):<6}"
        f"{_mark(c['policy_forbidden_clear']):<6}{c['policy_ambiguous_count']:<5}"
        f"| {_mark(c['route_ok']):<6}{_mark(c['policy_consistent']):<5}{_mark(c['confidence_ok']):<5}"
        f"{_mark(c['draft_events_ok']):<5}{_mark(c['draft_policy_ok']):<5}"
        f"| {_mark(c['trajectory_ok']):<5}{_mark(c['action_ok']):<4}"
    )


def confusion_lines(scored: list[dict[str, Any]]) -> list[str]:
    """Expected route (rows) by final route (columns), in a fixed order so runs compare by eye."""
    counts = Counter((r["route_expected"], r["checks"]["route_final"]) for r in scored)
    corner, short = "expected / final", [route[:8] for route in ROUTES]  # adjustme explain_ need_mor manual_e
    lines = [f"{corner:<20}" + "".join(f"{h:<10}" for h in short)]
    for expected in ROUTES:
        lines.append(f"{expected:<20}" + "".join(f"{counts[expected, final]:<10}" for final in ROUTES))
    return lines


def select_scenarios(ds: Dataset, split: str, scenario_id: str | None) -> list[Scenario]:
    """One scenario by ID, else the split; `all` is dev and held-out together (the one held-out pass)."""
    if scenario_id:
        return [s for s in ds.scenarios.values() if s.scenario_id == scenario_id]
    return [s for s in ds.scenarios.values() if split == "all" or s.split == split]


def run_one(s: Scenario, graph: Any, ds: Dataset, sections: dict[str, PolicySection], producer: str) -> dict[str, Any]:
    """Run and score one scenario as an eval JSON row; an exception is printed and recorded as the row, never raised."""
    tags = [
        f"scenario={s.scenario_id}", f"category={s.expected_category}", f"trap={s.trap}", f"wording={s.wording}",
        f"note_driven={str(s.note_driven).lower()}", "slice=investigate",
    ]
    try:
        if producer == "agent":
            inv = investigate(ds, sections, None, s.account_id, s.rep_message, tags, s.scenario_id, graph=graph)
        else:
            inv = PRODUCERS[producer](ds, sections, s)
    except Exception as exc:
        print(f"{s.scenario_id:<9}EXCEPTION {type(exc).__name__}: {exc}")
        return {"scenario_id": s.scenario_id, "exception": f"{type(exc).__name__}: {exc}"}
    checks = score(s, inv, ds)
    print(row_line(s, checks), flush=True)
    findings, draft = inv.account["findings"], inv.synthesis.draft
    return {
        "scenario_id": s.scenario_id, "split": s.split, "trap": s.trap, "wording": s.wording,
        "note_driven": s.note_driven, "category_pred": inv.category, "category_expected": s.expected_category,
        "route_expected": s.expected_route, "case": inv.case.model_dump(),
        "review": inv.review.model_dump(), "followups": inv.followups,
        "checks": checks,
        "findings": findings.model_dump() if findings else None,
        "derived": derived_json(inv.derived),
        "policy": inv.policy.model_dump(mode="json"),
        "sync": inv.sync.model_dump(mode="json"),
        "draft": draft.model_dump() if draft else None,
        "tool_errors": inv.account["tool_errors"],
    }


def run_eval(
    data_dir: str,
    split: str,
    scenario_id: str | None,
    min_disputed_acc: float,
    min_policy_recall: float,
    min_route_acc: float = 0.0,
    producer: str = "agent",
    experiment: bool = False,
) -> int:
    """Score the split (or one scenario), write runs/eval_<timestamp>.json, return the exit code.

    `producer` is the live agent by default; `golden_echo` and `empty_stub` (golden.py) score the key and an empty
    answer through the same path with no LLM, to prove the scorer itself before a model is graded. `experiment` runs
    the same rows as a LangSmith experiment on the golden dataset, so each row's trace is attached to its scores.
    """
    if experiment and not os.getenv("LANGSMITH_API_KEY"):
        print(f"eval: {NO_KEY}")
        return 2
    stamp = f"{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}"  # names both the JSON and the experiment
    out_path = Path("runs") / f"eval_{stamp}.json"
    ds = load_dataset(data_dir)
    sections = load_policies(Path(data_dir) / "policies")
    graph = build_graph(ds, sections, build_account_agent(ds)) if producer == "agent" else None
    scenarios = select_scenarios(ds, split, scenario_id)
    if os.getenv("LANGSMITH_TRACING", "").lower() == "true" and not experiment:  # experiment runs go to their own project
        print(f"tracing to LangSmith project: {os.getenv('LANGSMITH_PROJECT', 'default')}")
    print(HEADER)

    linked: dict[str, Any] = {}
    if experiment:
        chosen, by_id = {s.scenario_id: s for s in scenarios}, {}

        def target(inputs: dict[str, Any]) -> dict[str, Any]:
            row = by_id[inputs["scenario_id"]] = run_one(chosen[inputs["scenario_id"]], graph, ds, sections, producer)
            return replay_output(row)

        try:
            results = run_experiment(
                Client(), langsmith_evaluate, target, by_id, set(chosen),
                prefix=f"{'release' if producer == 'agent' else producer}-{stamp}",
                description="Live capture: the graph on every golden scenario, scored by the six code axes; "
                            "judge feedback is added by `judge --experiment`",
                metadata={"model_id": MODEL_ID, "producer": producer, "split": split, "git_sha": git_sha(),
                          "eval_path": str(out_path)},
            )
        except RuntimeError as exc:  # the dataset on LangSmith no longer matches golden.xlsx
            print(f"eval: {exc}")
            return 1
        rows = [by_id[s.scenario_id] for s in scenarios if s.scenario_id in by_id]
        name, url = experiment_line(results)
        linked = {"experiment_name": name, "experiment_url": url}
    else:
        rows = [run_one(s, graph, ds, sections, producer) for s in scenarios]

    scored = [r for r in rows if "checks" in r]
    failures = len(rows) - len(scored) + sum(r["findings"] is None for r in scored)  # exceptions + failed finalizes
    totals = {k: sum(bool(r["checks"][k]) for r in scored) for k in CHECKS}
    per_check = "  ".join(f"{k}={v}/{len(scored)}" for k, v in totals.items())
    print(f"\nscored {len(scored)}/{len(rows)}, {failures} failed; {per_check}")
    n = len(scored) or 1
    disputed_acc = totals["disputed_ok"] / n
    policy_recall = sum(bool(r["checks"]["policy_cited_ok"]) for r in scored) / n
    route_acc = totals["route_ok"] / n

    out_path.parent.mkdir(exist_ok=True)
    report = {
        "category_source": "classify", "split": split, "model_id": MODEL_ID, "golden_path": GOLDEN_PATH,
        "producer": producer, "rows": rows, "totals": totals, **linked,
    }
    out_path.write_text(json.dumps(report, indent=2, default=str))
    print(
        f"disputed-event accuracy {disputed_acc:.2f} (min {min_disputed_acc}); "
        f"policy citation recall {policy_recall:.2f} (min {min_policy_recall}); "
        f"route accuracy {route_acc:.2f} (min {min_route_acc}); wrote {out_path}"
    )
    print("\n".join(confusion_lines(scored)))
    grades = [grade(row_verdicts(r)) for r in rows]
    print(f"grade PASS {grades.count('PASS')}/{len(rows)}")
    if linked:
        print(f"experiment {linked['experiment_name']}: {linked['experiment_url'] or 'no URL returned'}")
    below = disputed_acc < min_disputed_acc or policy_recall < min_policy_recall or route_acc < min_route_acc
    return 1 if failures or below else 0
