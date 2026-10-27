# Plan: Live validation of the v0.6 graph

> Written to `docs/plans/` rather than `tasks/`: this repo keeps one plan per slice there (see README "Layout").

## 1. Status

- **Version 1.0, 2026-10-26.** The sixth slice. Validates, against the real models and LangSmith, that the v0.6 graph built from [orchestrator.md](orchestrator.md) works end to end: the Account Agent and the Policy Agent each do their job, sync joins them and fires its two re-requests when it should, synthesize writes a usable draft, the gate and `create_review_case` close the run. It builds the two small tools that validation needs (a stability report over repeated evals and a trace-shape check) and one fault switch, then runs the validation and writes the findings up.
- Follows [orchestrator.md](orchestrator.md) v1.0, which is built and uncommitted: 157 offline tests, one live `ask`, one dev eval (`runs/eval_20261026T220633Z.json`: route 11/12, all else 12/12, send-backs 0, re-retrieves 0).
- **Supersedes** nothing. The eval columns and thresholds from `sync-synthesize.md` stay as they are.
- Decided with Rachit on 2026-10-26 (do not re-ask):
  - **Scope:** three dev-eval repeats plus eight ad-hoc complaints (rephrasings of dev scenarios, so the answer key carries over).
  - **Re-request paths are exercised live** through one environment variable, `DISPUTE_FAULT`, read only in `pipeline.py`; off by default; `eval` refuses to run with it set.
  - **Trace shape is checked programmatically** with the `langsmith` SDK through a `trace` CLI command; exit 0 is the pass signal.
  - **Fix budget: anything needed to make it work.** Every fix is named in the report with its before/after runs. Prompt changes are allowed but each one is a named entry, and a prompt gaining a rule about a structured field is still a stop-and-replan (the global rule in the plan skill).
  - **Measured cost:** $0.06 and 18 s per graph run (13 traced runs today, Sonnet 5.5 at $2/$10 per MTok). This plan's model spend is capped at **$8**; the expected total is about $4.50.

## 2. Why & goal

**Context.** v0.6 changed the graph's shape (parallel agents, join, two re-requests, a real `StateGraph`, a new last node) and was verified by one eval and one `ask`. One run cannot tell a flaky check from a broken one, the re-request paths have only run offline with fakes, and nobody has confirmed that LangSmith shows one parent run with the expected children. Rachit presents this system as multi-agent; the validation has to show the agents, the join and the re-requests doing their jobs in real traces, not only in unit tests.

**The bet.** Across three repeated dev evals, every check passes on every scenario at least twice out of three, except the known S-11 route miss; both re-request paths produce the expected trace shape when forced; and eight rephrased complaints land on the same route and disputed event as their originals in at least six of eight cases. Where that fails, the report names the component (Account Agent, Policy Agent, sync, synthesize, gate) and the trace.

**Use case:**

```bash
DISPUTE_FAULT=finalize_once .venv/bin/python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues, conf # ends in 8812" --quiet
.venv/bin/python -m dispute_agent trace
```

The first prints `account attempts: 2  policy attempts: 1` and the normal draft. The second fetches that run from LangSmith and prints:

```
dispute A-1002  tags=[slice=investigate, cli]  in=21k out=4.1k $0.09 31s
children: classify > account_agent > policy_agent > derive_states > sync > account_agent_retry > derive_states_retry > sync > synthesize > gate > create_review_case
expected for account_attempts=2 policy_attempts=1: match
```

**This plan covers:** `validate.py` (stability report, trace check), the `stability` and `trace` CLI commands, the `DISPUTE_FAULT` switch, test hygiene for tracing, the validation runs themselves, and the report `docs/validation.md`. Nothing else.

## 3. Definition of Done

### Acceptance criteria

