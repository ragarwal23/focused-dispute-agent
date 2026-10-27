# Evaluation framework, phase 2: metrics, judge, calibration, before/after, LangSmith experiment

## 1. Status

v0.2, 2026-10-27 (v0.1 had 11 new files; Rachit cut it to 5 by folding producers into `golden.py`, calibration into `judge.py` + `report.py`, and the LangSmith upload into `validate.py`). Depends on phase 1 (`tasks/eval-framework-phase1.md` v0.2) being merged: `dispute_agent/evaluation/golden.py`, `golden.xlsx`, `report.py`, the `report` command, `eval --split all`, and the committed `reports/release_2026-10-27.xlsx` with its JSON in `runs/` (git-ignored; the agent executing this plan needs that JSON on disk, else re-capture is a stop-and-ask: one pass ≈ $1). Implements §13 of the phase 1 plan verbatim; nothing in §13 is re-derived here. Phase 3 (later): ad-hoc complaints into the golden set, the S-11 fix story.

Reference evals, the only ones to model on: Aurelius `capture → judge → summary` stages and its `Eval` sheet (`/Users/rachit/Documents/aristotle/aristotle-be/app/surfaces/aurelius/evals/`), the Ask Aristotle judge prompt shape (`.../surfaces/ask_aristotle/evals/judge.py`, 0–3 per dimension, JSON out), the demand-forecasting `golden_echo` calibration producer and judge-model independence (`.../surfaces/demand_forecasting/evals/forecasting/README.md`).

## 2. Why & goal

**The bet:** phase 1 put golden and actual side by side. The take-home asks for deterministic checks, an LLM judge where judgment is needed, a judge calibrated against human labels, slices, before/after, and links to evaluation runs. All six land on the phase 1 workbook without touching the agent.

**Concrete use case:** after `judge` and `calibrate`, `python -m dispute_agent report runs/eval_<release>.json --before runs/eval_20261026T224024Z.json --judge runs/judge_<ts>.json --labels dispute_agent/evaluation/labels.xlsx --out dispute_agent/evaluation/reports/release_2026-10-27.xlsx` re-renders the release workbook with a `Verdict Matrix` (6 code axes, 4 judge axes, grade, score), `Slices`, `Compare` (pre-F1 vs release), `Calibration` (judge vs Rachit), and a verdict column plus judge panel on every scenario sheet. `langsmith-sync` uploads the golden as a dataset and the release as an experiment, and the README gets the link.

**This plan covers:** `metrics.py`; the `golden_echo` / `empty_stub` producers in `golden.py`; `judge.py` (judge + agreement math) + `judge` and `calibrate` commands + `labels.xlsx`; the LangSmith dataset/experiment upload in `validate.py` + `langsmith-sync` command; the `report.py` extensions; tests; README updates.

## 3. Definition of Done

### Acceptance criteria

1. `pytest -q` exits 0 (no LLM, no network).
2. `python -m dispute_agent eval --producer golden_echo --split all` exits 0 and its last line reads `grade PASS 16/16`.
3. `python -m dispute_agent eval --producer empty_stub --split dev` exits 1.
4. `python -m dispute_agent judge runs/eval_<release>.json` exits 0 and writes `runs/judge_<ts>.json` with 16 scenario entries (LLM: Opus, ≈ $1).
5. `python -m dispute_agent calibrate --build runs/eval_<release>.json` exits 0 and writes `dispute_agent/evaluation/labels.xlsx` with 12 dev rows and four empty score columns. After Rachit fills it: `python -m dispute_agent calibrate --check runs/judge_<ts>.json --labels dispute_agent/evaluation/labels.xlsx` exits 0 or 1 and prints the agreement table (1 means an axis is under 75 percent within-1: a finding to report, not a plan failure); exit 2 is a failure.
6. The `report` command in §2 exits 0, and `python -c "import openpyxl;wb=openpyxl.load_workbook('dispute_agent/evaluation/reports/release_2026-10-27.xlsx');n=set(wb.sheetnames);assert {'Run','Cases','Verdict Matrix','Slices','Compare','Calibration'}<=n and all(f'S-{i:02d}' in n for i in range(1,17))"` exits 0.
7. `python -m dispute_agent langsmith-sync runs/eval_<release>.json --judge runs/judge_<ts>.json` exits 0 and prints one `https://smith.langchain.com/...` experiment URL; that URL is in `dispute_agent/evaluation/README.md`.
8. `git status --porcelain data/ dispute_agent/orchestrator dispute_agent/account_agent dispute_agent/policy_agent dispute_agent/sync dispute_agent/gate dispute_agent/review app/` prints nothing.

