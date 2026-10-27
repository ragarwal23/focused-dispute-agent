# Live validation of the v0.6 graph — 2026-10-26

## What was validated

This records execution of the [live-validation plan](plans/live-validation.md): offline behavior, both forced
re-request paths, three dev-eval repeats, and the twelve most recent normal LangSmith trace shapes. Validation
paused before the ad-hoc complaints because the stability gate identified an out-of-tolerance structured-draft
failure that requires a re-plan under the plan's prompt-change rule.

## Flake matrix

Reports: `eval_20261026T224024Z.json`, `eval_20261026T224405Z.json`, and
`eval_20261026T224743Z.json`. Every checked cell was 3/3 except: S-11 `route_ok` was 0/3 (the pre-approved
known miss), and S-07 `draft_policy_ok` was 1/3. S-07 therefore fails the `n - 1` stability rule; it is not
listed as known. All three reports had 0 send-backs, 0 re-retrieves, and 0 exceptions.

Each repeat had disputed-event accuracy 1.00, policy-citation recall 1.00, and route accuracy 0.92 (11/12),
above the 0.75 thresholds.

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

Not run. The eight rephrasings in `plans/live-validation-adhoc.sh` are deliberately deferred until the S-07
stability finding is resolved or explicitly accepted; running them after a failed gate would spend model budget
without completing the planned validation claim.

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
and five single `ask` checks (≈ $0.25) bring the total to ≈ $4.70 of the $8 cap. The deferred ad-hoc runs have not
spent budget.

## Open findings

- **F1b is unvalidated at scale.** Next: three dev evals, then `stability --known S-11:route_ok`. This needs
  eval runs 7–9, which are over the raised budget of 6, so it is a stop-and-ask.
- **Ad-hoc complaints L1–L8 not run.** They come after the matrix above.
- **Synthesize can fail outright on S-07.** In one live `ask` after F1b, the draft cited `E-0135` (the other
  customer's payment, which the key forbids). The narrowed `Draft` schema rejected it, and the run ended with no
  draft: gate → `manual_escalation`, case created, no case note. The route was correct and the trap held, but the rep
  would get an empty note. This did not happen in any of the six evals. Synthesize is single-shot by design.
  Recorded, not fixed.
