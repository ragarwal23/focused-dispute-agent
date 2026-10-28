"""The web server's HTTP contract, on the fake graph: inbox, streaming to pause and result, the refusals, the thread list."""

import json
import socket

import pytest
from fastapi.testclient import TestClient

from app.server import free_port, make_app
from test_derive import oracle_findings
from test_graph import FakeAccountAgent
from test_review import graph_for
from test_sync import account_state


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


class FlakyAgent(FakeAccountAgent):
    """Fails like a dropped API call on its first run, then works."""

    def invoke(self, state, config=None):
        if self.calls == 0:
            self.calls += 1
            raise ConnectionError("network down")
        return super().invoke(state, config)


@pytest.fixture
def make(ds, sections):
    def _make(agent=None, trace_url=lambda run_id: f"https://smith.example/r/{run_id}", static_dir=None):
        agent = agent or FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"])))
        return TestClient(make_app(ds, sections, graph_for(ds, sections, "S-02", agent), trace_url, static_dir))
    return _make


def post(client, ds, sid="S-02", **extra):
    s = ds.scenarios[sid]
    return client.post("/api/threads", json={"account_id": s.account_id, "message": s.rep_message, "label": sid, **extra})


def test_inbox_dev_only_and_no_key(make, ds):
    r = make().get("/api/inbox")
    items = r.json()["items"]
    assert [i["scenario_id"] for i in items] == sorted(s.scenario_id for s in ds.scenarios.values() if s.split == "dev")
    assert set(items[0]) == {"scenario_id", "account_id", "customer", "channel", "message"}
    assert {i["channel"] for i in items} <= {"Customer message", "Call notes"}
    assert "expected_" not in r.text and "rationale" not in r.text and "heldout" not in r.text


def test_accounts(make, ds):
    items = make().get("/api/accounts").json()["items"]
    assert [i["account_id"] for i in items] == sorted(ds.accounts) and items[0]["customer"]


def test_run_streams_to_pause(make, ds):
    r = post(make(), ds)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
    ev = events(r)
    assert ev[-1]["type"] == "pause" and ev[-1]["thread_id"] == r.headers["x-thread-id"]
    assert ev[-1]["request"]["route"] == "adjustment_review" and ev[-1]["request"]["can_answer"] is False
    assert all(e["type"] == "step" for e in ev[:-1]) and ev[0]["key"] == "classify"
    src = ev[-1]["request"]["sources"]
    assert set(src) == {*ev[-1]["request"]["draft"]["event_ids"], *ev[-1]["request"]["draft"]["policy_source_ids"]}
    assert {x["kind"] for x in src.values()} == {"event", "policy"}
    assert all(x["title"] for x in src.values() if x["kind"] == "policy")


def test_resume_streams_to_result(make, ds):
    client = make()
    tid = post(client, ds).headers["x-thread-id"]
    ev = events(client.post(f"/api/threads/{tid}/resume", json={"action": "approve"}))
    assert ev[-1]["type"] == "result" and ev[-1]["case"]["case_id"] == "C-004" and ev[-1]["case"]["created"] is True
    assert ev[-1]["review"]["action"] == "approve" and ev[-1]["route"] == "adjustment_review" and ev[-1]["draft"]
    assert "gate" not in ev[-1]
    assert ev[-1]["case"]["disputed_event_id"] in ev[-1]["sources"]
    assert client.post(f"/api/threads/{tid}/resume", json={"action": "approve"}).status_code == 409


def test_override_result(make, ds):
    client = make()
    tid = post(client, ds).headers["x-thread-id"]
    body = {"action": "override", "route": "manual_escalation", "reason": "supervisor asked"}
    ev = events(client.post(f"/api/threads/{tid}/resume", json=body))
    assert ev[-1]["case"]["route"] == "manual_escalation" and ev[-1]["review"]["reason"] == "supervisor asked"


