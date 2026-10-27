# Plan: Deterministic Policy & Authorization Gate

## 1. Status

- **Version 1.0, 2026-10-26.** The third code slice. Implements the "Deterministic Policy & Authorization Gate" box of [docs/architecture.md](docs/architecture.md) v0.5.1 §4 (D6, D7, D9) and the "Gate registry" table in [data/policies/README.md](data/policies/README.md).
- Follows `docs/plans/policy-agent.md` v1.3, which is built: the dev eval scores 12/12 on every check (`runs/eval_20261026T163446Z.json`).
- Decided with Rachit on 2026-10-26 (do not re-ask):
  - **Gate before synthesize.** The gate defines the `Draft` contract synthesize must satisfy, and it is verifiable today without an LLM.
  - The gate's checks are shown on live runs through `ask` and one `eval` column, even though no draft exists yet.
- On completion this file is copied to `docs/plans/gate.md` (the repo keeps plans there, not in `tasks/`).

## 2. Why & goal

**Context.** Classify, the Account Agent, `derive_states` and the Policy Agent are built. Everything after them (sync, synthesize, gate, create review case, the LangGraph orchestrator) is not. The gate is the one piece that is pure code with right answers: our own P1–P7 rules, written as Python checks over event fields, keyed by policy section ID. It is the backstop that makes the LLM's route trustworthy, and the reason an injected memo cannot talk the agent into `explain_events` on S-04.

**The bet.** If each P1–P7 check can be computed from structured event fields alone (type, timestamps, amounts, links) and returns `true` only when the records prove it, then a perfect draft is never rejected and a wrong route is caught wherever the records are decisive. Verified by prototype on 2026-10-26 against all 16 scenarios (§6, "Verified on the data").

**Use case:**

```bash
.venv/bin/python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues, conf # ends in 8812"
```

Prints what it prints today, then:

```
gate checks (code): late_fee_unwarranted=true  lender_side_failure=true  customer_cancelled_autopay=false  return_fee_valid=unknown  refund_overdue=unknown
cited sections:     LF.v3.s2 late_fee_unwarranted=true rules out explain_events · LF.v3.s5 lender_side_failure=true rules out explain_events
```

When synthesize exists (next plan), `gate()` takes its `Draft` and returns the final route plus `gate_rejected` and reasons.

**This plan covers:** `gate.py` (constants, registry, five checks, `Draft`, `GateResult`, duplicate lookup, `gate()`), its no-LLM tests, the `ask` and `eval` wiring, and doc updates. Nothing else.

## 3. Definition of Done

### Acceptance criteria

```bash
# A1 no-LLM tests, the whole suite (50 today + the new file)
.venv/bin/python -m pytest tests -q
# A2 size fence on the two new code files
for f in dispute_agent/gate.py tests/test_gate.py; do [ "$(wc -l < "$f")" -le 300 ] || exit 1; done
# A3 one real run prints the "gate checks" block and exits 0 (needs .env)
.venv/bin/python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues, conf # ends in 8812"
# A4 dev eval runs with the existing thresholds and prints the new `gate` column; exit 0
.venv/bin/python -m dispute_agent eval --min-disputed-acc 0.75 --min-policy-recall 0.75
# A5 only inventory files changed
git status --porcelain
```

- **`gate` column, reported not gated:** for each dev scenario, would the gate let the **key's route** through given the **LLM's live citations**? Every `X` is listed in the final message with its reason (an unregistered `EX.v1.*` citation, a stale section, an ambiguity flag, or a check contradiction). No new threshold flag.
- **Held-out scenarios are never run through `eval`.** The deterministic tests do cover all 16 (G7).

### Budgets

| Budget | Max |
|---|---|
| New files | 3 (`dispute_agent/gate.py`, `tests/test_gate.py`, `docs/plans/gate.md`) |
| LOC per new code file | 300 (was 260; raised 2026-10-26 during execution: `gate.py` landed at 285 with no scope added, the extra is docstrings and the registry table) |
| New DB tables / columns / spreadsheet columns | 0 |
| New metric / event names | 0 (one eval column `gate`, one JSON key `check_results`, one check key `gate_ok`) |
| New dependencies | 0 |
| New constants in `config.py` | 0 (policy constants live in `gate.py`, G6) |
| LLM calls added | 0 |
| Prompt changes | 0 |

