# Remove the gate: P1–P7 move into the eval

## 1. Status

v0.2, 2026-10-27 (v0.1 plus the amendments marked "during execution": criteria 2 and 9, decisions 7 and 9). New plan; supersedes nothing. **Executed 2026-10-27**, including the diagram redraw Rachit asked for at execution time (`docs/diagrams/architecture_v0.8.png`). It reverses decisions D6 and D7 in `docs/architecture.md` and the gate paragraphs of `docs/project_brief.md` §4.

**Prerequisite: eval phase 2 (`tasks/eval-framework-phase2.md`) is committed.** Phase 2 is being built on top of the gate right now: `metrics.axis_verdicts` reads `gate_rejected`, `golden.investigation()` calls `gate()`, the `Verdict Matrix` has a `Gate` column, and `validate.replay_target` returns `gate_rejected`. Its acceptance criterion 8 forbids touching `dispute_agent/gate` during phase 2. Do not start this plan while `git status --porcelain dispute_agent/ tests/` shows uncommitted phase 2 work; stop and ask.

This is the first of the simplification cuts Rachit agreed to on 2026-10-27, and the only one in this plan. The others (sync's retry loop, the rep's answer loop, a sequential agent order, a synthesize retry) are explicitly out of scope (§3).

## 2. Why & goal

**The bet:** the gate adds a node, a 287-line folder, two output fields and a UI badge, but it has never changed an outcome. Across all 43 eval JSONs in `runs/` (279 scored rows), it rejected 8 drafts: S-03 ×4, S-07 ×2, S-10 and S-14. In every one, the draft had already chosen `manual_escalation`, or there was no draft. The P1–P7 "rules out" path never fired. Its other checks are already enforced earlier:

- `derive_states` keeps only events that exist and sit on this account (`dispute_agent/account_agent/derive.py`, step 1).
- `synthesize` builds its output schema per call, so `event_ids` and `policy_source_ids` accept only the IDs shown. Shown events are the on-account evidence, and shown sections are the in-force rules. An unshown ID raises, the draft is `None`, and the case is escalated (`tests/test_synthesize.py::test_run_rejects_off_account_event`).
- The ambiguity check is synthesize's route rule 1 ("a policy rule is marked ambiguous" → `manual_escalation`).

The P1–P7 checks are still valuable, as a measurement, not as a runtime veto:

- **Golden-label audit.** On all 16 scenarios, the golden route is never ruled out by P1–P7. This was verified on 2026-10-27 with the answer-key findings.
- **A metric that needs no golden answer.** `policy_consistent` is true when the final route is not ruled out by P1–P7 on the agent's own derived facts. A backtest over the 196 past rows with findings found 0 inconsistent. It can score live production cases, where no golden answer exists.

**Concrete use case:** a reviewer opens `dispute_agent/orchestrator/graph.py` and reads `classify → agents → sync → synthesize → review → create_review_case`, with no gate. They then run `python -m dispute_agent.evaluation.policy_checks` and see the 16 golden routes audited against P1–P7. In the eval table, `cons` is a column next to `route`.

**This plan covers:** deleting `dispute_agent/gate/`, the runtime replacements (2 lines in synthesize, the duplicate lookup in `create_review_case`), `evaluation/policy_checks.py`, the `policy_consistent` check, phase 2's gate-dependent code, the CLI, the web app, the tests, the docs, and one three-pass dev eval to show nothing regressed.

## 3. Definition of Done

### Acceptance criteria

