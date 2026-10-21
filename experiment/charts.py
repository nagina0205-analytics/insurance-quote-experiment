"""Charts (SVG) and the one-page readout published to GitHub Pages."""
from __future__ import annotations

import html
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

INK, MUTED, GRID = "#1f2933", "#6b7785", "#e4e7eb"
CONTROL, TREATMENT, BAD = "#8a94a6", "#1f6feb", "#c2410c"

plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 10, "axes.edgecolor": GRID, "axes.labelcolor": MUTED,
                     "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
                     "svg.fonttype": "none"})


def _save(fig, path: Path):
    fig.tight_layout()
    fig.savefig(path, format="svg", transparent=True)
    plt.close(fig)


def srm_chart(daily: pd.DataFrame, path: Path, bug_days: int = 3):
    d = daily.copy()
    d["group"] = d.device.where(d.device == "android", "ios and web")
    g = d.groupby(["day", "group"])[["control_users", "treatment_users"]].sum().reset_index()
    g["share"] = g.treatment_users / (g.control_users + g.treatment_users)
    fig, ax = plt.subplots(figsize=(7, 2.8))
    days = sorted(g.day.unique())
    ax.axvspan(days[0] - pd.Timedelta(hours=12), days[bug_days - 1] + pd.Timedelta(hours=12), color=BAD, alpha=0.08, lw=0)
    ax.text(days[0], 0.36, "logging bug\n(excluded)", color=BAD, fontsize=9, va="bottom")
    for name, color in [("android", BAD), ("ios and web", TREATMENT)]:
        s = g[g.group == name]
        ax.plot(s.day, s.share, marker="o", ms=3, lw=1.5, color=color, label=name)
    ax.axhline(0.5, color=INK, lw=0.8, ls="--")
    ax.set_ylim(0.28, 0.56)
    ax.set_ylabel("share in treatment")
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1, decimals=0))
    ax.xaxis.set_major_formatter(matplotlib.dates.DateFormatter("%b %d"))
    ax.grid(axis="y", color=GRID)
    ax.legend(frameon=False, loc="lower right")
    _save(fig, path)


def interval_chart(rows: list[dict], label_key: str, path: Path, title_unit: str = "pts"):
    fig, ax = plt.subplots(figsize=(7, 0.55 * len(rows) + 0.9))
    for i, r in enumerate(reversed(rows)):
        color = TREATMENT if r["ci_low"] > 0 else MUTED
        ax.plot([r["ci_low"] * 100, r["ci_high"] * 100], [i, i], color=color, lw=2.5, solid_capstyle="round")
        ax.plot(r["diff"] * 100, i, "o", color=color, ms=7)
        ax.text(r["ci_high"] * 100 + 0.05, i, f"{r['diff'] * 100:+.2f}", va="center", fontsize=9, color=INK)
    ax.axvline(0, color=INK, lw=0.8)
    ax.set_yticks(range(len(rows)), [r[label_key] for r in reversed(rows)])
    ax.set_xlabel(f"change in bind rate ({title_unit}), 95% interval")
    ax.grid(axis="x", color=GRID)
    _save(fig, path)


def cancel_chart(rows: list[dict], path: Path):
    lookup = {(r["arm"], int(r["vehicle_mismatch"])): r for r in rows}
    ctrl = [r for r in rows if r["arm"] == "control"]
    pol = sum(r["policies"] for r in ctrl)
    control_all = {"policies": pol, "cancel_rate": sum(r["cancelled_30d"] for r in ctrl) / pol}
    bars = [("Manual entry\n(control)", control_all, CONTROL),
            ("Pre-fill, right vehicle", lookup[("treatment", 0)], TREATMENT),
            ("Pre-fill, wrong trim", lookup[("treatment", 1)], BAD)]
    fig, ax = plt.subplots(figsize=(7, 2.6))
    for i, (name, r, color) in enumerate(bars):
        ax.barh(i, r["cancel_rate"] * 100, color=color, height=0.6)
        ax.text(r["cancel_rate"] * 100 + 0.3, i, f"{r['cancel_rate'] * 100:.1f}%  ({int(r['policies']):,} policies)",
                va="center", fontsize=9, color=INK)
    ax.set_yticks(range(3), [b[0] for b in bars])
    ax.invert_yaxis()
    ax.set_xlim(0, 32)
    ax.set_xlabel("policies cancelled within 30 days")
    ax.grid(axis="x", color=GRID)
    _save(fig, path)


