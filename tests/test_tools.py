"""The five read-only tools."""

import json

from dispute_agent.tools import MEMO_CLOSE, MEMO_OPEN


def test_account_id_hidden_from_llm(tools):
    for t in tools.values():
        assert "account_id" not in t.tool_call_schema.model_fields


def test_get_account_wraps_memo(tools):
    out = tools["get_account"].invoke({"account_id": "A-1004"})
    assert MEMO_OPEN in out and MEMO_CLOSE in out and "AUTOMATED INSTRUCTION" in out


def test_list_events_sorted(tools):
    out = json.loads(tools["list_events"].invoke({"account_id": "A-1001"}))
    times = [e["occurred_at"] for e in out["events"]]
    assert out["count"] >= 7 and times == sorted(times)
    assert all(e["on_this_account"] for e in out["events"])


def test_get_event(tools):
    assert json.loads(tools["get_event"].invoke({"event_id": "E-9999", "account_id": "A-1007"}))["found"] is False
    off = json.loads(tools["get_event"].invoke({"event_id": "E-0135", "account_id": "A-1007"}))
    assert off["account_id"] == "A-1008" and off["on_this_account"] is False


def test_find_events_by_reference(tools):
    def find(fragment, account_id):
        return json.loads(
            tools["find_events_by_reference"].invoke({"reference_fragment": fragment, "account_id": account_id})
        )

    hit = find("48213", "A-1007")
    assert hit["count"] == 1 and hit["events"][0]["event_id"] == "E-0135" and hit["events"][0]["on_this_account"] is False
    assert [e["event_id"] for e in find("8812", "A-1002")["events"]] == ["E-0154"]
    hit = find("77", "A-1006")
    assert hit["count"] == 4 and {e["account_id"] for e in hit["events"]} == {"A-1006", "A-1007", "A-1009", "A-1013"}


def test_list_review_cases(tools):
    out = json.loads(tools["list_review_cases"].invoke({"account_id": "A-1012"}))
    assert [c["case_id"] for c in out["cases"]] == ["C-002"]
