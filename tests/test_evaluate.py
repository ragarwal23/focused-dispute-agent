"""The eval scorer."""

from dispute_agent.derive import derive_states
from dispute_agent.evaluate import score


def test_score_failed_finalize_is_not_right(ds):
    s = ds.scenarios["S-06"]
    account = {"findings": None, "tool_calls_made": 2, "budget_hit": False, "tool_errors": ["finalize failed"]}
    c = score(s, s.expected_category, account, derive_states(None, s.expected_category, s.account_id, ds), ds)
    model_checks = ("disputed_ok", "required_found", "forbidden_clear", "payment_ok", "refund_ok", "gap_ok", "oam_ok")
    assert not any(c[k] for k in model_checks)
