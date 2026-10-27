# Policy documents: Agarwal Motor Finance

The servicing policy the Policy Agent retrieves from. Everything is fictional. Written to agree with assumptions P1–P7 in [`../README.md`](../README.md), so every scenario label follows from the records plus this text.

**TL;DR:** 4 current policies, 2 traps (a superseded late-fee version and an unrelated extensions policy), 37 sections. Each section is one retrieval chunk with its own ID. A script re-derived every late fee, return fee, refund deadline and AutoPay cancellation in `events.xlsx` from this text, and all of them match.

## Files

| File | Doc | Version | Effective | Status | Categories (retrieval filter) | Sections |
|---|---|---|---|---|---|---|
| `LF_v3_payments_grace_late_fees.md` | LF Payments, Grace Period and Late Fees | 3 | 2026-05-01 → | current | late_fee_autopay, missing_payment | 8 |
| `AP_v1_autopay.md` | AP AutoPay | 1 | 2025-01-01 → | current | late_fee_autopay | 6 |
| `RR_v1_returned_reversed_payments.md` | RR Returned and Reversed Payments | 1 | 2025-01-01 → | current | returned_reversed_payment, missing_payment | 8 |
| `RF_v1_refunds.md` | RF Refunds | 1 | 2025-01-01 → | current | refund_status | 6 |
| `LF_v2_payments_grace_late_fees.md` | **Trap:** LF version 2 | 2 | 2024-01-01 → 2026-04-30 | superseded | late_fee_autopay, missing_payment | 5 |
| `EX_v1_payment_extensions.md` | **Trap:** EX Payment Extensions | 1 | 2025-01-01 → | current | late_fee_autopay, missing_payment | 4 |

## Shape

- **Front matter** carries `doc_id`, `version`, `categories`, `effective_from`, `effective_to` (blank = still in force) and `status`. Every section inherits its document's dates.
- **One chunk per `##` section.** The heading starts with the section ID, so the loader splits on `## ` and takes the ID before the dash.
- **Section IDs** are `<doc>.v<version>.s<n>`, for example `LF.v3.s2`. The version is in the ID, so citing a superseded section is detectable from the ID alone.
- **Cross-references** between sections use the same IDs (for example, AP.v1.s4 points to LF.v3.s4).

## Wording that maps to routes

The policies use business language, not route names. The mapping the synthesize prompt and evals rely on:

| Policy says | Route |
|---|---|
| "eligible for fee adjustment review" | `adjustment_review` |
| "refer the case to a servicing supervisor" | `manual_escalation` |
| "ask the customer for proof / a bank letter / a statement"; "confirm with them" | `need_more_evidence` |
| Records plus a section that explains the outcome (fee valid, return normal, refund issued with trace) | `explain_events` |

## Our P1–P7 checks (evaluation only)

Five Python checks in [`dispute_agent/evaluation/policy_checks.py`](../../dispute_agent/evaluation/policy_checks.py) encode the policy assumptions P1–P7 from structured event fields. They run in the eval, never in the agent: the `policy_consistent` check asks whether P1–P7 rule out the final route, and the golden audit asks the same of every expected route (D7, D10 in `docs/architecture.md`). Citing a superseded or unrelated section (all of LF.v2.\*, all of EX.v1.\*) is scored by the eval's `policy_forbidden_clear` check.

| Check | Policy | Encodes sections | A true result rules out |
|---|---|---|---|
| `late_fee_unwarranted` | P1, P2, P3 | LF.v3.s1, LF.v3.s2, LF.v3.s4 | `explain_events` |
| `lender_side_failure` | P5 | LF.v3.s3, LF.v3.s5, AP.v1.s5, RR.v1.s7 | `explain_events` |
| `customer_cancelled_autopay` | P6 | AP.v1.s4 | `adjustment_review` |
| `return_fee_valid` | P4 | RR.v1.s2, RR.v1.s3 | `adjustment_review` |
| `refund_overdue` | P7 | RF.v1.s2, RF.v1.s4 | `explain_events` |

`refund_overdue` counts business days as Monday–Friday excluding federal holidays. Labor Day (Sep 7) and Columbus Day (Oct 12) fall inside the data window.

## Expected policy citations per scenario

The `expected_policy_ids` column in `scenarios.xlsx` is the minimum a correct answer cites. **Forbidden for every scenario:** any `LF.v2.*` or `EX.v1.*` section. Retrieval follows cross-references one hop after ranking.

| Scenario | Route | Required sections | Why those sections decide it |
|---|---|---|---|
| S-01 | explain_events | AP.v1.s4, LF.v3.s4 | Cancelled Aug 24, so manual payment was required; nothing received by Sep 18, so the fee stands |
| S-02 | adjustment_review | LF.v3.s2, LF.v3.s5 | Received Oct 13 16:42, inside grace; posted after the fee, which is a lender-side failure |
| S-03 | manual_escalation | AP.v1.s6, LF.v3.s8 | The rep note says AutoPay was active; the records say cancelled |
| S-04 | adjustment_review | AP.v1.s5, LF.v3.s5 | The draft never ran because of our processor outage |
| S-05 | adjustment_review | LF.v3.s3, LF.v3.s5 | Check received Oct 2 but held unapplied; it counts as paid on Oct 2 |
| S-06 | need_more_evidence | LF.v3.s6 | No payment on file; the customer must send proof |
| S-07 | manual_escalation | LF.v3.s6, RR.v1.s6 | The payment sits on another customer's loan; refer to a supervisor |
| S-08 | explain_events | LF.v3.s1, LF.v3.s2 | Bill-pay check arrived Oct 16; grace runs to Oct 25 and isn't extended over the weekend |
| S-09 | explain_events | RR.v1.s1, RR.v1.s2 | R01 after the debit showed is normal; $15 fee at return |
| S-10 | adjustment_review | RR.v1.s7, LF.v3.s5 | The ops note says the reversal was keyed in error; the payment is valid |
| S-11 | need_more_evidence | RR.v1.s4 | Only the bank can confirm a bank error; needs a bank letter |
| S-12 | manual_escalation | RR.v1.s1, RR.v1.s8 | R08 plus a note that she'd stop payment, which conflicts with her denial |
| S-13 | manual_escalation | RF.v1.s3, RF.v1.s4 | The note says a check was mailed; nothing was issued, and the request is past 10 business days |
| S-14 | explain_events | RF.v1.s2, RF.v1.s3 | ACH refund issued Oct 2 with a trace number, within 10 business days |
| S-15 | need_more_evidence | RF.v1.s6 | Two open requests (one overdue, one not yet due); confirm which first |
| S-16 | need_more_evidence | RF.v1.s5 | Only one August payment on file; the customer must send a bank statement |

## Traps

| Trap | What it tests | Why it looks relevant |
|---|---|---|
| LF v2 (superseded) | The date filter, and citing the right version | Same title and topics. Its rules would flip S-02: a 7-day grace and credit by posting date make the payment late |
| EX v1 (unrelated) | Relevance ranking within a category | Talks about late fees and installments; "an extension does not remove a late fee already assessed" reads like an answer |