### Budgets

| Budget | Max |
|---|---|
| New files | 5 (`metrics.py`, `judge.py`, `labels.xlsx`, `tests/test_metrics.py`, `tests/test_judge.py`) |
| LOC per new file | 240 |
| LOC per updated file | 360 (`report.py`, `validate.py`, `golden.py`, `evaluate.py`) |
| New DB tables / columns | 0 |
| New metric / event names | 13: axes `route`, `disputed_event`, `evidence`, `policy`, `confidence`, `guardrails`; judge axes `faithfulness`, `reasoning_agreement`, `actionability`, `safety`; `grade`, `score`, `judge_mean`. The same 13 are the only LangSmith feedback keys. |
| LangSmith objects | 1 dataset (`dispute-agent-golden`), 1 experiment per `langsmith-sync` run, 1 extra tracing project for judge calls (`<LANGSMITH_PROJECT>-judge`) |
| New CLI commands / flags | 3 commands (`judge`, `calibrate`, `langsmith-sync`); 4 flags (`eval --producer`, `report --judge`, `report --before`, `report --labels`) |
| New dependencies | 0 (`langchain-anthropic`, `langsmith`, `openpyxl` already pinned) |
| LLM spend | judge: at most 3 passes of 16 (≈ $3 total); `langsmith-sync` replay target: 0 model calls; producers: 0 |
| Changes to `data/*.xlsx`, prompts, graph, app, `CHECKS`, `score()` | 0 |

### Must-handle edge cases

- JSON row with `"exception"`: all six axes FAIL, grade FAIL, judge not called, four judge scores recorded as `0` with reason `no draft (exception)`.
- `draft: null` (S-07 pattern): `evidence`, `policy`, `confidence` FAIL; `route` PASS/PARTIAL/FAIL by the §13 rule on the gate's route; judge not called, scores `0`, reason `no draft`.
- `route_ok` true with `gate_rejected` true → `route` PARTIAL, never PASS.
- Judge call fails twice (exception, refusal, schema rejection): the scenario gets `judge_error: <text>`, scores `null`; rendered as `ERR`, excluded from `judge_mean` and from calibration with `n` shown. The run continues.
- Judge returns a score below 3 with an empty `quote`: schema rejection, counts as one failed attempt.
- `--before` JSON with a different scenario set (pre-F1 run is dev-only, 12 rows; release is 16): `Compare` covers the intersection and names the excluded scenarios in its header.
- `--before` JSON lacking `model_id` / `golden_path` (every pre-phase-1 run): render blanks, no error.
- `--judge` sidecar whose `eval_path` is not the `report` input: exit 2 naming both files.
- `labels.xlsx` partially filled: rows with any blank score are skipped per axis; `n` is printed per axis; zero filled rows → `Calibration` sheet says `no labels yet`, exit 0 for `report`, exit 2 for `calibrate --check`.
- Held-out scenarios are never in `labels.xlsx` (dev only, by `split`).
- `langsmith-sync` with `LANGSMITH_API_KEY` unset: exit 2 with one line. Dataset already exists: deleted and recreated (golden is 16 rows). Without `--judge`: only the six axes are uploaded, a warning says so.
- `golden_echo` must produce every axis PASS on all 16, including S-11 (existing case → no create) and S-15 (two candidates → `need_more_evidence`).

