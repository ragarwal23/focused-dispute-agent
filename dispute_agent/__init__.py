"""The payment-dispute review agent. Each folder is one box of docs/diagrams/architecture_v0.8.png, in run order:

    orchestrator/    2. the LangGraph graph: classify, fan out, join, pause, file. Start reading at graph.py
    account_agent/   3a. the Account Agent: an LLM with read-only tools, then code that checks its answer
    policy_agent/    3b. the Policy Agent: code search over the policy files, then one LLM call that reads them
    sync/            3c. the join's cross-check (code), then the LLM call that writes the draft
    review/          4. the pause for the human rep, then the one write: create_review_case

Shared by all of them: data.py (record types and the Excel loader for data/) and config.py (model, "today",
limits). Outside the run: evaluation/ (score the scenarios, our P1-P7 policy checks, stability and traces) and __main__.py (the CLI).
"""
