# Plan: Fix the three live-run findings

## 1. Status

- **Version 1.0, 2026-10-26.** The fifth slice. Follows `docs/plans/sync-synthesize.md` v1.1, which is built (141 offline tests; dev eval `runs/eval_20261026T194129Z.json`: route 11/12, exit 0).
- **Revises** sync-synthesize decision S3 ("synthesize does not filter event or policy IDs"): the LLM's output schema now allows only the IDs it was shown. The draft still reaches the gate unmodified.
- **Revises** the gate's `refund_overdue` check, which the gate and sync-synthesize plans left fixed.
- **Spends** the one `SYNTHESIZE_SYSTEM` change that sync-synthesize §3 allowed.
- Decided with Rachit on 2026-10-26 (do not re-ask): all three fixes below, and up to **three** full dev eval runs.

## 2. Why & goal

**Context.** Six live `ask` runs on fresh complaint wording (outputs were in the session scratchpad; the findings are restated here) found three problems:

1. **A-1012** ("bank admits THEY messed up, he has a letter"): the draft wrote "ask Darnell for the bank letter" and cited RR.v1.s4, yet routed `explain_events`. The dev eval shows the same miss on S-11 (key: `need_more_evidence`, "only his bank can confirm a bank error, so a bank letter or statement is needed"). The route rules never say that a claim resting on proof the customer must supply is a missing fact.
2. **A-1015**: the Policy Agent cited `RF.v1.s3`; the draft wrote `RF.v3.s3`, which does not exist. The gate rejected it ("policy RF.v3.s3 is unknown"), so a correct `explain_events` case was escalated over a typo.
3. **A-1016** ("the one from october specifically, not the earlier one"): the Account Agent correctly picked E-0138 (requested Oct 6, due by Oct 21, not overdue). `refund_overdue` reads every refund request in the evidence, found the older E-0128 (Sep 29, overdue), returned `true`, and the gate forced `manual_escalation`.

**The bet.** One prompt sentence fixes the S-11 class without flipping S-09 (same RR.v1.s4 rule, but nothing for the customer to prove). A per-call enum schema makes an unshown ID impossible to emit. Scoping `refund_overdue` to the disputed event's chain matches what the rep asked about and changes no pinned verdict.

**Use case:**

```bash
.venv/bin/python -m dispute_agent ask A-1016 "jamal rivers - asking abt his refund, the one from october specifically, not the earlier one. wants to know when the check goes out"
# before: gate (code): route=manual_escalation gate_rejected=True reasons=['refund_overdue is true (RF.v1.s2) ...']
# after:  refund_overdue=false in the checks line; no refund_overdue reason
```

**This plan covers:** the route-rule sentence, the per-call draft schema, the `refund_overdue` scope, tests, the eval runs, three live re-runs and the architecture doc. Nothing else.

## 3. Definition of Done

### Acceptance criteria

```bash
# A1 offline suite (141 today + new); test_checks_pinned passes with PINNED unchanged
.venv/bin/python -m pytest tests -q
# A2 size fence
[ "$(wc -l < dispute_agent/synthesize.py)" -le 150 ] && [ "$(wc -l < tests/test_synthesize.py)" -le 200 ]
# A3 PINNED byte-identical to the version this plan was written against
[ "$(sed -n '/^PINNED = {/,/^}/p' tests/test_gate.py | shasum | cut -c1-40)" = "95ce0aa79d7da0234b01b72caaae439a4a0419d9" ]
# A4 dev eval after all changes: thresholds hold, route >= 11/12; exit 0
.venv/bin/python -m dispute_agent eval --min-disputed-acc 0.75 --min-policy-recall 0.75 --min-route-acc 0.9
# A5 S-11 and S-09 both route right in the latest eval file
.venv/bin/python -c "import json,glob; r={x['scenario_id']:x for x in json.load(open(sorted(glob.glob('runs/eval_*.json'))[-1]))['rows']}; assert r['S-11']['checks']['route_ok'] and r['S-09']['checks']['route_ok']"
# A6 no draft in the latest eval was rejected for an unknown or unshown ID
.venv/bin/python -c "import json,glob; rows=json.load(open(sorted(glob.glob('runs/eval_*.json'))[-1]))['rows']; assert not [g for x in rows for g in x['checks']['gate_reasons'] if ' is unknown' in g or ', not A-' in g]"
# A7 live re-run of A-1016: exit 0 and no refund_overdue reason
.venv/bin/python -m dispute_agent ask A-1016 "jamal rivers - asking abt his refund, the one from october specifically, not the earlier one. wants to know when the check goes out" --quiet | grep -q "refund_overdue=false"
# A8 only inventory files changed
git status --porcelain
```

