"""Run the full experiment readout: data checks, primary result, guardrails, segments, decision.

    python -m experiment.analyze

Writes reports/results.json, reports/decision_memo.md and the GitHub Pages site in docs/.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from . import charts
from .generate import BUG_DAYS, COMMISSION_RATE, START, generate
from .stats import (benjamini_hochberg, cuped, mean_difference, minimum_detectable_effect,
                    sample_size_per_arm, srm_test, two_proportions)
from .warehouse import Warehouse

ROOT = Path(__file__).resolve().parent.parent
SEED = 7

# Decision rule, written down before looking at results.
ALPHA = 0.05
SRM_P_MIN = 0.001                 # below this, the split is broken and results aren't trusted
CANCEL_MARGIN = 0.010             # 30-day cancellations may not rise by more than 1.0 point (upper bound of the 95% CI)
PLANNED_USERS_PER_DAY = 9_500
PLANNED_DAYS = 21


def proportions(df: pd.DataFrame, col: str, metric: str) -> "two_proportions":
    c, t = df[df.arm == "control"][col], df[df.arm == "treatment"][col]
    return two_proportions(metric, int(c.sum()), int(c.size), int(t.sum()), int(t.size), ALPHA)


def run(seed: int = SEED, warehouse_path: str = ".warehouse", write: bool = True) -> dict:
    data = generate(seed)
    wh = Warehouse(warehouse_path).load(data)
    users = wh.sql("user_metrics", commission_rate=COMMISSION_RATE, start=START.isoformat())
    window_start = (pd.Timestamp(START) + pd.Timedelta(days=BUG_DAYS)).date().isoformat()

    # 1. Plan: how big an effect could this test detect?
    baseline_planned = 0.05
    n_planned = PLANNED_USERS_PER_DAY * PLANNED_DAYS // 2
    plan = {"baseline_bind_rate": baseline_planned, "users_per_arm": n_planned,
            "mde_abs": minimum_detectable_effect(baseline_planned, n_planned),
            "users_per_arm_for_0.3pt": sample_size_per_arm(baseline_planned, 0.003)}

    # 2. Trust the data first: sample ratio mismatch
    counts = users.arm.value_counts()
    srm_all = srm_test(int(counts["control"]), int(counts["treatment"]))
    daily = wh.sql("srm_daily")
    daily["day"] = pd.to_datetime(daily["day"])
    by_cell = daily.assign(p=[srm_test(int(r.control_users), int(r.treatment_users)) for r in daily.itertuples()])
    broken = by_cell[by_cell.p < SRM_P_MIN / len(by_cell)]   # Bonferroni: many day x device cells are scanned
    clean = users[users.day >= BUG_DAYS].copy()
    cc = clean.arm.value_counts()
    srm_clean = srm_test(int(cc["control"]), int(cc["treatment"]))
    srm = {"p_value_full_window": srm_all, "control_full": int(counts["control"]), "treatment_full": int(counts["treatment"]),
           "broken_cells": [{"day": d.day.date().isoformat(), "device": d.device, "treatment_share": float(d.treatment_share),
                             "p_value": float(d.p)} for d in broken.itertuples()],
           "analysis_window_start": window_start, "p_value_analysis_window": srm_clean,
           "control_window": int(cc["control"]), "treatment_window": int(cc["treatment"]),
           "excluded_users": int(len(users) - len(clean))}

    # 3. Primary metric: policies bound per user who opened the app
    t = (clean.arm == "treatment").to_numpy()
    primary = proportions(clean, "bound", "bind_rate")
    primary_cuped, var_reduction = cuped("bind_rate_cuped", clean.bound, clean.prior_quotes_90d, t, ALPHA)

    funnel = wh.sql("funnel", window_start=window_start)
    steps = {s: proportions(clean, s, f"{s}_rate").to_dict() for s in ["started", "viewed", "bound"]}

    # 4. Novelty: is the lift stable over time?
    periods = [("Days 4-7", 3, 6), ("Days 8-14", 7, 13), ("Days 15-21", 14, 20)]
    over_time = [{"period": name, **proportions(clean[(clean.day >= a) & (clean.day <= b)], "bound", "bind_rate").to_dict()}
                 for name, a, b in periods]
    steady = proportions(clean[clean.day >= 7], "bound", "bind_rate")

    # 5. Guardrail: 30-day cancellations among bound policies
    bound = clean[clean.bound == 1]
    cancel = proportions(bound, "cancelled_30d", "cancel_rate_30d")
    cancel_by_source = wh.sql("cancellations", window_start=window_start)
    guardrail_ok = cancel.ci_high <= CANCEL_MARGIN

    # 6. What the business keeps: policies still active after 30 days, and commission
    retained = proportions(clean, "retained_policy", "retained_policy_rate")
    revenue = mean_difference("retained_commission_per_user",
                              clean[~t].retained_commission.to_numpy(), clean[t].retained_commission.to_numpy(), ALPHA)

    # 7. Segments, with a false discovery correction
    seg_rows = []
    for col, values in [("user_type", ["new", "returning"]), ("device", ["ios", "android", "web"])]:
        for v in values:
            e = proportions(clean[clean[col] == v], "bound", "bind_rate")
            seg_rows.append({"segment": f"{col}={v}", **e.to_dict()})
    keep = benjamini_hochberg([r["p_value"] for r in seg_rows], q=ALPHA)
    for r, k in zip(seg_rows, keep):
        r["survives_fdr"] = bool(k)

    # 8. Projection: the same flow with a "confirm your vehicle" step
    tb = bound[bound.arm == "treatment"]
    matched_cancel = tb[tb.vehicle_mismatch == 0].cancelled_30d.mean()
    proj_t = primary.treatment * (1 - matched_cancel)
    projection = {"assumption": "mismatched vehicles are caught before purchase, so treatment policies cancel at the rate of correctly matched ones",
                  "treatment_retained_rate": float(proj_t), "control_retained_rate": retained.control,
                  "diff": float(proj_t - retained.control), "relative": float((proj_t - retained.control) / retained.control),
                  "extra_retained_policies_per_year": float((proj_t - retained.control) * PLANNED_USERS_PER_DAY * 365)}

    # 9. Did the analysis find the effect that was planted? (only possible because the data is synthetic)
    truth = data.truth[data.truth.day >= BUG_DAYS]
    true_ate = float((truth.p_bind_treatment - truth.p_bind_control).mean())

    # 10. Decision
    if srm_clean < SRM_P_MIN:
        decision, why = "Do not decide", "The traffic split is still broken in the analysis window."
    elif primary.significant and primary.diff > 0 and guardrail_ok:
        decision, why = "Ship", "More customers buy a policy and cancellations stay inside the agreed margin."
    elif primary.significant and primary.diff > 0:
        decision, why = ("Fix, then ship",
                         "Pre-fill gets more customers to buy, but 30-day cancellations rise past the agreed margin. "
                         "The rise comes from wrong-trim vehicle lookups, so add a confirm-your-vehicle step and retest.")
    else:
        decision, why = "Don't ship", "No reliable lift in policies bound."

    results = {
        "seed": seed, "plan": plan, "srm": srm, "primary": primary.to_dict(),
        "primary_cuped": primary_cuped.to_dict() | {"variance_reduction": var_reduction},
        "funnel": funnel.to_dict(orient="records"), "funnel_steps": steps,
        "over_time": over_time, "steady_state": steady.to_dict(),
        "guardrail_cancel_30d": cancel.to_dict() | {"margin": CANCEL_MARGIN, "passes": bool(guardrail_ok)},
        "cancellations_by_vehicle_source": cancel_by_source.to_dict(orient="records"),
        "retained_policies": retained.to_dict(), "retained_commission": revenue.to_dict(),
        "segments": seg_rows, "projection_with_confirm_step": projection,
        "true_effect_planted": true_ate, "decision": decision, "decision_reason": why,
        "users_per_day": float(len(clean) / (21 - BUG_DAYS)),
    }
    wh.close()
    if write:
        (ROOT / "reports").mkdir(exist_ok=True)
        (ROOT / "reports" / "results.json").write_text(json.dumps(results, indent=2, default=float))
        (ROOT / "reports" / "decision_memo.md").write_text(memo(results))
        charts.build_site(results, daily, ROOT / "docs")
    return results


def cancel_mix(r: dict) -> dict:
    rows = {(x["arm"], int(x["vehicle_mismatch"])): x for x in r["cancellations_by_vehicle_source"]}
    ctrl = [x for x in r["cancellations_by_vehicle_source"] if x["arm"] == "control"]
    t0, t1 = rows[("treatment", 0)], rows[("treatment", 1)]
    return {"control": sum(x["cancelled_30d"] for x in ctrl) / sum(x["policies"] for x in ctrl),
            "matched": t0["cancel_rate"], "mismatch": t1["cancel_rate"],
            "mismatch_share": t1["policies"] / (t0["policies"] + t1["policies"])}


def usd(x: float) -> str:
    return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"


def pts(x: float, digits: int = 2) -> str:
    return f"{x * 100:+.{digits}f} pts"


def pct(x: float, digits: int = 1) -> str:
    return f"{x * 100:.{digits}f}%"


def memo(r: dict) -> str:
    p, c, g, rp, rv = r["primary"], r["primary_cuped"], r["guardrail_cancel_30d"], r["retained_policies"], r["retained_commission"]
    s, pr = r["srm"], r["projection_with_confirm_step"]
    seg = "\n".join(f"| {x['segment']} | {pct(x['control'], 2)} | {pct(x['treatment'], 2)} | {pts(x['diff'])} "
                    f"[{pts(x['ci_low'])}, {pts(x['ci_high'])}] | {'yes' if x['survives_fdr'] else 'no'} |" for x in r["segments"])
    time_rows = "\n".join(f"| {x['period']} | {pts(x['diff'])} | [{pts(x['ci_low'])}, {pts(x['ci_high'])}] |" for x in r["over_time"])
    broken = ", ".join(f"{b['day']} {b['device']} ({pct(b['treatment_share'])} treatment)" for b in s["broken_cells"])
    first, steady = r["over_time"][0]["diff"], r["steady_state"]["diff"]
    novelty_text = ("The first days run hotter, as new features often do, though the intervals overlap, so treat that as a hint." if first > steady * 1.2
                    else "There's no sign of a launch-week bump.")
    cuped_text = (f"It removed {pct(c['variance_reduction'])} of the variance and tightened the interval without moving the estimate."
                  if c["variance_reduction"] >= 0.05 else
                  f"It removed only {pct(c['variance_reduction'])} of the variance, because past quoting predicts little about who buys. "
                  "Both versions agree, so I report the raw result.")
    sig = [x["segment"].split("=")[1] for x in r["segments"] if x["survives_fdr"]]
    flat = [x["segment"].split("=")[1] for x in r["segments"] if not x["survives_fdr"]]
    segment_text = (f"The lift holds up for: {', '.join(sig)}. It's not distinguishable from zero for: {', '.join(flat) or 'none'}.")
    if "new" in sig and "returning" not in sig:
        segment_text = ("The gain comes from new users, who have the most typing to skip. Returning users already have a vehicle "
                        "on file, so pre-fill changes little for them. " + segment_text)
    cx = cancel_mix(r)
    return f"""# Decision memo: vehicle pre-fill in the quote flow

