# Payment Dispute Review Agent — Project Brief

An internal agent that reads a customer's payment complaint, checks the account records, prior rep notes and servicing policy, then drafts a review case for a human rep with the evidence attached and a recommended review queue. It never touches money.

**Design rule:** the simplest system that still supports a good evaluation story.

## 1. The problem

A servicing rep at an auto lender gets complaints like:

- "I was charged a late fee but autopay was on."
- "My bank shows I paid, your system says I didn't."
- "Why was my payment reversed?"
- "Someone on the phone promised me a refund and it never came."

Today the rep reads the complaint, digs through account history, rereads notes from earlier calls, checks policy and writes up a case by hand. The agent does the digging and drafts the write-up. The rep still makes the decision.

### Why an LLM, and where it stops

**Language goes to the LLM. Rules and control flow go to code.**

| The LLM handles | Code handles |
|---|---|
| Interpreting vague or messy complaints | What runs next, the parallel fan-out and failure handling |
| Matching "the payment from Tuesday" to one specific event | Confirming that event is on this account, and deriving payment and refund state from its chain |
| Reading free-text notes from earlier rep calls | Filtering policy by dispute category and effective date |
| Reading policy written in prose, and flagging where it's unclear | Limiting citations to the event and section IDs the model was shown |
| Choosing the route and drafting the case note | Duplicate cases and the one write; scoring every route against policy rules P1–P7 in the eval |

## 2. What the agent produces

One drafted review case per dispute:

| Field | Meaning |
|---|---|
| `category` | Which of the four dispute types this is |
| `review_route` | Which human queue it goes to (table below) |
| `event_ids` | The account records, including rep notes, that support the recommendation |
| `policy_source_ids` | The policy sections that support it |
| `facts` | Typed facts code can check: disputed event, payment state, refund state |
| `confidence` | `high`, `medium` or `low` |
| `open_questions` | What is still unknown, if anything |
| `case_note` | Three to five plain sentences the rep can edit and file |
| `case_id` | The simulated review case that was created, or the existing one if it is a duplicate |

### The four routes

| `review_route` | When |
|---|---|
| `adjustment_review` | Records suggest the customer may be right; someone with authority should look at a correction |
| `explain_events` | Records and policy clearly explain what happened; the rep can walk the customer through it |
| `need_more_evidence` | Something critical is missing, usually from the customer, or the complaint could mean more than one record |
| `manual_escalation` | Records conflict, notes contradict records or the customer, evidence points at the wrong account, policy is unclear, or no draft could be written |

### The four categories

`late_fee_autopay`, `missing_payment`, `returned_reversed_payment`, `refund_status`

Category and route are independent: a reversal complaint can land on any route, depending on what the records say. The route is the only thing the agent proposes. There is no separate action field.

## 3. How it works

One LangGraph graph. Code decides what runs next; the LLM is called to classify, to run the two agents and to draft. The diagram and every step are in [architecture.md](architecture.md).

1. **Classify.** One LLM call picks the category.
2. **Investigate, in parallel.** The **Account Agent** (LLM + five read-only tools, at most six calls) finds the disputed event, or every candidate if the complaint doesn't say which. Code then confirms the event is on this account and derives its state. The **Policy Agent** filters policy sections by category and date, ranks them with BM25, and one LLM call turns them into structured rules with citations.
3. **Sync.** A code join checks that the two sets of findings line up: the account findings exist, each rule was in force on the disputed event's date, and the rules cover the category. Anything that doesn't line up is recorded and steers the draft to a cautious route. No agent is sent back, so every run takes the same path.
4. **Synthesize.** One LLM call writes the draft: route, citations, facts, confidence, open questions and case note. Its output schema accepts only the IDs it was shown. If no draft comes back, the route falls back to `manual_escalation`.
5. **Review.** The graph pauses for the rep, who approves, overrides the route with a reason, or answers the open questions once on `need_more_evidence`. A checkpointer saves state, so the run can resume later in a new process.
6. **Create review case.** One simulated, idempotent write under the final route. Only the servicing backend moves money, and the agent has no access to it.

Failures are recorded, not hidden. A tool error, a budget hit or an empty result is written to state, marked unknown and carried into the draft; the run continues. Unclear policy routes to `manual_escalation`; unclear or missing facts route to `need_more_evidence` with open questions.