### Must-handle edge cases

- **Off-account citation** (S-07 citing E-0135, S-06 citing E-0158): rejected, route `manual_escalation`, `gate_rejected: true`, reason names the ID. Rejected even when the route is already `manual_escalation` (G5).
- **Unknown, stale or unregistered policy ID** (`XX.v9.s9`; `LF.v2.s3` on 2026-10-14; `EX.v1.s1`, in force but not in the registry): each rejected and escalated.
- **Policy ambiguity** (a cited section whose Policy Agent rule has `policy_ambiguous=True`): escalated with the reason.
- **Fact ambiguity** (S-15, `fact_ambiguous=True`, route `need_more_evidence`): left alone (D9).
- **Cautious routes are never ruled out:** S-02 with route `need_more_evidence` or `manual_escalation` passes despite two `true` checks.
- **No draft** (synthesize failed validation or raised): `gate(None, ...)` returns rejected, `manual_escalation`, reason `"no draft from synthesize"` (D5).
- **Duplicate:** S-11 oracle draft → `existing_case_id == "C-002"`, **not** rejected, route stays `need_more_evidence`. C-001 (closed) does not block S-05; C-003 (open, other category) does not block S-13.
- **Returned payment does not count as received** (LF.v3.s4): on S-11, a cited late-fee section gives `late_fee_unwarranted=false` even though E-0144 was received in grace, because its chain holds a `payment_returned`.
- **Note-driven cases the gate cannot catch** (S-03 → `explain_events`, S-10 → `explain_events`, S-12): pass the gate. This is by design and is written down in the tests as the documented limit, not hidden.
- **No late fee in play** (S-06, S-08, S-16): late-fee checks return `unknown`, never `false`.

### Out of scope