**Decision: {r['decision']}.** {r['decision_reason']}

All data is synthetic. See the README for how it was generated.

## The question

Should every customer get the license-plate vehicle pre-fill? The business question is not "do more people start a quote."
It is "do we end up with more active policies and more commission." So the primary metric is policies bound per user, and the
decision also depends on how many of those policies are still active after 30 days.

Decision rule, set before the readout: ship only if the traffic split is healthy, the bind rate rises with a 95% interval
above zero, and 30-day cancellations among bound policies do not rise by more than {g['margin'] * 100:.1f} points
(upper end of the 95% interval).

## 1. Can we trust the data?

Not at first. The full window has a sample ratio mismatch: {s['control_full']:,} control vs {s['treatment_full']:,} treatment users
(p = {s['p_value_full_window']:.1e}). Splitting by day and device found it: {broken}. That matches the Android logging bug
fixed in the day-4 release. Treatment users who hit it were never recorded, so including those days would compare
a filtered treatment group to a complete control group.

I dropped the first 3 days for everyone ({s['excluded_users']:,} users). From {s['analysis_window_start']} the split is healthy:
{s['control_window']:,} vs {s['treatment_window']:,} (p = {s['p_value_analysis_window']:.2f}).

## 2. Primary result: policies bound per user

| | Control | Treatment | Difference [95% CI] | Relative |
| --- | --- | --- | --- | --- |
| Bind rate | {pct(p['control'], 2)} | {pct(p['treatment'], 2)} | {pts(p['diff'])} [{pts(p['ci_low'])}, {pts(p['ci_high'])}] | {pct(p['relative'])} |
| Bind rate, CUPED adjusted | | | {pts(c['diff'])} [{pts(c['ci_low'])}, {pts(c['ci_high'])}] | {pct(c['relative'])} |

