"""Settings shared by every step: the model, the fictional "today", and the agents' limits.

Importing this module also loads API keys from `.env`.
"""

from __future__ import annotations

from datetime import datetime

from dotenv import load_dotenv
from langchain_anthropic import ChatAnthropic

load_dotenv()

MODEL_ID = "claude-sonnet-5-5"
WORLD_TODAY = datetime(2026, 10, 20)  # every complaint arrives on this day
TOOL_BUDGET = 6  # most tool calls the Account Agent may make per complaint
POLICY_TOP_K = 10  # policy sections kept after ranking, before cross-references are added


def make_model(effort: str | None = None) -> ChatAnthropic:
    # Adaptive thinking counts against max_tokens, so 16k. langchain-anthropic merges a constructor
    # output_config with the `format` that with_structured_output(method="json_schema") binds.
    extra = {"output_config": {"effort": effort}} if effort else {}
    return ChatAnthropic(model=MODEL_ID, max_tokens=16000, **extra)
