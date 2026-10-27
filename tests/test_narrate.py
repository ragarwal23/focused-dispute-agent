"""The status key: debug-stream chunks from the fake graph become the exact step sequence the page shows."""

import json

from langchain_core.messages import AIMessage
from langgraph.types import Command

from app.narrate import STEPS, narrate
from dispute_agent.account_agent.agent import build_account_agent
from dispute_agent.orchestrator.graph import initial_graph_state
from dispute_agent.orchestrator.run import thread_config
from test_derive import oracle_findings
from test_graph import FakeAccountAgent
from test_policy_agent import FakeModel
from test_review import ANSWER, graph_for, s15_agent
from test_sync import account_state


def trail(graph, step, thread):
    """Stream the graph the way the server does and return every UI event."""
    events = []
    for ns, mode, data in graph.stream(step, thread_config(thread), stream_mode=["debug", "updates"], subgraphs=True):
        events.extend(narrate(ns, mode, data))
    return events


def start(ds, sections, sid, agent, thread="t"):
    s = ds.scenarios[sid]
    graph = graph_for(ds, sections, sid, agent)
    return graph, trail(graph, initial_graph_state(s.account_id, s.rep_message, sid), thread)


def starts(events):
    return [e["key"] for e in events if e["type"] == "step" and e["phase"] == "start"]


def test_clean_sequence(ds, sections):
    _, events = start(ds, sections, "S-02", FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]))))
    keys = starts(events)
    assert keys[0] == "classify" and set(keys[1:3]) == {"account_agent", "policy_agent"}  # the two agents run in parallel
    assert keys[3:] == ["derive_states", "sync", "synthesize", "review"]
    for e in events[:-1]:
        assert e["type"] == "step" and e["owner"] == STEPS[e["key"]].owner and e["parent"] is None and e["label"]
    ends = {e["key"]: e for e in events if e.get("phase") == "end"}
    assert set(ends) == set(keys) - {"review"}  # review has no end: the pause closes it
    assert ends["classify"]["label"] == "Dispute type: late fee with autopay on"
    assert ends["synthesize"]["label"] == "Draft written: route adjustment_review" and ends["synthesize"]["detail"] is None
    assert ends["sync"]["label"] == "Cross-check: no issues"
    assert events[-1]["type"] == "pause" and events[-1]["request"]["route"] == "adjustment_review"
    assert [e["owner"] for e in events if e.get("key") == "review"] == ["you"]


def test_resume_to_case(ds, sections):
    graph, _ = start(ds, sections, "S-02", FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]))))
    events = trail(graph, Command(resume={"action": "approve"}), "t")
    done = [e["label"] for e in events if e.get("phase") == "end"]
    assert done == ["You approved", "Case C-004 filed under adjustment_review (new)"]
    assert all(e["type"] == "step" for e in events)


def test_clarify_labels(ds, sections):
    agent = FakeAccountAgent(account_state(None, errors=["finalize failed: x"]), account_state(oracle_findings(ds.scenarios["S-02"])))
    _, events = start(ds, sections, "S-02", agent)
    keys = starts(events)
    assert keys.count("account_agent_retry") == 1 and keys.count("account_agent") == 1
    labels = [e["label"] for e in events if e.get("key") == "account_agent_retry"]
    assert labels == ["The account search came back empty; looking again (once)", "Account records read again (2 lookups)"]
    assert "derive_states_retry" in keys and keys.count("sync") == 2


def test_followup_labels(ds, sections):
    graph, first = start(ds, sections, "S-15", s15_agent(ds))
    assert first[-1]["type"] == "pause" and first[-1]["request"]["can_answer"] is True
    events = trail(graph, Command(resume=ANSWER.model_dump()), "t")
    keys = starts(events)
    assert keys[:3] == ["review", "account_agent_followup", "derive_states_followup"]
    assert [e["label"] for e in events if e.get("key") == "review" and e["phase"] == "end"] == ["You answered"]
    assert "Looking at the account again with your answer" in [e.get("label") for e in events]
    assert events[-1]["type"] == "pause" and events[-1]["request"]["can_answer"] is False


def test_account_agent_inner_steps(ds, sections):
    """The real Account Agent loop surfaces under its parent: one line per tool, the model's turns hidden once done."""

    class ToolModel:
        calls = 0

        def invoke(self, messages, config=None):
            ToolModel.calls += 1
            if ToolModel.calls == 1:
                return AIMessage("", tool_calls=[{"name": "list_events", "args": {}, "id": "c1"},
                                                 {"name": "get_event", "args": {"event_id": "E-0150"}, "id": "c2"}])
            return AIMessage("done")

    agent = build_account_agent(ds, model=ToolModel(), finalize=FakeModel(oracle_findings(ds.scenarios["S-02"])))
    _, events = start(ds, sections, "S-02", agent)
    inner = [e for e in events if e.get("parent") == "account_agent"]
    assert [(e["phase"], e["key"]) for e in inner][:6] == [
        ("start", "llm_call"), ("end", "llm_call"), ("start", "tool_node"),
        ("start", "tool:list_events"), ("start", "tool:get_event"), ("end", "tool:list_events")]
    labels = {(e["phase"], e["key"]): e["label"] for e in inner}
    assert labels[("start", "tool:get_event")] == "Looking at record E-0150"
    assert labels[("end", "tool:get_event")] == "Record E-0150 read"
    assert labels[("end", "llm_call")] == "" and labels[("end", "finalize")] == "Records summarised"
    assert {e["owner"] for e in inner if e["key"].startswith("tool:")} == {"code"}


def test_unknown_node_falls_back():
    task = {"type": "task", "payload": {"id": "1", "name": "mystery", "input": {}}}
    done = {"type": "task_result", "payload": {"id": "1", "name": "mystery", "result": {"x": 1}, "error": None, "interrupts": []}}
    assert narrate((), "debug", task)[0] | {} == {
        "type": "step", "phase": "start", "id": "1", "key": "mystery", "label": "mystery", "owner": "code", "parent": None, "detail": None}
    assert narrate((), "debug", done)[0]["label"] == "mystery"
    assert narrate((), "debug", {"type": "checkpoint"}) == [] and narrate((), "values", {}) == [] and narrate((), "debug", "odd") == []


def test_no_answer_key_leaks(ds, sections):
    for sid in ("S-02", "S-15"):
        agent = s15_agent(ds) if sid == "S-15" else FakeAccountAgent(account_state(oracle_findings(ds.scenarios[sid])))
        _, events = start(ds, sections, sid, agent)
        text = json.dumps(events, default=str)
        assert "expected_" not in text and "rationale" not in text
