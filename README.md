# Auto-finance payment dispute agent

A LangGraph agent that investigates a customer's payment dispute and drafts a review case for a human rep. Everything is fictional: the lender, Agarwal Motor Finance, and all of its data.

## Layout

| Path | What's in it |
|---|---|
| [`dispute_agent/`](dispute_agent/) | The agent code: data loader, Account Agent, CLI |
| [`tests/`](tests/) | Pytest suite |
| [`data/`](data/) | Synthetic accounts, events, review cases, eval scenarios and [policy documents](data/policies/) |
| [`docs/`](docs/) | [Project brief](docs/project_brief.md), [architecture](docs/architecture.md), [data contract](docs/data_contract.md), diagrams |
| [`tasks/`](tasks/) | Build plans, one per slice of work |
| `runs/` | Eval output, git-ignored, created on first `eval` |

## Run it

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # add your Anthropic and LangSmith keys

python -m dispute_agent show             # list accounts (no LLM)
python -m dispute_agent show A-1002      # one account's events, notes and cases (no LLM)
python -m dispute_agent ask A-1002 "cust got hit w a late fee on oct but says he paid over the phone last tues"
python -m dispute_agent eval --split dev
pytest
```
