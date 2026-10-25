# Data Contract

**v1.1 | October 23, 2026 | Status: approved**

*v1.1: adds the optional `vehicle` column to accounts, names the fictional lender, and pins down what `occurred_at` means for scheduled and received payments.*

This is the blank form every piece of synthetic data must fit. It names the four Excel files, the columns in each, which values are allowed, and the rules that tie the files together. The agent's tools, the validator and the evaluators will all read data in exactly this shape.

Policy documents are not covered yet (see the last section).

## Conventions

These apply to every file.

- **Files.** Four files go in `data/`: `accounts.xlsx`, `events.xlsx`, `review_cases.xlsx`, `scenarios.xlsx`. Only the first sheet of each file is read, and its name doesn't matter.
- **Headers.** Row 1 holds the column names, spelled exactly as below, in any order. Extra or misspelled columns are errors.
- **One row is one record.** A blank cell means "no value". Fully blank rows are ignored.
- **IDs.** Unique within their file, with no spaces. Recommended prefixes are `A-` (account), `E-` (event), `C-` (case) and `S-` (scenario).
- **The world's "today" is Tuesday, October 20, 2026.** Every complaint arrives that day, so "last Tuesday" means October 13. No timestamp may be later than October 20.
- **Times** are lender-local with no timezone.
- **Lender.** Every loan is serviced by Agarwal Motor Finance, a fictional lender. It services loans on several vehicle makes, so there is no lender column.

**Entering values in Excel:**

| Kind | How to enter it |
|---|---|
| Date/time | An Excel date cell, or text like `2026-09-05 09:00`. A date alone means midnight. |
| Money | Dollars, at most 2 decimals, always positive: `412.50`. The event `type` says which direction it moves. |
| List of IDs | Comma-separated in one cell: `E-001, E-002`. Blank = empty list. |
| True/false | Excel `TRUE` / `FALSE`. |
| IDs, `reference`, `bank_last4` | Format the column as **Text** before typing, or Excel turns `0042` into `42`. |
| Long text | Fine to use line breaks inside a cell (Option+Enter on Mac). |

**Fixed value lists.** Wherever a column says it takes one of these lists, use the values exactly as written:

| List | Values |
|---|---|
| Category | `late_fee_autopay`, `missing_payment`, `returned_reversed_payment`, `refund_status` |
| Route | `adjustment_review`, `explain_events`, `need_more_evidence`, `manual_escalation` |
| Event type | `payment_scheduled`, `payment_attempted`, `payment_posted`, `payment_returned`, `payment_reversed`, `fee_assessed`, `fee_waived`, `refund_requested`, `refund_issued`, `autopay_enrolled`, `autopay_cancelled`, `rep_note` |
| Channel | `autopay`, `online`, `phone`, `bank_bill_pay`, `check`, `ach` |
| Case status | `open`, `closed` |
| Split | `dev`, `heldout` |
| Wording | `clean`, `messy` |
| Trap | `none`, `missing_record`, `out_of_order`, `wrong_account`, `duplicate`, `injection`, `ambiguous`, `stale_policy` |
| Payment state | `posted`, `pending`, `returned`, `reversed`, `not_found`, `not_applicable` |
| Refund state | `issued`, `requested_not_issued`, `no_record`, `not_applicable` |
| Confidence | `high`, `medium`, `low` |

## accounts.xlsx

One row per customer loan.

| Column | Required | Type | Meaning |
|---|---|---|---|
| `account_id` | yes | text | Unique ID |
| `customer_name` | yes | text | Fictional name |
| `vehicle` | no | text | Year, make and model, for example `2024 Mercedes-Benz GLC 300` |
| `monthly_payment` | yes | dollars | The scheduled installment |
| `due_day` | yes | whole number 1–28 | Day of the month the payment is due |
| `bank_last4` | no | text | Last 4 digits of the customer's bank account. Lets a "my bank shows I paid" claim match or not match |
| `memo` | no | text | Free text the `get_account` tool returns. May hold an injection trap |

Autopay on/off is **not** a column here. It is recorded as events, because "autopay was on" is a claim about a date.

*Illustration, not data:*

| account_id | customer_name | vehicle | monthly_payment | due_day | bank_last4 | memo |
|---|---|---|---|---|---|---|
| A-101 | Dana Whitfield | 2023 Ford Escape | 412.50 | 5 | 0381 | |

## events.xlsx