```bash
# A1 offline tests, whole suite (157 today + new)
.venv/bin/python -m pytest tests -q
# A2 size fence
[ "$(wc -l < dispute_agent/validate.py)" -le 160 ] && [ "$(wc -l < dispute_agent/pipeline.py)" -le 220 ] && [ "$(wc -l < tests/test_validate.py)" -le 120 ]
# A3 clarify path, live: the Account Agent runs twice and the trace shows it
set -a; . ./.env; set +a
DISPUTE_FAULT=finalize_once .venv/bin/python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues, conf # ends in 8812" --quiet | grep -q "account attempts: 2  policy attempts: 1"
.venv/bin/python -m dispute_agent trace
# A4 re-retrieve path, live: the Policy Agent runs twice, the second time on the disputed event's date
DISPUTE_FAULT=policy_date_mismatch .venv/bin/python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues, conf # ends in 8812" --quiet | grep -q "account attempts: 1  policy attempts: 2"
.venv/bin/python -m dispute_agent trace
# A5 three dev evals, each within the existing thresholds, then the stability gate over the three
for i in 1 2 3; do .venv/bin/python -m dispute_agent eval --min-disputed-acc 0.75 --min-policy-recall 0.75 --min-route-acc 0.75 || exit 1; done
.venv/bin/python -m dispute_agent stability $(ls -t runs/eval_*.json | head -3) --known S-11:route_ok
# A6 the last 12 eval traces all have the normal shape
.venv/bin/python -m dispute_agent trace --last 12
# A7 eight ad-hoc complaints run to the end (the table in §6 is scored by hand into the report), then their traces check
bash docs/plans/live-validation-adhoc.sh     # the eight `ask` lines from §6; `set -e`
.venv/bin/python -m dispute_agent trace --last 8
# A8 the report exists with its required sections
grep -q "## Flake matrix" docs/validation.md && grep -q "## Re-request paths" docs/validation.md && grep -q "## Ad-hoc complaints" docs/validation.md && grep -q "## Cost" docs/validation.md
# A9 only inventory files changed
git status --porcelain
```

- `trace` with no arguments checks the most recent root run; `--last N` checks the N most recent. It passes when each root run has the children that its own `account_attempts` and `policy_attempts` outputs imply (§6), and prints tokens, cost and latency per run. It exits 1 on a shape mismatch, 2 when no qualifying run is found within 30 seconds.
- `stability` passes when every (scenario, check) pair passes in at least `n − 1` of the `n` files (so one flake in three is tolerated), except pairs named with `--known`. It exits 1 otherwise and prints the full matrix either way.
- **If A5 or A6 fails:** read the traces, fix, name the fix in the report, rerun the failed criterion. Spend stays under the $8 cap (§3 Budgets); hitting it is a stop-and-ask.
- **Held-out scenarios (S-04, S-08, S-12, S-16) are never run.** The ad-hoc complaints rephrase dev scenarios only.

### Budgets

