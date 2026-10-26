"""Shared fixtures. Every test runs without an LLM: real data, fake models."""

from pathlib import Path

import pytest

from dispute_agent.data import load_dataset
from dispute_agent.policy_agent import load_policies
from dispute_agent.tools import make_tools

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="session")
def ds():
    return load_dataset(DATA)


@pytest.fixture(scope="session")
def sections():
    return load_policies(DATA / "policies")


@pytest.fixture(scope="session")
def tools(ds):
    return {t.name: t for t in make_tools(ds)}
