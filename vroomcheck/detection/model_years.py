"""Affected model years for each complaint cluster, with the binomial test from ESV26-289.

The paper names an event's affected model years by comparing each year's share
of the event's complaints with a uniform baseline. Here the event is a cluster:
for every model year in the cluster's span we test whether it holds significantly
more of the cluster's complaints than an even split would give (one-sided
binomial test, Bonferroni-corrected across the span). Like the paper's, the
result is conservative and often names a subset of the years a recall covers.

run_model_years() runs the test inside Snowflake. The tail probability is summed
exactly, sum over j >= k of C(n, j) p^j (1 - p)^(n - j), with C(n, j) from a
log-factorial table, so it matches scipy's binom.sf. elevated_years() is the
same test in Python, used by the unit tests and the parity check.
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import binom

from vroomcheck import config
from vroomcheck.db import connect

log = logging.getLogger(__name__)


def elevated_years(year_counts: pd.Series, alpha: float = config.PAPER_MODEL_YEAR_ALPHA) -> pd.DataFrame:
    """year_counts: complaints per model year for one cluster (index = model year)."""
    span = np.arange(int(year_counts.index.min()), int(year_counts.index.max()) + 1)
    k = year_counts.reindex(span, fill_value=0).to_numpy()
    n, m = int(k.sum()), len(span)
    if m == 1:
        p = np.zeros(1)
        elevated = np.ones(1, dtype=bool)
    else:
        p = binom.sf(k - 1, n, 1 / m)          # P(X >= k) under an even split
        elevated = p < alpha / m
    return pd.DataFrame({"model_year": span, "n": k, "expected": n / m,
                         "p_value": p, "is_elevated": elevated})


def model_years_sql(max_n: int) -> str:
    return f"""
        create or replace transient table ml.cluster_model_years as
        with counts as (
            select m.cluster_id, c.model_year, count(*) as n
            from ml.cluster_members m join marts.fct_complaints c using (odino)
            group by 1, 2
        ),
        span as (
            select cluster_id, min(model_year) as y0, max(model_year) as y1, sum(n) as total
            from counts group by 1
        ),
        numbers as (
            select row_number() over (order by seq4()) - 1 as i from table(generator(rowcount => {max_n + 1}))
        ),
        years as (                  -- every model year in each cluster's span, zero-filled
            select s.cluster_id, s.y0 + g.i as model_year, s.y1 - s.y0 + 1 as m, s.total
            from span s join numbers g on g.i <= s.y1 - s.y0
        ),
        k as (
            select y.*, coalesce(c.n, 0) as n
            from years y left join counts c using (cluster_id, model_year)
        ),
        lnfact as (                 -- ln(i!)
            select i, sum(ln(greatest(i, 1)::float)) over (order by i rows between unbounded preceding
                                                                     and current row) as v
            from numbers
        ),
        tail as (                   -- P(X >= k) for X ~ Binomial(total, 1/m)
            select k.cluster_id, k.model_year,
                   sum(exp(fn.v - fj.v - fnj.v + j.i * ln(1::float / k.m)
                           + (k.total - j.i) * ln(1 - 1::float / k.m))) as p
            from k
            join numbers j on j.i between k.n and k.total
            join lnfact fn on fn.i = k.total
            join lnfact fj on fj.i = j.i
            join lnfact fnj on fnj.i = k.total - j.i
            where k.m > 1
            group by 1, 2
        )
        select k.cluster_id, k.model_year, k.n, k.total::float / k.m as expected,
               iff(k.m = 1, 0, least(t.p, 1)) as p_value,
               iff(k.m = 1, true, t.p < {config.PAPER_MODEL_YEAR_ALPHA} / k.m) as is_elevated
        from k left join tail t using (cluster_id, model_year)
    """


def run_model_years() -> int:
    """The paper's binomial test for every cluster, inside Snowflake."""
    con = connect()
    max_n = con.execute("select max(size) from ml.clusters").fetchone()[0]
    con.execute(model_years_sql(int(max_n)))
    clusters, named, years_named, span = con.execute("""
        with c as (select cluster_id, count_if(is_elevated) as e, count(*) as s
                   from ml.cluster_model_years group by 1)
        select count(*), count_if(e > 0), avg(iff(e > 0, e, null)), avg(s) from c""").fetchone()
    con.close()
    log.info("model years: %d clusters, %d name at least one elevated year (mean %.1f of %.1f years in span)",
             clusters, named, years_named, span)
    return named


def evaluate_model_years(con) -> pd.DataFrame:
    """How well each method names the years the matching recall actually covers.

    cluster_span: every year from the cluster's first to last model year (before)
    binomial:     the paper's elevated years, or the span if none is significant (after)
    """
    actual = con.execute("""
        select distinct r.cluster_id, c.min_model_year, c.max_model_year, v.model_year
        from ml.cluster_recall_match r
        join ml.clusters c using (cluster_id)
        join marts.bridge_recall_vehicle v
          on v.campno = r.campno and v.make = c.make and v.model = c.model
        where r.rank = 1 and r.is_match and v.model_year >= ?""", [config.MIN_MODEL_YEAR]).df()
    named = con.execute("select cluster_id, model_year from ml.cluster_model_years where is_elevated").df()
    named = named.groupby("cluster_id").model_year.apply(set).to_dict()
    best = actual.groupby(["cluster_id", "min_model_year", "max_model_year"]).model_year.apply(set).reset_index()
    rows = []
    for r in best.rename(columns={"model_year": "actual"}).itertuples():
        actual = set(r.actual)
        span = set(range(int(r.min_model_year), int(r.max_model_year) + 1))
        for method, pred in (("cluster_span", span), ("binomial", set(named.get(r.cluster_id, [])) or span)):
            hit = len(pred & actual)
            rows.append(dict(cluster_id=r.cluster_id, method=method, years_named=len(pred),
                             precision=hit / len(pred), recall=hit / len(actual),
                             jaccard=hit / len(pred | actual)))
    return pd.DataFrame(rows)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_model_years()
