"""No-LLM tests: loader, tools, derive_states on the dev scenarios, and the agent loop with fakes."""

from pathlib import Path

import openpyxl
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from dispute_agent.account_agent import (
    AccountFindings, OtherAccountMatch, build_account_agent, derive_states, initial_state, render_transcript,
)
from dispute_agent.data import MEMO_CLOSE, MEMO_OPEN, Scenario, _read_rows, load_dataset, make_tools

DATA = Path(__file__).resolve().parent.parent / "data"


@pytest.fixture(scope="session")
def ds():
    return load_dataset(DATA)


@pytest.fixture(scope="session")
def tools(ds):
    return {t.name: t for t in make_tools(ds)}


def dev_scenarios(ds):
    return [s for s in ds.scenarios.values() if s.split == "dev"]


# --- loader --------------------------------------------------------------------------------


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
    wb = openpyxl.Workbook()
    wb.active.append(["account_id", "customer_nam", "vehicle", "monthly_payment", "due_day", "bank_last4", "memo"])
    wb.save(tmp_path / "accounts.xlsx")
    with pytest.raises(ValueError, match="customer_nam"):
        _read_rows(tmp_path / "accounts.xlsx", {"account_id", "customer_name", "vehicle", "monthly_payment", "due_day", "bank_last4", "memo"})


# --- tools ---------------------------------------------------------------------------------


def test_account_id_hidden_from_llm(tools):
    for t in tools.values():
        assert "account_id" not in t.tool_call_schema.model_fields


def test_get_account_wraps_memo(tools):
    out = tools["get_account"].invoke({"account_id": "A-1004"})
    assert MEMO_OPEN in out and MEMO_CLOSE in out and "AUTOMATED INSTRUCTION" in out


def test_list_events_sorted(tools, ds):
    import json
    out = json.loads(tools["list_events"].invoke({"account_id": "A-1001"}))
    times = [e["occurred_at"] for e in out["events"]]
    assert out["count"] >= 7 and times == sorted(times)
    assert all(e["on_this_account"] for e in out["events"])


def test_get_event(tools):
    import json
    assert json.loads(tools["get_event"].invoke({"event_id": "E-9999", "account_id": "A-1007"}))["found"] is False
    off = json.loads(tools["get_event"].invoke({"event_id": "E-0135", "account_id": "A-1007"}))
    assert off["account_id"] == "A-1008" and off["on_this_account"] is False


def test_find_events_by_reference(tools):
    import json
    hit = json.loads(tools["find_events_by_reference"].invoke({"reference_fragment": "48213", "account_id": "A-1007"}))
    assert hit["count"] == 1 and hit["events"][0]["event_id"] == "E-0135" and hit["events"][0]["on_this_account"] is False
    hit = json.loads(tools["find_events_by_reference"].invoke({"reference_fragment": "8812", "account_id": "A-1002"}))
    assert [e["event_id"] for e in hit["events"]] == ["E-0154"]
    hit = json.loads(tools["find_events_by_reference"].invoke({"reference_fragment": "77", "account_id": "A-1006"}))
    assert hit["count"] == 4 and {e["account_id"] for e in hit["events"]} == {"A-1006", "A-1007", "A-1009", "A-1013"}


def test_list_review_cases(tools):
    import json
    out = json.loads(tools["list_review_cases"].invoke({"account_id": "A-1012"}))
    assert [c["case_id"] for c in out["cases"]] == ["C-002"]


# --- derive_states -------------------------------------------------------------------------


def oracle_findings(s: Scenario) -> AccountFindings:
    """What a perfect agent would return for this scenario, built from the answer key."""
    return AccountFindings(
        relevant_event_ids=s.expected_required_event_ids,
        disputed_event_id=s.expected_disputed_event_id,
        candidate_event_ids=s.expected_required_event_ids if s.trap == "ambiguous" else [],
        other_account_matches=[OtherAccountMatch(event_id="E-0135", account_id="A-1008", reference="PHN-0048213", why="keyed to another customer")]
        if s.scenario_id == "S-07" else [],
        claimed_but_not_found=["per key"] if s.trap in ("missing_record", "wrong_account") else [],
        notes_summary="", summary="",
    )


def _dev_ids():
    return [s.scenario_id for s in load_dataset(DATA).scenarios.values() if s.split == "dev"]


@pytest.mark.parametrize("sid", _dev_ids())
def test_derive_matches_key(ds, sid):
    s = ds.scenarios[sid]
    d = derive_states(oracle_findings(s), s.expected_category, s.account_id, ds)
    assert d.payment_state == s.expected_payment_state, d.reasons
    assert d.refund_state == s.expected_refund_state, d.reasons
    assert d.disputed_event_id == s.expected_disputed_event_id
    assert d.fact_ambiguous is (s.trap == "ambiguous")
    assert d.other_account_match is (s.trap == "wrong_account")
    assert d.fact_gap is (s.trap in ("missing_record", "wrong_account"))
    assert d.policy_date_basis == ("disputed_event" if s.expected_disputed_event_id else "complaint_date")
    assert d.dropped_off_account_ids == []
    assert [e.event_id for e in d.evidence_events] and all(e.account_id == s.account_id for e in d.evidence_events)


def test_derive_drops_off_account_citation(ds):
    f = AccountFindings(relevant_event_ids=["E-0157", "E-0135"], disputed_event_id="E-0135", notes_summary="", summary="")
    d = derive_states(f, "missing_payment", "A-1007", ds)
    assert d.dropped_off_account_ids == ["E-0135"] and d.disputed_event_id is None and d.fact_gap is True
    assert d.relevant_event_ids == ["E-0157"] and d.payment_state == "not_found"


