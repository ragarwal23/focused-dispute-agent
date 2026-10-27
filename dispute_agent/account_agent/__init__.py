"""3a. Account Agent: investigates the account the way a rep would, then code checks what it found. Read in order:

    agent.py   the LLM loop: up to six read-only tool calls, then one call that writes structured findings
    tools.py   the five read-only tools it can call (records, events, references, review cases)
    derive.py  derive_states: code that confirms the disputed event and works out payment/refund state and dates
"""
