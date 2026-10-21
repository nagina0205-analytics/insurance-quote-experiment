import pandas as pd
import pytest

from experiment.analyze import run
from experiment.generate import BUG_DAYS, COMMISSION_RATE, START, generate
from experiment.warehouse import Warehouse


@pytest.fixture(scope="module")
def result(tmp_path_factory):
    return run(warehouse_path=str(tmp_path_factory.mktemp("wh")), write=False)


def test_generator_is_deterministic():
    a, b = generate(3, 2_000), generate(3, 2_000)
    pd.testing.assert_frame_equal(a.users, b.users)
    pd.testing.assert_frame_equal(a.policies, b.policies)


def test_clickhouse_metrics_match_a_pandas_recount(tmp_path):
    data = generate(5, 20_000)
    wh = Warehouse(str(tmp_path / "wh")).load(data)
    m = wh.sql("user_metrics", commission_rate=COMMISSION_RATE, start=START.isoformat()).set_index("user_id")
    wh.close()
    bound = set(data.events[data.events.event == "policy_bound"].user_id)
    assert m.bound.sum() == len(bound)
    assert m.cancelled_30d.sum() == data.policies.cancelled_at.notna().sum()
    kept = data.policies[data.policies.cancelled_at.isna()]
    assert m.retained_commission.sum() == pytest.approx((kept.annual_premium * COMMISSION_RATE).sum(), abs=1)


def test_the_logging_bug_is_caught_and_the_window_is_clean(result):
    s = result["srm"]
    assert s["p_value_full_window"] < 0.001
    assert {b["device"] for b in s["broken_cells"]} == {"android"}
    assert len(s["broken_cells"]) == BUG_DAYS
    assert s["p_value_analysis_window"] > 0.01


def test_analysis_recovers_the_planted_effect(result):
    p = result["primary"]
    assert p["ci_low"] <= result["true_effect_planted"] <= p["ci_high"]


def test_guardrail_catches_wrong_trim_cancellations(result):
    assert not result["guardrail_cancel_30d"]["passes"]
    rates = {(r["arm"], r["vehicle_mismatch"]): r["cancel_rate"] for r in result["cancellations_by_vehicle_source"]}
    assert rates[("treatment", 1)] > 3 * rates[("treatment", 0)]


def test_decision_follows_the_rule(result):
    assert result["decision"] == "Fix, then ship"