def test_derive_linked_return_fee(ds):
    f = AccountFindings(relevant_event_ids=["E-0108"], disputed_event_id="E-0108", notes_summary="", summary="")
    assert derive_states(f, "returned_reversed_payment", "A-1010", ds).payment_state == "returned"


def test_derive_no_findings(ds):
    d = derive_states(None, "missing_payment", "A-1006", ds)
    assert d.fact_gap is True and d.payment_state == "not_found" and d.policy_date_basis == "complaint_date"


def test_derive_other_account_match_must_be_off_account(ds):
    def oam(*ids):
        return AccountFindings(relevant_event_ids=[], disputed_event_id=None, notes_summary="", summary="",
                               other_account_matches=[OtherAccountMatch(event_id=i, account_id="A-1008", why="x") for i in ids])
    assert derive_states(oam("E-0109", "E-9999"), "missing_payment", "A-1006", ds).other_account_match is False  # own event, unknown
    assert derive_states(oam("E-0135"), "missing_payment", "A-1007", ds).other_account_match is True


def test_derive_noisy_claims_set_gap_without_a_disputed_event(ds):
    """Why the claimed_but_not_found spec matters: any entry, however vague, sets fact_gap when nothing is disputed."""
    s = ds.scenarios["S-15"]
    f = oracle_findings(s).model_copy(update={"claimed_but_not_found": ["a rep note of the call"]})
    assert derive_states(f, s.expected_category, s.account_id, ds).fact_gap is True


def test_score_failed_finalize_is_not_right(ds):
    from dispute_agent.__main__ import score
    s = ds.scenarios["S-06"]
    out = {"findings": None, "tool_calls_made": 2, "budget_hit": False, "tool_errors": ["finalize failed"]}
    c = score(s, s.expected_category, out, derive_states(None, s.expected_category, s.account_id, ds), ds)
    assert not any(c[k] for k in ("disputed_ok", "required_found", "forbidden_clear", "payment_ok", "refund_ok", "gap_ok", "oam_ok"))


# --- the loop with fakes -------------------------------------------------------------------


class ScriptedModel:
    def __init__(self, turns):
        self.turns, self.calls = list(turns), 0

    def invoke(self, messages):
        self.calls += 1
        return self.turns.pop(0)


class FakeFinalize:
    def __init__(self):
        self.inputs = []

    def invoke(self, messages):
        self.inputs.append(messages)
        return AccountFindings(relevant_event_ids=["E-0150"], disputed_event_id="E-0150", notes_summary="none", summary="fake")


def _ai(calls):
    return AIMessage(content="", tool_calls=[{"name": n, "args": a, "id": f"c{i}", "type": "tool_call"} for i, (n, a) in enumerate(calls)])


def test_loop_budget_cap(ds):
    turns = [
        _ai([("get_account", {}), ("list_events", {}), ("list_review_cases", {})]),
        _ai([("get_event", {"event_id": "E-0150"}), ("get_event", {"event_id": "E-0154"}), ("find_events_by_reference", {"reference_fragment": "8812"})]),
        _ai([("get_event", {"event_id": "E-0086"}), ("get_event", {"event_id": "E-0059"}), ("list_events", {})]),
    ]
    model, fin = ScriptedModel(turns), FakeFinalize()
    out = build_account_agent(ds, model=model, finalize=fin).invoke(initial_state("A-1002", "late_fee_autopay", "late fee, paid by phone"))
    assert out["tool_calls_made"] == 6 and out["budget_hit"] is True and model.calls == 3
    results = [m for m in out["messages"] if isinstance(m, ToolMessage)]
    assert len(results) == 9 and sum("not executed" in m.content for m in results) == 3
    ids = [c["id"] for m in out["messages"] if isinstance(m, AIMessage) for c in m.tool_calls]
    assert [m.tool_call_id for m in results] == ids
    assert len(fin.inputs) == 1 and out["findings"].disputed_event_id == "E-0150"
    text = fin.inputs[0][1].content
    assert all(name in text for name in ("get_account", "list_events", "get_event", "find_events_by_reference", "list_review_cases"))
    assert isinstance(out["messages"][0], HumanMessage) and "A-1002" in out["messages"][0].content


def test_loop_tool_error_recorded(ds):
    turns = [_ai([("explode", {}), ("get_account", {})]), AIMessage(content="done, the fee is E-0150")]
    model, fin = ScriptedModel(turns), FakeFinalize()
    out = build_account_agent(ds, model=model, finalize=fin).invoke(initial_state("A-1002", "late_fee_autopay", "x"))
    assert out["tool_errors"] == ["KeyError: 'explode'"] and out["tool_calls_made"] == 2 and out["budget_hit"] is False
    err = [m for m in out["messages"] if isinstance(m, ToolMessage)][0]
    assert err.status == "error" and err.tool_call_id == "c0"
    assert "[agent]\ndone, the fee is E-0150" in render_transcript(out)


def test_loop_truncated_turn_goes_to_finalize(ds):
    ai = _ai([("get_account", {})])
    ai.response_metadata = {"stop_reason": "max_tokens"}
    out = build_account_agent(ds, model=ScriptedModel([ai]), finalize=FakeFinalize()).invoke(initial_state("A-1002", "late_fee_autopay", "x"))
    assert out["tool_calls_made"] == 0 and "llm turn truncated (max_tokens)" in out["tool_errors"] and out["findings"] is not None
