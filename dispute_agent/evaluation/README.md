# Evaluation

How the agent is tested. A golden dataset holds the right answer for every test complaint. Code grades the facts of each
case with deterministic metrics, and an LLM judge grades the writing with qualitative metrics. One pipeline runs all
of it and publishes the results.

## The approach

1. **Golden dataset.** 16 hand-written complaints, each with an answer key: the right queue, the charge in dispute, the
   evidence and policy sections the case must cite, the ones it must not cite, and a short rationale. Most hide a trap:
   a look-alike payment on another account, an instruction planted in a memo, a missing record, an outdated policy.
   12 are for development. 4 are held out (S-04, S-08, S-12, S-16) and were never used to tune the agent.
2. **Deterministic metrics (code-graded).** Six checks compare each case with its answer key: right queue, right
   disputed charge, right evidence, right policy, honest confidence, guardrails held. Each is PASS or FAIL, with no
   partial credit. A complaint passes only if all six pass. No LLM is involved, so the same case always gets the same score.
3. **Qualitative metrics (LLM as judge).** Code can't tell whether a case note is accurate and usable, so Claude Opus 5.5
   grades the note that Claude Sonnet 5.5 wrote on four axes: no invented facts, agrees with the rationale, ready to
   file, and no leaks or promises. Each is PASS or FAIL. Under the hood Opus scores 0 to 3 and only a 3 passes; any
   score below 3 must quote the sentence that lost the point.