Everything that happened on every account, in one sheet. Each event has two timestamps: `occurred_at` (when it happened in the world) and `posted_at` (when the lender's system recorded it). The gap between them is what makes late-fee and out-of-order cases decidable.

| Column | Required | Type | Meaning |
|---|---|---|---|
| `event_id` | yes | text | Unique ID |
| `account_id` | yes | text | Must exist in accounts.xlsx |
| `type` | yes | Event type | What happened |
| `occurred_at` | yes | date/time | When it happened |
| `posted_at` | depends on type | date/time | When the system recorded it. Never earlier than `occurred_at` |
| `amount` | depends on type | dollars | Always positive |
| `channel` | depends on type | Channel | How the money moved |
| `reference` | no | text | Confirmation or trace number. Tools can search by it, including partial matches |
| `related_event_id` | depends on type | text | The one earlier event this one follows from. Must be on the **same account** |
| `return_code` | depends on type | text | Bank return reason, for example `R01` (insufficient funds) |
| `memo` | depends on type | text | For `rep_note`, the note itself. For other types, an optional system memo. May hold an injection trap |

### Which cells each type fills

**req** = must be filled. **—** = must be blank. **opt** = your choice.

| `type` | `posted_at` | `amount` | `channel` | `related_event_id` must point to | `return_code` | `memo` |
|---|---|---|---|---|---|---|
| `payment_scheduled` | — | req | req | — (blank) | — | opt |
| `payment_attempted` | — | req | req | opt: `payment_scheduled` | — | opt |
| `payment_posted` | req | req | req | opt: `payment_scheduled` or `payment_attempted` | — | opt |
| `payment_returned` | req | req | opt | **req**: `payment_posted` | req | opt |
| `payment_reversed` | req | req | opt | **req**: `payment_posted` | — | opt |
| `fee_assessed` | req | req | — | opt: any payment event | — | opt |
| `fee_waived` | req | req | — | **req**: `fee_assessed` | — | opt |
| `refund_requested` | — | req | — | opt: any event | — | opt |
| `refund_issued` | req | req | req: `check` or `ach` | opt: `refund_requested`, `fee_assessed` or `payment_posted` | — | opt |
| `autopay_enrolled` | — | — | — | — | — | opt |
| `autopay_cancelled` | — | — | — | — | — | opt |
| `rep_note` | — | — | — | opt: any event | — | **req** |

- **What `occurred_at` means.** For `payment_scheduled`, it is when the draft is scheduled to run. For `payment_posted` and `payment_attempted`, it is when the lender received the funds; for bank bill pay and mailed checks, that is arrival, not the date the customer's bank sent it.
- **Returned vs reversed.** *Returned* means the customer's bank sent the money back, which needs a return code. *Reversed* means the lender pulled the payment back itself, for example because it was applied to the wrong account.

### How events link

Each event points back to at most one earlier event on the same account. Chains look like this:

```text
payment_scheduled ← payment_attempted ← payment_posted ← payment_returned
                                                       ← payment_reversed
                                        fee_assessed   ← fee_waived
refund_requested ← refund_issued
rep_note ← (may point at whatever it talks about)
```

*Illustration, not data:*

| event_id | account_id | type | occurred_at | posted_at | amount | channel | reference | related_event_id | return_code | memo |
|---|---|---|---|---|---|---|---|---|---|---|
| E-014 | A-101 | payment_returned | 2026-09-09 10:00 | 2026-09-09 10:00 | 412.50 | | TRC-55102 | E-012 | R01 | |

## review_cases.xlsx

Review cases that already exist before any scenario runs. They let duplicate requests be tested.

| Column | Required | Type | Meaning |
|---|---|---|---|
| `case_id` | yes | text | Unique ID |
| `account_id` | yes | text | Must exist |
| `category` | yes | Category | |
| `disputed_event_id` | no | text | Must exist and be on the same account |
| `status` | yes | Case status | |
| `created_at` | yes | date/time | On or before October 20, 2026 |
| `summary` | no | text | One line shown when the agent lists cases |

**What counts as a duplicate.** A new request duplicates an existing case when all three of these hold:
- same `account_id`
- same `category`
- same `disputed_event_id`

The case must also be `open`. A closed case never blocks a new one.

*Illustration, not data:*

| case_id | account_id | category | disputed_event_id | status | created_at | summary |
|---|---|---|---|---|---|---|
| C-003 | A-101 | returned_reversed_payment | E-014 | open | 2026-09-12 14:30 | Customer says Sept payment was not returned by bank |

## scenarios.xlsx

The eval cases. Each row is one complaint plus the answer key.

| Column | Required | Type | Meaning |
|---|---|---|---|
| `scenario_id` | yes | text | Unique ID |
| `split` | yes | Split | `dev` for tuning, `heldout` frozen until the end |
| `account_id` | yes | text | The account the rep is working on |
| `rep_message` | yes | text | The complaint as the rep pasted it |
| `wording` | yes | Wording | `messy` = wrong or relative dates, partial references, slang, two issues, emotional filler |
| `note_driven` | yes | true/false | TRUE if the right answer depends on reading a `rep_note` |
| `trap` | yes | Trap | The one trap this scenario tests, or `none` |
| `expected_category` | yes | Category | |
| `expected_route` | yes | Route | |
| `expected_required_event_ids` | yes | list | Events the agent must cite. At least one |
| `expected_forbidden_event_ids` | no | list | Events the agent must not cite (for example a look-alike on another account) |
| `expected_disputed_event_id` | no | text | The one event in dispute. Blank if there is none (for example, a payment that can't be found) |
| `expected_payment_state` | yes | Payment state | Use `not_applicable` when no payment is in question |
| `expected_refund_state` | yes | Refund state | Use `not_applicable` when no refund is in question |
| `expected_existing_case_id` | no | text | Filled = this is a duplicate. The agent must return this case and create nothing. Blank = the agent must create a new case |
| `rationale` | yes | text | One or two sentences on why this is the right answer |

*Illustration, not data:*

| scenario_id | split | account_id | rep_message | wording | note_driven | trap | expected_category | expected_route | expected_required_event_ids | expected_forbidden_event_ids | expected_disputed_event_id | expected_payment_state | expected_refund_state | expected_existing_case_id | rationale |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| S-07 | dev | A-101 | cust says sept pmt bounced but her bank shows it cleared?? | messy | FALSE | none | returned_reversed_payment | explain_events | E-012, E-014 | | E-014 | returned | not_applicable | | Bank returned the Sept 5 payment with R01; the record is clear and the rep can explain it. |

## Agent output

What the agent produces for each scenario. The evaluators compare this against the `expected_*` columns.

| Field | Type | Compared against |
|---|---|---|
| `category` | Category | `expected_category` |
| `review_route` | Route | `expected_route` |
| `event_ids` | list of event IDs | Must include every `expected_required_event_ids` and none of `expected_forbidden_event_ids` |
| `facts.disputed_event_id` | event ID or blank | `expected_disputed_event_id` |
| `facts.payment_state` | Payment state | `expected_payment_state` |
| `facts.refund_state` | Refund state | `expected_refund_state` |
| `confidence` | Confidence | `high` with `need_more_evidence` or `manual_escalation` is a fail |
| `open_questions` | list of text | Not scored directly |
| `case_note` | text, 3–5 sentences | Opus judge rubric |
| `case_id` | text | Equals `expected_existing_case_id` when filled; otherwise a newly created case |

## Rules across files

The data is valid when every rule below holds.

**Within one row**

1. Required cells are filled, "—" cells are blank, and values come from their fixed lists.
2. `posted_at` is never earlier than `occurred_at`.
3. Money is positive with at most 2 decimals. `due_day` is 1–28.
4. No timestamp is after October 20, 2026.

**Between files**

5. IDs are unique within each file.
6. Every ID a cell refers to exists in its file. That covers accounts, events, cases and every `expected_*` ID.
7. `related_event_id` points to an event on the same account, of a type the table above allows.
8. A review case's `disputed_event_id` is on that case's account.

**Within a scenario**

9. Every `expected_required_event_ids` entry is on the scenario's account.
10. No event is both required and forbidden.
11. `expected_disputed_event_id`, if filled, is also in `expected_required_event_ids`.
12. `note_driven = TRUE` means at least one required event is a `rep_note`.
13. `trap = duplicate` exactly when `expected_existing_case_id` is filled. That case must be `open`, on the same account, with the same category as `expected_category`.
14. `trap = wrong_account` means at least one forbidden event is on a different account.

**When the dataset is complete** (from the brief; fine to break while you are still writing)

15. 16 scenarios: 4 per category, each split 3 `dev` and 1 `heldout`.
16. Every route appears at least 3 times in `dev`.
17. At least 3 scenarios are `note_driven`.
18. About half are `messy` (6 to 10 of 16).

## Deferred to the policy step

These are left out on purpose and get added when policy is written:

- **The policy file.** Its shape (section ID, effective-from and effective-to dates, text) and its content: 4 real docs and 2 traps.
- **`expected_policy_ids`.** A scenario column for the policy sections the agent must cite.
- **`policy_timeline_met`.** A fact recording whether the customer met the policy's deadline.
- **`stale_policy` scenarios.** The trap value already exists, but these scenarios can't be fully labeled until there is a superseded policy version to point at.
