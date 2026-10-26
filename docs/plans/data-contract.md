# Plan: Data contract spec for the payment dispute agent

## 1. Status

- **Version 1.1, 2026-10-23.** v1.1 cuts the plan to the spec doc only; v1.0 also planned the code.
- Implements the "data contract" line of Day 1 in [the brief](../docs/project_brief.md) (v0.4, section 7).
- The repo contains only the brief.

## 2. Why & goal

**The bet:** if the shape of the data is fixed before any rows are written, the tools, the validator and the evaluators can all be built against one stable definition. Renaming a column on Day 2 would break all three.

**Use case:** Rachit opens Excel, builds `events.xlsx` from the doc's column table, and knows from the per-type rules exactly which cells a `payment_returned` row must fill.

**This plan covers:** one plain-language doc, `docs/data_contract.md`. It defines four tables (accounts, events, existing review cases, eval scenarios), the shape of the agent's output, and the rules that tie them together.

**This plan does not cover:**
- policy documents (their shape or content)
- any synthetic data rows or fixtures
- any code

## 3. Definition of Done

### Acceptance criteria

The doc exists and has every section:

```bash
test -f docs/data_contract.md
for h in "## Conventions" "## accounts.xlsx" "## events.xlsx" "## review_cases.xlsx" "## scenarios.xlsx" "## Agent output" "## Rules across files" "## Deferred to the policy step"; do grep -qF "$h" docs/data_contract.md || { echo "missing: $h"; exit 1; }; done
```

Then Rachit reviews and approves the doc in chat. His approval is the real pass signal.

### Budgets

| Budget | Max |
|---|---|
| New files | 1 (`docs/data_contract.md`) |
| Lines in the doc | 300 |
| New code files | 0 |
| New DB tables / columns | 0 |
| New metric / event names | 0 |
| New dependencies | 0 |

### Must-handle edge cases (the doc must tell Rachit how to enter these)

- **Blank cells.** A blank cell means "no value".
- **Dates.** Use an Excel date/time cell or ISO text such as `2026-09-05 09:00`. No timezones.
- **Number-like text.** IDs, `reference` and `bank_last4` must be formatted as Text, or Excel will drop leading zeros.
- **Lists in one cell.** Use comma-separated IDs, for example `E-001, E-002`.
- **True/false.** Use Excel TRUE/FALSE.
- **Money.** Enter dollars with at most 2 decimals, always positive.

### Out of scope

**Features not being built:**
- policy shape or content
- fixtures or data rows (apart from one row per table labeled "illustration, not data")
- template workbook
- Python models, loader, checker, tools, graph

**Tempting additions, forbidden by name:**
- No code files.
- No JSON Schema.
- No second input format (YAML/JSON/CSV).
- No policy columns "for later".
- No template .xlsx.
- No ID-prefix enforcement (prefixes are only recommended).
- No separate sheet per event type.

## 4. File inventory (closed)

| Path | Action | Why |
|---|---|---|
| `docs/data_contract.md` | CREATE | The contract Rachit reviews and builds his sheets from. |

**Read first:** [the brief](../docs/project_brief.md), sections 3, 5 and 6.

## 5. Closed decisions (do not re-litigate)

| # | Decision | Why this, not X |
|---|---|---|
| D1 | One `.xlsx` per table: accounts, events, review_cases, scenarios. | Rachit's choice over one workbook with tabs. |
| D2 | Shared tables; scenarios point into them by ID. | Matches the brief (shared accounts make cross-account traps possible). Per-scenario folders were declined. |
| D3 | Policy is left out entirely, including its shape. | Rachit's choice; it comes in the policy step. |
| D4 | Spec doc only; code waits for approval and its own plan. | Rachit's choice. |
| D5 | No template workbook. | Rachit's choice. |
| D6 | One world date, `2026-10-20` (a Tuesday): every scenario is "received" that day. | Relative dates ("last Tuesday") need one anchor. A per-scenario date would clash with shared accounts. |
| D7 | Times are lender-local with no timezone. | No scenario turns on timezones. |
| D8 | Money is entered in dollars; code will store cents. | Dollars are natural in Excel; cents keep the math exact. |
| D9 | One flat events sheet plus a per-type rules table. | Easy to hand-write; still strict. |
| D10 | One optional `related_event_id`, same account only. | Covers every chain in the brief, and keeps wrong-account traps visible in the data. |
| D11 | Autopay on/off is recorded as events, not as an account column. | "Autopay was on" is about a date; only history answers it. |
| D12 | One free-text field, `memo`, holds rep note text, system memos and injection traps. | The brief puts injection in tool-returned memos. One field gives one place to read untrusted text. |
| D13 | Exactly one `trap` per scenario. | Matches the brief's tag table and slices cleanly. |
| D14 | A filled `expected_existing_case_id` means the scenario is a duplicate; blank means create a new case. | One column instead of two. |
| D15 | Facts for now: `disputed_event_id`, `payment_state`, `refund_state`. | The brief's typed facts minus the policy one (D3). |

## 6. Contract content

Section 6 of v1.0 drafted the full content: enums, column tables, per-type rules, scenario columns, output shape and cross-file rules. That content now lives in `docs/data_contract.md`, the single source of truth. Do not duplicate it here.

### Why these columns exist (code vs model)

Every column below is carried because an output field depends on it.

| Output field | Raw columns it depends on | Owner |
|---|---|---|
| `category` | `scenarios.rep_message` | model |
| `facts.disputed_event_id` | `events.occurred_at`, `amount`, `reference`, `channel` | model picks; code checks the account |
| `facts.payment_state` | `events.type`, `related_event_id` | code (see section 12) |
| `facts.refund_state` | `refund_requested` / `refund_issued` events | code (see section 12) |
| `event_ids` | all event columns | model cites; code rejects any off-account ID |
| `review_route` | facts, `memo` on rep notes, policy | model, plus code overrides |
| `case_id` | `review_cases.account_id`, `category`, `disputed_event_id`, `status` | code |

## 7–8. API surface / migration

None.

## 9. Ordered steps

1. Write `docs/data_contract.md` in plain language, with section headings exactly as in section 3.
   - Each table gets a column table and one row labeled "illustration, not data".
   - Include the Excel entry tips from section 3.
2. Run the section 3 commands.
3. **STOP** for Rachit's review. Apply his changes to the doc.

## 10. Tests

None (no code). Section 3's commands are the check.

## 11. Footguns

- **Illustrations can look like fixtures.** Keep exactly one row per table and label it.
- **Policy creep.** The `stale_policy` trap value stays in the list (it is in the brief), but no policy columns get added.

## 12. Open questions

All closed for this plan. Deferred:

- **`TODO(code)`:** Turning the doc into Python models, an Excel loader and a checker script is a separate plan, written after approval.
- **`TODO(policy)`:** The policy step adds:
  - the policy file shape
  - `scenarios.expected_policy_ids`
  - the `policy_timeline_met` fact
  - rules tying `stale_policy` scenarios to a superseded policy version
- **`TODO(agent-plan)`:** The brief has the LLM write all facts. `payment_state` and `refund_state` follow from the event chain by a written rule, so they are candidates for code ownership. Decide in the agent plan.
