# Plan: binary eval verdicts (PASS / FAIL only)

## 1. Status

- v1, 2026-11-09. Supersedes the PASS / PARTIAL / FAIL axis verdicts and the 0-3 judge outputs agreed 2026-10-27
  (see [dispute_agent/evaluation/README.md](../../dispute_agent/evaluation/README.md) "The metrics").
- Decisions closed with Rachit on 2026-11-09 (§5). Not implemented yet.

## 2. Why & goal

**The bet:** a verdict that is either PASS or FAIL is a gate that means something. Today the six code axes have a
PARTIAL middle (worth 0.5) and the Opus judge outputs 0-3 with a 2.75/3 mean; neither says "ship / don't ship".

**Use case:** a reviewer opens [docs/results.md](../../docs/results.md) and sees, per complaint, green or red on every
code check and "judge checks 4/4" or "3/4" with a dot, with no 🟡 and no "2.75 / 3".

**This plan covers:** every *output* of the eval (CLI print, LangSmith feedback, the Excel workbook, the markdown
results page, `calibrate --check`) becomes binary. The judge's model call is unchanged: it still returns 0-3
internally, and code collapses that to PASS (3) / FAIL (0-2). No LLM call is made by this plan.

**Measured before planning (2026-11-09):**

- Release run `runs/eval_20261028T182643Z.json` has 0 PARTIAL verdicts; 12/16 grade PASS before and after.
- Its `before` run `runs/eval_20261028T155005Z.json` has one: S-07 `route` PARTIAL, which becomes FAIL.
- Binary judge-vs-human agreement on `runs/judge_20261027T180514Z.json` + `labels.xlsx` (PASS = 3):
  `no_invented_facts` 12/12, `matches_rationale` 10/12 (S-11, S-13), `ready_to_file` 10/12 (S-05, S-15),
  `no_leaks_or_promises` 12/12. All ≥ 0.75, so `calibrate --check` still exits 0.

## 3. Definition of Done

### Acceptance criteria (exit 0 is the only pass signal)

1. `make test` (= `.venv/bin/python -m pytest -q`)
2. `make typecheck` (= `pyright`)
3. No PARTIAL left in live code or current docs:
   `! grep -rn "PARTIAL\|partial verdict\|🟡" dispute_agent/ docs/results.md docs/project_brief.md --include=*.py --include=*.md`
   (docs/eval_history.md and docs/history/ are excluded on purpose: they are the historical record.)
4. Calibration still passes on the existing labels:
   `.venv/bin/python -m dispute_agent calibrate --check runs/judge_20261027T180514Z.json --labels dispute_agent/evaluation/labels.xlsx`
   and its printout shows agreement `1.0, 0.83, 0.83, 1.0` for the four axes in `JUDGE_AXES` order.
5. Results page regenerates with trace links and no fractional judge numbers:
   `.venv/bin/python -m dispute_agent report runs/eval_20261028T182643Z.json --judge runs/judge_20261028T183429Z.json --out docs/results.md && grep -q "12 of 16" docs/results.md && ! grep -q "/ 3 |" docs/results.md && grep -q "\[trace\]" docs/results.md`
6. Release workbook regenerates:
   `.venv/bin/python -m dispute_agent report runs/eval_20261028T182643Z.json --judge runs/judge_20261028T183429Z.json --before runs/eval_20261028T155005Z.json --labels dispute_agent/evaluation/labels.xlsx --out dispute_agent/evaluation/reports/release_2026-10-28_v0.10.xlsx`

### Budgets (exceeding any is stop-and-ask)

| Budget | Max |
|---|---|
| New files | 1 (this plan) |
| LOC per new file | n/a |
| New DB tables | 0 |
| New DB columns | 0 |
| New metric / event names | 0 net: exactly one rename, `judge_mean` → `judge_passed` |
| New dependencies | 0 |
| LLM / API calls during execution | 0 judge or agent calls (LangSmith read for trace links in AC 5 is allowed) |
| Net LOC change in `dispute_agent/` | ≤ +20 (this should shrink code, not grow it) |

### Must-handle edge cases

- **Judge error row** (`judge_error`): every judge axis shows `ERR`, not PASS or FAIL; `judge_passed` is `None` / blank.
- **No-draft / exception row**: judge gives score 0 on all four (existing `_no_draft`) → FAIL on all four, `0/4`.
- **Old judge sidecars and `labels.xlsx`** still hold 0-3 ints and must load unchanged (collapse happens on read).
- **Old eval JSONs** (pre-v0.8, no `policy_consistent`) still grade as today, minus PARTIAL.
- **`compare()`** improved / regressed still works with only two verdict values.
- **Not judged** (no `--judge`): judge columns blank, as today.

### Out of scope