1. `pytest -q` exits 0 (no LLM, no network).
2. `test ! -e dispute_agent/gate && test ! -e tests/test_gate.py && ! grep -rnE --include=*.py --include=*.ts --include=*.tsx "dispute_agent\.gate|GateResult|SectionCheck|gate_rejected|gate_reasons|gate_node" dispute_agent app/server.py app/narrate.py app/src` exits 0. (Amended during execution: tests keep deliberate "no gate key" assertions and one old-format fixture, so `tests/` is not grepped.)
3. `python -m dispute_agent.evaluation.policy_checks` exits 0 and prints 16 lines, each ending in `ok`.
4. `python -m dispute_agent.evaluation.golden --check` exits 0 (the golden workbook is untouched).
5. `(cd app && npm run build)` exits 0 (this runs `tsc -b`, so the TypeScript types match the new payloads).
6. Three LLM passes: `python -m dispute_agent eval --split dev`, run 3 times, each exits 0 and writes `runs/eval_<ts>.json`. Then `python -m dispute_agent stability <A> <B> <C> --known S-11:route_ok` exits 0. That is the same `--known` set the F1b matrix passed with (`docs/validation.md`), so the comparison is like for like.
7. `python -m dispute_agent eval --producer golden_echo --split all` exits 0. Phase 2's scorer calibration still passes every axis with `policy_consistent` added.
8. `grep -niE "\bgate\b|gate_" README.md dispute_agent/evaluation/README.md data/policies/README.md dispute_agent/__init__.py | grep -viE "no.gate|gate was removed|where the gate was|Gate removed"` prints nothing. (Amended during execution: the new release is named `no-gate`, and its write-up says where the gate was.)
9. `grep -niE "\bgate\b|gate_" docs/architecture.md docs/project_brief.md | grep -vE "^[^:]+:[0-9]+:(\*v0\.|> \*\*v0\.|\| \*\*D(6|7|10)\*\*)" | grep -v "Until v0.8"` prints nothing. Gate mentions survive only in version-history lines, the D6/D7/D10 decision rows and the one "Until v0.8" paragraph that explains the removal (amended during execution).
10. `git status --porcelain data/*.xlsx docs/plans/ dispute_agent/account_agent dispute_agent/policy_agent` prints nothing.

### Budgets

| Budget | Max |
|---|---|
| New files | 3 (`evaluation/policy_checks.py`, `tests/test_policy_checks.py`, one release workbook) |
| Deleted files | 4 (`gate/__init__.py`, `gate/gate.py`, `gate/checks.py`, `tests/test_gate.py`) |
| LOC per new file | 200 |
| New DB tables / columns | 0 |
| New state / model fields | 1 (`SynthesisResult.route`) |
| New metric / check names | 1 (`policy_consistent`) |
| Removed output fields | `GraphState.gate`, `Investigation.gate`, `ReviewRequest.gate_rejected`, `ReviewRequest.gate_reasons`, result event `gate`, checks `gate_rejected` and `gate_reasons` |
| New CLI commands / flags | 0 (`policy_checks` gets a module `__main__`, like `golden --check`) |
| New dependencies | 0 |
| LLM spend | 3 dev passes of 12 scenarios (about $2.50). No judge run. A fourth pass needs a stop-and-ask |
| Prompt changes | 0 (`SYNTHESIZE_SYSTEM`, `POLICY_SYSTEM`, `ACCOUNT_SYSTEM`, `CLASSIFY_SYSTEM` untouched) |

### Must-handle edge cases

- **Synthesize fails** (`draft is None`, the S-07 pattern): `synthesis.route == "manual_escalation"`, the pause shows the "could not write a draft" alert, and the case is filed under `manual_escalation`.
- **Override still wins:** `create_review_case` files under the rep's route when they override, else under `synthesis.route`.
- **Duplicates behave exactly as before:** S-11 returns open case C-002 and mints nothing. C-001 is closed, so it does not block S-05. C-003 is in another category, so it does not block S-13. A `None` disputed event never matches.
- **Duplicate lookup after a rep answer:** the follow-up can change the disputed event, so `existing_case_id` is computed at each pause from the current `derived`, never cached.
- **`policy_consistent` with no disputed event:** checks return `unknown`, nothing is ruled out, and the result is `True`. `need_more_evidence` and `manual_escalation` are never ruled out.
- **Old-format eval JSON** (has a `gate` key, no `policy_consistent`) passed to `report`: renders, takes the route from `checks.route_final` (present in both formats), and leaves `policy_consistent` blank.
- **Old checkpoints:** threads paused in `runs/checkpoints.sqlite` before this change hold a `GateResult` and cannot be resumed. This is not supported. The README says to delete the file; the executor must not delete it without asking.

### Out of scope

Not building: any change to sync, its retry nodes or the fault switch; the rep's answer loop; a sequential agent order; a synthesize retry on schema rejection; removing the `trace` command (only its node lists change); the LLM judge run; re-rendering `docs/diagrams/architecture_v0.7.png`; edits to `docs/plans/*` (historical records); rewriting past sections of `docs/validation.md` (append only); `data/*.xlsx`.

