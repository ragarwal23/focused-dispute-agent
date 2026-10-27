# Live validation of the v0.6 graph — 2026-10-26

## What was validated

This records execution of the [live-validation plan](plans/live-validation.md): offline behavior, both forced
re-request paths, three dev-eval repeats, and the twelve most recent normal LangSmith trace shapes. The first
matrix failed the stability gate on S-07, which led to the F1/F1b Policy Agent prompt fix. After Rachit approved
eval runs 7–9 on 2026-10-26, a second three-run matrix on the F1b wording passed the stability gate, and the eight
ad-hoc complaints all matched their source scenarios on route and disputed event.

## Flake matrix

Reports: `eval_20261026T224024Z.json`, `eval_20261026T224405Z.json`, and
`eval_20261026T224743Z.json`. Every checked cell was 3/3 except: S-11 `route_ok` was 0/3 (the pre-approved
known miss), and S-07 `draft_policy_ok` was 1/3. S-07 therefore fails the `n - 1` stability rule; it is not
listed as known. All three reports had 0 send-backs, 0 re-retrieves, and 0 exceptions.

Each repeat had disputed-event accuracy 1.00, policy-citation recall 1.00, and route accuracy 0.92 (11/12),
above the 0.75 thresholds.

**After F1b** (`eval_20261027T022319Z.json`, `T022756Z.json`, `T023159Z.json`): `stability --known S-11:route_ok`
exited 0. Every cell was 3/3 except S-11 `route_ok` 0/3 (known), and four 2/3 cells, all within the `n - 1` rule:

| Scenario | Cells at 2/3 | Run | Cause |
|---|---|---|---|
| S-07 | `confidence_ok`, `draft_events_ok`, `draft_policy_ok` | T022319Z | Synthesize returned no draft (`draft = null`); the gate rejected with "no draft from synthesize" and routed `manual_escalation`, which is the correct route. Same failure as the S-07 open finding. |
| S-02 | `category_ok`, `disputed_ok`, `draft_policy_ok` | T023159Z | Classified `missing_payment` instead of `late_fee_autopay`, so the Policy Agent read the wrong category's sections. Route was still `adjustment_review` (correct). |

All three had 0 send-backs, 0 re-retrieves, 0 exceptions. Thresholds: disputed-event accuracy 1.00/1.00/0.92,
policy-citation recall 1.00/1.00/0.92, route accuracy 0.92 on all three. Confusion matrix identical on all three:
the only off-diagonal cell is S-11 (`need_more_evidence` → `explain_events`).

## Re-request paths

`DISPUTE_FAULT=finalize_once` on A-1002 produced `account attempts: 2  policy attempts: 1`; its trace was
`classify > account_agent > policy_agent > derive_states > sync > account_agent_retry > derive_states_retry >
sync > synthesize > gate > create_review_case` and matched.

`DISPUTE_FAULT=policy_date_mismatch` on A-1002 produced `account attempts: 1  policy attempts: 2`; its trace
included `policy_agent_retry > sync` after the first sync and matched. The forced root costs were $0.0896 and
$0.0773 respectively.

## Trace shapes

`trace --last 12` passed: all twelve normal roots had the expected eight-child shape, in each case with one
Account Agent and one Policy Agent call. The observed traces were 13.3–24.0 seconds and $0.0507–$0.0689 each.

## Ad-hoc complaints

Run 2026-10-26 with `bash plans/live-validation-adhoc.sh` (exit 0), then `trace --last 8` (exit 0, all eight the
normal eight-child shape). **8/8 matched on route and disputed event**, against the bet of 6/8. No gate rejections,
no re-requests, no tool errors.

| # | Source | Account | Expected route / disputed | Actual route / disputed | Match |
|---|---|---|---|---|---|
| L1 | S-01 | A-1001 | explain_events / E-0117 | explain_events / E-0117 | yes |
| L2 | S-03 | A-1003 | manual_escalation / E-0122 | manual_escalation / E-0122 | yes |
| L3 | S-05 | A-1005 | adjustment_review / E-0131 | adjustment_review / E-0131 | yes |
| L4 | S-07 | A-1007 | manual_escalation / none | manual_escalation / none | yes |
| L5 | S-09 | A-1010 | explain_events / E-0107 | explain_events / E-0107 | yes |
| L6 | S-10 | A-1011 | adjustment_review / E-0142 | adjustment_review / E-0142 | yes |
| L7 | S-14 | A-1015 | explain_events / E-0132 | explain_events / E-0132 | yes |
| L8 | S-15 | A-1016 | need_more_evidence / none | need_more_evidence / none | yes |