- **Reported, not gated:** the live A-1012 and A-1015 routes (one LLM sample each proves little). Report the route and gate line for each.
- **A3** hashes the `PINNED` block; `tests/test_gate.py` is untracked, so `git diff` cannot guard it.
- **A6** may fail only through the gate's off-account check if the evidence somehow contained another account's event; that would be a `derive_states` bug. Stop and report it.

### Eval run protocol (three full dev runs at most)

| Run | When | Purpose |
|---|---|---|
| 1 | After steps 1–2 (gate scope + schema), **before** the prompt change | Isolates the schema's effect; becomes the "before" for the prompt change |
| 2 | After step 3 (prompt change) | The "after". Keep the change if S-11 is `route ok` and route accuracy ≥ run 1's |
| 3 | Only if run 2 is mixed: S-11 still `X`, or S-11 fixed but another scenario flipped | Separates flake from effect. If the same scenario fails again, **revert the prompt change** and report it as the v2 story |

A7 and the two reported live asks run once, after the final eval. Name every run file in the final message.

### Budgets

| Budget | Max |
|---|---|
| New files | 1 (this plan) |
| LOC: `synthesize.py` / `tests/test_synthesize.py` | 150 / 200 |
| New DB tables / columns / spreadsheet columns | 0 |
| New metric / event names, eval columns, JSON keys | 0 |
| New dependencies | 0 |
| New CLI flags | 0 |
| New `config.py` constants | 0 |
| Changes to `SYNTHESIZE_SYSTEM` | 1 (the sentence in §6), revertible per the protocol |
| Changes to other prompts (classify, Account Agent, finalize, Policy Agent) | 0 |
| Full dev eval runs | 3 |
| Live `ask` runs | 3 (A-1012, A-1015, A-1016) |
| Held-out runs | 0 |

### Must-handle edge cases

- **No evidence events** (finalize failed twice, or nothing cited): `event_ids` falls back to unconstrained `list[str]`; the gate still checks every ID. Same for **no kept rules** and `policy_source_ids`. `Literal[()]` is invalid, so this fallback is required, not optional.
- **A fake or real model returns an unshown ID**: validation against the per-call schema fails, giving `SynthesisResult(draft=None, errors=["synthesize failed: ValidationError: ..."])`. The gate then rejects with "no draft from synthesize". No retry.
- **`run_synthesize` returns a plain `Draft`**, never the per-call subclass, so `gate`, `score`, `model_dump` and equality tests see one type.
- **`refund_overdue` with a disputed event**: only `refund_requested` events in `ds.chain_of(disputed)` count. A-1016 with E-0138 disputed gives `"false"`.
- **`refund_overdue` with no disputed event** (S-15 ambiguous: two candidates): behaves as today, reading the evidence events, so S-15 stays `"true"`.
- **Disputed event with no refund request in its chain** (S-14: disputed E-0132 `refund_issued`, request E-0125 in the same chain): reads the chain, finds E-0125, stays `"false"`. A disputed event whose chain has no request gives `"unknown"`.
- **S-09 must not flip.** It has the same `ask_customer` reading of RR.v1.s4 as S-11, but the customer claims no bank error, and the rep can explain the return from the records.
- **S-05 must not flip.** It has `ask_customer` on LF.v3.s6, but the check was received, so nothing rests on customer proof.