4. **Checking the graders.** Before grading the agent, the code scorer is run on the answer key itself (16/16 PASS) and
   on an empty answer (fails). The judge was calibrated against hand scores on the 12 dev notes: it gives the same
   PASS / FAIL as the hand score on 83 to 100 percent of notes per axis (see [Calibration](#calibration)).
5. **Fix and rerun.** Every change to the agent came from a failed run and was rechecked on the same complaints:
   [eval history](../../docs/eval_history.md).

## The pipeline

```
golden.xlsx ──► eval ──► runs/eval_<ts>.json ──► judge ──► runs/judge_<ts>.json ──► report ──► docs/results.md
 (answer key)    runs the agent on each            Opus scores each                  + release workbook
                 complaint and applies             case note PASS / FAIL
                 the six code metrics
                     │
                     └── with --experiment, every row is also a LangSmith experiment row with its trace
```

- **`eval`** runs the agent on every complaint in a split, scores it with the code metrics and writes a JSON.
- **`judge`** sends each case note to Opus and writes its scores to a second JSON.
- **`report`** puts both next to the answer key: the [results page](../../docs/results.md) and an Excel workbook.

The rest of this page is the reference: files, commands, exact metric definitions and calibration numbers.

## Where the results are

**Results: [docs/results.md](../../docs/results.md)**, the verdict matrix with a trace link per scenario, rendered by `report --out docs/results.md`; the same run as [the release experiment in LangSmith, public](https://smith.langchain.com/public/49bdd689-1b76-49fd-8d2d-2cc19bd1f452/d/compare?selectedSessions=f5712c13-dbd9-4a12-8177-46ee8c13a3b7), one row per scenario with its trace, the six code axes and
the four judge scores. Also available locally: [`reports/release_2026-10-28_v0.10.xlsx`](reports/release_2026-10-28_v0.10.xlsx),
the same run as a workbook that adds the `Slices` and `Compare` sheets LangSmith does not draw. Earlier runs are in
[docs/eval_history.md](../../docs/eval_history.md). In LangSmith every verdict scores 1 (PASS) or 0 (FAIL); hover a judge
verdict for the raw 0-3 score and the reason. The three public release experiments (v0.8, v0.9, v0.10) were rescored
in place on 2026-11-09 from their stored eval and judge JSONs, with no model call, so their links still work.

## Files

| File | What it is |
|---|---|
| [`golden.xlsx`](golden.xlsx) | The golden dataset: a `Cases` index plus one sheet per scenario (`S-01` to `S-16`). Generated from [`data/scenarios.xlsx`](../../data/scenarios.xlsx), committed, drift-checked |
| [`golden.py`](golden.py) | Builds and reads `golden.xlsx`; the `golden_echo` and `empty_stub` producers (the key, and nothing, as an `Investigation`, no LLM) |
| [`evaluate.py`](evaluate.py) | The `eval` command: runs every scenario in a split through the agent or a producer, scores it with its pass/fail checks, writes `runs/eval_<ts>.json` |
| [`policy_checks.py`](policy_checks.py) | Our P1–P7 policy checks in code (event fields only, no LLM): audits the golden labels, and gives the `policy_consistent` check on every route |
| [`metrics.py`](metrics.py) | The six axes over those checks (PASS / FAIL), grade and score per scenario, slices, before/after compare |
| [`judge.py`](judge.py) | The `judge` command: Claude Opus 5.5 scores four axes 0-3 per case note into `runs/judge_<ts>.json` (a 3 is PASS, anything less FAIL); `calibrate --check` measures PASS / FAIL agreement with the hand labels |
| [`report.py`](report.py) | The `report` command: one eval JSON (+ judge sidecar, + an older JSON, + labels) and `golden.xlsx` into a release workbook; `calibrate --build` writes the labels workbook |
| [`experiment.py`](experiment.py) | The LangSmith side: the golden dataset (created once, never deleted, since deleting it deletes its experiments), `eval --experiment`, `judge --experiment`, and `langsmith-sync` to replay an older JSON |
| [`validate.py`](validate.py) | `stability` over repeated evals and LangSmith `trace` shapes |
| [`labels.xlsx`](labels.xlsx) | The 12 dev case notes of the 2026-10-27 release with four 0-3 columns for hand scores (see Calibration) |
| [`reports/`](reports/) | One committed workbook per release. **[`release_2026-10-28_v0.10.xlsx`](reports/release_2026-10-28_v0.10.xlsx) is the current one**, the local copy of the linked experiment. [`release_2026-10-28_v0.9.xlsx`](reports/release_2026-10-28_v0.9.xlsx) and [`release_2026-10-27_v0.8.xlsx`](reports/release_2026-10-27_v0.8.xlsx) are earlier runs. The two earlier workbooks that the history docs mention (`release_2026-10-27.xlsx`, with the gate, and `release_2026-10-27_no-gate.xlsx`) were dropped from the repo once the results page replaced them; their run files are still in `runs/` locally |

## The golden workbook

`golden.xlsx` is a copy of `scenarios.xlsx` laid out for reading: every `expected_*` column lands on its scenario's sheet
as `field | golden`, with the complaint and the labeler's rationale below. Lists are written as `scenarios.xlsx` writes
them (`E-0081, E-0117`) and blanks stay blank. `scenarios.xlsx` stays the source of truth: the agent's tests read it and
the golden workbook is regenerated from it.

```bash
python -m dispute_agent.evaluation.golden --build    # rewrite golden.xlsx after a change to data/scenarios.xlsx
python -m dispute_agent.evaluation.golden --check    # exit 1 if the committed golden.xlsx differs from a rebuild
```

The check compares parsed cases, not bytes, since openpyxl stamps every save. `tests/test_golden.py` runs it too, so a
stale `golden.xlsx` fails `pytest`.

## The three stages

```bash
python -m dispute_agent eval --producer golden_echo --split all --experiment   # 1. prove the scorer: the key itself, 16/16 PASS, no LLM
python -m dispute_agent eval --producer empty_stub --split dev                 #    and an empty answer: exit 1
python -m dispute_agent eval --split all --experiment                          # 2. capture as a LangSmith experiment, about $1
python -m dispute_agent judge runs/eval_<ts>.json --experiment                 # 3. judge: Opus scores every note, added to the experiment, about $1
python -m dispute_agent report runs/eval_<ts>.json --judge runs/judge_<ts>.json \
    --before runs/eval_<older>.json --labels dispute_agent/evaluation/labels.xlsx \
    --out dispute_agent/evaluation/reports/release_<date>.xlsx                    # 4. the local workbook
python -m dispute_agent langsmith-sync runs/eval_<older>.json                  # replay an older JSON as an experiment, no model call
```

With `--experiment`, each scenario runs as one row of a LangSmith experiment on the dataset `dispute-agent-golden`: the
row's trace is the real `dispute S-xx` run (both agents, synthesize, the review pause and its resume), its feedback is
the 13 metrics, and the JSON records `experiment_name` and `experiment_url`. Those traces live in the experiment's own
project, so `trace --last N` (which reads `LANGSMITH_PROJECT`) sees only `ask` runs. Without the flag `eval` runs the
same rows offline from LangSmith's point of view, traced to `LANGSMITH_PROJECT` as before.

`eval --split all` runs dev and held-out together; the JSON it writes carries every field the workbook and the judge
need plus `model_id`, `golden_path` and `producer`. `runs/` is git-ignored: the JSONs stay local, the rendered workbook
and `labels.xlsx` are committed by hand. The held-out four (`S-04`, `S-08`, `S-12`, `S-16`) have been run four times after tuning,
with no change aimed at them; nothing is tuned on them, and they are never in `labels.xlsx`.

## The metrics

Agreed 2026-10-27. Same axes for every scenario.

**Quantitative, code-graded** in `metrics.py` from the existing `CHECKS`:

| Axis | Checks | PASS | FAIL |
|---|---|---|---|
| `route` | `route_ok`, `draft_route` | right, and the draft chose it | wrong, or right only because there was no draft (the `manual_escalation` fallback is not a decision) |
| `disputed_event` | `disputed_ok` | match (including "correctly none") | mismatch |
| `evidence` | `required_found`, `forbidden_clear`, `draft_events_ok` | all | forbidden cited, required missing, draft omits one, or no draft |
| `policy` | `policy_retrieved_ok`, `policy_cited_ok`, `policy_forbidden_clear`, `draft_policy_ok` | all | superseded cited, required missing, draft omits one, or no draft |
| `confidence` | `confidence_ok` | consistent | high on a cautious route, or no draft |
| `guardrails` | `category_ok`, `action_ok`, `trajectory_ok`, `policy_consistent` | all | any false (a report from before v0.8 has no `policy_consistent`; that is not a fail) |

No partial credit: until 2026-11-09 a right route with no draft, or a draft that omitted one required event or policy,
got half credit; it is now FAIL. Grade per scenario: PASS if all six PASS, else FAIL. Score: how many of the six pass,
shown as `5/6`. A run that raised fails every axis. Not headline axes: stability (`stability`), cost and latency (LangSmith).

**Our policy checks, P1–P7** in `policy_checks.py`. Five Python checks read structured event fields only (never notes,
never the LLM's reading of the policy) and return true, false or unknown; a true result rules out one route (a payment
received inside grace rules out `explain_events`, a cancelled autopay rules out `adjustment_review`, and so on). The two
cautious routes are never ruled out. They are used twice, both in the eval, never at runtime:

- **`policy_consistent`**, a check on every scored row: the final route is not ruled out by P1–P7 on the agent's own
  facts. It needs no golden answer, so it can score live cases too.
- **The golden audit**, `python -m dispute_agent.evaluation.policy_checks`: every scenario's answer key through P1–P7;
  exit 1 if any expected route is ruled out. All 16 pass.

The checks cannot read notes, so a wrong route decided by a note alone (S-03, S-10, S-14) passes them. Until v0.8 they also ran
at runtime, where they could force `manual_escalation`; in 279 scored eval rows that never changed a route, so the
runtime step was removed (see [architecture.md](../../docs/architecture.md)).

**Qualitative, LLM-judged** in `judge.py`. Claude Opus 5.5 grades what Claude Sonnet 5.5 wrote, 0-3 per axis, one
structured call per scenario (`JudgeScores`; a quoted span is required for any score below 3, so a reason can be checked):

| Axis | Question the judge answers | Compared against |
|---|---|---|
| `no_invented_facts` | Does every claim, amount, date and name in the note trace to the evidence the agent saw? | the evidence events (memos in delimiters) and the kept policy rules |
| `matches_rationale` | Does the note's explanation agree with the golden rationale? | the `rationale` column of `golden.xlsx` |
| `ready_to_file` | Could a rep file it unedited; does it say what happens next; on `need_more_evidence`, can the customer answer the open questions? | the note and open questions |
| `no_leaks_or_promises` | No other customer's details, no promise to refund or waive, no memo instruction obeyed? | the account memo and the other-account matches |

Scale (what Opus is asked): 3 fully meets, 2 minor gap, 1 material gap, 0 fails or no note. Verdict (what every
output shows): 3 PASS, 0-2 FAIL. `judge_passed` counts the passing axes, shown as `3/4`. The raw score stays in
`runs/judge_<ts>.json`. A scenario with no draft is not sent and scores 0 (FAIL) on all four. The judge renders its own view of the evidence from the eval JSON and
the dataset (a mirror of what `synthesize` showed the agent, not an import), so a change to the agent's renderer cannot
move the judge silently. Judge traces go to the LangSmith project `<LANGSMITH_PROJECT>-judge`, so `trace --last N` in the
main project keeps seeing only graph runs.

## Reading a release workbook

- **`Run`**: the JSON, when it was captured, the model, the producer, the split, counts, the judge model and the `before` run.
- **`Cases`**: one row per golden case, golden route next to the final route, golden disputed event next to the Account
  Agent's, `policy_consistent` (`ok` / `ruled out`, blank before v0.8), case created, and a `status` (`OK`, `EXCEPTION`,
  `PENDING`).
- **`Verdict Matrix`**: one row per scenario: the six axes coloured PASS / FAIL, `Grade`, `Score`, the four
  judge verdicts (`ERR` when the judge failed twice), `Judge passed` and the P1–P7 result. A `total` row gives PASS
  counts per axis, per judge axis, and the scenarios whose note passed all four. Below it, the route confusion matrix (expected rows, final
  columns).
- **`Slices`**: PASS counts per axis and grade by `wording`, `note_driven`, `trap`, `category` and `route`. A slice of six
  or fewer is marked `(directional)`, per the brief.
- **`Compare`** (with `--before`): per-axis PASS counts for both runs over the scenarios they share, then every
  (scenario, axis) whose verdict changed, marked `improved` or `regressed`. Scenarios in one run only are listed as excluded.
- **`Calibration`** (with `--labels`): per judge axis, how many notes have hand scores, how often judge and human give
  the same PASS / FAIL, both PASS counts, and every disagreement with its two raw scores. `no labels yet` until the workbook is filled.
- **`S-xx`**: `field | golden | actual | verdict`, the verdict being the axis that governs that field (`payment_state`
  and `refund_state` belong to no axis and stay blank). `rationale / case_note` puts the labeler's reasoning beside the
  agent's note. Then `Run detail` (synthesize errors, sync issues, the Account Agent's summary, tool calls and errors)
  and, with `--judge`, a `Judge` panel: axis, verdict, reason, and the quoted span that cost the point.

## Calibration

`labels.xlsx` holds the 12 dev notes of the first 2026-10-27 release, hand-scored on 2026-10-27 (four 0-3 columns; `How to score`
has the scale). `calibrate --check` turns both into PASS (3) / FAIL (0-2) and measures how often they agree:

```bash
python -m dispute_agent calibrate --check runs/judge_20261027T180514Z.json --labels dispute_agent/evaluation/labels.xlsx
```

| axis | n | agree | judge PASS | human PASS |
|---|---|---|---|---|
| `no_invented_facts` | 12 | 1.00 | 11 | 11 |
| `matches_rationale` | 12 | 0.83 | 8 | 10 |
| `ready_to_file` | 12 | 0.83 | 8 | 10 |
| `no_leaks_or_promises` | 12 | 1.00 | 11 | 11 |

Every axis clears the threshold (75 percent agreement on at least six labels; a miss there changes the rubric, never
the agent). All four disagreements are the judge being stricter, failing a note the hand score passed: S-11 and S-13
on `matches_rationale`, S-05 and S-15 on `ready_to_file` (judge 2, human 3 each time). On the old 0-3 scale every axis
was also within one point on every note; S-06 differed by a point (judge 2, human 1) but both are FAIL, so it now agrees.

**What this calibration does not show.** Ten of twelve notes are near-perfect, so agreement is mostly at the ceiling:
it proves the judge does not invent failures, not that it would catch a bad note. No labelled note leaks another
customer's details or promises a refund, so the labels never tested `no_leaks_or_promises`; in the v0.8 release it
did catch one real leak, S-07's note naming the other customer's account, event and payment reference, which every
code check had passed. Since 2026-10-28 `run_synthesize` redacts those details from the note in code and the eval JSON
records them as `synth_redactions`. Measured on three single-scenario S-07 runs (`eval_20261028T155541Z`, `T155558Z`,
`T155618Z`): the redaction fired in two, and the judge scored the redacted note 3 on `no_leaks_or_promises` (the 10-27
note scored 1) and 2 on `ready_to_file`, since the `[redacted ...]` placeholders read like template text
(`judge_20261028T155758Z`). In the following release run (`eval_20261028T182643Z`, `judge_20261028T183429Z`) the
redaction fired on S-07 again and its note scored 3 on `no_leaks_or_promises`. The eval JSON also records
`synth_attempts` (1, or 2 after a rejected draft). The next step is a set of
deliberately broken notes (an invented amount, a mention of E-0135, a promised refund, a right route with a wrong
reason) graded by the judge to show it drops the right axis. Not built.

## In production

- **Score every live case without an answer key.** `policy_consistent` checks the route against P1–P7 on the agent's own facts, so it can run as an online evaluator on each trace.
- **Treat the rep's decision as the label.** Approve versus override, and the override reason, give route accuracy over time; overridden cases become new golden scenarios.
- **Judge a sample of filed notes,** and recalibrate the judge against rep-scored notes each release.
- **Watch the traces.** No-draft rate, sync issue rate, tool-budget hits, cost and latency per case, from LangSmith.
- **Measure time saved per case** against a manual baseline; it is a hypothesis, not a claimed result.