def funnel_chart(steps: dict, path: Path):
    names = [("started", "Started a quote"), ("viewed", "Viewed quotes"), ("bound", "Bought a policy")]
    fig, axes = plt.subplots(1, 3, figsize=(7, 2.4))
    for ax, (k, label) in zip(axes, names):
        s = steps[k]
        ax.bar([0, 1], [s["control"] * 100, s["treatment"] * 100], color=[CONTROL, TREATMENT], width=0.6)
        for x, v in [(0, s["control"]), (1, s["treatment"])]:
            ax.text(x, v * 100, f"{v * 100:.1f}%", ha="center", va="bottom", fontsize=9, color=INK)
        ax.set_xticks([0, 1], ["control", "treatment"])
        ax.set_title(label, fontsize=10, color=INK)
        ax.set_yticks([])
        ax.spines["left"].set_visible(False)
        ax.set_ylim(0, max(s["control"], s["treatment"]) * 125)
    _save(fig, path)


def _usd(x): return f"{'+' if x >= 0 else '-'}${abs(x):.2f}"
def _pts(x): return f"{x * 100:+.2f} pts"
def _pct(x, d=2): return f"{x * 100:.{d}f}%"


def build_site(r: dict, daily: pd.DataFrame, out: Path):
    out.mkdir(parents=True, exist_ok=True)
    srm_chart(daily, out / "srm.svg")
    funnel_chart(r["funnel_steps"], out / "funnel.svg")
    interval_chart([{"label": x["period"], **x} for x in r["over_time"]], "label", out / "over_time.svg")
    interval_chart([{"label": x["segment"].split("=")[1], **x} for x in r["segments"]], "label", out / "segments.svg")
    cancel_chart(r["cancellations_by_vehicle_source"], out / "cancellations.svg")
    p, c, g, rp, rv, pr, s = (r["primary"], r["primary_cuped"], r["guardrail_cancel_30d"], r["retained_policies"],
                              r["retained_commission"], r["projection_with_confirm_step"], r["srm"])
    e = html.escape
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Quote Flow Experiment</title>
<style>
:root {{ --ink:#1f2933; --muted:#6b7785; --line:#e4e7eb; --bg:#fbfbfa; --card:#fff; --blue:#1f6feb; --bad:#c2410c; --ok:#15803d; }}
* {{ box-sizing: border-box; }}
body {{ margin:0; background:var(--bg); color:var(--ink); font:16px/1.55 -apple-system, "Segoe UI", Roboto, sans-serif; }}
main {{ max-width: 820px; margin: 0 auto; padding: 32px 16px 64px; }}
h1 {{ font-size: 28px; margin: 0 0 6px; }} h2 {{ font-size: 19px; margin: 36px 0 8px; }}
.sub {{ color: var(--muted); margin: 0 0 24px; }}
.decision {{ background: var(--card); border: 1px solid var(--line); border-left: 4px solid var(--bad); padding: 16px 18px; border-radius: 6px; }}
.decision strong {{ font-size: 20px; }}
.stats {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr)); gap: 12px; margin: 20px 0; }}
.stats div {{ background: var(--card); border:1px solid var(--line); border-radius:6px; padding:12px 14px; }}
.stats b {{ display:block; font-size: 22px; }} .stats span {{ color: var(--muted); font-size: 13px; }}
.good {{ color: var(--ok); }} .bad {{ color: var(--bad); }}
img {{ width: 100%; height: auto; background: var(--card); border:1px solid var(--line); border-radius: 6px; padding: 8px; }}
table {{ border-collapse: collapse; width: 100%; font-size: 14px; background: var(--card); }}
th, td {{ text-align: left; padding: 7px 10px; border-bottom: 1px solid var(--line); }} th {{ color: var(--muted); font-weight: 600; }}
.scroll {{ overflow-x: auto; }} .note {{ color: var(--muted); font-size: 14px; }}
a {{ color: var(--blue); }}
</style></head><body><main>
<h1>Should every customer get vehicle pre-fill?</h1>
<p class="sub">A/B test readout for an insurance quote flow. Analysis in ClickHouse SQL and Python. All data is synthetic.
<a href="https://github.com/nagina0205-analytics/insurance-quote-experiment">Code, notebook and memo</a></p>