CUPED uses each user's quote count in the 90 days before the test. {cuped_text} The planned test could detect a change of
{r['plan']['mde_abs'] * 100:.2f} points at 80% power, so this result is above the noise floor.

Lift over time (novelty check):

| Period | Lift | 95% CI |
| --- | --- | --- |
{time_rows}

{novelty_text} From day 8 on, the lift is {pts(r['steady_state']['diff'])}
[{pts(r['steady_state']['ci_low'])}, {pts(r['steady_state']['ci_high'])}]. I'd plan on that number, not the launch-week one.

## 3. Guardrail: 30-day cancellations

| | Control | Treatment | Difference [95% CI] | Margin | Passes |
| --- | --- | --- | --- | --- | --- |
| Cancelled within 30 days | {pct(g['control'])} | {pct(g['treatment'])} | {pts(g['diff'])} [{pts(g['ci_low'])}, {pts(g['ci_high'])}] | +{g['margin'] * 100:.1f} pts | {'yes' if g['passes'] else 'no'} |

The rise is not spread evenly. Policies where the plate lookup returned the wrong trim cancel at {cx['mismatch']:.0%}, about
{cx['mismatch'] / cx['control']:.1f} times the {cx['control']:.1%} rate for manual entry. Correctly matched pre-fill policies cancel at
{cx['matched']:.1%}, close to manual entry. Wrong-trim lookups were {cx['mismatch_share']:.0%} of pre-fill policies. See `cancellations_by_vehicle_source` in results.json.