Traces were 16.9–24.5 s and $0.0561–$0.0742 each ($0.53 total). L2 and L6 are note-driven: the route depends on rep
notes the gate cannot check, so they test the Account Agent and synthesize alone. In L4, the case note names
`E-0135` as a possible misapplied payment on another account and tells the rep not to share its details. The draft's
`event_ids` do not cite it.

## Per-component notes

Account Agent and Policy Agent completed all 36 dev runs without exceptions or bounded re-requests. Sync, the
gate, and `create_review_case` reached the end of every run. S-07 occasionally omitted the expected policy
source from the synthesis draft even though its policy retrieval and citation checks were 3/3; this localizes the
stability issue to synthesis output rather than retrieval.

## Fixes made

Authorized 2026-10-26 (option A; dev-eval run budget raised from 5 to 6).

**F1: the Policy Agent reads one rule per condition, not one per section.** Cause: `LF.v3.s6` states two rules
(not found → ask for proof; applied to another customer → refer to a supervisor). With one slot per section, the
model kept the first, `ask_customer`, so synthesize never cited `LF.v3.s6` on a `manual_escalation` route.
Changed: `POLICY_SYSTEM` line 2 and `check_citations` (now keeps one rule per (section, outcome)), plus
`test_citation_check`. After-runs `eval_20261027T014852Z`, `T015305Z`, `T015656Z`: S-07 `draft_policy_ok` 3/3,
but S-14 `draft_policy_ok` fell to 0/3 and policy recall to 0.92. The new wording made the model skip the purely
explanatory refund sections `RF.v1.s1` and `RF.v1.s2`.

**F1b: rewording so explanatory sections still get a rule.** `POLICY_SYSTEM` line 2 now reads "a rule for every
relevant section, including one that only explains how things work; one rule per condition when a section states
two." One live `ask` each: S-14 rules again include `RF.v1.s1/s2/s3` and the draft cites all three. S-07 reads
`LF.v3.s6` as two rules. **This wording has not been through a three-run matrix** (see Open findings).

## Cost

The twelve sampled normal roots cost $0.7174. At that observed rate, the three 12-scenario evals cost about $2.15;
the two forced-path runs cost $0.1669, for approximately $2.32 before the fix. The three post-F1 evals (≈ $2.15)
and five single `ask` checks (≈ $0.25) bring the total to ≈ $4.70. The three post-F1b evals (≈ $2.15, at the same
rate) and the eight ad-hoc complaints ($0.53, measured) bring the total to ≈ $7.38 of the $8 cap.

## Open findings

