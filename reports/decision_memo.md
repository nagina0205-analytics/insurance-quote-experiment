# Decision memo: vehicle pre-fill in the quote flow

**Decision: Fix, then ship.** Pre-fill gets more customers to buy, but 30-day cancellations rise past the agreed margin. The rise comes from wrong-trim vehicle lookups, so add a confirm-your-vehicle step and retest.

All data is synthetic. See the README for how it was generated.

## The question

Should every customer get the license-plate vehicle pre-fill? The business question is not "do more people start a quote."
It is "do we end up with more active policies and more commission." So the primary metric is policies bound per user, and the
decision also depends on how many of those policies are still active after 30 days.

Decision rule, set before the readout: ship only if the traffic split is healthy, the bind rate rises with a 95% interval
above zero, and 30-day cancellations among bound policies do not rise by more than 1.0 points
(upper end of the 95% interval).

## 1. Can we trust the data?

Not at first. The full window has a sample ratio mismatch: 99,734 control vs 97,416 treatment users
(p = 1.8e-07). Splitting by day and device found it: 2026-08-03 android (33.2% treatment), 2026-08-04 android (34.3% treatment), 2026-08-05 android (34.2% treatment). That matches the Android logging bug
fixed in the day-4 release. Treatment users who hit it were never recorded, so including those days would compare
a filtered treatment group to a complete control group.

I dropped the first 3 days for everyone (25,686 users). From 2026-08-06 the split is healthy:
85,617 vs 85,847 (p = 0.58).

## 2. Primary result: policies bound per user

| | Control | Treatment | Difference [95% CI] | Relative |
| --- | --- | --- | --- | --- |
| Bind rate | 5.02% | 5.49% | +0.47 pts [+0.26 pts, +0.68 pts] | 9.3% |
| Bind rate, CUPED adjusted | | | +0.47 pts [+0.26 pts, +0.68 pts] | 9.3% |

CUPED uses each user's quote count in the 90 days before the test. It removed only 0.5% of the variance, because past quoting predicts little about who buys. Both versions agree, so I report the raw result. The planned test could detect a change of
0.27 points at 80% power, so this result is above the noise floor.

Lift over time (novelty check):

| Period | Lift | 95% CI |
| --- | --- | --- |
| Days 4-7 | +0.63 pts | [+0.17 pts, +1.09 pts] |
| Days 8-14 | +0.34 pts | [+0.01 pts, +0.68 pts] |
| Days 15-21 | +0.50 pts | [+0.16 pts, +0.84 pts] |

The first days run hotter, as new features often do, though the intervals overlap, so treat that as a hint. From day 8 on, the lift is +0.42 pts
[+0.18 pts, +0.66 pts]. I'd plan on that number, not the launch-week one.

## 3. Guardrail: 30-day cancellations

| | Control | Treatment | Difference [95% CI] | Margin | Passes |
| --- | --- | --- | --- | --- | --- |
| Cancelled within 30 days | 6.4% | 7.8% | +1.35 pts [+0.29 pts, +2.41 pts] | +1.0 pts | no |

The rise is not spread evenly. Policies where the plate lookup returned the wrong trim cancel at 23%, about
3.6 times the 6.4% rate for manual entry. Correctly matched pre-fill policies cancel at
5.6%, close to manual entry. Wrong-trim lookups were 12% of pre-fill policies. See `cancellations_by_vehicle_source` in results.json.

## 4. What the business keeps

| | Control | Treatment | Difference [95% CI] |
| --- | --- | --- | --- |
| Policies active after 30 days, per user | 4.70% | 5.06% | +0.36 pts [+0.16 pts, +0.57 pts] |
| Retained commission per user | $7.63 | $8.18 | +$0.55 [+$0.20, +$0.91] |

The cancellations eat part of the bind-rate gain. That's why the headline metric alone would have been the wrong basis for this call.

## 5. Who it works for

| Segment | Control | Treatment | Lift [95% CI] | Survives FDR correction |
| --- | --- | --- | --- | --- |
| user_type=new | 4.25% | 4.75% | +0.50 pts [+0.27 pts, +0.74 pts] | yes |
| user_type=returning | 6.83% | 7.21% | +0.38 pts [-0.06 pts, +0.82 pts] | no |
| device=ios | 5.08% | 5.47% | +0.39 pts [+0.07 pts, +0.71 pts] | yes |
| device=android | 5.00% | 5.52% | +0.52 pts [+0.18 pts, +0.85 pts] | yes |
| device=web | 4.91% | 5.48% | +0.57 pts [+0.03 pts, +1.12 pts] | yes |

The gain comes from new users, who have the most typing to skip. Returning users already have a vehicle on file, so pre-fill changes little for them. The lift holds up for: new, ios, android, web. It's not distinguishable from zero for: returning. Segment results are
corrected with Benjamini-Hochberg so that slicing five ways doesn't manufacture a finding.

## 6. Recommendation

Fix, then ship. Add a one-tap "Is this your vehicle?" confirmation showing year, make, model and trim, then rerun for two weeks
with the same guardrail.

Projection, not a measured result: if the confirmation step catches the wrong-trim lookups, so treatment policies cancel at
the matched-vehicle rate, retained policies per user would rise by +0.48 pts (10.2%). At about
9,500 users a day, that's roughly 16,679 extra active policies a year.
The rerun is what confirms or kills this number.

## Validation

The data is synthetic, so the true effect is known. Planted lift on the bind rate in the analysis window:
+0.46 pts. Measured: +0.47 pts [+0.26 pts, +0.68 pts]. A test in CI fails if the
interval ever stops covering the planted effect.
