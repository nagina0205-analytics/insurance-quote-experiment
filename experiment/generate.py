"""Synthetic data for a car insurance quote-flow experiment.

The story: an insurance shopping app tests a new quote flow. In the treatment,
the app looks up the customer's vehicle from their license plate and pre-fills
it, instead of asking them to type year, make, model and trim.

Everything here is synthetic. The true effects are planted on purpose, so the
tests can check that the analysis recovers them:
  - pre-fill makes more new users start a quote (+6 points); returning users, who already have a vehicle on file, don't change
  - a small novelty bump in the first days after launch that fades
  - the plate lookup returns the wrong trim 12% of the time; those policies
    get cancelled much more often in the first 30 days (wrong vehicle, wrong price)
  - a logging bug on Android drops half of treatment Android users in the first 3 days,
    which shows up as a sample ratio mismatch
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

START = date(2026, 8, 3)
DAYS = 21
BUG_DAYS = 3                 # Android logging bug, fixed in the day-4 release
BUG_DROP = 0.50              # share of treatment Android users never logged during the bug
STATES = ["TX", "CA", "FL", "OH", "IL", "GA", "NC", "AZ"]
STATE_W = [0.18, 0.17, 0.15, 0.11, 0.10, 0.10, 0.10, 0.09]
STATE_PREMIUM = {"TX": 1.05, "CA": 1.15, "FL": 1.30, "OH": 0.80, "IL": 0.95, "GA": 1.05, "NC": 0.85, "AZ": 1.00}
COMMISSION_RATE = 0.10       # share of annual premium earned when a policy stays active 30+ days
MISMATCH_RATE = 0.12         # plate lookup returns the wrong trim
CANCEL_MATCH, CANCEL_MISMATCH = 0.06, 0.24


@dataclass
class Dataset:
    users: pd.DataFrame      # one row per logged, assigned user (what the warehouse sees)
    events: pd.DataFrame     # funnel events
    policies: pd.DataFrame   # bound policies and 30-day cancellations
    truth: pd.DataFrame      # each logged user's true bind probability under both arms (never loaded into the warehouse)


def assign(user_id: str, salt: str = "quote-prefill-v1") -> str:
    """Deterministic 50/50 assignment by hashing the user id."""
    h = int(hashlib.md5(f"{salt}:{user_id}".encode()).hexdigest()[:8], 16)
    return "treatment" if h % 2 else "control"


def start_prob(prior: np.ndarray, new: np.ndarray, treated: np.ndarray, day: np.ndarray) -> np.ndarray:
    base = 0.48 + 0.035 * np.minimum(prior, 5)
    lift = np.where(new, 0.06, 0.0)
    novelty = np.where((day >= BUG_DAYS) & (day < BUG_DAYS + 4), 0.03 * (1 - (day - BUG_DAYS) / 4), 0.0)
    return base + treated * (lift + novelty)


def view_prob(treated: np.ndarray) -> np.ndarray:
    return np.where(treated, 0.81, 0.80)


def bind_given_view(prior: np.ndarray) -> np.ndarray:
    return 0.10 + 0.02 * np.minimum(prior, 5)


def generate(seed: int = 7, n_users: int = 200_000) -> Dataset:
    rng = np.random.default_rng(seed)
    ids = np.array([f"U{seed:02d}{i:07d}" for i in range(n_users)])
    arm = np.array([assign(u) for u in ids])
    treated = (arm == "treatment").astype(float)
    day = rng.integers(0, DAYS, n_users)
    device = rng.choice(["ios", "android", "web"], n_users, p=[0.45, 0.40, 0.15])
    new = rng.random(n_users) < 0.70
    state = rng.choice(STATES, n_users, p=STATE_W)
    prior = np.where(new, rng.poisson(0.3, n_users), rng.poisson(2.5, n_users))

    p_start = start_prob(prior, new, treated, day)
    p_view = view_prob(treated.astype(bool))
    p_bind = bind_given_view(prior)
    started = rng.random(n_users) < p_start
    viewed = started & (rng.random(n_users) < p_view)
    bound = viewed & (rng.random(n_users) < p_bind)

    # the logging bug: some treatment Android users in the first days never reach the warehouse
    logged = ~((arm == "treatment") & (device == "android") & (day < BUG_DAYS) & (rng.random(n_users) < BUG_DROP))

    open_ts = pd.Timestamp(START) + pd.to_timedelta(day, unit="D") \
        + pd.to_timedelta(rng.integers(6 * 3600, 23 * 3600, n_users), unit="s")
    users = pd.DataFrame({"user_id": ids, "arm": arm, "assigned_at": open_ts, "device": device,
                          "user_type": np.where(new, "new", "returning"), "state": state,
                          "prior_quotes_90d": prior.astype(np.uint16)})

    ev = [pd.DataFrame({"user_id": ids, "event": "app_open", "ts": open_ts})]
    t = open_ts
    for name, mask, gap in [("quote_started", started, 60), ("quotes_viewed", viewed, 240), ("policy_bound", bound, 900)]:
        t = t + pd.to_timedelta(rng.integers(20, gap, n_users), unit="s")
        ev.append(pd.DataFrame({"user_id": ids[mask], "event": name, "ts": t[mask]}))
    bound_ts = t

    b = np.flatnonzero(bound)
    mismatch = np.where(arm[b] == "treatment", rng.random(b.size) < MISMATCH_RATE, rng.random(b.size) < 0.02)
    cancelled = rng.random(b.size) < np.where(mismatch, CANCEL_MISMATCH, CANCEL_MATCH)
    premium = np.round(rng.lognormal(np.log(1450), 0.35, b.size) * np.array([STATE_PREMIUM[s] for s in state[b]]), 2)
    cancel_ts = bound_ts[b] + pd.to_timedelta(rng.integers(1, 30, b.size), unit="D")
    policies = pd.DataFrame({"policy_id": [f"P{seed:02d}{i:07d}" for i in range(b.size)], "user_id": ids[b],
                             "bound_at": bound_ts[b], "annual_premium": premium,
                             "vehicle_source": np.where(arm[b] == "treatment", "plate_lookup", "manual_entry"),
                             "vehicle_mismatch": mismatch.astype(np.uint8),
                             "cancelled_at": cancel_ts.where(cancelled, pd.NaT)})
    ev.append(pd.DataFrame({"user_id": ids[b][cancelled], "event": "policy_cancelled", "ts": cancel_ts[cancelled]}))
    events = pd.concat(ev, ignore_index=True)

    # true bind probability for each user under each arm, from the generator's own parameters
    p_c = start_prob(prior, new, np.zeros(n_users), day) * view_prob(np.zeros(n_users, bool)) * p_bind
    p_t = start_prob(prior, new, np.ones(n_users), day) * view_prob(np.ones(n_users, bool)) * p_bind
    truth = pd.DataFrame({"user_id": ids, "day": day, "p_bind_control": p_c, "p_bind_treatment": p_t,
                          "user_type": users.user_type, "device": device})

    keep = set(ids[logged])
    return Dataset(users=users[logged].reset_index(drop=True),
                   events=events[events.user_id.isin(keep)].sort_values("ts").reset_index(drop=True),
                   policies=policies[policies.user_id.isin(keep)].reset_index(drop=True),
                   truth=truth[logged].reset_index(drop=True))
