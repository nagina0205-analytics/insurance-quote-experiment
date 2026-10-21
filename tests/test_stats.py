import numpy as np
import pytest
from statsmodels.stats.multitest import multipletests
from statsmodels.stats.proportion import proportions_ztest

from experiment.stats import (benjamini_hochberg, cuped, mean_difference, minimum_detectable_effect,
                              sample_size_per_arm, srm_test, two_proportions)


def test_two_proportions_matches_statsmodels():
    e = two_proportions("m", 500, 10_000, 560, 10_000)
    _, p = proportions_ztest([560, 500], [10_000, 10_000])
    assert e.p_value == pytest.approx(p, rel=1e-9)
    assert e.diff == pytest.approx(0.006)
    assert e.ci_low < e.diff < e.ci_high


def test_interval_covers_truth_about_95_percent_of_the_time():
    rng = np.random.default_rng(0)
    hits = 0
    for _ in range(2_000):
        xc, xt = rng.binomial(5_000, 0.05), rng.binomial(5_000, 0.06)
        e = two_proportions("m", xc, 5_000, xt, 5_000)
        hits += e.ci_low <= 0.01 <= e.ci_high
    assert 0.93 < hits / 2_000 < 0.97


def test_srm_flags_a_small_imbalance_at_scale_but_not_noise():
    assert srm_test(50_000, 49_000) < 0.01          # 1% short on 100k users is a real problem
    assert srm_test(50_000, 49_950) > 0.5           # this is just noise


def test_cuped_is_unbiased_and_reduces_variance_when_the_covariate_predicts():
    rng = np.random.default_rng(1)
    n = 40_000
    x = rng.normal(0, 1, n)
    t = rng.random(n) < 0.5
    y = 2 * x + 0.1 * t + rng.normal(0, 1, n)
    raw = mean_difference("y", y[~t], y[t])
    adj, reduction = cuped("y", y, x, t)
    assert reduction > 0.7
    assert adj.ci_high - adj.ci_low < (raw.ci_high - raw.ci_low) * 0.6
    assert adj.ci_low < 0.1 < adj.ci_high


def test_power_calculations_agree_with_each_other():
    n = sample_size_per_arm(0.05, 0.003)
    mde = minimum_detectable_effect(0.05, n)
    assert mde == pytest.approx(0.003, rel=0.06)


def test_benjamini_hochberg_matches_statsmodels():
    p = [0.001, 0.008, 0.039, 0.041, 0.042, 0.06, 0.074, 0.205]
    assert benjamini_hochberg(p) == multipletests(p, method="fdr_bh")[0].tolist()
