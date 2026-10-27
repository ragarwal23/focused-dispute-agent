"""The Account Agent's loop, driven by scripted fake models: budget cap, tool errors, truncation."""

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from dispute_agent.account_agent.agent import AccountFindings, build_account_agent, initial_state, render_transcript


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