### Out of scope

Not building: any change to prompts, the graph, the gate, data workbooks or the web app; the code-only baseline; stability rewrite (`validate.stability` stays); new scenarios; judge on held-out hand labels; LangSmith online evaluators, automation rules or annotation queues; a Makefile; a screen recording.

Forbidden by name: no `EvalHarness`/`Runner`/`Adapter`/`Stage` class hierarchy or a shared eval core like Aristotle's `app.eval`; no pandas; no YAML or new env variables (the judge project name is derived from `LANGSMITH_PROJECT`); no new judge axes, no axis weights, no composite "quality score" beyond `score` and `judge_mean`; no edits to `CHECKS`, `score()`, `HEADER` or `row_line` in `evaluate.py` (the console gains one trailing grade line only); no re-running the capture to get "better" numbers; no importing `dispute_agent.sync.synthesize.render_case` into the judge (the judge renders its own evidence from the JSON, mirror not import, so a renderer change cannot silently move the judge). Overriding any of these requires a stop-and-ask with a "why this, not the existing X" line.

## 4. File inventory (closed)

| Path | Action | Why this file owns this change |
|---|---|---|
| `dispute_agent/evaluation/metrics.py` | CREATE | §13 as code: `AXES`, `JUDGE_AXES`, `axis_verdicts(checks)`, `grade`, `score_str`, `SLICE_KEYS`, `slice_table(rows)`. Pure functions over JSON rows. |
| `dispute_agent/evaluation/judge.py` | CREATE | The model step and its check: `JUDGE_MODEL_ID`, rubric, `JudgeScores`, `render_judge_input`, `judge_row`, `judge_report -> runs/judge_<ts>.json`, `read_labels`, `agreement(judge, labels)`. |
| `dispute_agent/evaluation/labels.xlsx` | CREATE (generated by `calibrate --build`, then hand-filled, committed) | Rachit's 12 × 4 labels. |
| `tests/test_metrics.py` | CREATE | Every §13 row, exception and null-draft rows, gate-rescued PARTIAL, slices. |
| `tests/test_judge.py` | CREATE | Render content, `FakeModel` judge, empty-quote rejection, double-failure path, null-draft skip, agreement arithmetic, partial and empty labels. |
| `dispute_agent/evaluation/golden.py` | UPDATE | Gains `investigation()` (moved from tests), `golden_echo(ds, sections, case)`, `empty_stub(...)`: the golden answer as an `Investigation`, no LLM. |
| `dispute_agent/evaluation/evaluate.py` | UPDATE | `run_eval(..., producer="agent")` dispatching to `golden.golden_echo` / `golden.empty_stub`; one trailing `grade PASS k/n` line; JSON gains `"producer"`. |
| `dispute_agent/evaluation/report.py` | UPDATE | Verdict column on scenario sheets, judge panel, `Verdict Matrix`, `Slices`, `Compare`, `Calibration`; colour fills in the matrix; `build_labels(eval_json, out)` (the labels workbook, with its `How to score` sheet). |
| `dispute_agent/evaluation/validate.py` | UPDATE | Already the LangSmith module (stability, trace shapes). Gains `upsert_dataset(cases)`, `replay_target`, the two evaluators, `sync(eval_json, judge_json) -> url`. |
| `dispute_agent/__main__.py` | UPDATE | `eval --producer`; `judge`, `calibrate`, `langsmith-sync` subcommands; `report --judge/--before/--labels`. |
| `dispute_agent/evaluation/__init__.py` | UPDATE | Reading-order docstring. |
| `dispute_agent/evaluation/README.md` | UPDATE | The three stages, how to read the matrix, calibration result, the LangSmith dataset and experiment links. |
| `dispute_agent/evaluation/reports/release_2026-10-27.xlsx` | UPDATE (regenerated) | Same workbook, now with verdicts, judge panels, compare and calibration. |
| `README.md` | UPDATE | "Run it" gains `judge`, `calibrate`, `langsmith-sync`; the start-here step names the matrix and the experiment link. |
| `tests/test_golden.py` | UPDATE | `golden_echo` all-PASS on 16, `empty_stub` FAIL where expected. |
| `tests/test_evaluate.py` | UPDATE | Import `investigation` from `golden`; `--producer` exit-code tests. |
| `tests/test_report.py` | UPDATE | New sheets, judge panel, `ERR` cell, compare intersection, sidecar mismatch exit 2, labels workbook build (12 dev rows, blank scores). |
| `tests/test_validate.py` | UPDATE | Dataset example shape, replay target output, feedback keys are the 13, missing API key exit 2, with a fake client (no network). |