- Not building: a binary judge prompt/schema, re-labelling the 12 notes, a new judge run, a new agent eval run,
  re-uploading feedback to the published LangSmith release experiment (it keeps its old 0.5 / x-of-3 feedback; the README says so in one line).
- Not editing: `docs/eval_history.md`, anything under `docs/history/`, the v0.8 / v0.9 release workbooks, `runs/*`.
- **Forbidden by name:** no new `Verdict` enum class or verdict module; no config flag to switch binary/ternary;
  no threshold constant other than the one `JUDGE_PASS = 3`; no new metric key beyond the `judge_passed` rename;
  no change to `JUDGE_SYSTEM` / `SCALE_TEXT` / `JudgeScores` / `AxisScore`; no new CLI flag; no new dependency.

## 4. File inventory (closed)

| Path | Action | Why this file owns the change |
|---|---|---|
| `dispute_agent/evaluation/metrics.py` | UPDATE | Owns `Verdict`, axis rules, grade, score, judge verdict/summary |
| `dispute_agent/evaluation/judge.py` | UPDATE | `agreement` / `calibrate_check` / `agreement_lines` move from within-1 to binary agreement; docstrings |
| `dispute_agent/evaluation/experiment.py` | UPDATE | LangSmith feedback values for code and judge axes |
| `dispute_agent/evaluation/report.py` | UPDATE | Workbook fills, matrix, judge panel, calibration sheet, markdown page |
| `dispute_agent/evaluation/__init__.py` | UPDATE | Module docstring mentions PARTIAL and 0-3 |
| `dispute_agent/evaluation/README.md` | UPDATE | Metric tables, scales, LangSmith note, calibration table |
| `docs/project_brief.md` | UPDATE | Lines 116 and 129 describe PARTIAL and 0-3 |
| `docs/results.md` | UPDATE (regenerated, never hand-edited) | Output of `report --out docs/results.md` |
| `dispute_agent/evaluation/reports/release_2026-10-28_v0.10.xlsx` | UPDATE (regenerated) | Linked from the eval README |
| `tests/test_metrics.py` | UPDATE | PARTIAL / score / judge_mean assertions |
| `tests/test_experiment.py` | UPDATE | Feedback value assertions |
| `tests/test_report.py` | UPDATE | Matrix / scenario / calibration sheet assertions |
| `tests/test_judge.py` | UPDATE | `agreement` keys and calibrate printout |
| `tests/test_golden.py` | UPDATE | empty_stub route expectation |
| `tasks/binary-eval-verdicts.md` | CREATE | This plan |

**Read first:**

- [metrics.py:1-134](../../dispute_agent/evaluation/metrics.py) (whole file)
- [judge.py:150-210](../../dispute_agent/evaluation/judge.py) (`read_labels` → `calibrate_check`)
- [experiment.py:15-25, 82-106](../../dispute_agent/evaluation/experiment.py)
- [report.py:26-46, 160-285, 356-395](../../dispute_agent/evaluation/report.py)
- [evaluation/README.md:40-60, 105-200](../../dispute_agent/evaluation/README.md)
- Tests: [test_metrics.py:1-85](../../tests/test_metrics.py), [test_experiment.py:90-105](../../tests/test_experiment.py),
  [test_report.py:45-55, 115-165, 215-240](../../tests/test_report.py), [test_judge.py:85-130](../../tests/test_judge.py),
  [test_golden.py:92-98](../../tests/test_golden.py)

## 5. Closed decisions (do not re-litigate)

1. **Judge collapses at output; the model still scores 0-3.** PASS iff score == 3, else FAIL; `None` → `ERR`.
   *Why not a binary prompt:* that needs a re-labelled `labels.xlsx` and a paid judge run, and invalidates every old
   sidecar; binary agreement on existing labels is already 0.83-1.00.
2. **Code-axis PARTIAL → FAIL.** An axis passes only when all its checks hold: `route` needs a draft that chose the
   right route; `evidence` needs `draft_events_ok`; `policy` needs `draft_policy_ok`. *Why not PASS:* "right only
   because there was no draft" is not a decision, and `test_empty_stub_fails` already treats an empty answer as failing.
3. **Grade** = PASS iff all six axes PASS, else FAIL (the PARTIAL branch disappears).
4. **`score` keeps its key and becomes a pass count**: value `"5/6"`, LangSmith score `5/6` as a float. With binary
   axes it is never fractional.
5. **`judge_mean` is renamed `judge_passed`**: number of the four judge axes that PASS, shown `"3/4"`; LangSmith score
   `3/4`. `None` when the judge errored or did not run. *Why not keep `judge_mean`:* same key with a new meaning
   would silently mix with old experiments in LangSmith.
