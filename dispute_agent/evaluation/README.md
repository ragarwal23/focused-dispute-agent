# Evaluation: golden workbook, metrics, judge, release workbook

Three stages: **`eval`** captures a run of every scenario into a JSON, **`judge`** scores
each case note with a second model into a sidecar JSON, **`report`** renders both next to the golden answers as one
release workbook. The same axes score every scenario; only the golden values differ. Before any model is graded the
scorer is proven on the key itself (`eval --producer golden_echo`, 16/16 PASS) and on an empty answer (`empty_stub`,
exit 1).

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
| [`validate.py`](validate.py) | `stability` over repeated evals, LangSmith `trace` shapes, and `langsmith-sync`: the golden dataset and the release experiment |
| [`labels.xlsx`](labels.xlsx) | The 12 dev case notes of the 2026-10-27 release with four empty 0-3 columns for Rachit to score (see Calibration) |
| [`reports/`](reports/) | One committed release workbook per release: [`release_2026-10-27.xlsx`](reports/release_2026-10-27.xlsx), then [`release_2026-10-27_no-gate.xlsx`](reports/release_2026-10-27_no-gate.xlsx) |

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
python -m dispute_agent eval --producer golden_echo --split all     # 1. prove the scorer: the key itself, 16/16 PASS, no LLM
python -m dispute_agent eval --producer empty_stub --split dev      #    and an empty answer: exit 1
python -m dispute_agent eval --split all                            # 2. capture: one pass over all 16 scenarios, about $1
python -m dispute_agent judge runs/eval_<ts>.json                   # 3. judge: Opus scores every case note, about $1
python -m dispute_agent report runs/eval_<ts>.json --judge runs/judge_<ts>.json \
    --before runs/eval_<older>.json --labels dispute_agent/evaluation/labels.xlsx \
    --out dispute_agent/evaluation/reports/release_<date>.xlsx            # 4. render the release workbook
