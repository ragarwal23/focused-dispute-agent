# Evals on LangSmith: one live experiment, one table

## 1. Status

v1.0, 2026-10-27. Supersedes the `langsmith-sync` replay story in `dispute_agent/evaluation/README.md` ("LangSmith:
dataset ... and the experiment `release-20261027T174341Z-90f5290d`"). Builds on eval phases 1 and 2
(`docs/history/eval-framework-phase{1,2}.md`). Not implemented yet.

Interview decisions (Rachit, 2026-10-27), all closed, see §5:
live experiment through `langsmith.evaluate` with the real graph as target; all 16 scenarios (a second, untuned pass
over the held-out four, brief reworded); spend ≤ $3; the two old release write-ups in the eval README are deleted,
not archived.

## 2. Why & goal

Reviewers use LangSmith and will spend ~30 minutes on the submission. Today the evals are presented as two
spreadsheets plus a LangSmith experiment whose rows **replay a stored JSON**: clicking a row opens a no-op function,
not the agent. Analogy: the dataset is the answer key, an experiment is one graded sitting of the exam; the current
sitting is a scanned grade sheet with the exam papers in another drawer.

**Goal:** `python -m dispute_agent eval --split all --experiment` runs the graph on every golden scenario *as* a
LangSmith experiment, so each row's trace is the real `dispute S-xx` run with both agents inside, scored by the same
six code axes; `judge --experiment` adds the four Opus scores to the same experiment. The READMEs then present evals as
**one experiment link plus one results table**; the workbooks become "also available locally".

Use case: a reviewer opens the link, sorts by `route`, clicks S-11, sees the Policy Agent's rules and the synthesize
call that chose `explain_events`, and reads the judge's quoted span in the feedback comment.

## 3. Definition of Done

### Acceptance criteria (each must exit 0)

```bash
# offline
.venv/bin/python -m pytest -q                                                            # all tests, incl. the moved ones
.venv/bin/python -m dispute_agent.evaluation.golden --check                              # golden unchanged
test "$(grep -c 'Release 2026-10-27, no gate' dispute_agent/evaluation/README.md)" = 0    # old prose gone
grep -q 'smith.langchain.com' README.md && grep -q 'smith.langchain.com' dispute_agent/evaluation/README.md
grep -q 'also available locally' dispute_agent/evaluation/README.md
# live, in this order (LANGSMITH_API_KEY set; see §9 step 6 for the stop points between them)
.venv/bin/python -m dispute_agent eval --producer golden_echo --split all --experiment    # $0 scorer proof, 16/16
.venv/bin/python -m dispute_agent eval --scenario S-01 --experiment                      # ≈$0.07 nesting spike
.venv/bin/python -m dispute_agent eval --split all --experiment                          # ≈$1, the release capture
.venv/bin/python -m dispute_agent judge runs/eval_<ts>.json --experiment                 # ≈$1
.venv/bin/python -m dispute_agent report runs/eval_<ts>.json --judge runs/judge_<ts>.json \
    --before runs/eval_20261027T174341Z.json --labels dispute_agent/evaluation/labels.xlsx \
    --out dispute_agent/evaluation/reports/release_2026-10-27_v0.8.xlsx
# the experiment really holds 16 scored rows with exactly the 13 feedback keys
.venv/bin/python - <<'EOF'
import json, sys
from dotenv import load_dotenv; load_dotenv(".env")
from langsmith import Client
from dispute_agent.evaluation.metrics import METRIC_NAMES
data = json.load(open(sys.argv[1] if len(sys.argv) > 1 else max(__import__("glob").glob("runs/eval_*.json"))))
c = Client(); runs = list(c.list_runs(project_name=data["experiment_name"], is_root=True))
keys = {f.key for r in runs for f in c.list_feedback(run_ids=[r.id])}
assert len(runs) == 16 and keys == set(METRIC_NAMES), (len(runs), sorted(keys))
EOF
```

### Budgets

| Budget | Max |
|---|---|
| New files | 2 (`dispute_agent/evaluation/experiment.py`, `tests/test_experiment.py`) |
| LOC per new file | 220 (experiment.py), 200 (test_experiment.py) |
| LOC growth of an existing file | +40 (evaluate.py), +20 (`__main__.py`), +6 (report.py); validate.py must **shrink** |
| New DB tables / columns | 0 / 0 |
| New metric / feedback names | 0 (the 13 in `metrics.METRIC_NAMES` are the whole feedback vocabulary) |
| New dependencies | 0 (`langsmith` 0.14.12 is installed; `evaluate_existing` is in it) |
| New CLI flags | 2 (`eval --experiment`, `judge --experiment`) |
| New eval-JSON fields | 2 (`experiment_name`, `experiment_url`, top level) |
| Paid LLM runs | 1 all-16 capture + 1 single-scenario spike + 1 judge pass; **$3 total**. A re-run of any of them is a stop-and-ask |
| Prompt or agent code changes | 0 |

### Must-handle edge cases

- **The dataset already exists on LangSmith with the headline experiment attached.** Deleting a dataset deletes its
  experiments. Nothing in this plan may call `delete_dataset`; the dataset is created only if missing.
- **An exception inside one scenario** (S-02 has thrown before): the experiment row must still exist, carry
  `{"status": "exception"}` outputs and FAIL on every axis, exactly like the JSON row does today.
- **`--split dev` / `--scenario S-07` with `--experiment`**: only those examples run; the held-out four are not touched.
- **`judge --experiment` on a JSON captured without `--experiment`**: exit 2 with a one-line message, no upload.
- **No `LANGSMITH_API_KEY`** with `--experiment`: exit 2 before any model call, same wording as `langsmith-sync`.
- **The golden dataset on LangSmith differs from `golden.xlsx`** (count or scenario IDs): exit 1 with a message that
  names the fix (delete the dataset in the UI, which deletes its experiments, then rerun). Never auto-repair.
- **S-07 produces no draft again, or S-11 misses the route**: report the numbers as they are. No tuning in this slice.

### Out of scope

Not building: a code-only baseline experiment; judge sensitivity on broken notes; a Slices or Calibration view in
LangSmith; pairwise/comparative experiments; online evaluators on `ask` traces; a `--rebuild-dataset` flag; uploading
the pre-F1 run as a "before" experiment (the gate-era experiment `release-20261027T174341Z-90f5290d` already exists
and is the before).

Forbidden by name: no new feedback keys (so no `route_pass` summary evaluators, no `trace_url` feedback); no summary
evaluators at all; no `experiment` config surface in `config.py` or `.env` (the two flags are the whole surface); no
new module beyond `experiment.py`; no wrapper class around the LangSmith client (plain functions taking `client`, as
`validate.py` does today); no change to `judge.py`'s prompt, model or sidecar format; no change to any agent prompt,
graph node or `score()` check; no reworking of `report.py` beyond the one `Run` row; no `docs/history` additions.
Overriding any of these = stop and ask with a "why this, not the existing X" line.

## 4. File inventory (closed)

| Path | Action | Why this file owns the change |
|---|---|---|
| `dispute_agent/evaluation/experiment.py` | CREATE | The LangSmith upload concern, one file: dataset, live experiment, replay experiment, judge append. Moved out of `validate.py` so "checks" and "upload" are separate files a reader can map |
| `dispute_agent/evaluation/validate.py` | UPDATE | Loses `DATASET_NAME`, `GOLDEN_OUTPUTS`, `dataset_examples`, `upsert_dataset`, `replay_output`, `axes_feedback`, `judge_feedback`, `sync_langsmith` and the module docstring's second paragraph; keeps `stability`, `trace_check` |
| `dispute_agent/evaluation/evaluate.py` | UPDATE | `run_eval` gains the `experiment` path: the per-scenario body becomes `run_one`, the timestamp is fixed before the run, the JSON gains the two fields |
| `dispute_agent/evaluation/report.py` | UPDATE | `RUN_KEYS` + `"experiment"` so the workbook's `Run` sheet shows the link |
| `dispute_agent/evaluation/__init__.py` | UPDATE | The reading-order file list gains `experiment.py` and `validate.py`'s line shrinks |
| `dispute_agent/__main__.py` | UPDATE | `eval --experiment`, `judge --experiment`, `langsmith-sync` imports from `experiment` |
| `tests/test_experiment.py` | CREATE | `FakeLangSmith` and the five sync/feedback tests move here from `test_validate.py`; new tests for `ensure_dataset`, the live path, `append_judge` |
| `tests/test_validate.py` | UPDATE | Keeps only the stability and trace tests; imports trimmed |
| `tests/test_evaluate.py` | UPDATE | One new test: `run_eval(..., experiment=True)` through `golden_echo` with the fake client writes a JSON carrying `experiment_name` |
| `tests/test_report.py` | UPDATE only if a test asserts the `Run` sheet's row count | Otherwise untouched |
| `dispute_agent/evaluation/README.md` | UPDATE | Top = link + one table + "also available locally"; both old release sections deleted; commands and files table updated |
| `README.md` | UPDATE | Line 59 (Traces row), the `eval` paragraph (≈ line 136) and the two "held-out run" mentions in Known failures |
| `docs/project_brief.md` | UPDATE | Line 112 and process step 3 (line 135): the held-out four were run twice with nothing changed between |

Read first: `dispute_agent/evaluation/validate.py:1-30,130-224`, `dispute_agent/evaluation/evaluate.py:145-225`,
`dispute_agent/evaluation/judge.py:131-152`, `dispute_agent/evaluation/report.py:26,316-327`,
`dispute_agent/__main__.py:214-235,257-261,298-322`, `dispute_agent/orchestrator/run.py:53-101`,
`tests/test_validate.py:1-13,78-168`, `tests/test_report.py:40-55`, `tests/test_evaluate.py:70-89`.

Touching any other file is a scope breach: stop and ask. In particular **not** `judge.py`, `metrics.py`, `golden.py`,
`policy_checks.py`, anything under `orchestrator/`, `account_agent/`, `policy_agent/`, `sync/`, `review/`, `app/`,
`docs/architecture.md`, `docs/history/*`.

## 5. Closed decisions (do not re-litigate)

1. **Live experiment, not replay + links.** Why: a reviewer clicking a row must land on the agent's trace; a
   `trace_url` output field renders as text. Replay stays only as `langsmith-sync` for old JSONs.
2. **All 16 scenarios, a second untuned held-out pass; the brief is reworded.** Why: one link, one table. The first
   pass's held-out numbers stay quoted as the held-out result; nothing changes between passes (budget row "prompt or
   agent code changes: 0" enforces it). Alternative rejected: dev-only live + gate-era replay for held-out = two links.
3. **`--experiment` is opt-in, for any producer.** Why: `eval` without a key or in tests must keep working; and
   `golden_echo --experiment` gives a $0 experiment that proves the scorer on LangSmith itself.
4. **One module `experiment.py` for everything LangSmith-upload; `validate.py` keeps the checks.** Why: Rachit's
   layout rule is one file per concern a junior can name; "stability/trace checks" and "dataset/experiment upload"
   are two concerns and the file was already 224 lines. Not a package, not a class.
5. **The dataset is created if missing, verified if present, never deleted.** Why: deleting it deletes the headline
   experiment. Rejected: `--rebuild-dataset` (a footgun with a flag on it).
6. **Judge scores are appended with `evaluate_existing`, after the sidecar is written.** Why: keeps `judge.py` and its
   sidecar untouched, keeps the Opus calls traced to `<project>-judge` as today, and the sidecar stays the input to
   `report` and `calibrate`. Rejected: judge as a second evaluator inside `evaluate()` (couples the $1 capture to the
   $1 judge and changes `judge.py`).
7. **Sequential execution (`max_concurrency=0`) for the live path.** Why: printed rows stay in order, Anthropic rate
   limits, and one shared graph/checkpointer. The replay path keeps `4`.
8. **`disable_evaluator_tracing=True` on both `evaluate` and `evaluate_existing`.** Why: the evaluators read local
   dicts; tracing them spends the monthly unique-trace quota that refused uploads on 2026-10-27.
9. **Exceptions are caught inside the target** and returned as `{"status": "exception", ...}` outputs, as the replay
   does. Why: the row must exist and score FAIL on all axes; an errored run has no feedback.
10. **LangSmith shows mean scores (PARTIAL = 0.5); the README table shows PASS counts.** Why: a PASS-count column
    would need summary evaluators and new feedback names (budget 0). The README says so in one sentence.
11. **Timestamp before the run.** `stamp = now:%Y%m%dT%H%M%SZ` is computed once and used for `runs/eval_<stamp>.json`
    and `experiment_prefix=f"release-{stamp}"` (or `f"{producer}-{stamp}"` for a non-agent producer), so JSON and
    experiment name match by eye. `report.captured_at` keeps parsing the filename unchanged.
12. **Old release prose is deleted**, calibration section kept. Why: Rachit's call; the calibration is about the
    judge, not a release, and `labels.xlsx` still holds the notes it was done on.
13. **The results table lives in both READMEs** (root and evaluation), identical, one row. Why: the 30-minute reviewer
    reads the root README; drift risk is one row, filled from the same experiment in the same step.

## 6. Architecture

No new model step. Code vs model split for everything this plan produces:

| Output field | Where it comes from | Owner |
|---|---|---|
| experiment row inputs (`scenario_id`, `account_id`, `rep_message`) | `golden.xlsx` via `dataset_examples` (unchanged) | code |
| experiment row reference outputs (`expected_*`, `rationale`) | `golden.xlsx` (unchanged) | code |
| experiment row outputs (`route`, `draft_route`, `disputed_event_id`, `event_ids`, `policy_source_ids`, `confidence`, `open_questions`, `case_note`, `policy_consistent`, `case_created`, `status`) | `replay_output(row)` over the row `run_one` just built (same function as the replay; the agent's own fields, already scored) | code (the agent produced them upstream; nothing new is read from text) |
| feedback: 6 axes, `grade`, `score` | `axes_feedback(row)` (unchanged) | code |
| feedback: 4 judge axes, `judge_mean` | `judge_feedback(sidecar row)` (unchanged) | code over the existing judge sidecar |
| `experiment_name`, `experiment_url` in the eval JSON | `ExperimentResults.experiment_name`, `.url` | code |

### `experiment.py` (new, ≤ 220 LOC)

```
DATASET_NAME, GOLDEN_OUTPUTS                       moved verbatim
dataset_examples(cases)                            moved verbatim
ensure_dataset(client, cases) -> dataset           NEW: create if missing (same description); if present, list its
                                                   examples and require {inputs.scenario_id} == {c.scenario_id} and the
                                                   same count; else raise RuntimeError(<fix message>, see §3)
select_examples(client, cases) -> list[Example]    NEW: client.list_examples(dataset_name=DATASET_NAME), kept if
                                                   scenario_id in {c.scenario_id for c in cases}, sorted by scenario_id
replay_output(row), axes_feedback(row),
judge_feedback(judge_row)                          moved verbatim
run_experiment(client, evaluate_fn, target, rows, cases, *, prefix, description, metadata,
               judge=None, max_concurrency=0) -> ExperimentResults
                                                   NEW: ensure_dataset; data=select_examples; evaluators=[axes]
                                                   (+[judged] when judge); disable_evaluator_tracing=True; returns results.
                                                   `rows` is the dict the target fills (live) or the loaded JSON rows (replay)
append_judge(client, evaluate_existing_fn, experiment_name, judge) -> ExperimentResults
                                                   NEW: evaluate_existing(experiment_name, evaluators=[judged],
                                                   client=client, max_concurrency=0, disable_evaluator_tracing=True)
sync_langsmith(eval_path, judge_path, client, evaluate_fn) -> int
                                                   moved; now calls run_experiment with target=replay over the loaded
                                                   rows, prefix=f"release-{stamp}", max_concurrency=4. No delete.
                                                   Prints the same final line.
```

Pure functions with injected `client` / `evaluate_fn` / `evaluate_existing_fn`, exactly the seam the current tests use.

### `evaluate.py` changes

- `run_one(s, graph, ds, sections, producer) -> dict`: the current loop body (tags, `investigate` or producer,
  `score`, `row_line` print, the row dict; on exception prints and returns the `{"scenario_id", "exception"}` row).
- `run_eval(..., experiment: bool = False)`:
  - `stamp` computed first; `out_path = runs/eval_<stamp>.json`.
  - `experiment=False`: `rows = [run_one(s, ...) for s in scenarios]` (today's behaviour, byte-identical JSON apart
    from the two new fields being absent).
  - `experiment=True`: exit 2 if no `LANGSMITH_API_KEY`; `by_id = {}`; `target(inputs)` → `row = run_one(scenario for
    inputs["scenario_id"])`, `by_id[sid] = row`, return `replay_output(row)`; `results = run_experiment(Client(),
    evaluate, target, by_id, scenarios-as-cases, prefix=..., description="Live capture: the graph on every golden
    scenario, scored by the six code axes; judge feedback is added by `judge --experiment`", metadata={"model_id",
    "producer", "split", "git_sha", "eval_path"})`; `rows = [by_id[s.scenario_id] for s in scenarios if in by_id]`;
    the JSON gains `"experiment_name": results.experiment_name, "experiment_url": results.url`; the last printed line
    adds `experiment <name>: <url>`.
  - `git_sha` = `subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()`
    or `""` on failure. No new dependency.
  - `scenarios-as-cases`: `run_experiment` only needs scenario IDs for `select_examples`; pass
    `[c for c in read_golden() if c.scenario_id in selected_ids]` so the type stays `Case`.

### `__main__.py` changes

- `ev.add_argument("--experiment", action="store_true", help="run as a LangSmith experiment on the golden dataset (traces attached)")`;
  `cmd_eval` passes `experiment=args.experiment`.
- `jd.add_argument("--experiment", action="store_true", help="also append the scores to the experiment named in the eval JSON")`;
  `cmd_judge`: after `judge_report` returns the sidecar path, if `--experiment`: exit 2 with
  `judge: <json> was not captured with --experiment` when `data` has no `experiment_name`; exit 2 on a missing key
  (same wording as sync); else `append_judge(Client(), evaluate_existing, data["experiment_name"], json.loads(sidecar))`
  and print `experiment <name>: <url>`.
- `cmd_langsmith_sync` imports from `dispute_agent.evaluation.experiment`.

### `report.py`

`RUN_KEYS += ("experiment",)`; `values["experiment"] = data.get("experiment_url") or data.get("experiment_name", "")`.

### Trace shape inside the experiment

`evaluate()` traces each target call as the experiment's root run; the graph's `graph.invoke(..., run_name="dispute
S-01", tags=[...])` nests under it because langchain-core picks up langsmith's run-tree context. Children of that
child are unchanged (`classify > account_agent > policy_agent > derive_states > sync > synthesize > review`, then the
`dispute S-01 resume`). **This nesting is verified live in §9 step 6b before the $1 capture.** `trace --last N` keeps
looking for roots in `LANGSMITH_PROJECT` and therefore no longer sees eval runs; it still serves `ask`.

## 7. API surface

CLI only. New: `eval --experiment`, `judge --experiment`. Changed: `langsmith-sync` no longer deletes/recreates the
dataset (behaviour change, documented in its help string: "upload golden.xlsx as a LangSmith dataset if missing, and
replay an eval JSON as an experiment"). Eval JSON: two optional top-level string fields. Feedback keys: unchanged 13.
No HTTP routes, no graph state change.

## 8. Migration plan

None. Old eval JSONs lack the two fields and keep loading (`data.get`). The existing dataset `dispute-agent-golden`
(id `7d3c940e-0e7c-4b8d-bd0d-5b80c4a99476`, 16 examples) passes `ensure_dataset` as is; the existing experiment
`release-20261027T174341Z-90f5290d` is untouched and remains the "before" in the compare view. Down: delete
`experiment.py`, restore `validate.py` from git; nothing on LangSmith needs undoing.

## 9. Ordered steps

Each step ends with `.venv/bin/python -m pytest -q` green. Re-read §3 budgets and §4 before each.

1. **Move.** Create `experiment.py` with the moved functions verbatim plus `ensure_dataset`, `select_examples`,
   `run_experiment`, `append_judge`; rewrite `sync_langsmith` on top of `run_experiment` (no delete). Trim
   `validate.py` and its docstring. Create `tests/test_experiment.py` with `FakeLangSmith` (gains `list_examples`,
   `evaluate_existing`; `delete_dataset` now raises `AssertionError("never delete")`) and the moved tests; update
   `test_examples_shape`'s call expectations to `ensure_dataset` (`exists=True` → `["has", "list_examples"]`,
   `exists=False` → `["has", "create", "examples"]`, mismatch → `RuntimeError`). Trim `test_validate.py`. Point
   `__main__.py` and `__init__.py` at the new module.
2. **`run_one` + stamp.** Refactor `run_eval` without the experiment path yet. `test_row_has_review` and
   `test_producer_exit_codes` must pass unchanged; add an assertion that the JSON filename stamp equals the one printed.
3. **`eval --experiment`.** Add the flag and path. Test (`tests/test_evaluate.py`): monkeypatch
   `dispute_agent.evaluation.evaluate.Client`/`evaluate` to `FakeLangSmith` (`examples` pre-seeded via
   `dataset_examples(read_golden())`), run `run_eval(DATA, "all", None, 1, 1, 1, producer="golden_echo",
   experiment=True)` → exit 0, JSON has `experiment_name == "release-x"`-style name and `experiment_url`, 16 rows,
   `client.kwargs["max_concurrency"] == 0` and `disable_evaluator_tracing is True`; `--split dev` → the fake received
   12 examples. Missing key → exit 2 and no graph built.
4. **`judge --experiment`.** `append_judge` + CLI wiring. Test (`tests/test_experiment.py`): fake
   `evaluate_existing` replays stored runs and asserts the 5 judge keys per row; CLI test: JSON without
   `experiment_name` → exit 2 and the message; with it → `append_judge` called with that name (monkeypatch
   `judge_report` to return a prewritten sidecar so no model runs).
5. **`Run` sheet row.** `RUN_KEYS` + value; adjust `test_report.py` only if it counts rows.
6. **Live, in order, with stops:**
   a. `eval --producer golden_echo --split all --experiment` → exit 0, 16/16, URL printed. Open it: 16 rows, 8 feedback
      columns, dataset unchanged. ($0)
   b. `eval --scenario S-01 --experiment` (≈$0.07). Open the row: its trace must contain `dispute S-01` with the seven
      children and the resume. **If the graph run is not nested under the row, STOP** and report; the fallback
      (wrapping `investigate` in `tracing_context(parent=get_current_run_tree())`) touches `run.py`, which is out of
      inventory and needs Rachit's OK.
   c. `eval --split all --experiment` (≈$1). Exit code as it comes. Record the JSON path.
   d. `judge runs/eval_<ts>.json --experiment` (≈$1). Open the experiment: 13 feedback columns.
   e. `report ... --out dispute_agent/evaluation/reports/release_2026-10-27_v0.8.xlsx` with `--before
      runs/eval_20261027T174341Z.json`. Then the §3 verification script.
   Spend so far must stay ≤ $3; any re-run is a stop-and-ask.
7. **Docs, numbers from step 6 only.** Evaluation README: new top section "Release 2026-10-27 (v0.8)" = one sentence,
   the experiment link (the dataset compare URL with `selectedSessions=<new>`; a second link with
   `<old>,<new>` labelled "before/after"), the one table (see below), ≤ 4 finding bullets, then "Also available
   locally: `reports/release_2026-10-27_v0.8.xlsx` (adds the `Slices`, `Compare` and `Calibration` sheets LangSmith
   does not draw), rendered with `report`". Delete both old release sections. Update "The three stages" to the new
   commands (`--experiment` on eval and judge; `langsmith-sync` described as "replay an older JSON"). Files table:
   add `experiment.py`, fix `validate.py`'s row. One sentence under the table: "LangSmith's column averages are mean
   scores with PARTIAL = 0.5; this table counts PASS." Root README: line 59 links the experiment directly; the eval
   paragraph gets the same link + table; "Seen on the one held-out run" → what the two passes showed. Brief line 112:
   "The held-out four were run twice after tuning, with nothing changed between the passes: once for the first release
   and once to capture the release experiment. With four cases..." and step 3: "Run the held-out set after tuning and
   report the numbers as they are (run twice, untuned, to capture the experiment)."

   Table shape (both READMEs):

   | 16 scenarios | route | disputed_event | evidence | policy | confidence | guardrails | grade PASS | judge mean |
   |---|---|---|---|---|---|---|---|---|
   | release 2026-10-27 v0.8 | n/16 | n/16 | n/16 | n/16 | n/16 | n/16 | n/16 | x.xx / 3 |

8. **Fresh-context review** of the diff against this plan (a subagent with only the plan and the diff), then commit:
   `Run the eval as a LangSmith experiment; present evals as one link and one table`.

## 10. Tests

| Test | File | Criterion |
|---|---|---|
| `test_stability_matrix_and_gate`, `test_trace_*` | `tests/test_validate.py` (kept) | pytest green |
| `test_examples_shape` (ensure_dataset: create / verify / mismatch, never delete) | `tests/test_experiment.py` | pytest; edge case "dataset exists" |
| `test_replay_target`, `test_feedback_keys_are_the_13` | `tests/test_experiment.py` (moved) | pytest; "13 keys" |
| `test_sync_runs_dataset_then_experiment`, `test_sync_missing_key_exit_2` | `tests/test_experiment.py` (moved; calls now `["has", "list_examples"]`, kwargs include `disable_evaluator_tracing`) | pytest |
| `test_run_experiment_live_shape` (target fills rows; exception row → `status: exception` + 6 FAIL) | `tests/test_experiment.py` | edge case "exception inside one scenario" |
| `test_append_judge` + CLI exit 2 without `experiment_name` | `tests/test_experiment.py` | edge case "judge on a non-experiment JSON" |
| `test_eval_experiment_golden_echo` (JSON fields, 16 vs 12 examples, concurrency 0, exit 2 without key) | `tests/test_evaluate.py` | edge cases "split", "no key" |
| `test_producer_exit_codes`, `test_row_has_review` + stamp assertion | `tests/test_evaluate.py` | unchanged behaviour |
| `Run` sheet row | `tests/test_report.py` (only if it counts rows) | pytest |

Conventions: flat `tests/`, fixtures `ds`/`sections` from `conftest.py`, `eval_data`/`judge_data` from
`tests/test_report.py`, fakes as `SimpleNamespace`, no network, no model.

## 11. Edge cases / footguns

- **`delete_dataset` deletes experiments.** The fake raises on it so a regression fails offline.
- **`list_examples` order is not guaranteed**: sort by `inputs["scenario_id"]` before passing `data=`.
- **`evaluate(data=...)` wants `Example` objects** (from `list_examples`), not dicts, when passing a list.
- **`error_handling` stays `"log"`**, but the target never raises (decision 9), so it never triggers.
- **`LANGSMITH_PROJECT` is ignored for experiment runs**; they live in the experiment's own project. `trace --last`
  will not find them; say so in the eval README's `trace` line.
- **Quota**: the 2026-10-27 refusals were "monthly unique traces". The capture is 16 traces (+16 resumes as children,
  not roots) plus 1 for the golden_echo proof; evaluator tracing is off. If a 429 appears mid-capture, finish the run
  (scores are local), then stop and ask before re-running.
- **The stamp must stay `%Y%m%dT%H%M%SZ`** or `report.captured_at` breaks.
- **`FakeLangSmith.evaluate` runs the target itself**; the live-path test must therefore seed `examples` so the
  target is called 16 (or 12) times; `golden_echo` makes that $0 and graph-free (`build_graph` is monkeypatched to
  fail, as in `test_producer_exit_codes`).
- **Held-out discipline**: between step 6c and the commit, nothing under `dispute_agent/` except the files in §4 may
  change, and none of those changes affect scoring. If the capture shows a new failure, it goes into "Known failures",
  not into code.
- **Two copies of the table** (root and eval README): fill both from the same workbook `Verdict Matrix` total row in
  the same edit; the judge mean is the `judge_mean` column average from the experiment.
- **`ExperimentResults.url`** exists in langsmith 0.14.12 (checked); keep the `getattr(..., None)` guard the replay
  already has.
- **git sha in metadata** is taken before the run; commit the docs after, so the sha points at the code that ran,
  not the docs commit. Fine; say "code at <sha>" in the README if quoted.

## 12. Open questions

All closed.

## Execution-discipline note

Read the whole plan first. Execute §9 in order; steps 1–5 are offline and each ends green. Step 6 spends money and has
two explicit stop points (6b nesting, any 429 or re-run). Do not narrow scope silently; on a hard blocker leave
`TODO(<area>): <blocker>` with a compiling stand-in and continue. Re-read §3 budgets and §4 inventory before each step;
exceeding a budget or touching an out-of-inventory file is a stop-and-ask, not a judgement call. Finish with the
fresh-context review in step 8.