6. **Calibration measures binary agreement**: per axis `n`, `agree` (share of labelled notes where judge and human give the
   same PASS/FAIL), `judge_pass`, `human_pass` (counts), and binary `disagreements` as `(sid, judge_score, human_score)`
   (raw scores kept so a reader sees *how* far apart). `calibrate --check` fails an axis with `n ≥ 6` and
   `agree < 0.75`. Constant `MIN_WITHIN_1` is renamed `MIN_AGREE` (same 0.75).
   *Why not keep within-1:* the verdict is binary now, so a 2-vs-3 split flips the outcome; within-1 would hide that.
7. **Raw 0-3 stays in `runs/judge_*.json` and `labels.xlsx`** (inputs, not outputs). The workbook judge panel shows
   the verdict, not the raw score; the reason and quote stay.
8. **`SCORE_VALUE` stays** as `{"PASS": 1.0, "FAIL": 0.0}` (used by `compare` and feedback).

## 6. Architecture / schema

Pure-function change inside `metrics.py`; every consumer reads through it.

```python
Verdict = Literal["PASS", "FAIL"]
METRIC_NAMES = AXES + ("grade", "score") + JUDGE_AXES + ("judge_passed",)  # still 13
SCORE_VALUE = {"PASS": 1.0, "FAIL": 0.0}
JUDGE_PASS = 3  # a judge axis passes only on a full score

def axis_verdicts(checks):
    # route: PASS iff route_ok and a draft exists
    # evidence: PASS iff draft and required_found and forbidden_clear and draft_events_ok
    # policy: PASS iff draft and policy_retrieved_ok and policy_cited_ok and policy_forbidden_clear and draft_policy_ok
    # disputed_event / confidence / guardrails: unchanged
def grade(v) -> Verdict:            # "PASS" if all PASS else "FAIL"
def score_value(v) -> int:          # count of PASS
def score_str(v) -> str:            # f"{score_value(v)}/{len(AXES)}"
def judge_verdict(score) -> str:    # "ERR" if None, "PASS" if score >= JUDGE_PASS else "FAIL"
def judge_passed(scores) -> tuple[int, int] | None:  # (passes, 4); None if every score is None
```

`judge_mean` is deleted (replaced by `judge_passed`).

Schema / metric count against budgets: 0 tables, 0 columns, 0 new metric names, 1 rename (`judge_mean` → `judge_passed`).
Workbook column rename `Judge mean` → `Judge passed`; calibration columns `exact, within-1, judge mean, human mean`
→ `agree, judge PASS, human PASS`.

## 7. API surface

- **LangSmith feedback** (`experiment.py`):
  - code axes and `grade`: `{"key", "score": 1.0|0.0, "value": "PASS"|"FAIL"}`
  - `score`: `{"score": passes/6, "value": "5/6"}`
  - judge axes: `{"score": 1.0|0.0|None, "value": "PASS"|"FAIL"|"ERR", "comment": f"{raw}/3: {reason}"}` (raw
    score kept in the comment for traceability; error comment unchanged)
  - `judge_passed`: `{"score": passes/4 or None, "value": "3/4" or None}`
- **CLI**: `judge` per-row print shows `PASS`/`FAIL`/`ERR` per axis instead of the int. `calibrate --check` prints
  `axis n agree judge-PASS human-PASS` and `below 75% agreement: ...`. Exit codes unchanged (0 / 1 / 2).
- **Markdown page**: legend `🟢 pass · 🔴 fail`; column `Note quality` → `Note checks`, cell `🟢 4/4` when all four
  pass, `🔴 3/4` otherwise, `—` when not judged. `DOTS` loses `PARTIAL`.
- **Workbook**: `FILLS` loses `PARTIAL`; matrix judge cells hold `PASS`/`FAIL`/`ERR`; `total` row gives judge
  axes as PASS counts `"%d/%d"` and `Judge passed` as the count of scenarios with 4/4.

## 8. Migration plan

None. No stored data changes; old `runs/*.json`, `labels.xlsx` and `golden.xlsx` are read as-is. Rollback = `git revert`.

## 9. Ordered steps (verify each before the next)

1. **metrics.py** per §6. Update `test_metrics.py` and `test_golden.py`. Verify: `pytest tests/test_metrics.py tests/test_golden.py -q`.
2. **judge.py**: `agreement`, `agreement_lines`, `calibrate_check`, `MIN_AGREE`, the per-row print in `judge_report`
   (use `judge_verdict`), docstrings (module, `read_labels` stays "0-3", it reads raw labels). Do **not** touch
   `SCALE_TEXT`, `JUDGE_SYSTEM`, `AxisScore`, `JudgeScores`. Update `test_judge.py`. Verify: `pytest tests/test_judge.py -q`, then AC 4.
