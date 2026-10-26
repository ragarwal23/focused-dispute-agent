"""The Excel loader and the Dataset helpers."""

import openpyxl
import pytest

from dispute_agent.data import _read_rows


def test_counts(ds):
    assert (len(ds.accounts), len(ds.events), len(ds.review_cases), len(ds.scenarios)) == (17, 159, 3, 16)


def test_coercion(ds):
    e = ds.events["E-0154"]
    assert e.posted_at > e.occurred_at
    assert ds.scenarios["S-02"].expected_required_event_ids == ["E-0154", "E-0150"]
    assert isinstance(ds.accounts["A-1001"].due_day, int)
    assert ds.scenarios["S-03"].note_driven is True


def test_links_resolve_on_same_account(ds):
    for e in ds.events.values():
        if e.related_event_id:
            assert ds.events[e.related_event_id].account_id == e.account_id


def test_chain_of(ds):
    assert {e.event_id for e in ds.chain_of("E-0097")} >= {"E-0096", "E-0107"}
    assert {e.event_id for e in ds.chain_of("E-0108")} >= {"E-0107", "E-0097"}  # linked return fee
    assert ds.chain_of("E-9999") == []


def test_misspelled_header_raises(tmp_path):
    columns = ["account_id", "customer_name", "vehicle", "monthly_payment", "due_day", "bank_last4", "memo"]
    wb = openpyxl.Workbook()
    wb.active.append(["customer_nam" if c == "customer_name" else c for c in columns])
    wb.save(tmp_path / "accounts.xlsx")
    with pytest.raises(ValueError, match="customer_nam"):
        _read_rows(tmp_path / "accounts.xlsx", set(columns))