- Sync, synthesize, create_review_case, the LangGraph orchestrator, graph state. `pipeline.investigate` is unchanged.
- The `confidence` consistency rule (an eval check, not the gate's).
- Applying checks for sections the LLM did **not** cite (architecture §4 step 3 says cited sections; a "run every check" variant is a v2 idea, noted in §12).
- **Forbidden by name:** no rule-language or DSL for conditions; no `checks/` package or one-class-per-check hierarchy (five functions and two dicts); no new `config.py` constants; no change to `derive.py`, `policy_agent.py`, `account_agent.py`, `data.py`, `tools.py`, `pipeline.py`; no spreadsheet or policy-file edits; no new eval flag; no change to the diagram.

## 4. File inventory (closed)

| Path | Action | Owns |
|---|---|---|
| `dispute_agent/gate.py` | CREATE | Policy constants (P1–P7), `REGISTRY`, `RULES_OUT`, `add_business_days()`, the five checks, `run_checks()`, `Draft`, `SectionCheck`, `GateResult`, `find_duplicate()`, `gate()` |
| `tests/test_gate.py` | CREATE | Every test in §10 |
| `dispute_agent/__main__.py` | UPDATE | `cmd_ask` prints the "gate checks" and "cited sections" lines after the policy block |
| `dispute_agent/evaluate.py` | UPDATE | `score()` gains `gate_ok`; `HEADER`/`row_line` gain the `gate` column; `CHECKS` gains `gate_ok`; each row's JSON gains `check_results` |
| `docs/architecture.md` | UPDATE → v0.5.2 | §4: `Draft` is the LLM's fields; duplicates are recorded, not rejected; `gate(None)`. "Graph state": `check_results` holds all five verdicts plus per-section results; add `existing_case_id` |
| `README.md` | UPDATE | "Where each step lives": a Gate row → `gate.py`; "Not built yet" row shrinks to sync, synthesize, create review case |
| `docs/plans/gate.md` | CREATE | This plan, copied verbatim at the end |

**Not touched:** everything else, including `data/`, `derive.py`, `policy_agent.py`, `pipeline.py`, `config.py`, `conftest.py`, the diagram. Touching anything else: stop and ask.

**Read first:**
- [docs/architecture.md](docs/architecture.md) §4, §5, "Who owns each output field", "Graph state", D6, D7, D9.
- [data/README.md](data/README.md) "Policy assumptions" (P1–P7) and "Event timestamp conventions".
- [data/policies/README.md](data/policies/README.md) "Gate registry (D7)" and "Expected policy citations per scenario".
- [data/policies/LF_v3_payments_grace_late_fees.md](data/policies/LF_v3_payments_grace_late_fees.md) s3–s5, [AP_v1_autopay.md](data/policies/AP_v1_autopay.md) s4–s5, [RR_v1_returned_reversed_payments.md](data/policies/RR_v1_returned_reversed_payments.md) s2, s3, s7, [RF_v1_refunds.md](data/policies/RF_v1_refunds.md) s2, s4.
- [dispute_agent/derive.py](dispute_agent/derive.py) (`DerivedFacts`, `_chain_state`, `_payment_for_late_fee` for the window idea), [dispute_agent/data.py](dispute_agent/data.py) (`Route`, `Event`, `ReviewCase`, `Dataset.chain_of`, `events_for_account`, `cases_for_account`), [dispute_agent/policy_agent.py](dispute_agent/policy_agent.py) (`PolicySection`, `in_force`, `RuleReading`, `PolicyFindings`), [dispute_agent/config.py](dispute_agent/config.py) (`WORLD_TODAY`).
- [dispute_agent/__main__.py](dispute_agent/__main__.py) `cmd_ask`; [dispute_agent/evaluate.py](dispute_agent/evaluate.py) `score`, `HEADER`, `row_line`, `CHECKS`.
- [tests/test_derive.py](tests/test_derive.py) (`DEV_IDS`, `oracle_findings`), [tests/test_policy_agent.py](tests/test_policy_agent.py) (`facts()`, `rule()`).

## 5. Closed decisions (do not re-litigate)

| # | Decision | Why this, not X |
|---|---|---|
| G1 | **`Draft` is defined in `gate.py`** and holds only what the LLM writes: `review_route`, `event_ids`, `policy_source_ids`, `confidence`, `open_questions`, `case_note`. Synthesize (next plan) imports it as its output schema. `category` and `facts` are code-owned and stay in graph state | The consumer defines the contract. `synthesize.py` does not exist; a separate `draft.py` breaks the one-file-per-step layout for one class. Moving the import later is one line |
| G2 | **Checks read the whole account's events plus `DerivedFacts`, never the draft's citations.** The disputed event and `evidence_events` pick which fee or refund is in play; the payments that decide it are found by scanning the account | A check is a fact about the records. An LLM that omits the grace-period payment must not thereby hide it |
| G3 | **All five checks are always computed and recorded; only the checks of cited, registered sections rule routes out** (architecture §4 step 3) | Computing all five costs nothing and makes `ask`/eval informative. Applying uncited checks is a behaviour change to the spec; §12 |
| G4 | **A duplicate is recorded as `existing_case_id`, never a rejection** | S-11's expected route is `need_more_evidence` with `expected_existing_case_id=C-002`; create_review_case (later) returns the existing case |
| G5 | **Any failed check sets `gate_rejected=True`, even when the route already is `manual_escalation`** | D6: evals must tell "agent escalated correctly" from "gate rescued it" (S-07) |
| G6 | **Policy constants live in `gate.py`:** `GRACE_DAYS=10`, `LATE_FEE=25.0`, `RETURN_FEE=15.0`, `REFUND_BUSINESS_DAYS=10`, `HOLIDAYS={2026-09-07, 2026-10-12}` | They are P1–P7, the gate's rules, not run-time limits like `TOOL_BUDGET` |
| G7 | **Tests run on all 16 scenarios**, held-out included | The gate is deterministic code written from P1–P7, which were authored against all 16. Held-out protects the LLM eval, which this plan never runs on held-out |
| G8 | **`gate(None, ...)` is a rejection** with reason `"no draft from synthesize"` | D5: a failed step is recorded and the run continues; the cautious route wins |
| G9 | **Verdicts are the strings `"true" | "false" | "unknown"`** (`Verdict = Literal[...]`) | They are printed, JSON-dumped and compared in tests; a `bool | None` reads badly in all three |
| G10 | **Business days are Mon–Fri minus `HOLIDAYS`**, counted forward from the request date; overdue means `WORLD_TODAY.date() > deadline` | Matches data/policies/README.md. Verified: Sep 3 → Sep 18, Sep 29 → Oct 14, Oct 6 → Oct 21, Sep 24 → Oct 8 |
| G11 | **"Received" means `payment_attempted` or `payment_posted`**, judged by `occurred_at` (P2); a payment whose chain holds a `payment_returned` or `payment_reversed` is not received (LF.v3.s4) | P1–P3 plus the policy text. `payment_scheduled` is an intent, not funds |
| G12 | **The late fee in play** is the disputed event if it is a late fee (`fee_assessed`, `amount == 25.0`, `related_event_id is None`); else the latest late fee in `evidence_events`; else none → `unknown` | Mirrors `derive_states`'s choice of the disputed event; keeps the gate off the LLM's prose |
| G13 | **The grace window comes from the fee's own timestamp** (P3: assessed 00:05 the day after grace ends): `grace_end = 23:59:59.999999 on fee date − 1`, `due = grace_end − GRACE_DAYS` at 00:00, `window_lo = due − 10 days` | No account lookup, no duplication of `derive._payment_for_late_fee`. The 10-day look-back before the due date matches derive's |
| G14 | **`ask`/`eval` call `run_checks` + `REGISTRY` directly; no provisional draft is built** | Nothing fakes a route. The eval's `gate_ok` builds the one oracle-route draft it needs inside `score()` |
| G15 | **The ID and date checks intentionally repeat `derive_states` (off-account events) and the Policy Agent's date filter + `check_citations`.** The gate re-checks the draft anyway | Those steps clean the LLM's *inputs*; the draft is a fresh LLM output written later by synthesize, which can re-cite an off-account event it saw in the transcript or quote a section from memory. Nothing else checks what goes to the human. Hygiene upstream, authorization here; the repeat is a few dict lookups |

## 6. Architecture / schema

### Code vs model split

Every field this plan produces is owned by code. No prompt is touched.

| Output field | Where it comes from | Owner |
|---|---|---|
| `check_results` (five verdicts) | Event fields on the account + `DerivedFacts` | code |
| `section_checks` (per cited section: check, verdict, rules_out) | `REGISTRY` × `check_results` | code |
| `route` (final) | Draft's route, forced to `manual_escalation` on rejection | code (LLM proposed) |
| `gate_rejected`, `gate_reasons` | The five gate steps below | code |
| `existing_case_id` | `review_cases` with status `open`, same account, category, disputed event | code |

### `gate.py` layout (top to bottom)

```python
"""The gate: our own P1–P7 checks over event fields, keyed by policy section ID. No LLM. ..."""

# --- policy constants (P1–P7) ---
GRACE_DAYS, LATE_FEE, RETURN_FEE, REFUND_BUSINESS_DAYS, HOLIDAYS
Verdict = Literal["true", "false", "unknown"]
Confidence = Literal["high", "medium", "low"]

# --- registry: section ID -> check name or "procedural" (mirrors data/policies/README.md) ---
REGISTRY: dict[str, str]        # 28 entries: LF.v3.s1–s8, AP.v1.s1–s6, RR.v1.s1–s8, RF.v1.s1–s6
RULES_OUT: dict[str, Route]     # late_fee_unwarranted/lender_side_failure/refund_overdue -> explain_events;
                                # customer_cancelled_autopay/return_fee_valid -> adjustment_review

# --- the five checks (each: (ds, account_id, derived) -> Verdict) ---
def add_business_days(day: date, n: int) -> date
def _late_fee_in_play(ds, derived) -> Event | None                      # G12
def _grace_window(fee: Event) -> tuple[datetime, datetime, datetime]    # (window_lo, due, grace_end), G13
def _received_in_grace(ds, account_id, lo, grace_end) -> list[Event]    # G11
def _autopay_on(ds, account_id, at: datetime) -> bool                   # latest autopay_* event at/before `at` is enrolled
def late_fee_unwarranted(ds, account_id, derived) -> Verdict
def lender_side_failure(ds, account_id, derived) -> Verdict
def customer_cancelled_autopay(ds, account_id, derived) -> Verdict
def return_fee_valid(ds, account_id, derived) -> Verdict
def refund_overdue(ds, account_id, derived) -> Verdict
CHECKS = {name: fn, ...}  # in the README's order
def run_checks(ds, account_id, derived) -> dict[str, Verdict]

# --- the draft (what the LLM writes; synthesize imports this) ---
class Draft(BaseModel):
    review_route: Route
    event_ids: list[str]
    policy_source_ids: list[str]
    confidence: Confidence
    open_questions: list[str] = []
    case_note: str

# --- the gate ---
class SectionCheck(BaseModel):  section_id, check: str, result: Verdict | None, rules_out: Route | None
class GateResult(BaseModel):
    route: Route; gate_rejected: bool; gate_reasons: list[str]
    check_results: dict[str, Verdict]; section_checks: list[SectionCheck]; existing_case_id: str | None
def find_duplicate(ds, account_id, category, disputed_event_id) -> str | None
def gate(draft: Draft | None, account_id, category, derived, policy: PolicyFindings | None,
         sections: dict[str, PolicySection], ds) -> GateResult
```

### The five checks, exactly

Inputs: `ds`, `account_id`, `derived: DerivedFacts`. `d` = `ds.events[derived.disputed_event_id]` or `None`. `evid` = `derived.evidence_events` (already on-account, includes candidates).

| Check | Returns `unknown` when | `true` when | else |
|---|---|---|---|
| `late_fee_unwarranted` (P1–P3) | no late fee in play (G12) | a received payment (G11) has `window_lo ≤ occurred_at ≤ grace_end` (G13) | `false` |
| `lender_side_failure` (P5) | `d` is a `payment_returned`/`payment_reversed` (a keying error lives only in notes, S-10), or no late fee in play | for any chain head in `[window_lo, grace_end]` (`type in PAYMENT_TYPES`, `related_event_id is None`), its chain shows: an autopay `payment_scheduled` with no `payment_posted` while `_autopay_on(head.occurred_at)`; **or** a `payment_attempted` with no `payment_posted`; **or** a received event with `occurred_at ≤ grace_end` and `posted_at > fee.occurred_at` | `false` |
| `customer_cancelled_autopay` (P6) | no late fee in play | the latest `autopay_*` event at/before `due` is `autopay_cancelled` **and** no received payment in `[window_lo, grace_end]` | `false` |
| `return_fee_valid` (P4) | `d is None`, or `d`'s chain has no linked `fee_assessed` | the first linked fee in the chain has `amount == RETURN_FEE` and its parent is a `payment_returned` | `false` (e.g. a fee linked to a reversal) |
| `refund_overdue` (P7) | no `refund_requested` among `[d] + evid` | any such request whose chain has no `refund_issued` and `WORLD_TODAY.date() > add_business_days(request date, REFUND_BUSINESS_DAYS)` | `false` |

### Verified on the data (prototype, 2026-10-26, oracle inputs from the answer key)

| Scenario | Route | Verdicts that are not `unknown` | Cited sections' checks rule out | Oracle rejected? |
|---|---|---|---|---|
| S-01 | explain_events | lfu=false, lsf=false, **cca=true** | adjustment_review | no |
| S-02 | adjustment_review | **lfu=true, lsf=true**, cca=false | explain_events | no |
| S-03 | manual_escalation | lfu=false, lsf=false, cca=true | — (procedural) | no |
| S-04 | adjustment_review | lfu=false, **lsf=true**, cca=false | explain_events | no |
| S-05 | adjustment_review | **lfu=true, lsf=true**, cca=false | explain_events | no |
| S-06 | need_more_evidence | — | — | no |
| S-07 | manual_escalation | lfu=false, lsf=false, cca=false | — | no |
| S-08 | explain_events | — | — | no |
| S-09 | explain_events | **rfv=true** | adjustment_review | no |
| S-10 | adjustment_review | — (lsf=unknown, note-driven) | — | no |
| S-11 | need_more_evidence | rfv=true | — (procedural) | no; `existing_case_id=C-002` |
| S-12 | manual_escalation | rfv=true | — | no |
| S-13 | manual_escalation | **ro=true** | explain_events | no |
| S-14 | explain_events | ro=false | — | no |
| S-15 | need_more_evidence | ro=true | — (procedural) | no |
| S-16 | need_more_evidence | — | — | no |

(lfu = late_fee_unwarranted, lsf = lender_side_failure, cca = customer_cancelled_autopay, rfv = return_fee_valid, ro = refund_overdue.) **The implementation must reproduce this table**; it is pinned in `test_checks_pinned`.

### `gate()` steps, in order

Every step appends to `reasons`; nothing short-circuits, so the trace shows every failure.

0. `draft is None` → reasons `["no draft from synthesize"]`; skip to 6.
1. **IDs.** Each `event_ids` entry: in `ds.events` and `account_id` matches, else `"event E-xxxx is unknown"` / `"event E-xxxx is on A-yyyy, not A-zzzz"`. Each `policy_source_ids` entry: in `sections`, else `"policy XX.v9.s9 is unknown"`.
2. **Dates.** Each known section: `in_force(section, derived.policy_date.date())`, else `"policy LF.v2.s3 was not in force on 2026-10-14"`.
3. **Registry and ambiguity.** Each known, in-force section: in `REGISTRY`, else `"policy EX.v1.s1 is not registered to a check"` (policy ambiguity → escalate). If `policy` is given and any `policy.rules` entry with this `section_id` has `policy_ambiguous`, `"policy LF.v3.s5 reading is ambiguous: <ambiguity_reason>"`.
4. **Checks vs route.** `check_results = run_checks(...)`. For each registered, non-procedural cited section, build a `SectionCheck`; if its verdict is `"true"` and `RULES_OUT[check] == draft.review_route`, `"late_fee_unwarranted is true (LF.v3.s2), which rules out explain_events"`.
5. **Route value.** Enforced by `Draft.review_route: Route` at construction (a bad value never reaches the gate; synthesize records the `ValidationError` as "no draft"). The gate does not re-check it.
6. **Duplicate.** `existing_case_id = find_duplicate(ds, account_id, category, derived.disputed_event_id)` (open case, same three keys; `None` disputed never matches). Not a reason.
7. **Result.** `gate_rejected = bool(reasons)`; `route = "manual_escalation" if gate_rejected else draft.review_route`.

### Schema / metrics count against §3

- No tables, columns, spreadsheet columns or dependencies.
- Eval: one column `gate`, one check key `gate_ok` (added to `CHECKS`), one JSON key `check_results` per row.

## 7. API surface

| Name | Signature | Notes |
|---|---|---|
| `run_checks` | `(ds, account_id, derived) -> dict[str, Verdict]` | Always all five, README order |
| `gate` | `(draft, account_id, category, derived, policy, sections, ds) -> GateResult` | Pure; never raises on bad input, records reasons |
| `find_duplicate` | `(ds, account_id, category, disputed_event_id) -> str | None` | Used again by create_review_case later |
| `add_business_days` | `(day, n) -> date` | Public so refunds elsewhere can reuse it |
| `Draft`, `GateResult`, `SectionCheck`, `Verdict`, `Confidence`, `REGISTRY`, `RULES_OUT` | models/constants | Synthesize imports `Draft` and `Confidence` |
| `evaluate.score` | gains `sections: dict[str, PolicySection] | None = None` | `gate_ok` only when `policy` and `sections` are both given |

**`gate_ok` in `score()`:** `gate(Draft(review_route=s.expected_route, event_ids=derived.relevant_event_ids, policy_source_ids=[r.section_id for r in policy.rules], confidence="medium", case_note=""), s.account_id, s.expected_category, derived, policy, sections, ds).gate_rejected is False`. Row JSON gets `check_results` (the five verdicts) and `gate_reasons`.

**`cmd_ask` addition** (after the policy block): the two lines shown in §2, built from `run_checks` and `REGISTRY` over `[r.section_id for r in policy.rules]`; uncited or procedural sections print as `procedural`.

## 8. Migration plan

None. No data, schema or file moves.

## 9. Ordered steps

Each step's tests pass before the next starts.

0. **Baseline.** `.venv/bin/python -m pytest tests -q` → 50 passed. Record it.
1. **`gate.py`, checks only.** Constants, `REGISTRY`, `RULES_OUT`, `add_business_days`, the helpers, the five checks, `run_checks`. Tests: `test_registry_*`, `test_business_days`, `test_checks_pinned`, `test_returned_payment_not_received`. Pass.
2. **`gate.py`, the gate.** `Draft`, `SectionCheck`, `GateResult`, `find_duplicate`, `gate`. Tests: the rest of §10. Pass A1, A2.
3. **Wiring.** `cmd_ask` lines; `score`/`HEADER`/`row_line`/`CHECKS`/row JSON in `evaluate.py`. Run A3, then A4; paste the eval table into the final message and list every `gate = X` with its `gate_reasons`.
4. **Docs.** `architecture.md` → v0.5.2 (§4 and state rows per §4 inventory), `README.md` rows, copy this plan to `docs/plans/gate.md`.
5. **Fresh-context review.** Re-read `gate.py` and `test_gate.py` against §3 budgets, §4 inventory and the §6 table. Run A1, A2, A5.

## 10. Tests

`tests/test_gate.py`, no LLM. Fixtures `ds` and `sections` from `conftest.py`. Helpers at the top:

- `ALL_IDS = sorted(ds.scenarios)` built the way `test_derive.DEV_IDS` is (module-level `load_dataset`), all 16 (G7).
- `oracle_derived(ds, s)`: `derive_states(oracle_findings(s), s.expected_category, s.account_id, ds)`, importing `oracle_findings` from `test_derive`.
- `oracle_draft(s)`: `Draft(review_route=s.expected_route, event_ids=s.expected_required_event_ids, policy_source_ids=s.expected_policy_ids, confidence="medium", case_note="")`.
- `run(ds, sections, s, draft, policy=None)`: `gate(draft, s.account_id, s.expected_category, oracle_derived(ds, s), policy, sections, ds)`.

| Section | Test | Criterion |
|---|---|---|
| registry | `test_registry_matches_readme`: 28 entries; every `status == "current"` section whose ID doesn't start with `EX.` is registered; no `LF.v2.`/`EX.v1.` key; the five check names in `REGISTRY.values()` equal `RULES_OUT.keys()` equal `CHECKS.keys()` | A1 |
| dates | `test_business_days`: the four pairs in G10 | A1 |
| checks | `test_checks_pinned[S-xx]` (16, parametrized): `run_checks(oracle_derived)` equals the §6 table row, with every unlisted check `"unknown"` | A1 |
| checks | `test_returned_payment_not_received`: S-11 derived with `disputed_event_id="E-0159"` (the late fee) → `late_fee_unwarranted == "false"` | A1 |
| gate | `test_oracle_draft_passes[S-xx]` (16): `gate_rejected is False`, `route == s.expected_route`, `existing_case_id == s.expected_existing_case_id` | A1 |
| gate | `test_wrong_route_caught`: table `[(S-01, adjustment_review), (S-02, explain_events), (S-04, explain_events), (S-05, explain_events), (S-09, adjustment_review), (S-13, explain_events)]` → rejected, route `manual_escalation`, one reason naming the check | A1 |
| gate | `test_note_driven_limit`: `[(S-03, explain_events), (S-10, explain_events), (S-14, adjustment_review)]` → **not** rejected (the documented limit of code-only checks) | A1 |
| gate | `test_cautious_routes_never_ruled_out`: S-02 oracle draft with route `need_more_evidence`, then `manual_escalation` → not rejected, route unchanged | A1 |
| gate | `test_off_account_citation`: S-07 draft `event_ids=["E-0157", "E-0135"]`, route `manual_escalation` → `gate_rejected is True`, route `manual_escalation`, a reason contains `"E-0135"` and `"A-1008"`; S-06 with `E-0158` → rejected | A1 |
| gate | `test_unknown_event`: S-01 draft with `E-9999` → rejected, reason `"event E-9999 is unknown"` | A1 |
| gate | `test_policy_id_problems`: S-02 oracle draft plus one extra ID each: `XX.v9.s9` (unknown), `LF.v2.s3` (not in force on 2026-10-14), `EX.v1.s1` (unregistered) → each rejected with the matching reason text | A1 |
| gate | `test_policy_ambiguity_escalates`: S-02 oracle draft, `policy=PolicyFindings(policy_date=..., category=..., retrieved=[], rules=[rule("LF.v3.s5")` with `policy_ambiguous=True, ambiguity_reason="x"`]) → rejected, route `manual_escalation`, reason contains `"ambiguous"` | A1 |
| gate | `test_fact_ambiguity_left_alone`: S-15 oracle → not rejected, `need_more_evidence` (D9) | A1 |
| gate | `test_no_draft`: `gate(None, ...)` for S-01 → rejected, `manual_escalation`, `gate_reasons == ["no draft from synthesize"]`, `check_results` still filled | A1 |
| gate | `test_bad_route_rejected_by_model`: `Draft(review_route="waive_fee", ...)` raises `ValidationError` | A1 |
| gate | `test_duplicate_rule`: `find_duplicate` → `(A-1005, missing_payment, E-0131) is None` (closed), `(A-1012, returned_reversed_payment, E-0152) == "C-002"`, `(A-1014, refund_status, E-0093) is None`, `(A-1012, returned_reversed_payment, None) is None` | A1 |
| gate | `test_all_reasons_collected`: S-07 draft with `E-0135` **and** `XX.v9.s9` → two reasons (no short-circuit) | A1 |
| CLI | `ask` real run shows both lines | A3 |
| CLI | `eval` dev shows the `gate` column | A4 |

