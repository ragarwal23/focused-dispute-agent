# Auto-finance payment dispute agent

A LangGraph agent that investigates a customer's payment dispute and drafts a review case for a human rep. The lender (Agarwal Motor Finance) and all its data are fictional.

**[▶ Watch the 3-minute demo](docs/demo.mp4)** — the web app investigating a complaint end to end, from intake to the rep's review.

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

## Results

**13 of 16 test complaints pass every check.** S-11 picks the wrong queue; held-out S-04 and S-12 each miss one policy section.

**[See all 16, with a LangSmith trace for each →](docs/results.md)**

## Key design choices

- **Code decides what runs next; the LLM handles language.** Rules that must always hold never depend on a model.
- **Two agents in parallel, no retries.** A mismatch is recorded and pushes the case to a cautious queue, so a run can't loop.
- **The draft can only cite what it was shown.** The output schema rejects any other event or policy ID, so wrong-account evidence can't get in.
- **The human review is inside the graph.** It pauses before its one write and can be resumed later.

Reasoning for each, plus retrieval and policy dates: [architecture.md § Design decisions](docs/architecture.md#design-decisions).

## Known gaps

- **S-11 picks the wrong queue.** It says `explain_events` where only a bank letter can settle it (`need_more_evidence`).
- **S-07's note can mention another customer's account.** The schema blocks the wrong IDs, but not account details written in the prose; only the judge caught it.
- **The judge is untested on bad notes.** Next: grade deliberately broken notes and check the score drops.
- **No code-only baseline, and small sets.** Four held-out scenarios are a sanity check, not a measurement.

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