Forbidden by name:

- No replacement node or module for the gate (`guard`, `validator`, `postcheck`, `verify`).
- No code rule that forces `manual_escalation` from `policy_ambiguous` or any other flag.
- No `gate.py` shim or re-export.
- No compatibility layer for old checkpoints.
- No config flag to switch P1–P7 on at runtime.
- No section-to-check `REGISTRY` in the eval.
- No new dependency.

Overriding one requires a stop-and-ask with a "why this, not the existing X" line.

## 4. File inventory (closed)

| Path | Action | Why this file owns this change |
|---|---|---|
| `dispute_agent/gate/__init__.py`, `gate.py`, `checks.py` | DELETE | The gate. |
| `dispute_agent/evaluation/policy_checks.py` | CREATE | P1–P7 from `checks.py` minus `_REGISTERED`/`REGISTRY`, plus `ruled_out(ds, account_id, derived) -> set[Route]`, `audit(ds) -> tuple[list[str], bool]` and a module `__main__`. |
| `dispute_agent/sync/synthesize.py` | UPDATE | `SynthesisResult.route`; docstrings stop mentioning the gate. |
| `dispute_agent/review/create_review_case.py` | UPDATE | Gains `find_duplicate` (moved from `gate.py`); takes `route: Route` instead of `verdict: GateResult`. |
| `dispute_agent/review/review.py` | UPDATE | `ReviewRequest` loses `gate_rejected`/`gate_reasons`; `build_request` takes `existing_case_id`; route comes from `synthesis.route`. |
| `dispute_agent/orchestrator/graph.py` | UPDATE | No `gate` node, field, import or SERDE entries; edge `synthesize → review`; `review_node` computes `existing_case_id`; `case_node` passes the final route. |
| `dispute_agent/orchestrator/run.py` | UPDATE | `Investigation` loses `gate`. |
| `dispute_agent/sync/sync.py` | UPDATE | Docstring line 7 only ("the gate and the evals" → "the evals"). |
| `dispute_agent/__init__.py` | UPDATE | Reading-order docstring loses `gate/`; `evaluation/` mentions P1–P7. |
| `dispute_agent/__main__.py` | UPDATE | `_print_sections` drops the gate block and prints `route (final)`; `_print_outcome` drops the gate status; `--approve` help text. |
| `dispute_agent/evaluation/evaluate.py` | UPDATE | `score`: `route_final` from `inv.synthesis.route`; adds `policy_consistent` to the checks and `CHECKS`; drops `gate_rejected`/`gate_reasons`; table column `rej` → `cons`; row JSON drops `"gate"`. |
| `dispute_agent/evaluation/metrics.py` | UPDATE (phase 2 file) | `route` axis: PASS if `route_ok`, else FAIL (no PARTIAL); `guardrails` axis adds `policy_consistent`. |
| `dispute_agent/evaluation/golden.py` | UPDATE (phase 2 code) | `investigation()` drops `gate()`; sets `SynthesisResult.route`; calls the new `create_review_case` signature. |
| `dispute_agent/evaluation/report.py` | UPDATE | `Cases` column `gate_rejected` → `policy_consistent`; route from `checks.route_final`; `Run detail` drops `gate_reasons`; phase 2's `Verdict Matrix` `Gate` column → `Cons`. |
| `dispute_agent/evaluation/validate.py` | UPDATE | `NORMAL` and `RESUME_TAIL` lose `"gate"`; phase 2's `replay_target` drops `gate_rejected`. |
| `dispute_agent/evaluation/__init__.py` | UPDATE | Reading order gains `policy_checks.py`. |
| `dispute_agent/evaluation/README.md` | UPDATE | Cases/Run detail text; §13 route row and guardrails row; a `policy_checks` paragraph. |
| `dispute_agent/evaluation/reports/release_<YYYY-MM-DD>_no-gate.xlsx` | CREATE (generated, committed) | Release capture after the change, with a `Compare` sheet against the phase 2 release. |
| `app/narrate.py` | UPDATE | No `gate` step, `_gate` or gate branch in `_detail`; `result_event` sends `route` instead of `gate`. |
| `app/server.py` | UPDATE | Line 75: the route falls back to `v["synthesis"].route`. |
| `app/src/api.ts` | UPDATE | `ReviewRequest` loses the two gate fields; `ResultEvent.gate` → `route: Route`. |
| `app/src/Thread.tsx` | UPDATE | `PHASES` loses "Checking"; `PHASE_OF` loses `gate`; the struck-through route, the "Policy check" badge and the reasons list go. |
| `tests/test_gate.py` | DELETE | Its check tests move to `test_policy_checks.py`, and its duplicate test to `test_create_review_case.py`. |
| `tests/test_policy_checks.py` | CREATE | See §10. |
| `tests/test_synthesize.py`, `test_create_review_case.py`, `test_review.py`, `test_graph.py`, `test_evaluate.py`, `test_metrics.py`, `test_golden.py`, `test_report.py`, `test_validate.py`, `test_narrate.py`, `test_server.py`, `test_policy_agent.py` | UPDATE | Follow the removed fields; see §10. `test_policy_agent.py` only if a gate import or name appears in it. |
| `README.md` | UPDATE | Layer table (row 4 goes, rows renumber), test table (`test_gate.py` → `test_policy_checks.py`), `resume --approve` comment, the eval sentence (line 98), a one-line note about old checkpoints. |
| `docs/architecture.md` | UPDATE | v0.8; see §9 step 7. |
| `docs/project_brief.md` | UPDATE | v0.10; see §9 step 7. |
| `docs/validation.md` | UPDATE (append) | New section with the three-pass results. |
| `data/policies/README.md` | UPDATE | "Gate registry (D7)" section → "P1–P7 checks (eval only)". |
| `tasks/eval-framework-phase1.md` | UPDATE | §13 route and guardrails rows, so nobody re-derives `gate_rejected`. |