<div class="decision"><strong>{e(r['decision'])}.</strong> {e(r['decision_reason'])}</div>

<div class="stats">
<div><b class="good">{_pts(p['diff'])}</b><span>bind rate ({_pct(p['relative'], 1)} relative)</span></div>
<div><b class="bad">{_pts(g['diff'])}</b><span>30-day cancellations (margin +{g['margin']*100:.1f})</span></div>
<div><b>{_pts(rp['diff'])}</b><span>policies still active after 30 days</span></div>
<div><b>{_usd(rv['diff'])}</b><span>retained commission per user</span></div>
</div>

<h2>1. The data had to be fixed first</h2>
<p>The full window failed the sample ratio check (p = {s['p_value_full_window']:.1e}). Treatment Android users were missing for three days
because of a logging bug. I excluded those days for everyone. The split in the remaining window is healthy (p = {s['p_value_analysis_window']:.2f}).</p>
<img src="srm.svg" alt="Share of users in treatment by day; Android drops to about 40% for the first three days">

<h2>2. Pre-fill gets more people to buy</h2>
<img src="funnel.svg" alt="Funnel conversion by arm">
<div class="scroll"><table>
<tr><th>Bind rate</th><th>Control</th><th>Treatment</th><th>Difference, 95% CI</th></tr>
<tr><td>Raw</td><td>{_pct(p['control'])}</td><td>{_pct(p['treatment'])}</td><td>{_pts(p['diff'])} [{_pts(p['ci_low'])}, {_pts(p['ci_high'])}]</td></tr>
<tr><td>CUPED adjusted</td><td></td><td></td><td>{_pts(c['diff'])} [{_pts(c['ci_low'])}, {_pts(c['ci_high'])}]</td></tr>
</table></div>
<p>Lift by period, to check for a launch-week novelty effect:</p>
<img src="over_time.svg" alt="Lift by period with 95% intervals">

<h2>3. But the wrong trim gets cancelled</h2>
<p>Cancellations within 30 days rose {_pts(g['diff'])} [{_pts(g['ci_low'])}, {_pts(g['ci_high'])}], past the +{g['margin']*100:.1f} point margin.
The rise comes from plate lookups that returned the wrong trim. Correct pre-fills cancel like manual entry.</p>
<img src="cancellations.svg" alt="30-day cancellation rate: manual entry, correct pre-fill, wrong-trim pre-fill">
<p>After cancellations, retained commission per user moved {_usd(rv['diff'])} [{_usd(rv['ci_low'])}, {_usd(rv['ci_high'])}].</p>

<h2>4. Who it works for</h2>
<img src="segments.svg" alt="Lift by segment with 95% intervals">
<p class="note">Segments are corrected for multiple comparisons (Benjamini-Hochberg).</p>

<h2>5. Recommendation</h2>
<p>Add a one-tap "Is this your vehicle?" step and rerun for two weeks with the same guardrail. If it catches the wrong-trim lookups,
retained policies would rise about {_pts(pr['diff'])} per user, roughly {pr['extra_retained_policies_per_year']:,.0f} extra active policies a year.
That figure is a projection. The rerun decides it.</p>

<p class="note">Check on the method: the data is synthetic, so the true lift is known ({_pts(r['true_effect_planted'])}).
The measured interval covers it, and a CI test fails if it ever doesn't.</p>
</main></body></html>"""
    (out / "index.html").write_text(page)