## 4. What the business keeps

| | Control | Treatment | Difference [95% CI] |
| --- | --- | --- | --- |
| Policies active after 30 days, per user | {pct(rp['control'], 2)} | {pct(rp['treatment'], 2)} | {pts(rp['diff'])} [{pts(rp['ci_low'])}, {pts(rp['ci_high'])}] |
| Retained commission per user | ${rv['control']:.2f} | ${rv['treatment']:.2f} | {usd(rv['diff'])} [{usd(rv['ci_low'])}, {usd(rv['ci_high'])}] |

The cancellations eat part of the bind-rate gain. That's why the headline metric alone would have been the wrong basis for this call.

## 5. Who it works for

| Segment | Control | Treatment | Lift [95% CI] | Survives FDR correction |
| --- | --- | --- | --- | --- |
{seg}

{segment_text} Segment results are
corrected with Benjamini-Hochberg so that slicing five ways doesn't manufacture a finding.

## 6. Recommendation

{r['decision']}. Add a one-tap "Is this your vehicle?" confirmation showing year, make, model and trim, then rerun for two weeks
with the same guardrail.

Projection, not a measured result: if the confirmation step catches the wrong-trim lookups, so treatment policies cancel at
the matched-vehicle rate, retained policies per user would rise by {pts(pr['diff'])} ({pct(pr['relative'])}). At about
{PLANNED_USERS_PER_DAY:,} users a day, that's roughly {pr['extra_retained_policies_per_year']:,.0f} extra active policies a year.
The rerun is what confirms or kills this number.

## Validation

The data is synthetic, so the true effect is known. Planted lift on the bind rate in the analysis window:
{pts(r['true_effect_planted'])}. Measured: {pts(p['diff'])} [{pts(p['ci_low'])}, {pts(p['ci_high'])}]. A test in CI fails if the
interval ever stops covering the planted effect.
"""


if __name__ == "__main__":
    out = run()
    print(f"Decision: {out['decision']}")
    p = out["primary"]
    print(f"Bind rate {p['control']:.4f} -> {p['treatment']:.4f}, diff {p['diff']:+.4f} [{p['ci_low']:+.4f}, {p['ci_high']:+.4f}], "
          f"true {out['true_effect_planted']:+.4f}")
