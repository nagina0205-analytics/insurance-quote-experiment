"""Statistics for A/B tests on per-user metrics. Small, readable, and tested against statsmodels."""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
from scipy import stats


@dataclass
class Effect:
    metric: str
    control: float            # control mean
    treatment: float          # treatment mean
    diff: float               # treatment - control, absolute
    ci_low: float
    ci_high: float
    relative: float           # diff / control
    p_value: float
    n_control: int
    n_treatment: int

    @property
    def significant(self) -> bool:
        return self.ci_low > 0 or self.ci_high < 0

    def to_dict(self):
        d = {k: (float(v) if isinstance(v, (float, np.floating)) else v) for k, v in asdict(self).items()}
        return d | {"significant": bool(self.significant)}


def srm_test(n_control: int, n_treatment: int, expected_share: float = 0.5) -> float:
    """Chi-square test that the split matches the planned ratio. A tiny p-value means the data can't be trusted yet."""
    total = n_control + n_treatment
    exp = [total * (1 - expected_share), total * expected_share]
    return float(stats.chisquare([n_control, n_treatment], exp).pvalue)


def two_proportions(metric: str, x_c: int, n_c: int, x_t: int, n_t: int, alpha: float = 0.05) -> Effect:
    """Difference in proportions: pooled z-test for the p-value, unpooled standard error for the interval."""
    p_c, p_t = x_c / n_c, x_t / n_t
    pooled = (x_c + x_t) / (n_c + n_t)
    z = (p_t - p_c) / np.sqrt(pooled * (1 - pooled) * (1 / n_c + 1 / n_t))
    se = np.sqrt(p_c * (1 - p_c) / n_c + p_t * (1 - p_t) / n_t)
    q = stats.norm.ppf(1 - alpha / 2)
    d = p_t - p_c
    return Effect(metric, p_c, p_t, d, d - q * se, d + q * se, d / p_c, float(2 * stats.norm.sf(abs(z))), n_c, n_t)


def mean_difference(metric: str, y_c: np.ndarray, y_t: np.ndarray, alpha: float = 0.05) -> Effect:
    """Welch interval for a difference in means (revenue per user and other continuous metrics)."""
    y_c, y_t = np.asarray(y_c, float), np.asarray(y_t, float)
    d = y_t.mean() - y_c.mean()
    se = np.sqrt(y_c.var(ddof=1) / y_c.size + y_t.var(ddof=1) / y_t.size)
    res = stats.ttest_ind(y_t, y_c, equal_var=False)
    q = stats.norm.ppf(1 - alpha / 2)
    return Effect(metric, y_c.mean(), y_t.mean(), d, d - q * se, d + q * se, d / y_c.mean(), float(res.pvalue), y_c.size, y_t.size)


def cuped(metric: str, y: np.ndarray, x: np.ndarray, treated: np.ndarray, alpha: float = 0.05) -> tuple[Effect, float]:
    """CUPED: subtract the part of the metric a pre-experiment covariate already predicts.

    x must be measured before assignment (here: quotes in the prior 90 days), so it can't be
    affected by the treatment. The estimate stays unbiased; the interval gets narrower.
    Returns the adjusted effect and the share of variance removed.
    """
    y, x, t = np.asarray(y, float), np.asarray(x, float), np.asarray(treated, bool)
    theta = np.cov(y, x)[0, 1] / x.var(ddof=1)
    y_adj = y - theta * (x - x.mean())
    eff = mean_difference(metric, y_adj[~t], y_adj[t], alpha)
    eff.control, eff.treatment = y[~t].mean(), y[t].mean()   # report raw means; the diff and interval are adjusted
    eff.relative = eff.diff / eff.control
    reduction = 1 - y_adj.var(ddof=1) / y.var(ddof=1)
    return eff, float(reduction)


def sample_size_per_arm(p: float, mde_abs: float, alpha: float = 0.05, power: float = 0.8) -> int:
    """Users needed per arm to detect an absolute change of mde_abs in a proportion p."""
    za, zb = stats.norm.ppf(1 - alpha / 2), stats.norm.ppf(power)
    p2 = p + mde_abs
    return int(np.ceil((za + zb) ** 2 * (p * (1 - p) + p2 * (1 - p2)) / mde_abs ** 2))


def minimum_detectable_effect(p: float, n_per_arm: int, alpha: float = 0.05, power: float = 0.8) -> float:
    za, zb = stats.norm.ppf(1 - alpha / 2), stats.norm.ppf(power)
    return float((za + zb) * np.sqrt(2 * p * (1 - p) / n_per_arm))


def benjamini_hochberg(p_values: list[float], q: float = 0.05) -> list[bool]:
    """Which segment results survive a false discovery rate of q. Slicing by segment multiplies false positives."""
    p = np.asarray(p_values)
    order = np.argsort(p)
    thresh = q * np.arange(1, p.size + 1) / p.size
    passed = p[order] <= thresh
    k = np.max(np.flatnonzero(passed)) + 1 if passed.any() else 0
    out = np.zeros(p.size, bool)
    out[order[:k]] = True
    return out.tolist()
