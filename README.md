# Auto-finance payment dispute agent

A LangGraph agent that investigates a customer's payment dispute and drafts a review case for a human rep. The lender (Agarwal Motor Finance) and all its data are fictional.

**Demo video:** attached to the submission email. It shows the web app investigating a complaint end to end, from intake to the rep's review.

## What it does

A servicing rep at an auto lender gets complaints like "I was charged a late fee but autopay was on." Today the rep digs through the account history, rereads notes from earlier calls, checks the policy and writes up a case by hand.

The agent does the digging. For each complaint it drafts one review case with:

- **A recommended queue,** one of four:
  - `adjustment_review`: the customer may be right; someone with authority should look at a correction.
  - `explain_events`: the records and policy explain what happened; the rep walks the customer through it.
  - `need_more_evidence`: something critical is missing, usually from the customer.
  - `manual_escalation`: records conflict, evidence points at the wrong account, or policy is unclear.
- **The evidence:** the account events and policy sections behind the recommendation, each one cited.
- **A case note** the rep can read in a minute.

The rep then approves it, overrides the queue with a reason, or answers the agent's open questions. The agent never moves money. Full brief: [project_brief.md](docs/project_brief.md).

## How it works

[![Architecture diagram](docs/diagrams/architecture.png)](docs/diagrams/architecture.png)

An orchestrator runs two agents in parallel (Account and Policy), joins their findings, drafts the case and pauses for the rep. Details: [architecture.md](docs/architecture.md).

## How I tested it

A golden dataset of 16 complaints with an answer key (4 held out), graded two ways: **deterministic metrics** in code for the facts (queue, charge, evidence, policy, confidence, guardrails) and an **LLM judge** (Claude Opus 5.5) for the quality of the case note, calibrated against hand scores. **[Eval strategy and pipeline →](dispute_agent/evaluation/README.md)**

## Results

**12 of 16 test complaints pass every check.** Code grades each complaint against the answer key; Claude Opus 5.5 grades each case note.

| Failed | Why |
|---|---|
| S-04 (held out) | Right queue; one policy section missing from the draft |
| S-06 | Wrong queue: took a look-alike reference on another account as the customer's payment |
| S-11 | Wrong queue: `explain_events` where only a bank letter can settle it |
| S-12 (held out) | Right queue; one policy section missing from the draft |

**[All 16, with a trace each →](docs/results.md)** · [LangSmith experiment](https://smith.langchain.com/public/49bdd689-1b76-49fd-8d2d-2cc19bd1f452/d/compare?selectedSessions=f5712c13-dbd9-4a12-8177-46ee8c13a3b7) · [How the evals shaped the agent](docs/eval_history.md)

Sixteen hand-written complaints, four of them held out, are a sanity check, not a measurement.

## Key design choices

- **Code decides what runs next; the LLM handles language.** Rules that must always hold never depend on a model.
- **Two agents in parallel, nothing loops.** A mismatch is recorded and pushes the case to a cautious queue.
- **The draft can only cite what it was shown.** The output schema rejects any other event or policy ID, and code redacts other customers' details from the note.
- **The human review is inside the graph.** It pauses before its one write and can be resumed later.

Reasoning for each, plus retrieval and policy dates: [architecture.md § Design decisions](docs/architecture.md#design-decisions).

## Known gaps

- **S-11 picks the wrong queue.** The one prompt fix tried broke two other scenarios, so it was reverted.
- **The judge is untested on bad notes.** Next: grade deliberately broken notes and check the score drops.
- **Small sets, no code-only baseline.** Sixteen scenarios show the traps hold; they don't measure accuracy.

How I'd run this in production: [evaluation README § In production](dispute_agent/evaluation/README.md#in-production).

## Quickstart

**1. Set up**, then add your Anthropic and LangSmith keys to `.env`:

```bash
make setup
```

<details><summary>Without <code>make</code></summary>

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

</details>

**2. Run the web app** (no Node needed):

```bash
make run        # or: .venv/bin/python -m app.server
```

The inbox opens in your browser, and the terminal prints its address.

1. Pick a complaint from the inbox. It holds the 12 dev scenarios by account number, never the held-out four or the answer key.
2. Send it. The reply shows each step as it runs, then the recommended queue, the case note and its sources.
3. Approve it, override the queue with a reason, or, on `need_more_evidence`, answer the open questions.

"New conversation" starts a complaint on any account. It uses port 8000, or the next free one. Working on the page itself? `cd app && npm run dev` (Node) gives hot reload on :5173.

**Or from the terminal:**

```bash
source .venv/bin/activate
python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues"
python -m dispute_agent resume <thread> --approve   # ask prints the thread ID
```

Every run is traced in LangSmith under the `LANGSMITH_PROJECT` in `.env`.

<details>
<summary><b>All commands</b></summary>

```bash
python -m dispute_agent show A-1002            # one account's events, notes and cases (no LLM)
python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues"
python -m dispute_agent resume <thread> --approve   # or --route <route> --reason "...", or --answer "..."
python -m dispute_agent eval --split dev       # score the 12 dev scenarios, about $0.75
python -m pytest                               # offline tests, no LLM calls
```

Paused runs are saved in `runs/checkpoints.sqlite`, which the web app shares, so a thread paused in the page can be resumed with `resume`. `eval` never pauses: it approves every run, so its scores measure the agent. Judging, release workbooks and the LangSmith experiment are in the [evaluation README](dispute_agent/evaluation/README.md).

</details>

<details>
<summary><b>Repo layout</b></summary>

```
dispute_agent/          the agent: one folder per diagram box
  __main__.py           the CLI: show, ask, resume, eval, judge, calibrate, report, langsmith-sync, stability, trace
  config.py, data.py    shared settings and record types
  evaluation/           outside the run: scorer, P1–P7 policy checks, Opus judge, golden and release workbooks
app/                    the web app: server.py + narrate.py (Python), src/ (React), Vite config
tests/                  one test file per source file, no LLM calls; shared fakes in conftest.py
data/                   synthetic Excel data and policies/ (the policy documents the Policy Agent searches)
docs/                   brief, architecture, data contract, diagrams/
docs/history/           the build plan for each slice and the validation log, as written at the time (file paths predate this layout)
runs/                   git-ignored output: eval reports and checkpoints.sqlite (paused runs). Safe to delete
```

</details>
