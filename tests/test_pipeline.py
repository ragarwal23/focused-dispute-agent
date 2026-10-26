"""run_agents: the one bounded send-back fires once on missing findings, never twice, never on anything else."""

from dispute_agent.pipeline import MAX_ACCOUNT_ATTEMPTS, run_agents
from dispute_agent.policy_agent import PolicyReading
from test_derive import oracle_findings
from test_policy_agent import FakeModel, rule
from test_sync import account_state


class FakeAccountAgent:
    """Returns the states in `results` in order, repeating the last one; records every config."""

    def __init__(self, *results):
        self.results, self.configs = list(results), []

    def invoke(self, state, config=None):
        self.configs.append(config)
        return self.results[min(len(self.configs), len(self.results)) - 1]


def run(ds, sections, agent, sid="S-02"):
    s = ds.scenarios[sid]
    policy_model = FakeModel(PolicyReading(rules=[rule(i) for i in s.expected_policy_ids], summary="x"))
    out = run_agents(ds, sections, agent, s.account_id, s.expected_category, s.rep_message, ["t"], sid, policy_model)
    return (*out, policy_model)


def test_run_agents_clean(ds, sections):
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"])))
    account, derived, policy, synced, attempts, policy_model = run(ds, sections, agent)
    assert attempts == 1 and len(agent.configs) == 1 and policy_model.calls == 1
    assert synced.account_missing is False and synced.issues == []
    assert agent.configs[0] == {"run_name": "account_agent S-02", "tags": ["t"]}
    assert [r.section_id for r in policy.rules] == ["LF.v3.s2", "LF.v3.s5"]


def test_run_agents_send_back_once(ds, sections):
    agent = FakeAccountAgent(account_state(None, errors=["finalize failed: x"]), account_state(oracle_findings(ds.scenarios["S-02"])))
    account, derived, policy, synced, attempts, policy_model = run(ds, sections, agent)
    assert attempts == 2 and len(agent.configs) == 2 and policy_model.calls == 2
    assert agent.configs[1]["run_name"] == "account_agent S-02 retry"
    assert account["findings"] is not None and derived.policy_date_basis == "disputed_event"
    assert synced.account_missing is False and synced.issues == []


def test_run_agents_gives_up_after_two(ds, sections):
    agent = FakeAccountAgent(account_state(None, errors=["finalize failed: x"]))
    account, derived, policy, synced, attempts, _ = run(ds, sections, agent)
    assert attempts == MAX_ACCOUNT_ATTEMPTS == 2 and len(agent.configs) == 2
    assert synced.account_missing is True and synced.lean == "need_more_evidence"
    assert derived.policy_date_basis == "complaint_date" and account["findings"] is None


def test_run_agents_no_send_back_on_budget_hit(ds, sections):
    agent = FakeAccountAgent(account_state(oracle_findings(ds.scenarios["S-02"]), budget_hit=True))
    _, _, _, synced, attempts, _ = run(ds, sections, agent)
    assert attempts == 1 and len(agent.configs) == 1
    assert synced.budget_hit is True and synced.lean == "need_more_evidence"