### Out of scope

- The case-note judge, `create_review_case`, the LangGraph orchestrator, the PNG diagram.
- The Policy Agent's citation flakes (S-01 AP.v1.s4, S-13 RF.v1.s4).
- **Forbidden by name:**
  - No code that sets or overrides `review_route` from a rule's `outcome` (for example "any `ask_customer` rule means `need_more_evidence`"). That would be code wearing a prompt and would break S-05/S-09.
  - No mention of `ask_customer`, `outcome` or any scenario, account or section ID in the new prompt sentence.
  - No post-hoc filtering or rewriting of the draft's IDs.
  - No retry of synthesize on a validation failure.
  - No change to the gate's other four checks, `REGISTRY`, `RULES_OUT`, the ID or date checks, or `PINNED`.
  - No change to `Draft` in `gate.py` (the per-call schema subclasses it in `synthesize.py`).
  - No changes to `sync.py`, `pipeline.py`, `evaluate.py`, `__main__.py`, `derive.py`, `policy_agent.py`, `account_agent.py`, the data or the policies.
  - No edits to older plans, including `docs/plans/sync-synthesize.md`; this plan records what it revises.

## 4. File inventory (closed)

| Path | Action | Owns |
|---|---|---|
| `dispute_agent/synthesize.py` | UPDATE | `draft_schema()`; `synthesize()` validates against it and returns a plain `Draft`; `run_synthesize()` builds the schema from what it renders; the route-rule sentence; module docstring |
| `dispute_agent/gate.py` | UPDATE | `refund_overdue` scoped to the disputed chain, plus its docstring. Nothing else |
| `tests/test_synthesize.py` | UPDATE | Schema, unshown-ID and prompt-sentence tests (§10) |
| `tests/test_gate.py` | UPDATE | One new test (§10). `PINNED` and existing tests unchanged |
| `docs/architecture.md` | UPDATE → v0.5.4 | §3c synthesize paragraph (schema constraint, route sentence), §4 `refund_overdue` row, trap table `wrong_account` row, version note |
| `docs/plans/live-run-fixes.md` | CREATE | This plan |

**Not touched:** everything else. Needing another file means stop and ask.

**Read first:**
- `dispute_agent/synthesize.py`: all of it (105 lines). `SYNTHESIZE_SYSTEM` is at :27, `synthesize` at :84, `run_synthesize` at :91.
- `dispute_agent/gate.py:165-175` (`refund_overdue`) and `:194-200` (`Draft`, with the no-docstring comment).
- `dispute_agent/data.py:183-201` (`chain_of`: it walks up to the chain head, then down through all descendants).
- `tests/test_synthesize.py` (helpers `oracle_case`, `RecordingModel`) and `tests/test_gate.py:20-37` (`PINNED`), `:40-53` (`oracle_derived`, `oracle_draft`).
- `docs/architecture.md:128` (synthesize paragraph), `:151` (`refund_overdue` row), `:226` (`wrong_account` trap row), `:270` (latest version note).
- `docs/plans/policy-agent.md` §11: Sonnet 5.5 rejects forced `tool_choice`, so use `method="json_schema"`; output models carry no docstring.

## 5. Closed decisions (do not re-litigate)