def test_answer_refused_422(make, ds):
    client = make()
    tid = post(client, ds).headers["x-thread-id"]
    r = client.post(f"/api/threads/{tid}/resume", json={"action": "answer", "answer": "the Oct 6 refund"})
    assert r.status_code == 422 and "approve or override" in r.text
    assert client.post(f"/api/threads/{tid}/resume", json={"action": "override", "route": "manual_escalation"}).status_code == 422
    assert client.get(f"/api/threads/{tid}").json()["status"] == "paused"  # nothing reached the graph


def test_bad_requests(make, ds):
    client = make()
    r = client.post("/api/threads", json={"account_id": "A-9999", "message": "x"})
    assert r.status_code == 404 and "A-1002" in r.text
    assert client.post("/api/threads", json={"account_id": "A-1002", "message": "  "}).status_code == 422
    assert client.post("/api/threads/nope/resume", json={"action": "approve"}).status_code == 404
    assert client.get("/api/threads/nope").status_code == 404


def test_threads_list_status(make, ds):
    client = make()
    paused = post(client, ds).headers["x-thread-id"]
    done = post(client, ds).headers["x-thread-id"]
    client.post(f"/api/threads/{done}/resume", json={"action": "approve"})
    items = {i["thread_id"]: i for i in client.get("/api/threads").json()["items"]}
    assert items[paused]["status"] == "paused" and items[done]["status"] == "finished"
    assert items[done]["route"] == "adjustment_review" and items[paused]["label"] == "S-02" and items[paused]["account_id"] == "A-1002"
    one = client.get(f"/api/threads/{paused}").json()
    assert one["request"]["route"] == "adjustment_review" and one["result"] is None and one["message"]
    assert client.get(f"/api/threads/{done}").json()["result"]["case"]["case_id"] == "C-004"


def test_crash_then_continue(make, ds):
    client = make(FlakyAgent(account_state(oracle_findings(ds.scenarios["S-02"]))))
    r = post(client, ds)
    tid, ev = r.headers["x-thread-id"], events(r)
    assert ev[-1]["type"] == "error" and "network down" in ev[-1]["message"]
    summary = client.get(f"/api/threads/{tid}").json()
    assert summary["status"] == "stopped" and summary["stopped_at"] == ["account_agent"]
    assert events(client.post(f"/api/threads/{tid}/resume", json={"action": "approve"}))[-1]["type"] == "pause"


def test_trace_404_without_run(make, ds):
    client = make()
    assert client.get("/api/threads/never-run/trace").status_code == 404
    tid = post(client, ds).headers["x-thread-id"]
    assert client.get(f"/api/threads/{tid}/trace").json()["url"].startswith("https://smith.example/r/")

    def not_ingested(run_id):
        raise LookupError(run_id)

    client = make(trace_url=not_ingested)
    tid = post(client, ds).headers["x-thread-id"]
    assert client.get(f"/api/threads/{tid}/trace").status_code == 404


def test_serves_page_from_static_dir(make, tmp_path):
    (tmp_path / "index.html").write_text('<div id="root"></div>')
    r = make(static_dir=tmp_path).get("/")
    assert r.status_code == 200 and '<div id="root">' in r.text


def test_api_not_shadowed_by_page(make, tmp_path):
    (tmp_path / "index.html").write_text('<div id="root"></div>')
    r = make(static_dir=tmp_path).get("/api/inbox")
    assert r.status_code == 200 and "items" in r.json()


def test_no_static_dir_means_no_page(make):
    client = make()
    assert client.get("/").status_code == 404 and client.get("/api/inbox").status_code == 200


def test_free_port_skips_taken_port():
    with socket.socket() as taken:
        taken.bind(("127.0.0.1", 0))
        taken.listen()
        p = taken.getsockname()[1]
        port = free_port(p)
        assert port != p
        with socket.socket() as s:
            s.bind(("127.0.0.1", port))


def test_free_port_gives_up(monkeypatch):
    def refuse(self, addr):
        raise OSError("in use")

    monkeypatch.setattr(socket.socket, "bind", refuse)
    with pytest.raises(SystemExit, match="9000-9002"):
        free_port(9000, tries=3)
