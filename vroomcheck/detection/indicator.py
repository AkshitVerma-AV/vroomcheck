"""Fleet-normalised complaint indicator from ESV26-289.

Jones et al., "A Check Engine Light for the Automotive Industry: Detecting
Automotive Failures from Consumer Complaints and Online Forums" (NHTSA ESV),
adapted from daily to monthly data. For vehicle line v (make + model),
component c and month t, using only the 12 months ending at t:

    n         complaints about c for v
    share     n / (all of v's complaints)            c's share of v's own complaints
    baseline  c's share of every other vehicle's complaints
    h         Cohen's h between share and baseline   the paper's "Cohen's d-style
                                                     difference between proportions"
    D         ln(n) * h                              damped when few complaints back it
    P         2 * (Phi(D) - 0.5)                     in (-1, 1); above 0 = worse than the fleet

Sustained-elevation test, the paper's H1: over the last 6 months the mean of
0.5 * (P + 1) is above 0.65 (one-sided t-test, flag when 1 - p >= 0.99), with
more than 3 complaints in the trailing year behind it. Every input is trailing,
so the flag for month t never uses data after t and can be back-tested.

Where the paper leaves a choice open we take: the share of the vehicle's own
complaints (the printed formula is ambiguous), Cohen's h for the effect size,
the natural log, and a baseline that leaves the vehicle out of its own fleet.

Flagged months less than 12 months apart are merged into events for display.
The back-test reads the monthly flag, so merging never moves an alert earlier.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import norm
from scipy.stats import t as student_t

from vroomcheck import config
from vroomcheck.db import connect

log = logging.getLogger(__name__)

KEY = ["make", "model", "component"]


def _trailing_sum(x: np.ndarray, w: int) -> np.ndarray:
    """Sum of the last w months (inclusive) in every row; shorter at the start."""
    c = np.cumsum(x, axis=1)
    out = c.copy()
    out[:, w:] -= c[:, :-w]
    return out


def score_indicator(counts: pd.DataFrame, months: pd.DatetimeIndex) -> pd.DataFrame:
    """counts: make, model, component, month, n (complaints received that month).

    Returns the indicator for every vehicle-component-month with at least one
    complaint in the trailing year."""
    w, tw = config.PAPER_COUNT_MONTHS, config.PAPER_TEST_MONTHS
    keys = counts[KEY].drop_duplicates().sort_values(KEY).reset_index(drop=True)
    row = pd.MultiIndex.from_frame(keys).get_indexer(pd.MultiIndex.from_frame(counts[KEY]))
    col = months.get_indexer(pd.to_datetime(counts["month"]))
    ok = col >= 0
    n = np.zeros((len(keys), len(months)))
    np.add.at(n, (row[ok], col[ok]), counts["n"].to_numpy(dtype=float)[ok])

    n12 = _trailing_sum(n, w)
    veh, _ = pd.factorize(keys.make + "\x1f" + keys.model)
    comp, _ = pd.factorize(keys.component)
    veh_tot = np.zeros((veh.max() + 1, len(months)))
    comp_tot = np.zeros((comp.max() + 1, len(months)))
    np.add.at(veh_tot, veh, n12)
    np.add.at(comp_tot, comp, n12)
    n_v, n_c, total = veh_tot[veh], comp_tot[comp], n12.sum(axis=0)

    with np.errstate(divide="ignore", invalid="ignore"):
        share = np.where(n_v > 0, n12 / n_v, 0.0)
        others = total - n_v
        baseline = np.where(others > 0, (n_c - n12) / others, 0.0)
        h = 2 * np.arcsin(np.sqrt(share)) - 2 * np.arcsin(np.sqrt(baseline))
        d = np.where(n12 > 0, np.log(np.maximum(n12, 1)) * h, 0.0)
    score = norm.cdf(d)                       # = 0.5 * (P + 1), the quantity the test averages
    p_indicator = 2 * score - 1

    mean = _trailing_sum(score, tw) / tw
    var = np.maximum((_trailing_sum(score ** 2, tw) - tw * mean ** 2) / (tw - 1), 0.0)
    se = np.sqrt(var / tw)
    with np.errstate(divide="ignore", invalid="ignore"):
        p_value = np.where(se > 0, student_t.sf((mean - config.PAPER_THRESHOLD) / se, tw - 1),
                           np.where(mean > config.PAPER_THRESHOLD, 0.0, 1.0))
    # a verdict needs a full count window and a full test window behind it
    warm = np.arange(len(months)) >= w + tw - 2
    elevated = warm & (1 - p_value >= config.PAPER_CONFIDENCE) & (n12 > config.PAPER_MIN_SUPPORT)

    r, c = np.nonzero(n12 > 0)
    out = keys.iloc[r].reset_index(drop=True)
    out["month"] = months[c]
    for name, arr in [("n", n), ("n_12m", n12), ("share", share), ("baseline", baseline),
                      ("cohens_h", h), ("d_indicator", d), ("p_indicator", p_indicator),
                      ("window_mean", mean), ("p_value", p_value), ("is_elevated", elevated)]:
        out[name] = arr[r, c]
    return out


def merge_events(ind: pd.DataFrame, gap_months: int = config.PAPER_MERGE_GAP_MONTHS) -> pd.DataFrame:
    """Join each vehicle-component's flagged months into events (paper: gaps under 365 days)."""
    f = ind[ind.is_elevated].sort_values(KEY + ["month"]).reset_index(drop=True)
    cols = KEY + ["start_month", "end_month", "flagged_months", "peak_p_indicator", "event_complaints"]
    if f.empty:
        return pd.DataFrame(columns=cols)
    mi = f.month.dt.year * 12 + f.month.dt.month
    same_series = (f[KEY] == f[KEY].shift()).all(axis=1)
    f["event"] = (~same_series | (mi.diff() > gap_months)).cumsum()
    ev = f.groupby("event").agg(make=("make", "first"), model=("model", "first"),
                                component=("component", "first"), start_month=("month", "min"),
                                end_month=("month", "max"), flagged_months=("month", "size"),
                                peak_p_indicator=("p_indicator", "max"))
    # complaints from one year before the event starts through its end, as the paper counts them
    m = ev.reset_index().merge(ind[KEY + ["month", "n"]], on=KEY)
    m = m[(m.month > m.start_month - pd.DateOffset(months=config.PAPER_COUNT_MONTHS))
          & (m.month <= m.end_month)]
    ev["event_complaints"] = m.groupby("event").n.sum()
    return ev.reset_index(drop=True)[cols]


def run_indicator() -> int:
    con = connect()
    end = con.execute("""select date_trunc('month', max(received_date)) - interval 1 month
                         from marts.fct_complaints""").fetchone()[0]
    counts = con.execute("""
        select make, model, primary_component as component, received_month as month, count(*) as n
        from marts.fct_complaints
        where not is_remedy_complaint
        group by all""").df()
    months = pd.date_range(f"{config.MIN_RECEIVED_YEAR}-01-01", pd.Timestamp(end), freq="MS")
    ind = score_indicator(counts, months)
    events = merge_events(ind)
    con.register("ind_df", ind)
    con.register("ev_df", events)
    con.execute("""create or replace table ml.vehicle_component_indicator as
                   select * replace (month::date as month) from ind_df""")
    con.execute("""create or replace table ml.indicator_events as
                   select * replace (start_month::date as start_month, end_month::date as end_month)
                   from ev_df""")
    con.close()
    log.info("paper indicator: %d vehicle-component series, %d elevated months, %d events",
             ind[KEY].drop_duplicates().shape[0], int(ind.is_elevated.sum()), len(events))
    return len(events)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_indicator()
