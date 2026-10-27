# Auto-finance payment dispute agent

A LangGraph agent that investigates a customer's payment dispute and drafts a review case for a human rep. Everything is fictional: the lender, Agarwal Motor Finance, and all of its data.

## Start here

1. [Project brief](docs/project_brief.md): the problem and what the agent produces.
2. [Architecture](docs/architecture.md): the steps, and which ones are LLM and which are plain code.
3. [`pipeline.py`](dispute_agent/pipeline.py): one complaint, start to finish. Follow its calls into the files below.

## Where each step lives

The orchestrator is a LangGraph graph in [`pipeline.py`](dispute_agent/pipeline.py). After classify, the two agents run in parallel and meet at sync. "Code" means plain Python with no LLM.

| Step | Who | File |
|---|---|---|
| Classify the complaint into one of 4 categories | LLM, one call | [`pipeline.py`](dispute_agent/pipeline.py) |
| Account Agent: investigate the account, propose the disputed event | LLM + 5 read-only tools | [`account_agent.py`](dispute_agent/account_agent.py), tools in [`tools.py`](dispute_agent/tools.py) |
| Derive states: check the agent's answer, work out payment/refund state and the policy date | Code | [`derive.py`](dispute_agent/derive.py) |
| Policy Agent, in parallel with the two above: find the policy in force on the complaint date, read it into rules with citations | Code search + LLM, one call | [`policy_agent.py`](dispute_agent/policy_agent.py) |
| Sync: the join; confirm both agents' results line up. May send each agent back once: the Account Agent if it returned nothing, the Policy Agent if a different policy set was in force on the disputed event's date | Code | [`sync.py`](dispute_agent/sync.py), edges in [`pipeline.py`](dispute_agent/pipeline.py) |
| Synthesize: write the draft the rep reads (route, cited events and sections, confidence, open questions, note) | LLM, one call | [`synthesize.py`](dispute_agent/synthesize.py) |
| Gate: our P1–P7 checks on the draft's route, cited IDs, policy dates and duplicates | Code | [`gate.py`](dispute_agent/gate.py) |
| Review: the graph pauses for the rep, who approves, overrides the route with a reason, or (on `need_more_evidence`) answers once so the Account Agent looks again | Human; code renders and records | [`review.py`](dispute_agent/review.py), node and edges in [`pipeline.py`](dispute_agent/pipeline.py) |
| Create review case: the only write, simulated and idempotent, filed under the rep's route | Code | [`create_review_case.py`](dispute_agent/create_review_case.py) |

Supporting files:

| File | What's in it |
|---|---|
| [`config.py`](dispute_agent/config.py) | Model ID, the fictional "today", tool and retrieval limits |
| [`data.py`](dispute_agent/data.py) | Record types and the Excel loader |
| [`evaluate.py`](dispute_agent/evaluate.py) | Runs the scenarios and scores them against the answer key |
| [`validate.py`](dispute_agent/validate.py) | Stability over repeated evals and LangSmith trace-shape checks |
| [`__main__.py`](dispute_agent/__main__.py) | The `show`, `ask`, `resume`, `eval`, `stability` and `trace` commands |
| [`tests/`](tests/) | One test file per source file; no LLM calls |

## Layout

| Path | What's in it |
|---|---|
| [`dispute_agent/`](dispute_agent/) | The code, one file per step (above) |
| [`tests/`](tests/) | Pytest suite, shared fixtures in [`conftest.py`](tests/conftest.py) |
| [`data/`](data/) | Synthetic accounts, events, review cases, eval scenarios and [policy documents](data/policies/) |
| [`docs/`](docs/) | [Brief](docs/project_brief.md), [architecture](docs/architecture.md), [data contract](docs/data_contract.md), diagrams |
| [`docs/plans/`](docs/plans/) | The build plan for each slice, as written at the time. File paths in them predate the current layout |
| `runs/` | Eval output and `checkpoints.sqlite` (paused runs), git-ignored. Safe to delete; deleting the checkpoints forgets paused runs |

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your Anthropic and LangSmith keys

python -m dispute_agent show             # list accounts (no LLM)
python -m dispute_agent show A-1002      # one account's events, notes and cases (no LLM)
python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues"
#   ...prints each step, then pauses:  paused for review: thread A-1002-20261027T101500Z
python -m dispute_agent resume A-1002-20261027T101500Z --approve          # file under the gate's route
python -m dispute_agent resume <thread> --route manual_escalation --reason "..."   # or override it
python -m dispute_agent resume <thread> --answer "the \$731.50 refund"     # need_more_evidence only, once
python -m dispute_agent ask A-1002 "..." --approve                        # approve at the pause, no resume
python -m dispute_agent eval --split dev --min-disputed-acc 0.75 --min-policy-recall 0.75 --min-route-acc 0.75
python -m dispute_agent stability runs/eval_*.json --known S-11:route_ok
python -m dispute_agent trace --last 12
pytest
```

Every `ask` pauses at the `review` node; its state is saved in `runs/checkpoints.sqlite`, so `resume` can continue it
later in a new process. `eval` never pauses: it approves every run, so its scores measure the agents and the gate.

For live validation only, `DISPUTE_FAULT=finalize_once` forces the Account Agent's first result to be retried;
`DISPUTE_FAULT=policy_date_mismatch` forces the Policy Agent re-retrieval path. The variable is off by default and
`eval` refuses to run while it is set.