Read first: `dispute_agent/gate/gate.py` and `checks.py` in full; `dispute_agent/orchestrator/graph.py` (`GraphState`, `build_graph`, SERDE); `dispute_agent/review/review.py` and `create_review_case.py` in full; `dispute_agent/sync/synthesize.py` (`SynthesisResult`, `run_synthesize`); `dispute_agent/evaluation/evaluate.py` (`score`, `row_line`, `HEADER`, `CHECKS`, the row dict in `run_eval`); `dispute_agent/evaluation/metrics.py` (`axis_verdicts`); `dispute_agent/evaluation/golden.py` (`investigation`); `tests/test_gate.py` in full (what moves where); `app/narrate.py` (`STEPS`, `_detail`, `result_event`); `app/src/Thread.tsx:1-30, 55-95`. Phase 2 files are read by function name, not line number, because phase 2 is still changing them.

Touching any file not listed is a scope breach: stop and ask.

## 5. Closed decisions (do not re-litigate)

1. **Delete the gate entirely; don't shrink it.** *Why not a 20-line ID check:* the IDs are already enforced at generation time by the per-call schema and by `derive_states`, so a second check would never fire (it never did in 279 rows).
2. **P1–P7 move to `evaluation/policy_checks.py`; they are not deleted.** *Why not delete:* they give the golden-label audit (interview Q8) and a metric that works on unlabeled production cases (Q10). Rachit chose this on 2026-10-27.
3. **No code rule on `policy_ambiguous`.** *Why not keep the one gate rule that did something:* the flag is itself an LLM output (the Policy Agent's), so enforcing it in code makes one LLM's opinion override another's, the same reasoning as D7. It was also wrong 2 of the 3 times it fired (S-10, S-14). Synthesize's route rule 1 already escalates on it, and the eval scores the route.
4. **The final route is a real field, `SynthesisResult.route`** (default `"manual_escalation"`, set to `draft.review_route` on success). *Why not a property:* a field shows in LangSmith node outputs and eval JSON, and a junior reader sees it in the model.
5. **`find_duplicate` lives in `create_review_case.py`; `review_node` calls it to fill the pause.** *Why not in synthesize or sync:* it is case bookkeeping, and the rep needs to see the duplicate before approving.
6. **`ruled_out` runs all five checks, with no section registry.** *Why not keep the per-cited-section logic:* the backtest (0 of 196) and the golden audit (16 of 16) agree with all five checks, and dropping `REGISTRY` removes 24 lines and a README mirror that could drift.
7. **`policy_consistent` joins `CHECKS` and the `guardrails` axis; the `route` axis is PARTIAL when the route is right only because there was no draft.** *Amended during execution:* a plain PASS/FAIL axis would have scored an empty agent PASS on every `manual_escalation` scenario, losing the "escalated correctly vs rescued" distinction D6 existed for. Missing `policy_consistent` (reports before v0.8) is not a fail. Phase 2's 13 feedback keys (`METRIC_NAMES`) do not change.
9. **`run_synthesize` rejects any cited ID that was not shown** *(added during execution).* `draft_schema` falls back to `list[str]` when nothing is shown, and the gate used to backstop that case. The check is three lines in the existing function, not a new node, so the "an unshown ID cannot be emitted" claim holds in every case.
8. **The docs are rewritten in this plan; the diagram is not.** Rachit chose "Code + all docs" on 2026-10-27. `architecture.md` gets a one-line note under the image saying the diagram predates v0.8.

## 6. Architecture / schema

```
before: classify ─┬> account_agent -> derive_states ─┐
                  └> policy_agent ───────────────────┴> sync -> synthesize -> gate -> review -> create_review_case
after:  classify ─┬> account_agent -> derive_states ─┐
                  └> policy_agent ───────────────────┴> sync -> synthesize ---------> review -> create_review_case
```

13 nodes, down from 14. Retries and the answer loop are unchanged: their edges into `sync` and from `review` stay as they are.

**Who owns each output field after the change**

| Output field | Where it comes from | Owner |
|---|---|---|
| `synthesis.route` (recommended route) | `draft.review_route`; `manual_escalation` when there is no draft | model, with a code fallback |
| `ReviewRequest.existing_case_id` | `find_duplicate(ds, account_id, category, derived.disputed_event_id)` over `review_cases` | code |
| `case.route` | the rep's override, else `synthesis.route` | rep / code |
| `checks.route_final` (eval) | `inv.synthesis.route` (after the rep's approval in eval, the same value) | code |
| `checks.policy_consistent` (eval) | `route_final not in ruled_out(ds, account_id, inv.derived)`; P1–P7 over structured event fields | code |
| P1–P7 verdicts (eval) | `run_checks` over event type, amount, dates, chains and autopay status | code |
| `gate_rejected`, `gate_reasons`, `check_results`, `section_checks` | removed | — |

**Counted against §3:** 1 new field (`SynthesisResult.route`), 1 new check (`policy_consistent`), 0 tables, 0 columns. `CHECKS` grows by one; the table header gains `cons` in place of `rej`.

## 7. API surface

- `create_review_case(ds, account_id, category, derived, route) -> CaseResult`. It calls `find_duplicate` itself. `route` is already final (override applied by the caller).
- `find_duplicate(ds, account_id, category, disputed_event_id) -> str | None`. Moved unchanged.
- `build_request(s, label, max_followups, existing_case_id) -> ReviewRequest`
- `ReviewRequest`: `thread_label, route, existing_case_id, draft, open_questions, can_answer, account_attempts, policy_attempts, followups`
- `policy_checks.ruled_out(ds, account_id, derived) -> set[Route]`
- `policy_checks.audit(ds) -> tuple[list[str], bool]`. It builds answer-key findings (same shape as `tests/test_derive.py::oracle_findings`: required events as relevant, the expected disputed event), runs `derive_states` with the expected category, prints `S-xx <expected_route> ok|CONFLICT <checks that are true>`, and returns ok when none conflict.
- Web: the pause payload loses `gate_rejected` and `gate_reasons`; the `result` event replaces `gate: {...}` with `route`. `existing_case_id` stays on the pause payload; the result's `case` already carries `case_id` and `created`.
- Eval row JSON: no `"gate"` key; `checks` gains `policy_consistent` and loses `gate_rejected` and `gate_reasons`.

## 8. Migration plan

No database. The only persisted state affected is `runs/checkpoints.sqlite` (git-ignored). Old paused threads are unsupported (§3 edge cases); the README tells the user to delete the file. Old eval JSONs stay readable by `report` (route from `checks.route_final`). `stability` must not mix old and new JSONs: old rows lack `policy_consistent` and would count as misses.

## 9. Ordered steps

Each step ends with `pytest -q` green, unless it says otherwise.

1. **Eval module first, with the gate still in place.** Create `policy_checks.py` by copying the constants, helpers, five checks, `CHECKS`, `RULES_OUT` and `run_checks` from `gate/checks.py`, without `_REGISTERED`/`REGISTRY`. Add `ruled_out`, `audit` and `__main__` (exit 0 when ok, else 1). Create `tests/test_policy_checks.py`. Run criterion 3.
2. **Runtime.** Add `SynthesisResult.route`. Move `find_duplicate` and change the `create_review_case` signature. Change `build_request`/`ReviewRequest`. In `graph.py`: remove the gate node, field, imports and SERDE entries; add `review_node` → `build_request(..., existing_case_id=find_duplicate(...))`; `case_node` passes `route = review.route if override else synthesis.route`. Remove `gate` from `run.py`. Update the `sync.py` docstring and `dispute_agent/__init__.py`. Delete `dispute_agent/gate/` and `tests/test_gate.py`. Update `test_synthesize`, `test_create_review_case`, `test_review` and `test_graph`.
3. **Eval code.** Update `evaluate.py` (`score`, `CHECKS`, `HEADER`/`row_line`, row JSON, module docstring table), `metrics.py`, `golden.py` `investigation()`, `report.py`, `validate.py` and `evaluation/__init__.py`. Update `test_evaluate`, `test_metrics`, `test_golden`, `test_report` and `test_validate`. Run criteria 2 and 4. Then criterion 7, which costs no LLM calls: `golden_echo` makes none.
4. **CLI.** Update `__main__.py`. Smoke-check by eye: `python -m dispute_agent show A-1002` (no LLM).
5. **Web app.** Update `narrate.py`, `server.py`, `api.ts` and `Thread.tsx`, plus `test_narrate` and `test_server`. Run criterion 5.
6. **Short docs.** Update `README.md`, `evaluation/README.md` (Cases/Run detail text, §13 rows, a `policy_checks` paragraph), `data/policies/README.md` and `tasks/eval-framework-phase1.md` §13. Run criterion 8.
7. **Architecture and brief.**
   - **`architecture.md` v0.8.**
     - "In one paragraph" and "The five layers" become four layers.
     - "The graph" loses the gate.
     - §4 (the gate section) is deleted, and §5 Review becomes §4.
     - "Who owns each output field" and "Graph state" follow §6.
     - "How each trap is defended" names the real defender for each trap: `derive_states`, the schema, the synthesize rules or the eval.
     - D6 row: "Superseded v0.8: no gate." D7 row: "Revised v0.8: P1–P7 are eval checks (`policy_consistent`, golden audit), not a runtime veto."
     - D9 stays, enforced by the synthesize route rules.
     - New D10 row, "No runtime gate": the evidence from §2 and decisions 1 and 3 from §5.
     - A version-history entry, and a note under the diagram.
   - **`project_brief.md` v0.10.**
     - Header line.
     - §3: remove the `gate_rejected` row; `manual_escalation` reads "...policy is unclear, or no draft could be written".
     - §4: the paragraph at line 77, the ASCII diagram's gate block, and the three gate paragraphs (lines 117–121) become one paragraph: "P1–P7 are checked in the eval, not at runtime".
     - §6 evaluators gain `policy_consistent`.
     - §7 row 1: "gate with the P1–P7 checks" → "the P1–P7 checks".
     - §8: "Wrong-account evidence always escalates; a missing draft always escalates."
   - Run criterion 9.
8. **LLM passes (the only spend).** Run criterion 6: three dev passes, then `stability`. If any pass exits non-zero, or `stability` fails on a cell other than `S-11:route_ok`, stop and ask; do not rerun. Render `python -m dispute_agent report <pass C json> --before <phase 2 release JSON> --out dispute_agent/evaluation/reports/release_<UTC date>_no-gate.xlsx`. If phase 2 named the flag differently, use its compare option; if it has none, stop and ask.
9. **Record.** Append to `docs/validation.md` a section "Gate removed (v0.8)". It lists: the three JSON names; the `stability` output; route accuracy per pass, next to the F1b passes; the confusion matrix of pass C; the `policy_consistent` totals; and one sentence comparing against F1b. Then run criteria 1–10 in order.

## 10. Tests

| Test | Criterion |
|---|---|
| `test_policy_checks.py::test_business_days`, `::test_checks_pinned[S-01..S-16]` (moved with `PINNED`), `::test_refund_overdue_scoped_to_disputed`, `::test_returned_payment_not_received` | 1 |
| `::test_ruled_out_wrong_route[...]`: the six `(sid, route, check)` cases from `test_wrong_route_caught`, now asserting `route in ruled_out(...)` | 1 |
| `::test_note_driven_limit[S-03, S-10, S-14]`: the wrong route is not ruled out (the documented limit) | 1 |
| `::test_cautious_routes_never_ruled_out` | 1 |
| `::test_golden_audit`: `audit(ds)` returns 16 lines and `True`; `::test_audit_main_exit_0` | 1, 3 |
| `test_synthesize.py::test_route_follows_draft`, `::test_route_without_draft_is_manual_escalation` (extend `test_run_llm_error`, `test_run_bad_output`, `test_run_rejects_unshown_id`) | 1 |
| `test_create_review_case.py::test_duplicate_rule` (moved), `::test_duplicates_follow_the_answer_key`, `::test_no_draft_still_files_a_case`, `::test_override_route` | 1 |
| `test_review.py`: the pause payload has no gate keys and shows `existing_case_id == "C-002"` on S-11; approve files `synthesis.route` | 1 |
| `test_graph.py::test_graph_clean`: node list without `gate`; `synthesize` is followed by `review` | 1 |
| `test_evaluate.py::test_score_policy_consistent` (replaces `test_score_gate_rescue`): an S-02 draft routed `explain_events` scores `policy_consistent False`, `route_final == "explain_events"`; no draft scores `route_final == "manual_escalation"` | 1 |
| `test_metrics.py::test_route_axis_pass_fail` (replaces `test_gate_rescued_partial`), `::test_guardrails_fail_on_inconsistent` | 1 |
| `test_golden.py`: `golden_echo` all-PASS on 16 with `policy_consistent` | 1, 7 |
| `test_report.py::test_old_format_row`: a row with `gate`, no `policy_consistent`, renders with a blank cell; the `Cases` header has `policy_consistent` | 1 |
| `test_validate.py::test_expected_children`: `NORMAL` without `gate` | 1 |
| `test_narrate.py::test_clean_sequence`: no gate step; `test_server.py`: the result event has `route`, no `gate` | 1 |

## 11. Edge cases / footguns

- **Join barrier:** do not route `synthesize` to anything but `review`, and do not add a node between them. The retry edges into `sync` stay exactly as they are.
- **`review_node` re-runs from the top on every resume.** `find_duplicate` is a pure read, so calling it there is safe. Do not write state before `interrupt()`.
- **SERDE allow-list:** remove `GateResult` and `SectionCheck`. `SynthesisResult` is already listed; the new field needs nothing.
- **Old checkpoints silently default:** an old state's `SynthesisResult` has no `route`, so pydantic would give it `"manual_escalation"`. That is why old threads are unsupported, not "mostly working".
- **`CHECKS` order** drives the totals line and `stability`. Append `policy_consistent` at the end so old column positions don't shift.
- **The `HEADER` and `row_line` widths** must stay aligned; `cons` replaces `rej` in the same 4-character slot.
- **`app/dist/` is git-ignored;** `npm run build` regenerates it. Do not commit it.
- **The phase 2 `Verdict Matrix`, `replay_target` and README** may use slightly different names than phase 2's plan. Grep for `gate` in each phase 2 file and remove every hit, keeping the intent in §4.
- **Interview notes:** Q2 ("why does the gate run its own checks") and Q4 ("is the gate rescuing a bad agent") get new answers, which are §2 of this plan. Q8 and Q10 gain the audit and `policy_consistent`.

## 12. Open questions

All closed.

## Execution-discipline note

Read the whole plan first, and confirm phase 2 is committed (§1). Execute steps 1–9 in order; run each step's checks before moving on. Re-read §3 Budgets and §4 File inventory before each step. If you hit a budget or need a file outside the inventory, stop and ask. Step 8 is the only LLM spend: three passes, no reruns without asking. If one hard issue blocks a step, leave a `TODO(<area>)` with a compiling stand-in and continue. Finish with a fresh-context review of the full diff against §3 and §5, then run criteria 1–10.
