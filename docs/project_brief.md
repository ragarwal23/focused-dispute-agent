# Payment Dispute Review Agent — Project Brief

**Version 0.9 | October 26, 2026 | Timebox: 3 days**

*v0.9: matches [architecture.md](architecture.md) v0.7. The human decision moves inside the graph: after the gate the run pauses for the rep, who approves, overrides the route with a reason, or answers the open questions once on `need_more_evidence`. A checkpointer saves the state so the run resumes later. The eval approves every pause.*
*v0.8: matches [architecture.md](architecture.md) v0.6. Back to the v0.4 shape: the two agents run in parallel under the LangGraph orchestrator and meet at a sync join, which may send each agent back once (clarify when the Account Agent returned nothing, re-retrieve when a different policy set was in force on the disputed event's date). The Policy Agent's first read is on the complaint date. The gate, sync and synthesize keep their v0.7 behaviour; `create_review_case` is built.*
*v0.7: matches [architecture.md](architecture.md) v0.5. The agents run in sequence, Account then Policy, because policy retrieval needs the disputed event's date. LangGraph code, not the LLM, owns the run, and there are no retries or re-requests. The gate enforces policy with Python checks we wrote for P1–P7; the LLM's reading of the policy is advisory. Unclear policy escalates; unclear or missing facts go to `need_more_evidence`. The route is the only action. Data sizes updated to what was built.*
*v0.6: decisions D1–D6 closed (see [architecture.md](architecture.md)). Policy retrieval is a code filter by category and effective date, with vector search deferred; payment and refund states are derived by code; a gate rejection keeps the draft and adds `gate_rejected`.*
*v0.5: section 4 restated as an orchestrator with two specialist agents and a deterministic gate, delegation order fixed. Same steps as v0.4, named by owner. Policy retrieval no longer specified as vector search. Full detail in [architecture.md](architecture.md).*
**Rule for every decision in this document: simplest thing that still tells a good evaluation story.**

## 1. The one-sentence pitch

An internal agent that reads a customer's payment complaint, checks the account records, prior rep notes, and servicing policy, then drafts a review case for a human rep with the evidence attached and a recommended review queue. It never touches money.

## 2. The problem

A servicing rep at an auto lender gets complaints like:

- "I was charged a late fee but autopay was on."
- "My bank shows I paid, your system says I didn't."
- "Why was my payment reversed?"
- "Someone on the phone promised me a refund and it never came."

Today the rep reads the complaint, digs through account history, rereads notes from earlier calls, checks policy, and writes up a case by hand. The agent does the digging and drafts the write-up. The rep stays in charge of the decision.

### Why an LLM, and where it stops

This is a hybrid system on purpose. **Language goes to the LLM. Rules go to code.**

| The LLM handles | Code handles |
|---|---|
| Interpreting vague or messy complaints | Date, amount, and grace-period math |
| Matching "the payment from Tuesday" to one specific event | Following links between events |
| Reading free-text notes from earlier rep calls | Checking every cited record belongs to this account |
| Reading policy written in prose, and flagging where it's unclear | Enforcing policy rules P1–P7 as checks we wrote, not the LLM |
| Drafting the case note | Allowed routes, duplicate cases, and rejecting any output that fails these checks |

The claim that the LLM adds value is tested, not assumed: a code-only baseline runs on the same scenarios (section 6).

## 3. What the agent produces

One drafted review case per dispute:

| Field | Meaning |
|---|---|
| `category` | Which of the four dispute types this is |
| `review_route` | Which human queue it goes to (table below) |
| `event_ids` | The account records, including rep notes, that support the recommendation |
| `policy_source_ids` | The policy sections that support it |
| `facts` | A few typed facts the code can check: payment state, disputed event, whether the policy timeline was met |
| `confidence` | `high`, `medium`, or `low` |
| `open_questions` | What is still unknown, if anything |
| `case_note` | Three to five plain sentences the rep can edit and file |
| `case_id` | The simulated review case that was created, or the existing one if a duplicate |
| `gate_rejected` | `true` when the code gate rejected the draft and forced the route; the reasons are attached |

### The four routes

| `review_route` | When |
|---|---|
| `adjustment_review` | Records suggest the customer may be right; a human with authority should look at a correction |
| `explain_events` | Records and policy clearly explain what happened; rep can walk the customer through it |
| `need_more_evidence` | Something critical is missing, usually from the customer, or the complaint could mean more than one record |
| `manual_escalation` | Records conflict, notes contradict records or the customer, evidence points at the wrong account, policy is unclear, or the gate rejected the output |

### The four categories

`late_fee_autopay`, `missing_payment`, `returned_reversed_payment`, `refund_status`

Category and route are independent. A reversal complaint can land on any route depending on what the records say. The agent reasons from evidence, not keywords.

The route is the only thing the agent proposes. It picks which human queue gets the case; there is no separate action field.

## 4. How it works

One LangGraph graph: an **orchestrator** that fans out to two **specialist agents** in parallel, a **sync** join that cross-checks their findings, then a **deterministic gate** in code. The orchestrator is code: LangGraph decides what runs next, and the LLM is called only to classify and to synthesize. Both agents run after classify, at the same time; sync can send each one back once. Nothing chooses at runtime which agents to call.

```text
rep message + account_id
        |
   [Orchestrator]               LangGraph graph. Owns the run: fan-out, typed state, failures.
     1. classify                one LLM call: pick the category
        |
        +---------------------------+
        |                           |
   [Account Agent]             [Policy Agent]
   LLM + read-only account     code filters policy sections by category and the
   tools, max 6 calls.         complaint date (no embeddings in v1), then one LLM
   Proposes the disputed       call turns them into structured rules with
   event, or all candidates    citations. Advisory only
        |                           |
     code: confirms the event       |
     is on this account, derives    |
     payment and refund state,      |
     sets policy_date + fact flags  |
        |                           |
        +---------------------------+
        |
     2. sync                    code join: both findings present; the policy set read is the
        |  ⟲ clarify            one in force on policy_date; cited dates line up. Two bounded
        |  ⟲ re-retrieve        re-requests, each at most once: Account Agent again if it
        |                       returned nothing; Policy Agent again on policy_date if the set differs
     3. synthesize              one LLM call: pick route, cite evidence, write facts + case note
        |
   [Gate]                       plain Python, no LLM. Our checks for P1–P7 run against the
        |                       account's events; IDs, dates, allowed routes, duplicates
   [review]                     the graph pauses: the rep approves, overrides the route with a
        |                       reason, or answers once on need_more_evidence (Account Agent again)
   [create_review_case]         one simulated write, idempotent, under the rep's route
        |
   case filed for the rep       only the servicing backend moves money
```

The only second runs are sync's two re-requests and the rep's one follow-up answer, each bounded to one. A runtime-choosing orchestrator, or a second look by an agent on its own judgment, is the v2 candidate if evals show it is needed.

**The gate enforces our rules, not the LLM's.** Each policy assumption P1–P7 is a Python check keyed by its policy section ID. Each check reads structured event fields only and returns true, false or unknown. A true result can rule out `explain_events` or `adjustment_review`; the two cautious routes are never ruled out. The LLM's structured rules feed citations and the case note, never the gate's logic.

**Ambiguity is split.** If the policy text is unclear, the gate routes to `manual_escalation`. If the facts are unclear or missing, the route is `need_more_evidence` with open questions, and the gate leaves it alone.

If the gate rejects, the route is forced to `manual_escalation`. The output keeps the original citations and note and adds `gate_rejected` with the reasons, so evals can tell an agent that escalated correctly from one the gate rescued. The LLM does not get to argue.

When the Account Agent hits its tool budget, it returns what it has with a budget-hit flag. When a tool errors or an agent finds nothing useful, the orchestrator records the exact error, marks that finding unknown, and continues. No retries beyond sync's two re-requests.

### Tools

Account Agent, read only: `get_account`, `list_events`, `get_event`, `find_events_by_reference`, `list_review_cases`.
Policy Agent, read only: `search_policy`.
Graph, write: `create_review_case`. Simulated, idempotent, the only action the agent can take. Called by the graph after the rep's review, never chosen by an LLM.

### Models

Claude Sonnet 5.5 for the orchestrator's two LLM calls and both agents. No embeddings model in v1; vector search over policy is a Day 2 add-on if time allows. Claude Opus 5.5 for the LLM judge, so the agent is not graded by the exact model that produced the answer.

## 5. Synthetic data

Everything is fictional. The rules are written so that the right answer for every scenario follows from the records, including the notes.

| Asset | Size | Purpose |
|---|---|---|
| Accounts | 17 | One per scenario, plus a look-alike account for the wrong-account trap |
| Events | 159 | About five months of routine history per account, in one table. Scheduled, attempted, posted, returned, reversed, fee, refund, and **rep note** events with timestamps and links to each other |
| Policy docs | 4 real + 2 traps | Late fees, autopay, returns, refunds. Traps: one superseded version with an old timeline, one unrelated doc about payment extensions |
| Existing review cases | 3 | For duplicate-request tests |
| Eval scenarios | 16 | 12 dev, 4 held out. Expand to 24 only if time remains |

Design choices that make labels checkable and give the LLM real work:

- **Two timestamps per event.** `occurred_at` and `posted_at`. The gap is what makes late-fee and out-of-order scenarios decidable.
- **Effective-dated policy.** Chunks have `effective_from` and `effective_to`. Retrieval must pick the version in force on the date of the disputed event.
- **Rep notes as events.** A `rep_note` event carries free text from an earlier call, such as "Told customer refund mailed by check." Some disputes can only be resolved by reading a note and comparing it to the records or to the customer's claim.
- **Messy complaints.** About half the scenarios use realistic wording: wrong or relative dates, partial reference numbers, bank slang, two issues in one message, emotional filler.
- **Injection lives in tool output.** Trap instructions sit in `memo` fields returned by tools, not in the rep's message.

## 6. Evaluation

### Dataset

Four scenarios per category: three dev, one held out. Each scenario is tagged so results can be sliced:

| Tag | Values |
|---|---|
| `wording` | `clean` or `messy` |
| `note_driven` | `true` if the correct answer depends on reading a rep note; at least 3 scenarios |
| `trap` | `none`, `missing_record`, `out_of_order`, `wrong_account`, `duplicate`, `injection`, `ambiguous`, `stale_policy` |

Every route appears at least three times in dev so the agent cannot score well by always escalating.

Held-out cases are frozen before any prompt tuning. With four cases they are a sanity check against overfitting, not a measurement. Slices of six or fewer cases are directional only. Say both in the README.

### Evaluators

| Check | How |
|---|---|
| Category and route | Exact match, reported per category and as a route confusion matrix |
| Cited events | Required IDs present, forbidden IDs absent |
| Cited policy | Required source IDs present |
| Facts block | Exact match on typed fields |
| Confidence consistency | `need_more_evidence` or `manual_escalation` with `high` confidence is a fail |
| Action | `create_review_case` called exactly once, or zero when a case exists; correct arguments |
| Trajectory | Both agents ran, each at most twice (one re-request each); tool calls within budget |
| Case note quality | Opus judge with a rubric: grounded in cited evidence, no invented facts, clear to a rep |

Judge calibration: hand-label every dev case note from v1 and v2 for groundedness, about 24 items, and report agreement with the judge.

### Code-only baseline

A deterministic script with no LLM: keyword rules for category, most recent matching event as the disputed event, a decision table for route. It runs on the same 16 scenarios and is scored by the same deterministic evaluators.

Report baseline vs agent, sliced by `wording` and `note_driven`. The expected pattern is that the baseline holds up on clean cases and breaks on messy and note-driven ones. Report what actually happens, including any slice where the baseline wins.

### The story

1. Build v1. Run it on the 12 dev cases. Log to LangSmith.
2. Read the traces. Find the most common or most dangerous failure.
3. Make one change. Rerun. Report what improved, what regressed, by category.
4. Run the code-only baseline on the same cases. Show where the LLM earns its cost and where it does not.
5. Run the 4 held-out cases once. Report the numbers as they are.

## 7. Plan

| Day | Done when |
|---|---|
| 1 | Data contract, fixtures for 4 seed scenarios (one note-driven), tools, orchestrator + both agents + gate with the P1–P7 checks run end to end on all 4 |
| 2 | 16 scenarios with tags, all evaluators, v1 run in LangSmith, failure found, v2 run |
| 3 | Code-only baseline and comparison, held-out run, README, diagram, failure write-up, production eval plan, 5-minute recording |

Cut list if behind, in order: shrink to 12 scenarios, drop judge calibration, drop the code-only baseline, drop the policy traps. Never cut the before/after comparison.

## 8. Boundaries

- Synthetic data only. No real customers, no real lender policy.
- The agent recommends and drafts. A human decides. Nothing it does moves money, waives fees, or messages a customer.
- Wrong-account evidence and gate rejections always escalate.
- Time saved per case goes in the production plan as a hypothesis to measure, not a claimed result.
- Out of scope: UI, streaming, authentication, real integrations, training.

## 9. Deliverables

Repo with quickstart and Makefile, graph diagram, LangSmith links to traces and both experiment runs, before/after summary, baseline comparison, dataset and evaluator rationale, known failures, production eval plan, coding-assistant disclosure, short recording.
