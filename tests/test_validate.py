from datetime import datetime, timezone

from dispute_agent.validate import NORMAL, expected_children, stability, trace_check


def test_expected_children():
    assert expected_children(1, 1) == NORMAL
    assert expected_children(2, 1) == NORMAL[:5] + ["account_agent_retry", "derive_states_retry", "sync"] + NORMAL[5:]
    assert expected_children(1, 2) == NORMAL[:5] + ["policy_agent_retry", "sync"] + NORMAL[5:]
    assert expected_children(2, 2) == NORMAL[:5] + ["account_agent_retry", "derive_states_retry", "sync", "policy_agent_retry", "sync"] + NORMAL[5:]


def test_stability_matrix_and_gate():
    reports = []
    for good, flaky, known in ((True, True, True), (True, True, False), (True, False, False)):
        reports.append({"split": "dev", "rows": [
            {"scenario_id": "S-01", "checks": {"route_ok": good}, "account_attempts": 1, "policy_attempts": 1},
            {"scenario_id": "S-02", "checks": {"route_ok": flaky}, "account_attempts": 2, "policy_attempts": 1},
            {"scenario_id": "S-03", "checks": {"route_ok": known}, "account_attempts": 1, "policy_attempts": 2},
        ]})
    lines, ok = stability(reports, {("S-03", "route_ok")})
    assert ok is True and "S-02: route_ok=2/3" in lines
    assert any("send-backs=1 re-retrieves=1 exceptions=0" in line for line in lines)
    _, ok = stability(reports, set())
    assert ok is False


class FakeClient:
    def __init__(self, roots, children):
        self.roots, self.children = roots, children

    def list_runs(self, **kwargs):
        return self.roots if kwargs.get("is_root") else self.children


def _run(**values):
    now = datetime.now(timezone.utc)
    return {"start_time": now, "end_time": now, "tags": [], "prompt_tokens": 0, "completion_tokens": 0,
            "total_cost": 0, **values}


def test_trace_check_match_and_mismatch():
    root = _run(id="root", trace_id="trace", name="dispute S-02", tags=["slice=investigate"], prompt_tokens=5,
                outputs={"account_attempts": 1, "policy_attempts": 1})
    children = [_run(id=str(i), parent_run_id="root", name=name) for i, name in enumerate(NORMAL)]
    client = FakeClient([root, _run(name="dispute ignored", tags=[], prompt_tokens=5)], children)
    lines, ok = trace_check(client, "project", wait=0)
    assert ok is True and lines[-1].endswith("match")
    lines, ok = trace_check(FakeClient([root], children[:-1]), "project", wait=0)
    assert ok is False and "MISMATCH" in lines[-1]


def test_trace_check_waits_then_exits_2():
    lines, ok = trace_check(FakeClient([], []), "project", wait=0)
    assert ok is False and lines == ["no qualifying run found within 0s (wanted 1, found 0)"]