## 11. Edge cases / footguns

- **`chain_of` on an unlinked late fee returns only the fee.** The payment in question must be found by scanning `events_for_account` inside the window (G13), not the chain.
- **`datetime` vs `date`.** `in_force` takes a `date`; `derived.policy_date` is a `datetime`. `grace_end` must be a `datetime` (`datetime.combine(day, time.max)`) to compare with `occurred_at`.
- **Two fees on one account** (S-11: a $15 return fee linked to the return and a $25 late fee unlinked). `_late_fee_in_play` must require `related_event_id is None` **and** `amount == LATE_FEE`; `return_fee_valid` must look at the disputed chain only.
- **`posted_at` may be `None`** (`payment_attempted`, `payment_scheduled`). Guard before comparing.
- **`_autopay_on` with no `autopay_*` events at all** → `False` (so a never-enrolled account's scheduled draft is not a lender failure).
- **Fee amounts are floats** rounded to 2 places by the `Event` validator; `== 25.0` is safe.
- **Import direction.** `gate.py` imports from `data`, `derive`, `policy_agent`, `config`. Nothing already in the package may import `gate` except `__main__` and `evaluate`. Never import `pipeline` or `evaluate` from `gate`.
- **`policy` may be `None` or have `rules == []`** (Policy Agent errored). The registry and date checks still run on the draft's IDs; the ambiguity check just finds nothing.
- **`score()` builds the oracle-route draft from `derived.relevant_event_ids`**, which are already on-account, so `gate_ok` measures policy-side problems and check contradictions, not the Account Agent's citations (those are `forb`/`drop`).
- **`CHECKS` totals in `evaluate.py` index `r["checks"][k]`**; `gate_ok` must be set on every scored row (policy and sections are always present in `run_eval`).
- **Never run `--split heldout`.** Tests on all 16 are fine; the LLM eval is not.
- **Do not "fix" a §6 table mismatch by editing the table.** A mismatch means the check drifted from the spec; re-read the check.

## 12. Open questions

All closed. One idea deliberately parked for a later plan, not this one: applying every `true` check regardless of citation (would catch S-02 → `explain_events` even when the LLM cites only procedural sections). It changes architecture §4 step 3, so it needs a decision first.

## Execution-discipline note

Read the whole plan first. Execute §9 in order and run its tests at each step. Re-read §3 Budgets and §4 File inventory before each step; on hitting a budget or needing a file not in the inventory, stop and ask. If the §6 table cannot be reproduced for a scenario, stop and report which check and why, with the events involved. Never narrow scope silently. End with a fresh-context review (§9 step 5), the eval table, and the list of `gate = X` rows.