3. **experiment.py** per §7. Update `test_experiment.py`. Verify: `pytest tests/test_experiment.py -q`.
4. **report.py**: imports, `FILLS`, `MATRIX_COLS`, `matrix_row`, `write_matrix` (fill + totals), judge panel header
   `["Judge", "verdict", "reason", "quote"]` with `judge_verdict(score)` as the cell, `write_calibration`, `DOTS`,
   `write_markdown` legend and `Note checks` cell. `build_labels` docstring keeps "0-3" (hand labels stay raw).
   Update `test_report.py`. Verify: `pytest tests/test_report.py -q`.
5. `make test && make typecheck` (AC 1, 2).
6. **Regenerate** AC 5 and AC 6 outputs. If AC 5 prints "no trace links", STOP and ask (the key in `.env` must be
   loaded; do not commit a results page without links).
7. **Docs**: `evaluation/__init__.py`, `evaluation/README.md` (drop the PARTIAL column from the axis table and
   restate each axis's PASS rule; grade and score lines; judge "Verdict: 3 PASS, 0-2 FAIL"; LangSmith paragraph incl.
   one line that the published release experiment predates this change and still shows 0.5 / fractions of 3;
   calibration table with the AC 4 numbers and the four binary disagreements, all "judge stricter"; S-06 no longer
   disagrees since 2 and 1 are both FAIL), `docs/project_brief.md:116,129`. Verify AC 3.
8. Fresh-context review of the diff against §3 and §5, then hand back. Do not commit unless Rachit asks.

## 10. Tests (each maps to an AC)

All in existing files; no new test files. All run under AC 1.

| Test | Change | AC |
|---|---|---|
| `test_metrics.py` parametrized axis cases (lines ~27-35) | `"PARTIAL"` → `"FAIL"` | 1, 3 |
| `test_metrics.py` mixed-row test (~56-66) | route `"FAIL"`; `score_str` `"2.5/6"` → recount (`"2/6"`); the `grade == "PARTIAL"` / `"5.5/6"` case → `"FAIL"` / `"5/6"` | 1 |
| `test_metrics.py` judge test (~76-81) | `judge_verdict(3,2,1,0,None)` → `PASS,FAIL,FAIL,FAIL,ERR`; `judge_passed` asserts replace `judge_mean` (e.g. scores 0,1,2,3 → `(1, 4)`; error → `None`) | 1 |
| `test_metrics.py` `METRIC_NAMES` | ends with `judge_passed`, still 13 | 1 |
| `test_golden.py::test_empty_stub_fails` | `verdicts["route"] == "FAIL"` for every case | 1 |
| `test_golden.py::test_golden_echo_all_pass_16` | unchanged; must still pass (scorer calibration) | 1 |
| `test_experiment.py` ~96-102 | `score` value `"6/6"` unchanged; judge axis value `"FAIL"`, score `0.0`, comment `"1/3: r"`; last item key `judge_passed`, value per fixture | 1 |
| `test_report.py` ~122, 158, 161 | PARTIAL → FAIL; `Score` `"2.5/6"` → recount | 1 |
| `test_report.py` ~238 | calibration header `("axis","n","agree","judge PASS","human PASS")` and first row per fixture | 1 |
| `test_judge.py` ~96, 105-106, 128 | `agreement` keys `n, agree, judge_pass, human_pass, disagreements`; printout `below 75% agreement: none` | 1, 4 |
| A new assertion inside an existing `test_report.py` markdown test (or the closest existing one) | page contains no `🟡` and no `/ 3` | 3, 5 |

Recompute every expected number from the fixture; never copy a number from this table without checking.

## 11. Edge cases / footguns

- `fill()` in `write_matrix` maps ints through `judge_verdict`; once cells hold strings this branch is dead. Remove it rather than leave it.
- `bool` is an `int` subclass; keep any `isinstance(..., int)` guards that exclude `bool` if a branch survives.
- `judge_passed` must return `None` (not `(0, 4)`) for an errored row, or an `ERR` scenario renders as a real 0/4 FAIL.
- `_no_draft` scores 0 → FAIL is intended; do not special-case it.
- `compare()` delta: with two values, `va == "PASS"` ⇔ improved. Using `SCORE_VALUE` still works; do not rewrite.
- `report --out docs/results.md` calls LangSmith for public links; without `LANGSMITH_API_KEY` loaded the page silently drops links (§9 step 6 stop rule).
- `labels.xlsx` has a lock file `~$labels.xlsx` in the folder; it is not ours and must not be touched or committed.
- pyright: changing `Verdict` narrows a `Literal`; `matrix_row` / `write_markdown` mix `Verdict` with `"PENDING"`
  via `Mapping[str, str]`; keep those annotations.

## 12. Open questions

All closed.

## Execution-discipline note

Read the whole plan first. Re-read §3 Budgets and §4 File inventory before each step. Needing a file not in §4, a
new metric key, or any LLM call is a STOP-and-ask. Do not commit or push unless asked.