| Budget | Max |
|---|---|
| New files | 5: `dispute_agent/validate.py`, `tests/test_validate.py`, `docs/plans/live-validation-adhoc.sh`, `docs/validation.md`, this plan |
| LOC per new code file | 160 (`validate.py`), 120 (`tests/test_validate.py`) |
| New CLI commands / flags | 2 commands (`stability`, `trace`); flags `--known` (stability), `--last` (trace) |
| New environment variables | 1 (`DISPUTE_FAULT`, values `finalize_once`, `policy_date_mismatch`) |
| New `config.py` constants | 0 |
| New dependencies installed | 0. One explicit pin added to `requirements.txt` for `langsmith`, which is already installed as a transitive dependency and is now imported directly |
| New DB tables / columns / spreadsheet columns | 0 |
| New metric / event names | 0 (stability reuses the eval's `checks` keys; trace prints, it does not store) |
| Model spend | $8 total for this plan (expected ≈ $4.50); stop and ask at $8 |
| Prompt changes | Allowed, each one a named entry in `docs/validation.md` with before/after runs; a rule about a structured field is a stop-and-replan |
| Dev-eval runs | 3 planned + 3 after the S-07 fix (raised from 2 on 2026-10-26 so the rerun gets a full matrix); a seventh is a stop-and-ask |

### Must-handle edge cases

- **`DISPUTE_FAULT` unset:** zero behaviour change. A test asserts the graph output is identical with the variable absent and with it set to the empty string.
- **`DISPUTE_FAULT` set to an unknown value:** `ValueError` naming the allowed values, before any model call.
- **`DISPUTE_FAULT` set during `eval`:** `run_eval` exits 2 with "unset DISPUTE_FAULT before eval"; no scenario runs, no `runs/` file is written.
- **`finalize_once` fires only on the first Account Agent run:** the retry runs the real agent untouched, so the clarify path ends with findings.
- **`policy_date_mismatch` reads on 2026-04-15, when LF.v2 was in force:** sync sees a different section set from the disputed event's date and routes to `policy_agent_retry`. For a scenario with no disputed event (policy date = complaint date) sync still sees a different set, so the re-retrieve still fires; the report notes which.
- **LangSmith ingest delay:** `trace` polls for up to 30 s before exiting 2.
- **Offline tests must not create traces:** `tests/conftest.py` sets `LANGSMITH_TRACING=false` for the session. Today three zero-token `dispute S-02` roots exist from the tests; `trace` also ignores root runs with zero prompt tokens and root runs without the `slice=investigate` tag, so those cannot satisfy or fail a check.
- **Known flaky check:** S-13's `policy_cited_ok` (RF.v1.s4) has missed about one run in four historically. It is **not** pre-listed in `--known`; if it fails the `n − 1` rule, that is a finding for the report, and only then may it be added to `--known`, with the reason written next to it.
- **The S-11 route miss is known** and listed in `--known` from the start.
- **Ad-hoc complaints are scored against their source scenario's key** (route, disputed event, category) by hand in the report; `ask` cannot score, it only prints.

### Out of scope

- Fixing the S-11 route (the v2 story), the Opus case-note judge, the code-only baseline, held-out runs.
- Any change to the data files, policy files, or `scenarios.xlsx` (the ad-hoc complaints are not added to it; they live in the shell script and the report).
- A stored stability metric, a dashboard, or a LangSmith dataset/experiment; `stability` and `trace` print and exit.
- **Forbidden by name:** no second fault variable or fault config file; no retry-on-flake inside the graph; no change to `MAX_ATTEMPTS`; no new `config.py` constant; no `StateGraph` changes beyond the two fault reads; no new Pydantic models for the report; no pytest marker that runs live tests (live runs stay manual, through the CLI); no `langsmith` client call anywhere except `validate.py`.

## 4. File inventory (closed)

| Path | Action | Owns |
|---|---|---|
| `dispute_agent/validate.py` | CREATE | `FAULTS`, `stability(reports, known) -> (lines, ok)`, `expected_children(account_attempts, policy_attempts)`, `trace_check(client, project, last, wait) -> (lines, ok)` |
| `dispute_agent/pipeline.py` | UPDATE | `_fault()` (reads `DISPUTE_FAULT`, validates against `FAULTS`); `account_node` blanks findings once under `finalize_once`; `policy_node` reads on `FAULT_POLICY_DATE` under `policy_date_mismatch` |
| `dispute_agent/__main__.py` | UPDATE | `stability` and `trace` subcommands; docstring |
| `dispute_agent/evaluate.py` | UPDATE | `run_eval` refuses to start when `DISPUTE_FAULT` is set |
| `tests/conftest.py` | UPDATE | session fixture sets `LANGSMITH_TRACING=false` |
| `tests/test_pipeline.py` | UPDATE | fault tests (§10) |
| `tests/test_validate.py` | CREATE | stability and trace-check tests on synthetic inputs, no network (§10) |
| `tests/test_evaluate.py` | UPDATE | `run_eval` refusal test |
| `requirements.txt` | UPDATE | `langsmith>=0.14,<1` |
| `README.md` | UPDATE | "Run it": the two commands and the fault variable |
| `docs/architecture.md` | UPDATE | one sentence under "Models and observability" naming `trace` and the fault switch; version note v0.6.1 |
| `docs/plans/live-validation-adhoc.sh` | CREATE | the eight `ask` lines from §6, `set -e`, `.env` sourced |
| `docs/validation.md` | CREATE | the report (§6 template), filled during execution |
| `docs/plans/live-validation.md` | CREATE | this plan |

**Touching any other file is a scope breach: stop and ask.** In particular `account_agent.py`, `policy_agent.py`, `sync.py`, `synthesize.py`, `gate.py`, `derive.py`, `data/` are not in the inventory; a fix that needs one of them is allowed by the fix budget but must be announced first with the finding that motivates it, and added to this table.

**Read first:**
- [pipeline.py](../../dispute_agent/pipeline.py) all of it (197 lines): `build_graph`, `account_node`, `policy_node`, `after_sync`, `investigate`.
- [evaluate.py](../../dispute_agent/evaluate.py) lines 1–60 (`CHECKS`, the docstring's column list) and `run_eval` (the JSON row layout: `scenario_id`, `checks`, `account_attempts`, `policy_attempts`).
- [__main__.py](../../dispute_agent/__main__.py) `main()` for the argparse layout; `cmd_ask` for the printed line `sync (code): ...  account attempts: N  policy attempts: N`.
- [tests/test_pipeline.py](../../tests/test_pipeline.py): `FakeAccountAgent`, `run()`, `NORMAL`.
- [orchestrator.md](orchestrator.md) §3 edge cases and §6 (the graph; node names are the trace's child names).

## 5. Closed decisions (do not re-litigate)

| # | Decision | Why this, not X |
|---|---|---|
| V1 | **Faults are read in `pipeline.py` node wrappers, never inside the agents** | The agents stay exactly what the evals measure; the wrapper blanks the findings after a real run, which is also what a real finalize failure looks like to the graph |
| V2 | **The trace check derives the expected shape from the root run's own outputs** (`account_attempts`, `policy_attempts`), not from a flag | One command works for normal, clarify and re-retrieve runs; the graph's recorded state is the ground truth |
| V3 | **Stability tolerates one miss in `n` per (scenario, check)**, known pairs excepted | Earlier runs showed single-run flakes (S-13 citation, S-07 pick); a 100% bar would turn every flake into a stop, a 50% bar would hide real breakage |
| V4 | **Ad-hoc complaints are rephrasings of dev scenarios with the wording class flipped** (messy from clean, clean from messy) | Inherits the answer key with no new data; tests exactly the brief's messy/clean slice claim |
| V5 | **`langsmith` gets an explicit pin** | It is imported directly now; relying on a transitive install is a latent break |
| V6 | **The report is a repo file, `docs/validation.md`, not a `runs/` artifact** | `runs/` is git-ignored; reviewers read the repo |
| V7 | **`list_runs` is used even though it is deprecated for 2027** | It works on 0.14 and is spike-verified; the replacement `client.runs.query()` is not documented in this repo, and a 2027 removal is outside the take-home's life. Footgun noted in §11 |

## 6. Architecture / schema

### Code vs model split

No new model step. Every value this plan produces is code-owned:

| Output field | Where it comes from | Owner |
|---|---|---|
| stability matrix cell `(scenario, check) = k/n` | the `checks` dict in each `runs/eval_*.json` row | code |
| trace `children` | LangSmith child runs of the root, ordered by `start_time` | code |
| trace `expected` | `expected_children(outputs.account_attempts, outputs.policy_attempts)` | code |
| trace tokens / cost / latency | root run `prompt_tokens`, `completion_tokens`, `total_cost`, `end_time − start_time` | code (LangSmith's own figures) |
| ad-hoc `expected route / disputed event` | the source dev scenario's key | code (the key) |
| ad-hoc `actual` | `ask` output, copied by hand into the report | the executor, from printed code output |

### `DISPUTE_FAULT` (pipeline.py)

```python
FAULTS = ("finalize_once", "policy_date_mismatch")
FAULT_POLICY_DATE = datetime(2026, 4, 15)  # LF.v2 in force; LF.v3 starts 2026-05-01

def _fault() -> str | None:
    value = os.environ.get("DISPUTE_FAULT") or None
    if value and value not in FAULTS:
        raise ValueError(f"DISPUTE_FAULT must be one of {FAULTS}, got {value!r}")
    return value
```

- `account_node`: after the real `account_agent.invoke(...)`, if `_fault() == "finalize_once" and s["account_attempts"] == 0`, return the state with `findings=None` and `tool_errors + ["fault injected: finalize_once"]`.
- `policy_node`: `read_on = FAULT_POLICY_DATE if _fault() == "policy_date_mismatch" else WORLD_TODAY`; basis stays `complaint_date`.
- `run_eval`: `if os.environ.get("DISPUTE_FAULT"): print("unset DISPUTE_FAULT before eval"); return 2`.

### `validate.py`

```python
NORMAL = ["classify", "account_agent", "policy_agent", "derive_states", "sync", "synthesize", "gate", "create_review_case"]

def expected_children(account_attempts: int, policy_attempts: int) -> list[str]:
    head, tail = NORMAL[:5], NORMAL[5:]
    if account_attempts == 2: head += ["account_agent_retry", "derive_states_retry", "sync"]
    if policy_attempts == 2: head += ["policy_agent_retry", "sync"]
    return head + tail

def stability(reports: list[dict], known: set[tuple[str, str]]) -> tuple[list[str], bool]:
    # rows: one per scenario; columns: CHECKS; cell k/n; a cell fails when k < n-1 and (scenario, check) not in known
    # footer: send-backs, re-retrieves, exceptions per file

def trace_check(client, project: str, last: int = 1, wait: float = 30.0) -> tuple[list[str], bool]:
    # roots: list_runs(project_name=project, is_root=True, start_time=now-6h), name startswith "dispute ",
    #        prompt_tokens > 0, "slice=investigate" in tags; newest `last`; poll until `last` are found or `wait` elapses
    # per root: children = [r for r in list_runs(project_name=project, trace_id=root.trace_id) if r.parent_run_id == root.id], by start_time
    #           expected = expected_children(root.outputs["account_attempts"], root.outputs["policy_attempts"])
    #           line: name, tags, tokens, cost, latency, children, "match" | "MISMATCH expected ..."
    # ok = every root matches
```

Root-run `outputs` is the final `GraphState`; LangSmith serializes the Pydantic fields, and the two ints are read directly. If `outputs` is missing (a run that raised), the line says `no outputs` and counts as a mismatch.

### CLI

```
python -m dispute_agent stability RUN.json [RUN.json ...] [--known S-11:route_ok ...]
python -m dispute_agent trace [--last N]
```

### Ad-hoc complaints (`docs/plans/live-validation-adhoc.sh`)

Each line is `ask <account> "<message>" --quiet`. Expected values are the source scenario's key.

| # | Source | Account | Wording flip | Message | Expected route | Expected disputed |
|---|---|---|---|---|---|---|
| L1 | S-01 | A-1001 | clean → messy | `priya r says we hit her w/ a $25 late fee in sept but shes on autopay?? wants it gone` | explain_events | E-0117 |
| L2 | S-03 | A-1003 | clean → messy, note-driven | `cust says a rep told her early sept that autopay was still on. sept never drafted, late fee hit. wants it off` | manual_escalation | E-0122 |
| L3 | S-05 | A-1005 | clean → messy | `mailed a check ~9/28 for oct, her bank says cashed, acct still shows oct unpaid + a late fee. whats going on` | adjustment_review | E-0131 |
| L4 | S-07 | A-1007 | messy → clean | `Andre says he paid October by phone on the 5th for $455.30 and was given confirmation 48213. The account shows him late with a fee.` | manual_escalation | none |
| L5 | S-09 | A-1010 | messy → clean | `Customer received our letter saying his September payment was returned, but his banking app showed it clearing on the 5th. He also asks about a $15 charge.` | explain_events | E-0107 |
| L6 | S-10 | A-1011 | clean → messy, note-driven | `oct pmt left her checking 10/1 then got reversed like a wk later?? now past due w/ a late fee. why` | adjustment_review | E-0142 |
| L7 | S-14 | A-1015 | messy → clean | `Customer says the refund for her September double payment never reached her bank account ending 4471. It has been almost a month.` | explain_events | E-0132 |
| L8 | S-15 | A-1016 | messy → clean | `Customer asks where his refund is. He was told "a couple of weeks" on a call and was not told the amount.` | need_more_evidence | none |

### `docs/validation.md` template

```
# Live validation of the v0.6 graph — 2026-10-26
## What was validated   (one paragraph; links to this plan)
## Flake matrix          (the `stability` output over the three evals; which cells failed; known list and why)
## Re-request paths      (A3 and A4: the `ask` lines, attempt counts, the `trace` output, LangSmith links)
## Trace shapes          (A6: 12/12 normal; LLM calls per run; anything odd)
## Ad-hoc complaints     (the §6 table plus actual category / route / disputed event / gate / attempts; agreement count)
## Per-component notes   (Account Agent, Policy Agent, sync, synthesize, gate: what the runs showed; ambiguity flags; tool-call counts)
## Fixes made            (each: finding → change → before/after run IDs; "none" if none)
## Cost                  (runs, tokens, $ from `trace`; against the $8 cap)
## Open findings         (not fixed here; where they go next)
```

## 7. API surface

CLI only: `stability` and `trace` subcommands; `ask`/`eval` unchanged except the eval refusal. No new routes, contracts or message types. `Investigation`, `GraphState` and the eval JSON layout are unchanged.

## 8. Migration plan

None. No schema, no data, no stored state. `requirements.txt` gains one line; `pip install -r requirements.txt` is a no-op on the existing venv.

## 9. Ordered steps

1. **Test hygiene first:** `tests/conftest.py` sets `LANGSMITH_TRACING=false`; run A1; confirm no new `dispute S-02` roots appear in LangSmith after a test run.
2. `pipeline.py`: `_fault()` and the two reads; `evaluate.py` refusal; tests in `test_pipeline.py` and `test_evaluate.py` (§10). A1, A2.
3. `validate.py`: `expected_children`, `stability`; `tests/test_validate.py` on synthetic reports. A1, A2.
4. `validate.py`: `trace_check`; test with a fake client object (two methods: `list_runs`); `__main__.py` commands; `requirements.txt`. A1, A2.
5. **Live, cheap first:** A3 then A4 (two `ask` runs + two `trace` checks, ≈ $0.20). Fix anything the shapes reveal before spending on evals.
6. A5: three evals (≈ $2.20), then `stability`. Read every failing cell's trace before changing anything.
7. A6: `trace --last 12`.
8. A7: write `docs/plans/live-validation-adhoc.sh`, run it (≈ $0.50), `trace --last 8`, score by hand into the report.
9. Fixes, if any, each named; rerun only the criterion that failed; respect the eval-run and dollar budgets.
10. `docs/validation.md` complete (A8); README and architecture.md lines; A9.

## 10. Tests

All offline; conventions: one test file per source file, `ds`/`sections` fixtures from `conftest.py`, fakes with `.invoke()`.

| Test | File | Covers | Criterion |
|---|---|---|---|
| `test_fault_unset_is_identical` | `tests/test_pipeline.py` | graph output with `DISPUTE_FAULT` absent equals output with it set to `""` (`monkeypatch.delenv` / `setenv`) | A1 |
| `test_fault_finalize_once` | `tests/test_pipeline.py` | with `finalize_once`, order is the clarify order, `account_attempts == 2`, final findings present, `tool_errors` carries the marker once | A1, A3 |
| `test_fault_policy_date_mismatch` | `tests/test_pipeline.py` | with `policy_date_mismatch`, order is the re-retrieve order, `policy_attempts == 2`, final policy read on the disputed date; `FAULT_POLICY_DATE` replaces the existing monkeypatch of `WORLD_TODAY` in `test_re_retrieve_once` | A1, A4 |
| `test_fault_unknown_value_raises` | `tests/test_pipeline.py` | `ValueError` names the allowed values | A1 |
| `test_eval_refuses_fault` | `tests/test_evaluate.py` | `run_eval` returns 2 and writes no file when the variable is set | A1 |
| `test_expected_children` | `tests/test_validate.py` | the four shapes (1/1, 2/1, 1/2, 2/2) match `NORMAL`-based lists used in `test_pipeline.py` | A1, A6 |
| `test_stability_matrix_and_gate` | `tests/test_validate.py` | three synthetic reports: all-pass cell, 2/3 cell passes, 1/3 cell fails, 1/3 known cell passes; footer counts send-backs and re-retrieves | A1, A5 |
| `test_trace_check_match_and_mismatch` | `tests/test_validate.py` | a fake client returning one root (with `outputs`, tags, tokens) and children in order → match; children missing a retry node → mismatch; root without `slice=investigate` or with zero tokens is ignored | A1, A3–A6 |
| `test_trace_check_waits_then_exits_2` | `tests/test_validate.py` | fake client with no roots and `wait=0` → `ok False`, message "no qualifying run" | A1 |

## 11. Edge cases / footguns

- **`DISPUTE_FAULT` leaks into eval.** The refusal in `run_eval` is the guard; also never `export` it, set it inline on the `ask` command as in A3/A4.
- **Shell profile exports LangSmith vars.** That is why offline tests traced today. Step 1 fixes it for pytest; `trace` additionally filters by tag and tokens.
- **`list_runs` deprecation warning** prints to stderr on every call; `trace` suppresses it with `warnings.filterwarnings("ignore", category=DeprecationWarning)` scoped to the call. Do not migrate to `client.runs.query()` in this plan (V7).
- **LangSmith ingest lag.** Children sometimes arrive a few seconds after the root; `trace` re-fetches children until the count stops growing or `wait` elapses.
- **Root `outputs` serialization.** `GraphState` holds Pydantic models and LangChain messages; LangSmith serializes them, and `outputs["account_attempts"]` is a plain int. If a future change makes outputs huge, LangSmith may truncate; the check reads only the two ints.
- **Node order inside one superstep.** `account_agent` and `policy_agent` start in the same superstep; order by `start_time` can put `policy_agent` first. `expected_children` compares the two as an unordered pair at positions 1–2; everything else is ordered.
- **`ask` exit code is 0 even when the gate rejects.** A7's `set -e` catches crashes only; routes are scored by hand.
- **Three evals in a row re-use the compiled graph per process,** not across processes; each `eval` builds its own, which is fine, but do not run two evals concurrently: the `runs/` timestamp has second resolution and the stability command picks files by mtime.
- **`policy_date_mismatch` on a no-disputed-event scenario** still fires (the complaint date 2026-10-20 vs 2026-04-15 differ in set), which is the intended demonstration, not a bug.
- **Prompt-change temptation.** The fix budget allows it, but the stop-and-replan rule stands: if a fix wants to add a rule about `review_route`, `event_ids` or `policy_source_ids` to a prompt, stop; that rule belongs in code or in the gate.

## 12. Open questions

All closed.

## Execution-discipline note

Read the whole plan first. Execute §9 in order; steps 1–4 are offline and must be green (A1, A2) before any live step. Re-read §3 Budgets and §4 File inventory before each step; on hitting a budget (a sixth eval, $8, an out-of-inventory file) stop and ask rather than proceeding. Live steps spend money: run the cheap ones (A3, A4) before the evals, and never run the held-out split. Every fix is named in `docs/validation.md` with its before/after run IDs before the next live step starts. Finish with a fresh-context review of `validate.py` and the report against this plan.
