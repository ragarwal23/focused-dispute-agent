# Evaluation: golden workbook, metrics, judge, release workbook

Three stages: **`eval`** captures a run of every scenario into a JSON, **`judge`** scores
each case note with a second model into a sidecar JSON, **`report`** renders both next to the golden answers as one
release workbook. The same axes score every scenario; only the golden values differ. Before any model is graded the
scorer is proven on the key itself (`eval --producer golden_echo`, 16/16 PASS) and on an empty answer (`empty_stub`,
exit 1).

**Results: [docs/results.md](../../docs/results.md)**, the verdict matrix with a trace link per scenario, rendered by `report --out docs/results.md`; the same run as [the release experiment in LangSmith, public](https://smith.langchain.com/public/49bdd689-1b76-49fd-8d2d-2cc19bd1f452/d/compare?selectedSessions=f5712c13-dbd9-4a12-8177-46ee8c13a3b7), one row per scenario with its trace, the six code axes and
the four judge scores. Also available locally:
[`reports/release_2026-10-28_v0.10.xlsx`](reports/release_2026-10-28_v0.10.xlsx), the same run as a workbook that adds the
`Slices` and `Compare` sheets LangSmith does not draw. Its `Compare` is against the morning run of 2026-10-28 ([experiment](https://smith.langchain.com/public/49bdd689-1b76-49fd-8d2d-2cc19bd1f452/d/compare?selectedSessions=0cb67e9d-df79-48fe-a951-19f69515bf77), [`release_2026-10-28_v0.9.xlsx`](reports/release_2026-10-28_v0.9.xlsx)); both score 12 of 16, but S-07 passes and S-06 fails in the later one. The README has all three release runs side by side.
PASS and shows 0-3.

## Files

| File | What it is |
|---|---|
| [`golden.xlsx`](golden.xlsx) | The golden dataset: a `Cases` index plus one sheet per scenario (`S-01` to `S-16`). Generated from [`data/scenarios.xlsx`](../../data/scenarios.xlsx), committed, drift-checked |
| [`golden.py`](golden.py) | Builds and reads `golden.xlsx`; the `golden_echo` and `empty_stub` producers (the key, and nothing, as an `Investigation`, no LLM) |
| [`evaluate.py`](evaluate.py) | The `eval` command: runs every scenario in a split through the agent or a producer, scores it with its pass/fail checks, writes `runs/eval_<ts>.json` |
| [`policy_checks.py`](policy_checks.py) | Our P1–P7 policy checks in code (event fields only, no LLM): audits the golden labels, and gives the `policy_consistent` check on every route |
| [`metrics.py`](metrics.py) | The six axes over those checks (PASS / PARTIAL / FAIL), grade and score per scenario, slices, before/after compare |
| [`judge.py`](judge.py) | The `judge` command: Claude Opus 5.5 scores four axes 0-3 per case note into `runs/judge_<ts>.json`; `calibrate --check` measures its agreement with the hand labels |
| [`report.py`](report.py) | The `report` command: one eval JSON (+ judge sidecar, + an older JSON, + labels) and `golden.xlsx` into a release workbook; `calibrate --build` writes the labels workbook |
| [`experiment.py`](experiment.py) | The LangSmith side: the golden dataset (created once, never deleted, since deleting it deletes its experiments), `eval --experiment`, `judge --experiment`, and `langsmith-sync` to replay an older JSON |
| [`validate.py`](validate.py) | `stability` over repeated evals and LangSmith `trace` shapes |
| [`labels.xlsx`](labels.xlsx) | The 12 dev case notes of the 2026-10-27 release with four empty 0-3 columns for Rachit to score (see Calibration) |
| [`reports/`](reports/) | One committed workbook per release. **[`release_2026-10-28_v0.10.xlsx`](reports/release_2026-10-28_v0.10.xlsx) is the current one**, the local copy of the linked experiment. The two before it are [`release_2026-10-28_v0.9.xlsx`](reports/release_2026-10-28_v0.9.xlsx) (redaction, no redraft) and [`release_2026-10-27_v0.8.xlsx`](reports/release_2026-10-27_v0.8.xlsx). The two earlier workbooks that the history docs mention (`release_2026-10-27.xlsx`, with the gate, and `release_2026-10-27_no-gate.xlsx`) were dropped from the repo once the results page replaced them; their run files are still in `runs/` locally |

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

| Axis | Checks | PASS | PARTIAL | FAIL |
|---|---|---|---|---|
| `route` | `route_ok`, `draft_route` | right, and the draft chose it | right only because there was no draft (the `manual_escalation` fallback) | wrong |
| `disputed_event` | `disputed_ok` | match (including "correctly none") | — | mismatch |
| `evidence` | `required_found`, `forbidden_clear`, `draft_events_ok` | all | required found, none forbidden, draft omits one | forbidden cited, required missing, or no draft |
| `policy` | `policy_retrieved_ok`, `policy_cited_ok`, `policy_forbidden_clear`, `draft_policy_ok` | all | retrieved and cited, draft omits one | superseded cited, required missing, or no draft |
| `confidence` | `confidence_ok` | consistent | — | high on a cautious route, or no draft |
| `guardrails` | `category_ok`, `action_ok`, `trajectory_ok`, `policy_consistent` | all | — | any false (a report from before v0.8 has no `policy_consistent`; that is not a fail) |

Grade per scenario: PASS if all PASS; PARTIAL if no FAIL; else FAIL. Score: PASS 1, PARTIAL 0.5, FAIL 0, shown as
`5.5/6`. A run that raised fails every axis. Not headline axes: stability (`stability`), cost and latency (LangSmith).

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

Scale: 3 fully meets, 2 minor gap, 1 material gap, 0 fails or no note. Verdict: 3 PASS, 2 PARTIAL, 0-1 FAIL. A scenario
with no draft is not sent and scores 0 on all four. The judge renders its own view of the evidence from the eval JSON and
the dataset (a mirror of what `synthesize` showed the agent, not an import), so a change to the agent's renderer cannot
move the judge silently. Judge traces go to the LangSmith project `<LANGSMITH_PROJECT>-judge`, so `trace --last N` in the
main project keeps seeing only graph runs.

## Reading a release workbook

- **`Run`**: the JSON, when it was captured, the model, the producer, the split, counts, the judge model and the `before` run.
- **`Cases`**: one row per golden case, golden route next to the final route, golden disputed event next to the Account
  Agent's, `policy_consistent` (`ok` / `ruled out`, blank before v0.8), case created, and a `status` (`OK`, `EXCEPTION`,
  `PENDING`).
- **`Verdict Matrix`**: one row per scenario: the six axes coloured PASS / PARTIAL / FAIL, `Grade`, `Score`, the four
  judge scores coloured by their verdict (`ERR` when the judge failed twice), the judge mean and the P1–P7 result. A
  `total` row gives PASS counts per axis and judge means. Below it, the route confusion matrix (expected rows, final
  columns).
- **`Slices`**: PASS counts per axis and grade by `wording`, `note_driven`, `trap`, `category` and `route`. A slice of six
  or fewer is marked `(directional)`, per the brief.
- **`Compare`** (with `--before`): per-axis PASS counts for both runs over the scenarios they share, then every
  (scenario, axis) whose verdict changed, marked `improved` or `regressed`. Scenarios in one run only are listed as excluded.
- **`Calibration`** (with `--labels`): per judge axis, how many notes Rachit has scored, exact and within-1 agreement,
  both means, and every disagreement. `no labels yet` until the workbook is filled.
- **`S-xx`**: `field | golden | actual | verdict`, the verdict being the axis that governs that field (`payment_state`
  and `refund_state` belong to no axis and stay blank). `rationale / case_note` puts the labeler's reasoning beside the
  agent's note. Then `Run detail` (synthesize errors, sync issues, the Account Agent's summary, tool calls and errors)
  and, with `--judge`, a `Judge` panel: axis, score, reason, and the quoted span that cost the point.

## Calibration

`labels.xlsx` holds the 12 dev notes of the first 2026-10-27 release, scored by Rachit on 2026-10-27 (four 0-3 columns; `How to score`
has the scale). `calibrate --check` compares those scores to the judge's:

```bash
python -m dispute_agent calibrate --check runs/judge_20261027T180514Z.json --labels dispute_agent/evaluation/labels.xlsx
```

| axis | n | exact | within-1 | judge mean | human mean |
|---|---|---|---|---|---|
| `no_invented_facts` | 12 | 1.00 | 1.00 | 2.75 | 2.75 |
| `matches_rationale` | 12 | 0.75 | 1.00 | 2.50 | 2.58 |
| `ready_to_file` | 12 | 0.83 | 1.00 | 2.50 | 2.67 |
| `no_leaks_or_promises` | 12 | 1.00 | 1.00 | 2.75 | 2.75 |

Every axis is within one point on every note, so no rubric needs rewording (the threshold is 75 percent within-1 on at
least six labels; a miss there changes the rubric, never the agent). The five disagreements are all one point: the
judge is stricter on S-11 and S-13 (`matches_rationale`, judge 2 / human 3) and on S-05 and S-15 (`ready_to_file`,
judge 2 / human 3), and Rachit is stricter on S-06 (`matches_rationale`, judge 2 / human 1: right route for the wrong
reason, the note calls the "77" reference matches coincidental where the key says it belongs to another customer).

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