| # | Decision | Why this, not X |
|---|---|---|
| F1 | **Fix the S-11 class with one prompt sentence** in route rule 2 | Rachit's choice over leaving it as the v2 story. Whether a claim rests on proof the customer must bring is read from the complaint and notes (free text), so the model owns it. A code rule on `outcome == "ask_customer"` would misroute S-05 and S-09 |
| F2 | **The sentence names the situation, not a field:** "the customer's claim (for example a bank error) can only be confirmed by proof the customer or their bank must supply, such as a bank letter or statement, and that proof is not on file" | A rule about a structured field in a prompt is code wearing a prompt. This one restates D9 (a missing fact is the customer's to supply) for a case the table missed |
| F3 | **Constrain the draft's ID fields per call** with `Literal` enums of the IDs shown: evidence event IDs and kept-rule section IDs | Rachit's choice over leaving it to the gate or a prompt edit. The prompt already says "cite exactly as shown" and the model still typed `RF.v3.s3`. Structured output enforces enums at generation |
| F4 | **The per-call model subclasses `Draft` via `create_model` in `synthesize.py`**; `Draft` in `gate.py` is unchanged; the result is converted back to plain `Draft` | Keeps S4 (the contract lives in `gate.py`) and gives downstream one type. A subclass without a docstring has `__doc__ is None`, so nothing extra is sent (verified: no top-level `description` in the schema) |
| F5 | **Empty ID set falls back to `list[str]`** for that field only | `Literal[()]` is invalid, and an empty `enum` risks an API rejection. The gate still checks every ID |
| F6 | **The gate keeps every ID check** | Defense in depth: the gate also checks dates, the registry and P1–P7, and must hold if a future caller passes an unconstrained model |
| F7 | **The S-07 "gate rescue" disappears from live runs** and is accepted | E-0135 is never a shown evidence ID, so the schema stops it before the gate. `test_score_gate_rescue` and `test_off_account_citation` still prove the gate path on hand-built drafts |
| F8 | **`refund_overdue` reads the disputed event's chain when there is a disputed event, else the evidence events** | Rachit's choice. It mirrors `_late_fee_in_play`, which already prefers the disputed event. Verified that all pinned verdicts are unchanged: S-13 true, S-14 false, S-15 true, S-16 unknown |
| F9 | **Three eval runs, ordered schema-first** (§3 protocol) | Rachit allowed three. Run 1 lets the write-up attribute any change to the schema or the prompt separately |

## 6. Architecture / schema

### Code vs model split (changes only)

| Output field | Where it comes from | Owner |
|---|---|---|
| `draft.event_ids` | Chosen from `derived.evidence_events` IDs, enforced by the per-call enum | model picks, **code constrains** (new), gate checks |
| `draft.policy_source_ids` | Chosen from `synced.rules` section IDs, enforced by the per-call enum | model picks, **code constrains** (new), gate checks |
| `draft.review_route` | Route table plus the new rule-2 sentence; "claim rests on customer proof" is read from the complaint and notes | model (gate may override) |
| `gate.check_results["refund_overdue"]` | `refund_requested` events in `chain_of(disputed)` if disputed, else in `evidence_events`; 10 business days before `WORLD_TODAY`; no `refund_issued` in the request's chain | code |

### `synthesize.py`

```python
def draft_schema(event_ids: list[str], section_ids: list[str]) -> type[Draft]:
    """Draft with its two ID fields narrowed to the IDs shown. An empty list keeps list[str]."""
    fields = {}
    for name, ids in (("event_ids", event_ids), ("policy_source_ids", section_ids)):
        if ids:
            desc = Draft.model_fields[name].description
            fields[name] = (list[Literal[tuple(dict.fromkeys(ids))]], Field(description=desc))
    return create_model("Draft", __base__=Draft, **fields)

def synthesize(seed, model=None, config=None, schema: type[Draft] = Draft) -> Draft:
    model = model or make_model().with_structured_output(schema, method="json_schema")
    raw = model.invoke([SystemMessage(SYNTHESIZE_SYSTEM), HumanMessage(seed)], config=config)
    checked = schema.model_validate(raw if isinstance(raw, dict) else raw.model_dump())
    return Draft.model_validate(checked.model_dump())

# run_synthesize: unchanged signature; builds
#   schema = draft_schema([e.event_id for e in derived.evidence_events], [r.section_id for r in synced.rules])
# and passes it to synthesize. An injected `model` (tests) is still used, and its output is still validated against `schema`.
```

`schema.model_validate(raw.model_dump())` matters: a fake returning a plain `Draft` with an unshown ID must fail here, as the real API would refuse to emit it.

### `SYNTHESIZE_SYSTEM` rule 2 (the one prompt change)

Before:

```
2. need_more_evidence: the disputed record or a claimed transaction is not on this account (fact_gap); more than one record fits and the complaint does not say which (fact_ambiguous); the investigation hit its tool budget. Put what is needed in open_questions.
```

After:

```
2. need_more_evidence: the disputed record or a claimed transaction is not on this account (fact_gap); more than one record fits and the complaint does not say which (fact_ambiguous); the investigation hit its tool budget; the customer's claim (for example a bank error) can only be confirmed by proof the customer or their bank must supply, such as a bank letter or statement, and that proof is not on file. Put what is needed in open_questions.
```

### `gate.py` `refund_overdue`

```python
def refund_overdue(ds, account_id, derived) -> Verdict:
    """P7: the refund request in question was made more than 10 business days ago and nothing was issued.

    With a disputed event, only requests in its chain count (the customer asked about that one); without one, every
    request in the evidence counts (fact-ambiguous cases like S-15)."""
    pool = ds.chain_of(derived.disputed_event_id) if derived.disputed_event_id else derived.evidence_events
    requests = [e for e in pool if e.type == "refund_requested"]
    ...  # the loop below is unchanged
```

### Schema / metrics count against §3

No tables, columns, metrics, eval columns, JSON keys, flags or constants. One new function (`draft_schema`), one new keyword parameter (`synthesize(schema=)`).

## 7. API surface

| Name | Change |
|---|---|
| `draft_schema(event_ids, section_ids) -> type[Draft]` | New, in `synthesize.py` |
| `synthesize(seed, model=None, config=None, schema=Draft) -> Draft` | New keyword-only use of `schema`; the default keeps old behavior; always returns a plain `Draft` |
| `run_synthesize(...)` | Signature unchanged; now builds and passes the schema |
| `gate.refund_overdue` | Same signature; scope narrowed when there is a disputed event |

## 8. Migration plan

None. No data or file moves. Older `runs/eval_*.json` stay readable.

## 9. Ordered steps

Each step's tests pass before the next starts. Re-read §3 Budgets and §4 before each step.

0. **Baseline.** `.venv/bin/python -m pytest tests -q` gives 141 passed.
1. **Gate scope.** Edit `refund_overdue` and add `test_refund_overdue_scoped_to_disputed`. Run the suite; `test_checks_pinned` must pass with `PINNED` untouched.
2. **Per-call schema.** Add `draft_schema`, change `synthesize` and `run_synthesize`, and add the schema tests. Run the suite.
3. **Eval run 1** (schema and gate, old prompt). Record route per scenario, any `gate_reasons`, and the file name.
4. **Prompt change.** Edit rule 2 exactly as in §6; add the test assertion. Run the suite.
5. **Eval run 2.** Apply the §3 protocol. Run 3 only if run 2 is mixed; revert step 4 if the same scenario fails twice, and rerun the suite.
6. **Live re-runs:** A-1016 (A7), then A-1012 and A-1015, with the exact messages in §10. Report each one's route, gate line and reasons.
7. **Docs.** `architecture.md` v0.5.4: line 128 says the schema allows only the shown IDs and the gate re-checks them; route table row 2 gains "a claim that only the customer's proof can confirm"; line 151 states the disputed-chain scope; line 226's backstop becomes "the draft schema only allows this account's shown events; the gate still rejects off-account citations"; a version note that names this plan.
8. **Fresh-context review** of the two code files against §3–§6, then run A1, A2, A3, A8.

## 10. Tests

Fixtures `ds`, `sections` come from `conftest.py`; helpers `oracle_case`, `RecordingModel` from `test_synthesize.py`, `FakeModel` from `test_policy_agent.py`, `oracle_derived` from `test_gate.py`.

| File | Test | Criterion |
|---|---|---|
| test_gate | `test_refund_overdue_scoped_to_disputed`: A-1016, `AccountFindings(relevant_event_ids=["E-0128","E-0138"], disputed_event_id="E-0138")` through `derive_states` gives `run_checks(...)["refund_overdue"] == "false"`; the same findings with `disputed_event_id=None, candidate_event_ids=["E-0128","E-0138"]` give `"true"` | A1 |
| test_gate | `test_checks_pinned` (existing, unchanged) still passes on all 16 | A1, A3 |
| test_synthesize | `test_system_prompt` gains: `"can only be confirmed by proof the customer or their bank must supply"` in `SYNTHESIZE_SYSTEM`, and none of `ask_customer`, `S-1`, `RR.v1` in it | A1 |
| test_synthesize | `test_draft_schema_enums`: S-02 oracle case; the schema's `event_ids.items.enum` equals the evidence IDs, `policy_source_ids.items.enum` equals the kept-rule IDs, `"description" not in schema.model_json_schema()`, `schema.__doc__ is None` | A1 |
| test_synthesize | `test_draft_schema_empty_falls_back`: `draft_schema([], [])` gives a JSON schema whose `event_ids.items` is `{"type": "string"}` | A1 |
| test_synthesize | `test_run_rejects_unshown_id`: S-14 oracle case, fake returns a `Draft` citing `"RF.v3.s3"` gives `draft is None`, one error starting `"synthesize failed: ValidationError"`, fake called once | A1 |
| test_synthesize | `test_run_rejects_off_account_event`: S-07 oracle case, fake returns a `Draft` with `event_ids=["E-0157","E-0135"]` gives `draft is None` | A1 |
| test_synthesize | `test_run_with_fake` (existing) adds `type(result.draft) is Draft` | A1 |
| CLI | eval runs 1–2(–3) | A4, A5, A6 |
| CLI | `ask A-1016 ...` | A7 |

Live messages, verbatim:

```
A-1016: jamal rivers - asking abt his refund, the one from october specifically, not the earlier one. wants to know when the check goes out
A-1012: darnell wants the 15 return fee + the late fee gone, says his bank admits THEY messed up the oct autopay and he has a letter from them
A-1015: hannah l says she was promised a 500 dollar refund and nothing came, pretty angry, says the system should show it
```

## 11. Edge cases / footguns

- **`Literal[tuple(ids)]` needs a non-empty tuple.** Dedupe with `dict.fromkeys` so order stays stable.
- **`create_model(..., __base__=Draft)`**: pass the field description again, or the narrowed field loses it. Don't pass `__doc__`.
- **Validate through `model_dump()`.** `schema.model_validate(draft_instance)` on a parent-class instance may raise for the wrong reason or pass through unchecked; dumping first makes the check uniform.
- **`test_run_with_fake`** uses S-02 with `LF.v3.s2`, which is a kept rule in the oracle case, so it still passes. If it fails, the helper's rules changed, not the code.
- **A3 is a hash guard.** Adding the new test elsewhere in `test_gate.py` is fine; any edit inside the `PINNED` block fails it.
- **S-13's RF.v1.s4 citation is flaky (~1 in 4) at the Policy Agent.** It shows as `pcite`/`dpol` X, not a route miss. Don't spend run 3 on it.
- **The prompt sentence is the only allowed edit.** If run 2 shows a different miss, report it; do not tune.
- **Never run `--split heldout`.** A-1004, A-1009, A-1013 and A-1017 are held out; the live re-runs use dev accounts only.

## 12. Open questions

All closed.

## Execution-discipline note

Read the whole plan first. Execute §9 in order and verify each step. Re-read §3 and §4 before each step; on a budget hit or an out-of-inventory file, stop and ask. Spend eval runs only per the §3 protocol. On a hard blocker (for example the API rejecting the enum schema), leave `TODO(schema): <error>`, keep `schema=Draft` as the stand-in so the pipeline still runs, finish the other two fixes, and report. End with the fresh-context review, the run files named, a per-scenario route table across runs, and the three live results.