- **Closed: F1b validated.** Three dev evals plus `stability` passed (above), approved by Rachit 2026-10-26.
- **Closed: ad-hoc complaints L1–L8 run**, 8/8.
- **Synthesize can fail outright on S-07.** In one live `ask` after F1b, the draft cited `E-0135` (the other
  customer's payment, which the key forbids). The narrowed `Draft` schema rejected it, and the run ended with no
  draft: gate → `manual_escalation`, case created, no case note. The route was correct and the trap held, but the rep
  would get an empty note. It recurred in 1 of the 3 post-F1b evals (`T022319Z`), so it is now seen in 2 of 4
  S-07 runs after F1b. Synthesize is single-shot by design. Recorded, not fixed. The natural fix is one bounded
  synthesize retry on a schema rejection, which needs its own plan.
- **S-02 classification flake.** One run in three classified it `missing_payment`; the route held. Recorded.
- **S-11 route miss** (known): the model routes `explain_events` where the key wants `need_more_evidence`.

---

# Gate removed (v0.8) — 2026-10-27

## What changed and what was run

The runtime gate node was removed ([plan](../tasks/remove-gate.md), architecture D10): the recommended route is now the
draft's, or `manual_escalation` when synthesize returns no draft, and our P1–P7 checks run in the eval as
`policy_consistent` and a golden-label audit. Before removal, the 43 eval reports in `runs/` held 279 scored rows; the
gate rejected 8 drafts (S-03 ×4, S-07 ×2, S-10, S-14) and in every one the draft had already chosen
`manual_escalation` or there was no draft, so it never changed a route. A backtest of `policy_consistent` over the 196
of those rows that carry findings found 0 inconsistent routes.

Offline: `pytest -q` 256 passed; `python -m dispute_agent.evaluation.policy_checks` 16/16 `ok` (no golden route is ruled
out by P1–P7); `eval --producer golden_echo --split all` grade PASS 16/16 with `policy_consistent` 16/16; the web app
builds (`npm run build`).

Live: three dev passes, `eval_20261027T182359Z.json`, `T182756Z.json`, `T183153Z.json`, each exit 0, then
`stability --known S-11:route_ok`, the same rule and known set as the F1b matrix above.

## Results

`stability` exited 0. **Every cell was 3/3 except S-11 `route_ok` 0/3 (the known miss)**, including the new
`policy_consistent` 12/12 on all three. The F1b matrix had four cells at 2/3; this one has none.

| Pass | Route accuracy | Disputed-event accuracy | Policy-citation recall | `policy_consistent` | Grade PASS | Send-backs / re-retrieves / exceptions |
|---|---|---|---|---|---|---|
| T182359Z | 11/12 | 12/12 | 12/12 | 12/12 | 11/12 | 0 / 0 / 0 |
| T182756Z | 11/12 | 12/12 | 12/12 | 12/12 | 11/12 | 0 / 0 / 0 |
| T183153Z | 11/12 | 12/12 | 12/12 | 12/12 | 11/12 | 0 / 0 / 0 |
| F1b passes, for comparison | 11/12 each | 12, 12, 11 of 12 | 12, 12, 11 of 12 | (not measured) | — | 0 / 0 / 0 |

The confusion matrix was identical on all three and identical to F1b's: the only off-diagonal cell is S-11
(`need_more_evidence` → `explain_events`).

Release workbook [`release_2026-10-27_no-gate.xlsx`](../dispute_agent/evaluation/reports/release_2026-10-27_no-gate.xlsx)
renders pass T183153Z with a `Compare` sheet against the phase 2 release capture (`eval_20261027T174341Z.json`) over
the 12 dev scenarios: route 10 → 11, evidence, policy and confidence 11 → 12, grade 10 → 11. All four changes are S-07,
which wrote no draft in the release capture and wrote a correct one in all three passes here. **That is run-to-run
variance in synthesize, not an effect of this change**: synthesize runs before the point where the gate was, so
removing the gate cannot change what it writes. S-07's no-draft failure is now seen in 3 of 8 runs since F1b. It
remains the open finding below; the one-retry fix still needs its own plan.

Cost: 36 live scenario runs at the previously observed $0.05–0.07 each, about $2.15. No judge run.

## Notes

- **The ID guarantee is now complete.** With nothing shown, a schema cannot say "no IDs", so `draft_schema` falls
  back to plain strings; the gate used to catch an invented ID there. `run_synthesize` now rejects any ID that was not
  shown, so an off-account or unshown citation is impossible in every case, and the run falls back to
  `manual_escalation`.
- **The `route` axis keeps a PARTIAL.** A route that is right only because there was no draft scores PARTIAL, as
  "right only because the gate rejected" did before, so an empty answer still cannot score a clean PASS on the
  `manual_escalation` scenarios (`empty_stub` grades FAIL on all 16).
- **Trace shapes, and a LangSmith quota.** The graph now has one node fewer (`... > sync > synthesize > review`).
  Only the first five scenarios of pass T182359Z reached LangSmith: from S-07 on, every upload was refused with
  `429 ... tenant exceeded usage limits: Monthly unique traces usage limit exceeded` (32, 56 and 57 refusals in the three
  logs). The eval itself is unaffected (scores are computed locally and written to `runs/`), but those traces are lost.
  `trace --last 12` matched all five new roots it could see; the other seven it picked up were gate-era roots and
  reported the expected mismatch. After the limit was raised, a fourth dev pass (`runs/eval_20261027T202122Z.json`,
  exit 0) uploaded with no refusals and scored the same as the three above: route 11/12 (S-11), every other check
  12/12, grade PASS 11/12. `trace --last 12` then passed: all twelve roots had the seven-child shape
  `classify > account_agent > policy_agent > derive_states > sync > synthesize > review`, at 13.0–21.7 s and
  $0.0518–$0.0719 each ($0.76 total).
