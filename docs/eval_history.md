# Evaluation history

How each eval run led to a change, and what the next run showed. The README's results are the last run below, on the current code.

## Release runs

The three release runs on the same 16 scenarios:

| Run | Passed | Failed | Experiment |
|---|---|---|---|
| 2026-10-27 | 13/16 | S-04, S-11, S-12 | [link](https://smith.langchain.com/public/49bdd689-1b76-49fd-8d2d-2cc19bd1f452/d/compare?selectedSessions=fe12ec99-02aa-4357-92ea-16a793c49737) |
| 2026-10-28, with redaction | 12/16 | S-04, S-07 (no draft), S-11, S-12 | [link](https://smith.langchain.com/public/49bdd689-1b76-49fd-8d2d-2cc19bd1f452/d/compare?selectedSessions=0cb67e9d-df79-48fe-a951-19f69515bf77) |
| 2026-10-28, with redaction and redraft | 12/16 | S-04, S-06, S-11, S-12 | [link](https://smith.langchain.com/public/49bdd689-1b76-49fd-8d2d-2cc19bd1f452/d/compare?selectedSessions=f5712c13-dbd9-4a12-8177-46ee8c13a3b7) |

The four held-out complaints (S-04, S-08, S-12, S-16) were never tuned on. S-04 and S-12 missed the same policy section in every capture, so those are real misses, not noise.

## What each eval showed, and what changed

Every change to the agent was made because an eval run showed a failure, and each was checked by re-running the same scenarios. The run files are in `runs/` (git-ignored; the timestamps below name them) and the full log is in [docs/history/](history/).

| Date | What the eval showed | What changed, and why | Before → after |
|---|---|---|---|
| 10-25 | New checks for the two traps showed the Account Agent treating S-07's payment on another account as this customer's, and S-06's look-alike reference as a match | One prompt change: an other-account match means "maybe this customer's payment on the wrong account"; "claimed but not found" means transactions only | Disputed event 11/12 → 12/12, fact gaps 10/12 → 12/12 (`account_agent_20261025T223538Z` → `T223813Z`); S-06 stayed wrong until the null-disputed rule was narrowed on 10-26 (`eval_20261026T163446Z`: 12/12 on every check) |
| 10-26 | First full-graph run: S-07's draft cited `E-0135`, the other customer's event; a code gate caught it after the fact | The draft's output schema is built per call and accepts only the IDs the model was shown, so an off-account citation is impossible rather than caught | Draft policy 10/12 → 12/12 (`eval_20261026T194129Z` → `T201450Z`); S-07 no longer needed a rescue |
| 10-26 | S-11 routes `explain_events` instead of `need_more_evidence` | **Tried and reverted:** one sentence in the synthesize prompt ("proof only the customer or bank can supply") fixed S-11 in both runs but flipped S-10 and S-14 to wrong routes in both | Route 11/12 → 10/12 (`T201957Z`, `T202450Z`), so the sentence came out and S-11 stayed a known miss |
| 10-27 | A three-run stability matrix put S-07's policy citation at 1/3: `LF.v3.s6` states two rules and the Policy Agent kept only the first | The Policy Agent reads one rule per condition, not one per section. The first wording made it skip the explanatory refund sections (S-14 fell to 0/3), so it was reworded to keep those | S-07 1/3 → 3/3 and S-14 3/3 → 0/3 → 3/3 across three matrices (`T224024Z..`, `T014852Z..`, `T022319Z..`); details in [validation.md](history/validation.md) |
| 10-27 | Across 279 scored rows the runtime gate rejected 8 drafts and never changed a route: every rejected draft had already chosen `manual_escalation` | The gate node was removed; its P1–P7 checks moved into the eval as `policy_consistent`, where they score every route without an answer key | Three passes after: route 11/12 (S-11), every other check 12/12, no flaky cells (`T182359Z`, `T182756Z`, `T183153Z`) |
| 10-27 | All 16 as a LangSmith experiment, with the judge. Every code check passed S-07, but the Opus judge scored its note 1/3 on leaks: the prose named the other customer's account, event and payment reference | Code now redacts another customer's account ID, an unshown event ID or another account's reference from the case note and open questions, and records what it removed ([synthesize.py](../dispute_agent/sync/synthesize.py), `redact_prose`) | Offline: the exact leaked sentence ([test_synthesize.py](../tests/test_synthesize.py)). Live: S-07 wrote no draft in the 10-28 release run, so it was run alone three more times (`eval_20261028T155541Z`, `T155558Z`, `T155618Z`): a draft each time, the redaction fired in two, and the judge scored the redacted note 3/3 on leaks (was 1/3), 2/3 on ready-to-file because the `[redacted ...]` placeholders read like template text (`judge_20261028T155758Z`) |
| 10-28 | S-07 wrote no draft in 4 of 12 runs: it tried to cite `E-0135`, the schema rejected the draft, and the rep got the right queue with an empty note (including the morning release run) | One bounded redraft: a rejected draft gets one more call with the rejection and the allowed IDs appended, then falls back as before (`MAX_ATTEMPTS = 2` in [synthesize.py](../dispute_agent/sync/synthesize.py)). No new node, no loop | S-07 passed in the next release run with its note redacted and judged 3/3 on leaks (`eval_20261028T182643Z`, `judge_20261028T183429Z`) and in four solo runs. **The redraft itself never fired in those five runs:** every first draft was accepted, so only the offline tests ([test_synthesize.py](../tests/test_synthesize.py)) exercise it. The S-07 pass is not yet evidence that the redraft works |

## Still open

- **S-11:** wrong queue in every one of about 20 runs since 10-26. The one prompt fix tried broke two other scenarios. Next: a code check that a disputed return with no bank letter cannot route `explain_events`.
- **S-04 and S-12 (held out):** one policy section missing from the draft in all four held-out captures. Nothing is tuned on them, by design.
- **S-06 look-alike flake:** in the latest release run the Account Agent reported another customer's "77" reference as a possible match, so the case escalated. This was fixed by a prompt change on 10-25 and passed every run since until this one. No change made.
- **The S-07 redraft is unproven live:** it has not fired in five runs since it was built. The redacted note also costs a point on ready-to-file, because the `[redacted ...]` placeholders read like template text.
- **S-02 classification flake:** 1 run in 3 classified it `missing_payment`; the queue still held.