**Tools.** Account Agent: `get_account`, `list_events`, `get_event`, `find_events_by_reference`, `list_review_cases`. Policy Agent: `search_policy`. Graph: `create_review_case`, called by code after review, never chosen by an LLM.

**Models.** Claude Sonnet 5.5 runs the agent. Claude Opus 5.5 judges case notes in the eval, so the agent isn't graded by the model that wrote the answer.

## 4. Synthetic data

Everything is fictional, and the right answer for every scenario follows from the records, including the notes. Details in [data/README.md](../data/README.md).

| Asset | Size | Purpose |
|---|---|---|
| Accounts | 17 | One per scenario, plus a look-alike account for the wrong-account trap |
| Events | 159 | About five months of history per account: scheduled, attempted, posted, returned, reversed, fee, refund and **rep note** events, linked to each other |
| Policy docs | 4 real + 2 traps | Late fees, autopay, returns, refunds. Traps: a superseded late-fee version and an unrelated extensions policy |
| Existing review cases | 3 | For duplicate tests |
| Eval scenarios | 16 | 12 dev, 4 held out |

What makes the labels checkable and gives the LLM real work:

- **Two timestamps per event.** `occurred_at` and `posted_at`. The gap decides the late-fee and out-of-order scenarios.
- **Effective-dated policy.** Each section has `effective_from` and `effective_to`. Citing the superseded version is a failure even when its text looks right.
- **Rep notes as events.** Free text from earlier calls, such as "Told customer refund mailed by check." Some disputes can only be resolved by comparing a note to the records or to the customer's claim.
- **Messy complaints.** Half the scenarios use realistic wording: relative dates, partial reference numbers, bank slang, two issues in one message.
- **Injection in tool output.** Trap instructions sit in `memo` fields the tools return, not in the rep's message.

## 5. Evaluation

Full method and results: [evaluation README](../dispute_agent/evaluation/README.md) and [validation log](history/validation.md).

### Dataset

Four scenarios per category: three dev, one held out. Every route appears three times in dev, so always escalating can't score well. Each scenario is tagged for slicing:

| Tag | Values |
|---|---|
| `wording` | `clean` or `messy` |
| `note_driven` | `true` if the answer depends on reading a rep note (4 scenarios) |
| `trap` | `none`, `missing_record`, `out_of_order`, `wrong_account`, `duplicate`, `injection`, `ambiguous` |

The held-out four were run twice after tuning, with nothing changed between the passes: once for the first release and once to capture the release experiment. With four cases they are a sanity check against overfitting, not a measurement. Slices of six or fewer are directional only.

### Metrics

**Six code-graded axes**, each PASS, PARTIAL or FAIL per scenario:

| Axis | Checks |
|---|---|
| `route` | Final route matches the label, and the draft chose it |
| `disputed_event` | The Account Agent picked the right event, or correctly none |
| `evidence` | Required event IDs cited, forbidden ones (another account's, a look-alike) absent |
| `policy` | Required sections retrieved and cited, superseded or unrelated sections absent |
| `confidence` | `high` confidence on a cautious route is a fail |
| `guardrails` | Right category, one case created (none for a duplicate), agents within budget, and the route not ruled out by our P1–P7 policy checks |

The P1–P7 checks are plain Python over structured event fields. They need no answer key, so they can also score live cases, and they audit the hand-written labels: all 16 pass.

**Four judge axes**, Opus scores each case note 0–3: no invented facts, matches the labeler's rationale, ready to file, no leaks or promises. The judge was calibrated against 12 hand-labelled dev notes and agreed within one point on every note.

### Process

1. Run the dev set, read the traces in LangSmith, find the most common or most dangerous failure.
2. Make one change, rerun, and compare per scenario and per axis (two experiments side by side in LangSmith, or the workbook's `Compare` sheet).
3. Run the held-out set after tuning and report the numbers as they are (run twice, untuned, to capture the experiment).

**Not built:** a code-only baseline on the same scenarios, and a judge sensitivity test on deliberately broken notes.

## 6. Boundaries

- Synthetic data only. No real customers or lender policy.
- The agent recommends and drafts; a human decides. Nothing it does moves money, waives fees or messages a customer.
- Wrong-account evidence always escalates, and so does a run that couldn't write a draft.
- A localhost web page for the rep, but no authentication and no real integrations.
- Time saved per case is a hypothesis to measure in production, not a claimed result.