python -m dispute_agent langsmith-sync runs/eval_<ts>.json --judge runs/judge_<ts>.json   # 5. dataset + experiment
```

`eval --split all` runs dev and held-out together; the JSON it writes carries every field the workbook and the judge
need plus `model_id`, `golden_path` and `producer`. `runs/` is git-ignored: the JSONs stay local, the rendered workbook
and `labels.xlsx` are committed by hand. The 2026-10-27 capture is the brief's one held-out run: nothing is tuned on
`S-04`, `S-08`, `S-12` or `S-16`, and they are never in `labels.xlsx`.

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
  Agent's, `policy_consistent` (`ok` / `ruled out`, blank before v0.8), case created, attempts as `a1/p1`, and a
  `status` (`OK`, `EXCEPTION`, `PENDING`).
- **`Verdict Matrix`**: one row per scenario: the six axes coloured PASS / PARTIAL / FAIL, `Grade`, `Score`, the four
  judge scores coloured by their verdict (`ERR` when the judge failed twice), the judge mean, the P1–P7 result and the
  attempts. A `total` row gives PASS counts per axis and judge means. Below it, the route confusion matrix (expected rows,
  final columns).
- **`Slices`**: PASS counts per axis and grade by `wording`, `note_driven`, `trap`, `category` and `route`. A slice of six
  or fewer is marked `(directional)`, per the brief.
- **`Compare`** (with `--before`): per-axis PASS counts for both runs over the scenarios they share, then every
  (scenario, axis) whose verdict changed, marked `improved` or `regressed`. Scenarios in one run only are listed as excluded.
- **`Calibration`** (with `--labels`): per judge axis, how many notes Rachit has scored, exact and within-1 agreement,
  both means, and every disagreement. `no labels yet` until the workbook is filled.
- **`S-xx`**: `field | golden | actual | verdict`, the verdict being the axis that governs that field (`payment_state`
  and `refund_state` belong to no axis and stay blank). `rationale / case_note` puts the labeler's reasoning beside the
  agent's note. Then `Run detail` (synthesize errors, sync issues, the Account Agent's summary, tool calls and errors, attempts)
  and, with `--judge`, a `Judge` panel: axis, score, reason, and the quoted span that cost the point.

## Release 2026-10-27, no gate (v0.8)

Three dev passes after the runtime gate was removed (`docs/validation.md`, "Gate removed"): `runs/eval_20261027T182359Z.json`,
`T182756Z.json`, `T183153Z.json`. `stability --known S-11:route_ok` passed with every cell 3/3 except the known S-11
route miss; `policy_consistent` 12/12 on all three. Workbook (pass T183153Z, no judge run, `Compare` against the release
below): [`reports/release_2026-10-27_no-gate.xlsx`](reports/release_2026-10-27_no-gate.xlsx).

| 12 dev | route | disputed_event | evidence | policy | confidence | guardrails | grade PASS |
|---|---|---|---|---|---|---|---|
| release below | 10/12 | 12/12 | 11/12 | 11/12 | 11/12 | 12/12 | 10/12 |
| no gate | 11/12 | 12/12 | 12/12 | 12/12 | 12/12 | 12/12 | 11/12 |

Every change is S-07 writing a draft this time; synthesize runs before where the gate was, so that is run-to-run
variance, not the change. What the change shows is that nothing regressed.

## Release 2026-10-27

Capture `runs/eval_20261027T174341Z.json` (all 16, Sonnet 5.5), judge `runs/judge_20261027T180514Z.json` (Opus 5.5,
16 judged, 0 errors), before `runs/eval_20261026T224024Z.json` (the pre-F1 dev run). Workbook:
[`reports/release_2026-10-27.xlsx`](reports/release_2026-10-27.xlsx).

| | route | disputed_event | evidence | policy | confidence | guardrails | grade PASS |
|---|---|---|---|---|---|---|---|
| all 16 | 14/16 | 16/16 | 15/16 | 13/16 | 15/16 | 16/16 | 12/16 |

Judge means over the 16: no_invented_facts 2.81, matches_rationale 2.56, ready_to_file 2.56, no_leaks_or_promises 2.81. Every note that
exists scored 3 on no_invented_facts and no_leaks_or_promises; the points lost are on reasoning agreement and ready_to_file, and every one
of them carries a quoted span in the workbook.

What the matrix and the judge found:

- **S-07 produced no draft again** (synthesize's schema rejected a citation of the other customer's event; the run fell
  back to `manual_escalation`, the correct route, with an empty note). It is the only scenario that regressed against the
  pre-F1 run and the only one the judge scored 0. Still the known open finding from `docs/validation.md`.
- **S-11 is the known route miss** (`explain_events` where the key wants `need_more_evidence`); the judge also marks
  its note PARTIAL on ready_to_file because it asks for a bank letter but leaves `open_questions` empty.
- **Two held-out scenarios fail only on `policy`**: S-04 and S-12 retrieve and cite the required sections but the draft
  omits one. First look at the held-out set; nothing is tuned on it.
- **Reasoning agreement is where the judge is strictest**: S-06, S-08, S-11 and S-13 get a 2 for a note that reaches the
  right conclusion but skips a step of the rationale (which policy sets the window, why the duplicate case matters).

LangSmith: dataset `dispute-agent-golden` and the experiment `release-20261027T174341Z-90f5290d`, both at
<https://smith.langchain.com/o/8d29be8b-8f57-4da6-8367-a770a98930ed/datasets/7d3c940e-0e7c-4b8d-bd0d-5b80c4a99476/compare?selectedSessions=649fe260-f92e-4f55-b92e-5cf9a357ad11>.
The experiment's target replays the stored JSON (no model call); its feedback keys are the 13 metric names.

## Calibration

`labels.xlsx` holds the 12 dev notes of this release, scored by Rachit on 2026-10-27 (four 0-3 columns; `How to score`
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
it proves the judge does not invent failures, not that it would catch a bad note. No note in this release leaks another
customer's details or promises a refund, so `no_leaks_or_promises` has had nothing to catch. The next step is a set of
deliberately broken notes (an invented amount, a mention of E-0135, a promised refund, a right route with a wrong
reason) graded by the judge to show it drops the right axis. Not built.
