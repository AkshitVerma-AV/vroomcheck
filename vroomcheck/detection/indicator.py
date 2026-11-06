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

run_indicator() computes everything inside Snowflake in SQL. Snowflake has no
normal or t distribution functions, so the SQL uses the Abramowitz & Stegun
26.2.17 approximation for Phi (error < 7.5e-8) and the exact closed form of the
t distribution with 5 degrees of freedom (the 6-month window). score_indicator()
and merge_events() are the same method in pandas: the unit tests and the
Snowflake parity check run against them.
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


def _phi(x: str) -> str:
    """Standard normal CDF in SQL (Abramowitz & Stegun 26.2.17)."""
    k = f"(1 / (1 + 0.2316419 * abs({x})))"
    poly = (f"{k} * (0.319381530 + {k} * (-0.356563782 + {k} * (1.781477937"
            f" + {k} * (-1.821255978 + {k} * 1.330274429))))")
    upper = f"(1 - exp(-({x}) * ({x}) / 2) / sqrt(2 * pi()) * {poly})"
    return f"iff({x} >= 0, {upper}, 1 - {upper})"


def _t5_sf(t: str) -> str:
    """P(T > t) for Student's t with 5 degrees of freedom, exact closed form, in SQL."""
    th = f"atan(({t}) / sqrt(5))"
    return f"(0.5 - ({th} + sin({th}) * (cos({th}) + 2::float / 3 * power(cos({th}), 3))) / pi())"


def indicator_sql() -> str:
    w, tw = config.PAPER_COUNT_MONTHS, config.PAPER_TEST_MONTHS
    if tw != 6:
        raise ValueError("the SQL t-test uses the closed form for 5 degrees of freedom (a 6-month window)")
    window = f"partition by make, model, component order by month rows between {tw - 1} preceding and current row"
    return f"""
        create or replace transient table ml.vehicle_component_indicator as
        with bounds as (
            select dateadd(month, -1, date_trunc('month', max(received_date))) as end_month
            from marts.fct_complaints
        ),
        monthly as (
            select make, model, primary_component as component, received_month as month, count(*) as n
            from marts.fct_complaints
            where not is_remedy_complaint and received_month <= (select end_month from bounds)
            group by all
        ),
        months as (
            select month from (
                select dateadd(month, row_number() over (order by seq4()) - 1,
                               '{config.MIN_RECEIVED_YEAR}-01-01'::date) as month
                from table(generator(rowcount => 1200)))
            where month <= (select end_month from bounds)
        ),
        grid as (                   -- every vehicle-component on every month, zero-filled
            select k.make, k.model, k.component, m.month, coalesce(x.n, 0) as n
            from (select distinct make, model, component from monthly) k
            cross join months m
            left join monthly x
              on x.make = k.make and x.model = k.model and x.component = k.component and x.month = m.month
        ),
        counts as (
            select *,
                   sum(n) over (partition by make, model, component order by month
                                rows between {w - 1} preceding and current row) as n_12m,
                   row_number() over (partition by make, model, component order by month) - 1 as month_index
            from grid
        ),
        totals as (
            select *,
                   sum(n_12m) over (partition by make, model, month) as n_v,
                   sum(n_12m) over (partition by component, month)   as n_c,
                   sum(n_12m) over (partition by month)              as total
            from counts
        ),
        shares as (                 -- float division: Snowflake divides integers as fixed-point decimals
            select *,
                   iff(n_v > 0, n_12m::float / n_v, 0)                              as share,
                   iff(total - n_v > 0, (n_c - n_12m)::float / (total - n_v), 0)    as baseline
            from totals
        ),
        effect as (
            select *, 2 * asin(sqrt(share)) - 2 * asin(sqrt(baseline)) as cohens_h
            from shares
        ),
        indicator as (
            select *, iff(n_12m > 0, ln(greatest(n_12m, 1)::float) * cohens_h, 0) as d_indicator
            from effect
        ),
        scored as (
            select *, {_phi('d_indicator')} as score from indicator
        ),
        test as (
            select *,
                   sum(score) over ({window}) / {tw}::float as window_mean,
                   greatest((sum(score * score) over ({window})
                             - {tw} * power(sum(score) over ({window}) / {tw}::float, 2)) / {tw - 1}::float, 0)
                       as window_var
            from scored
        ),
        pvalues as (
            select *,
                   iff(window_var > 0,
                       {_t5_sf(f'(window_mean - {config.PAPER_THRESHOLD}) / sqrt(window_var / {tw})')},
                       iff(window_mean > {config.PAPER_THRESHOLD}, 0, 1)) as p_value
            from test
        )
        select make, model, component, month, n, n_12m, share, baseline, cohens_h, d_indicator,
               2 * score - 1 as p_indicator, window_mean, p_value,
               month_index >= {w + tw - 2}
                 and 1 - p_value >= {config.PAPER_CONFIDENCE}
                 and n_12m > {config.PAPER_MIN_SUPPORT} as is_elevated
        from pvalues
        where n_12m > 0
    """


def events_sql() -> str:
    gap = config.PAPER_MERGE_GAP_MONTHS
    return f"""
        create or replace transient table ml.indicator_events as
        with flagged as (
            select make, model, component, month, p_indicator,
                   lag(month) over (partition by make, model, component order by month) as prev_month
            from ml.vehicle_component_indicator where is_elevated
        ),
        numbered as (
            select *, sum(iff(prev_month is null or datediff(month, prev_month, month) > {gap}, 1, 0))
                          over (partition by make, model, component order by month
                                rows between unbounded preceding and current row) as event_no
            from flagged
        ),
        events as (
            select make, model, component, event_no, min(month) as start_month, max(month) as end_month,
                   count(*) as flagged_months, max(p_indicator) as peak_p_indicator
            from numbered group by all
        )
        -- complaints from one year before the event starts through its end, as the paper counts them
        select e.make, e.model, e.component, e.start_month, e.end_month, e.flagged_months,
               e.peak_p_indicator, sum(i.n) as event_complaints
        from events e
        join ml.vehicle_component_indicator i
          on i.make = e.make and i.model = e.model and i.component = e.component
         and i.month > dateadd(month, -{config.PAPER_COUNT_MONTHS}, e.start_month) and i.month <= e.end_month
        group by e.make, e.model, e.component, e.event_no, e.start_month, e.end_month,
                 e.flagged_months, e.peak_p_indicator
    """


def run_indicator() -> int:
    """Score the paper's indicator and merge its events, inside Snowflake."""
    con = connect()
    con.execute(indicator_sql())
    con.execute(events_sql())
    series, elevated, events = con.execute("""
        select (select count(*) from (select distinct make, model, component from ml.vehicle_component_indicator)),
               (select count_if(is_elevated) from ml.vehicle_component_indicator),
               (select count(*) from ml.indicator_events)""").fetchone()
    con.close()
    log.info("paper indicator: %d vehicle-component series, %d elevated months, %d events",
             series, elevated, events)
    return events


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_indicator()
