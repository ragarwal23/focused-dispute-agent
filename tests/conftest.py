"""Shared fixtures. Every test runs without an LLM: real data, fake models."""

import os
from pathlib import Path

import pytest

from dispute_agent.data import load_dataset
from dispute_agent.policy_agent import load_policies
from dispute_agent.tools import make_tools

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="session", autouse=True)
def disable_langsmith_tracing():
    """Tests use fakes and must never create LangSmith traces."""
    old = os.environ.get("LANGSMITH_TRACING")
    os.environ["LANGSMITH_TRACING"] = "false"
    yield
    if old is None:
        os.environ.pop("LANGSMITH_TRACING", None)
    else:
        os.environ["LANGSMITH_TRACING"] = old


@pytest.fixture(scope="session")
def ds():
    return load_dataset(DATA)


@pytest.fixture(scope="session")
def sections():
    return load_policies(DATA / "policies")


@pytest.fixture(scope="session")
def tools(ds):
    return {t.name: t for t in make_tools(ds)}
