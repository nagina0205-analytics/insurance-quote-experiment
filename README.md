# Insurance Quote Flow Experiment

An A/B test readout for a car insurance shopping app. The question is whether every customer should get vehicle pre-fill:
the app reads the license plate and fills in year, make, model and trim, instead of asking the customer to type them.

The answer turned out not to be yes or no. **Pre-fill gets more people to buy a policy, but it also brings more cancellations,
and the cancellations come from one specific failure.** The decision is "fix, then ship."

**Live readout:** https://nagina0205-analytics.github.io/insurance-quote-experiment/
**Decision memo:** [reports/decision_memo.md](reports/decision_memo.md)
**Working notebook:** [notebooks/experiment_analysis.ipynb](notebooks/experiment_analysis.ipynb)

Built with ClickHouse SQL (embedded through chdb), Python (pandas, SciPy, statsmodels), Jupyter and GitHub Actions.
All data is synthetic. About 200,000 users over 21 days.

## Results

| | Control | Treatment | Difference [95% CI] |
| --- | --- | --- | --- |
| Bind rate (primary) | 5.02% | 5.49% | +0.47 pts [+0.26 pts, +0.68 pts] |
| Cancelled within 30 days (guardrail) | 6.4% | 7.8% | +1.35 pts [+0.29 pts, +2.41 pts] |
| Policies still active after 30 days | 4.70% | 5.06% | +0.36 pts [+0.16 pts, +0.57 pts] |
| Retained commission per user | $7.63 | $8.18 | +$0.55 [+$0.20, +$0.91] |

**Decision: Fix, then ship.** Pre-fill gets more customers to buy, but 30-day cancellations rise past the agreed margin. The rise comes from wrong-trim vehicle lookups, so add a confirm-your-vehicle step and retest.

## What the analysis had to get right

**1. Define the decision before the analysis.** "Do more people start a quote?" is the easy question, and it says yes.
The business cares about policies that stay active and earn commission. So the primary metric is bind rate per user,
with a guardrail written down before the readout: 30-day cancellations may not rise by more than 1 point.

**2. Don't trust the data until it earns it.** The full window failed the sample ratio check (p = 1.8e-07).
Splitting by day and device pointed to Android in the first three days, where treatment users were missing because of a
logging bug. Including those days would have compared a filtered treatment group with a complete control group.
I dropped those days for both arms, and the split is healthy afterwards (p = 0.58).
The day-by-device scan uses a Bonferroni threshold. Without it, one ordinary iOS day was flagged by chance.

**3. Look past the headline.** Bind rate rose about 9%. But cancellations rose past the margin. The cause was narrow:
the plate lookup returns the wrong trim on about 12% of pre-fills, and those policies cancel at roughly four times the
normal rate (wrong vehicle, wrong price). Correctly matched pre-fills cancel like manual entry.

**4. Be honest about what didn't help.** CUPED, using each user's quote count from the 90 days before the test, removed only
0.5% of the variance. Past quoting predicts little about who buys. I report it anyway.

**5. Don't slice your way to a finding.** Five segments are tested, so the results go through a Benjamini-Hochberg correction.
The lift holds for new users. For returning users, who already have a vehicle on file, it can't be told apart from zero.

**6. Separate measured from projected.** If a one-tap "Is this your vehicle?" step catches the wrong-trim lookups, retained
policies would rise about +0.48 pts per user, around 16,679 extra active policies a year at
9,500 users a day. That's a projection under a stated assumption. The recommended rerun is what tests it.

## Checking the method

Because the data is synthetic, the true effect is known. The generator plants a lift for new users, a small launch-week novelty
bump, the wrong-trim cancellations and the Android logging bug. A test fails the build if the analysis stops finding any of them:

- the 95% interval must cover the planted lift (planted +0.46 pts, measured +0.47 pts)
- the sample ratio check must catch the bug, and only on Android
- the guardrail must flag the wrong-trim cancellations
- the metrics computed in ClickHouse SQL must match a separate recount in pandas
- the statistics match statsmodels (z-test, Benjamini-Hochberg), and the 95% interval covers the truth about 95% of the time in simulation

## How it's built

```
experiment/generate.py    synthetic users, funnel events and policies, with known planted effects
sql/*.sql                 metric definitions in ClickHouse SQL (countIf, uniqExactIf, MergeTree tables)
experiment/warehouse.py   loads the data into embedded ClickHouse and runs the SQL
experiment/stats.py       sample ratio check, z-test, Welch test, CUPED, power, Benjamini-Hochberg
experiment/analyze.py     the readout: data checks, primary metric, guardrail, segments, decision
notebooks/                the same analysis step by step, executed in CI
reports/                  decision memo and results.json
docs/                     the GitHub Pages readout
```

Unit of randomization and unit of analysis are both the user, so plain standard errors are valid.
The SQL runs unchanged on a ClickHouse server.

## Run it

```
pip install -r requirements.txt
pytest -q                          # 12 tests
python -m experiment.analyze       # writes the memo, results.json and the site
jupyter execute --inplace notebooks/experiment_analysis.ipynb
```

## What I'd do next

Rerun with the confirmation step for two weeks. Add a sequential testing method so the team can look at results daily without
inflating false positives. Track the wrong-trim rate as a standing data quality metric on the plate lookup vendor.