Read first: `dispute_agent/evaluation/evaluate.py:40-110` (`CHECKS`, `score`: which keys are booleans), `:136-213` (`run_eval`, JSON row shape); `dispute_agent/evaluation/report.py` and `golden.py` in full (phase 1 output); `dispute_agent/sync/synthesize.py:58-95` (`render_case`, the evidence the agent saw: the judge mirrors this list of blocks); `dispute_agent/config.py` (`make_model`, `MODEL_ID`); `dispute_agent/data.py:40-115` (`_split_ids`, `Scenario`), `:51-75` (`Account.memo`, `Event.memo`); `tests/test_evaluate.py:1-40` (`investigation`), `tests/test_policy_agent.py` (`FakeModel`), `tests/test_derive.py` (`oracle_findings`); `dispute_agent/evaluation/validate.py` in full (the module the upload joins; `_runs` shows how the client is called); `langsmith 0.14.12`: `evaluate(target, data, evaluators, summary_evaluators, metadata, experiment_prefix, description, max_concurrency, ..., client, blocking)`; `Client.has_dataset / create_dataset / create_examples / delete_dataset`.

Touching any file not listed is a scope breach: stop and ask.

## 5. Closed decisions (do not re-litigate)

1. **Three stages, Aurelius style: `eval` (capture, exists) → `judge` (sidecar JSON) → `report` (summary workbook).** Why not judge inside `eval`: the release JSON already exists and cost money; judging is re-runnable without re-capturing, and the judge can be swapped without touching the run.
2. **The judge renders its own evidence from the JSON row and `ds`, mirroring `render_case`'s blocks, and does not import it.** Why: eval independence (the DF README's rule); a renderer change in the agent must not silently change what the judge sees.
3. **Judge model `claude-opus-5-5`, structured output via `with_structured_output(JudgeScores, method="json_schema")`, `max_tokens=4000`, no thinking effort setting.** Why: the brief and architecture doc promise Opus so the writer (Sonnet) is not the grader; the same binding pattern as `synthesize` is known to work in this repo.
4. **Judge traces go to `<LANGSMITH_PROJECT>-judge` via `langsmith.tracing_context(project_name=...)`.** Why: `trace --last N` checks the newest roots in the main project for the graph's shape; judge roots there would fail it.
5. **One judge call per scenario with all four axes; `quote` required when a score is below 3.** Why: four calls per scenario quadruples cost for no independence gain; the quote makes the reason checkable by a human in calibration.
6. **Judge basis is the golden `rationale` plus the evidence; no golden reference note is written.** Why: Rachit's 2026-10-27 decision; the rationale column already exists for all 16.
7. **Calibration is exact and within-1 agreement per axis on the 12 dev notes, threshold 75 percent within-1, reported as found.** Why: §13; a failing axis changes the rubric, never the agent, and never in this plan (stop-and-ask).
8. **`golden_echo` and `empty_stub` are `eval --producer` values that run in-process with no LLM.** Why: the scorer is proven on the key before any model is graded (DF's `golden_echo`); the stub proves it can fail.
9. **LangSmith: dataset `dispute-agent-golden` is recreated on every sync; the experiment's target is a replay of the eval JSON (no model calls); feedback keys are exactly the 13 metric names.** Why replay, not re-running the graph: the experiment must describe the same run the workbook shows, and must cost nothing. Why recreate: 16 rows, no example-ID bookkeeping.
10. **`--before` for the committed workbook is `runs/eval_20261026T224024Z.json` (pre-F1, dev only); compare is over the intersection.** Why: it is the "before" of the only documented fix (F1/F1b), and it is the oldest full dev run with the current JSON shape.
11. **Colour fills are allowed in `Verdict Matrix`, `Compare` and the verdict column only.** Why: phase 1 banned colours because there were no verdicts; verdicts need them to scan.

## 6. Architecture / schema

### Code vs model split

| Output field | Where it comes from | Owner |
|---|---|---|
| Six axis verdicts, grade, score | `metrics.axis_verdicts` over the 18 booleans + `gate_rejected` in `row["checks"]` | code |
| Slice tables | arithmetic over `row["trap"|"wording"|"note_driven"|"category_expected"|"route_expected"]` | code |
| Compare deltas | per-cell verdict difference between two JSONs | code |
| Judge input text | `render_judge_input`: complaint, category, expected route, golden rationale, `derived.evidence_events` (with memos), `sync.rules`, `findings.summary`, `ds.accounts[account_id].memo`, the draft, the gate result | code |
| Four judge scores, reasons, quotes | read from the free-text `case_note` and `open_questions` | **model** (narrow: `JudgeScores`, four `Literal[0,1,2,3]` scores, validated; the model never sees or sets a structured field of the agent's output as a rule) |
| Calibration agreement | arithmetic over `labels.xlsx` and the judge sidecar | code |
| LangSmith example inputs/outputs, feedback | copies of golden fields and the above results | code |

Every golden field used by the judge (`rep_message`, `expected_route`, `rationale`) exists in `golden.xlsx`; every actual field exists in the eval JSON row. Nothing is dropped. If during execution the judge prompt gains a rule about a structured field (route, IDs, states), stop: that is code wearing a prompt.

### `metrics.py`

`AXES = ("route", "disputed_event", "evidence", "policy", "confidence", "guardrails")`, `JUDGE_AXES = ("faithfulness", "reasoning_agreement", "actionability", "safety")`, `Verdict = Literal["PASS", "PARTIAL", "FAIL"]`. `axis_verdicts(checks: dict) -> dict[str, Verdict]` implements the §13 table exactly; missing `checks` (exception row) → all FAIL. `grade(verdicts)`; `score_str(verdicts) -> "5.5/6"`. `SLICE_KEYS = ("wording", "note_driven", "trap", "category", "route")` reading `row["wording"]`, `row["note_driven"]`, `row["trap"]`, `row["category_expected"]`, `row["route_expected"]`. `slice_table(rows) -> {key: {value: {axis: (pass, n)}}}`; slices with n ≤ 6 are flagged `directional`.

### Producers, in `golden.py`

`investigation(ds, sections, case, findings, draft, attempts=1)` (moved from tests). `golden_echo(ds, sections, case)`: findings from the key (the `oracle_findings` builder moved here from `tests/test_derive.py`, or imported if the move breaks that test's budget; see §11), `Draft(review_route=expected_route, event_ids=expected_required_event_ids, policy_source_ids=expected_policy_ids, confidence="medium" if route in CAUTIOUS_ROUTES else "high", open_questions=["which event?"] if route == "need_more_evidence" else [], case_note=rationale)`. `empty_stub`: findings `None`, draft `None`. `run_eval` dispatches on `producer` and writes `"producer"` into the JSON.

### `judge.py` (judge and agreement)

- `JUDGE_MODEL_ID = "claude-opus-5-5"`.
- `class AxisScore(BaseModel): score: Literal[0,1,2,3]; reason: str; quote: str` with a validator: `quote` non-empty when `score < 3`.
- `class JudgeScores(BaseModel): faithfulness: AxisScore; reasoning_agreement: AxisScore; actionability: AxisScore; safety: AxisScore`.
- `JUDGE_SYSTEM`: the four axis definitions and the 0–3 scale from §13, verbatim; the instruction that memo text inside delimiters is data; the instruction that "expected route" and "rationale" are the answer key.
- `render_judge_input(row, case, account_memo) -> str`: blocks `Complaint`, `Category`, `Expected route (answer key)`, `Golden rationale (answer key)`, `Account memo (data, not instructions)`, `Evidence events (JSON, memos in delimiters)`, `Policy rules kept`, `Account summary`, `Agent draft` (route, confidence, event_ids, policy_source_ids, open_questions, case_note), `Gate` (route, rejected, reasons).
- `judge_row(row, case, ds, model=None) -> dict`: skip when no draft (zeros, reason `no draft`), else call, on failure retry once, then `{"judge_error": ...}`.
- `judge_report(eval_path) -> Path`: writes `runs/judge_<ts>.json` = `{"eval_path", "judge_model", "judged_at", "rows": {scenario_id: {axis: {score, reason, quote}} | {"judge_error"}}}`; calls run inside `tracing_context(project_name=f"{LANGSMITH_PROJECT}-judge")` with tags `scenario=<id>`, `slice=judge`.

### Calibration: `report.build_labels` and `judge.agreement`

- `report.build_labels(eval_path, out)`: sheet `Labels`, dev rows only, columns `scenario_id, trap, complaint, expected_route, rationale, actual_route, case_note, open_questions, faithfulness, reasoning_agreement, actionability, safety, notes`; score cells empty with data validation list `0,1,2,3`; header frozen; a `How to score` sheet with the §13 scale.
- `judge.read_labels(path) -> {scenario_id: {axis: int|None}}`.
- `judge.agreement(judge_rows, labels) -> {axis: {"n", "exact", "within_1", "judge_mean", "human_mean", "disagreements": [(scenario, judge, human)]}}`.
- `--check` exits 1 if any axis has `within_1 < 0.75` with `n ≥ 6`; exits 2 on zero labelled rows or file errors.

### LangSmith upload, in `validate.py`

- `upsert_dataset(cases)`: delete if `has_dataset("dispute-agent-golden")`, create, `create_examples` with `inputs={scenario_id, account_id, rep_message}`, `outputs={every expected_* field, rationale}`, `metadata={split, trap, wording, note_driven}`.
- `replay_target(inputs) -> dict`: finds the row by `scenario_id` in the eval JSON; returns `{route, draft_route, disputed_event_id, event_ids, policy_source_ids, confidence, open_questions, case_note, gate_rejected, case_created, exception}`.
- Evaluators: `axes_evaluator(run, example)` → `{"results": [{"key": axis, "score": 1|0.5|0, "value": verdict} for axis in AXES] + [grade, score]}`; `judge_evaluator` → `{"results": [{"key": axis, "score": s/3, "value": s, "comment": reason} ...] + [judge_mean]}`, both read the already-computed results (no model).
- `sync(eval_path, judge_path) -> str`: `evaluate(replay_target, data="dispute-agent-golden", evaluators=[...], experiment_prefix=f"release-{date}", metadata={"eval_path", "judge_path", "model_id", "judge_model"}, max_concurrency=4)`; prints and returns the experiment URL.

### Report extensions

- Scenario sheet: 4th column `verdict` per golden row (the governing axis: `category`→`guardrails`, `route`→`route`, `disputed_event_id`→`disputed_event`, event rows→`evidence`, state rows→`guardrails`? no: `payment_state`/`refund_state` carry no axis in §13 → `—`; `existing_case_id`→`guardrails`; `policy_ids`→`policy`; `confidence`→`confidence`); below the run-detail block a `Judge` panel `axis | score | reason | quote`, or `ERR: <judge_error>`, or `not judged`.
- `Verdict Matrix` sheet: `Q, Trap, Wording, Note, route, disputed_event, evidence, policy, confidence, guardrails, Grade, Score, faithfulness, reasoning_agreement, actionability, safety, Judge mean, Gate, Attempts`; totals row; route confusion matrix below (reuse `evaluate.confusion_lines`).
- `Slices`: one block per `SLICE_KEYS` key; cells `k/n`; `(directional)` suffix when n ≤ 6.
- `Compare` (with `--before`): header block with per-axis PASS counts for both runs over the intersection; then one row per (scenario, axis) that differs, columns `before | after | Δ` (`improved`/`regressed`); excluded scenarios listed.
- `Calibration` (with `--labels`): per-axis `n, exact, within-1, judge mean, human mean`; disagreement rows; `no labels yet` when empty.

## 7. API surface

- `python -m dispute_agent eval [...] [--producer agent|golden_echo|empty_stub]`.
- `python -m dispute_agent judge <eval.json>` → prints the sidecar path; exit 0 even with per-row `judge_error`s (they are findings); exit 2 on a non-report input.
- `python -m dispute_agent calibrate --build <eval.json> [--out labels.xlsx]` / `calibrate --check <judge.json> --labels <labels.xlsx>`.
- `python -m dispute_agent report <eval.json> --out <xlsx> [--judge <judge.json>] [--before <eval.json>] [--labels <labels.xlsx>]`.
- `python -m dispute_agent langsmith-sync <eval.json> [--judge <judge.json>]` → prints the experiment URL.

No HTTP routes, no graph state changes.

## 8. Migration plan

None. `labels.xlsx` is generated in step 4 and committed once filled. The release workbook is regenerated in place. Old `runs/*.json` without `producer` default to `agent`.

## 9. Ordered steps

1. **`metrics.py` + `tests/test_metrics.py`.** Pure; every §13 row parametrised.
2. **Producers in `golden.py`, `eval --producer`, `tests/test_golden.py` and `tests/test_evaluate.py` updates.** Run criteria 2 and 3.
3. **`report.py` extensions for verdicts, matrix, slices, compare; `tests/test_report.py` update.** Re-render the release workbook with `--before` only (no judge yet) and confirm criterion 6's sheet set minus `Calibration`.
4. **`judge.py`, `judge` command, `tests/test_judge.py`.** Then run criterion 4 once (≈ $1). Inspect the sidecar: `judge_error` count and any axis with every score 3 (a rubric that never fails is a finding; record it in the README, do not re-run).
5. **`report.build_labels`, `judge.agreement`, `calibrate` command, tests in `test_report.py` and `test_judge.py`; run `calibrate --build`.** **Stop here for Rachit to fill `labels.xlsx`.** An unattended run continues with steps 6–8 and leaves `Calibration` as `no labels yet`; `calibrate --check` and the final re-render happen when labels arrive.
6. **LangSmith upload in `validate.py`, `langsmith-sync` command, `tests/test_validate.py` update.** Run criterion 7; paste the URL into the README.
7. **Final render and docs.** Criterion 6 with every flag available; README files; `__init__.py`.
8. **Verify.** Criteria 1, 2, 3, 6, 8; paste outputs into the commit body. On labels arriving: criterion 5 `--check`, re-run step 7's render, commit `labels.xlsx` and the workbook.

## 10. Tests

| Test | Criterion |
|---|---|
| `tests/test_metrics.py::test_axis_table[...]`, `::test_exception_row`, `::test_null_draft`, `::test_gate_rescued_partial`, `::test_slices_directional` | 1, 6 |
| `tests/test_golden.py::test_golden_echo_all_pass_16`, `::test_empty_stub_fails` | 2, 3 |
| `tests/test_evaluate.py::test_producer_exit_codes` (fake graph, no LLM) | 2, 3 |
| `tests/test_judge.py::test_render_blocks_present`, `::test_fake_model_scores`, `::test_quote_required_below_3`, `::test_retry_then_error`, `::test_null_draft_not_called` | 4 |
| `tests/test_judge.py::test_agreement_math`, `::test_partial_labels`, `::test_no_labels_exit_2`; `tests/test_report.py::test_build_labels_12_dev_rows` | 5 |
| `tests/test_validate.py::test_examples_shape`, `::test_replay_target`, `::test_feedback_keys_are_the_13`, `::test_missing_key_exit_2` (fake client) | 7 |
| `tests/test_report.py::test_matrix_and_slices`, `::test_compare_intersection`, `::test_judge_panel_and_err`, `::test_sidecar_mismatch_exit_2`, `::test_calibration_sheet_empty` | 6 |
| whole suite | 1, 8 |

Conventions: one test file per source file; `ds`, `sections` session fixtures; `FakeModel` from `tests/test_policy_agent.py` for the judge; no network (patch `langsmith.Client` and `evaluate` in `test_validate.py`).

## 11. Edge cases / footguns

- `checks` mixes booleans with `route_final`, `draft_route`, `dropped`, `policy_dropped`, `policy_ambiguous_count`; `axis_verdicts` reads named keys only.
- `tests/test_evaluate.py` imports `oracle_findings` from `test_derive` and `rule` from `test_policy_agent`; `golden.py` must not import from `tests/`. Move `oracle_findings` into `golden.py` and import it back in `test_derive.py` (that file is not in the inventory: if the move needs an edit there, stop and ask; the alternative is a minimal re-implementation inside `golden.py`).
- `with_structured_output(..., method="json_schema")` on `ChatAnthropic` returns a dict or a model depending on version; `synthesize` handles both (`raw if isinstance(raw, dict) else raw.model_dump()`); do the same.
- `Literal[0,1,2,3]` for `score` serialises as integers in JSON schema; verify the Opus call accepts it, else use `conint(ge=0, le=3)`.
- Memo text in evidence events is already wrapped by `wrap_memo` only inside `render_case`; the judge renders raw `memo` fields from the JSON and must wrap them itself in the same `<<memo>> … <</memo>>`-style delimiters (copy the literal delimiter strings, do not import).
- `tracing_context(project_name=...)` must wrap the `invoke`, not just the model construction.
- `evaluate()` with `max_concurrency` > 0 runs the replay target in threads; the JSON rows dict must be read-only inside.
- `has_dataset` + `delete_dataset` need the dataset name, not ID; `create_examples` takes parallel `inputs=`, `outputs=`, `metadata=` lists in 0.14.
- `experiment_prefix` makes LangSmith append a random suffix; capture the real name from `results.experiment_name` for the URL.
- openpyxl data validation for the `0,1,2,3` list: `DataValidation(type="list", formula1='"0,1,2,3"')`, add to the sheet then `add(cell_range)`.
- Sheet names: 31-char cap; `Verdict Matrix` and `Calibration` are fine; never derive one from text.
- Colour fills via `PatternFill("solid", fgColor=...)`, no conditional formatting.
- Judge cost: Opus input ≈ 3–4k tokens per scenario; 16 scenarios ≈ $1. Three passes is the cap; a fourth is a stop-and-ask.

## 12. Open questions

All closed. One execution pause, not a question: step 5 waits for Rachit's labels.

## Execution-discipline note

Read the whole plan and phase 1's §13 first. Execute steps 1–8 in order; run that step's tests before moving on. Re-read §3 Budgets and §4 File inventory before each step; on hitting a budget or needing a file not in the inventory, stop and ask. Never edit `data/`, the graph folders, `app/`, prompts, or `CHECKS`/`score()`. LLM spend is step 4 only, one pass by default. If the judge prompt needs a rule about a structured field to score well, stop: that is code's job. If a step is blocked by one hard issue, leave a `TODO(<area>)` with a compiling stand-in and continue. Finish with a fresh-context review of the diff against §3 and run criterion 8 last.
