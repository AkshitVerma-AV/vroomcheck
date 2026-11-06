"""Affected model years for each complaint cluster, with the binomial test from ESV26-289.

The paper names an event's affected model years by comparing each year's share
of the event's complaints with a uniform baseline. Here the event is a cluster:
for every model year in the cluster's span we test whether it holds significantly
more of the cluster's complaints than an even split would give (one-sided
binomial test, Bonferroni-corrected across the span). Like the paper's, the
result is conservative and often names a subset of the years a recall covers.
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


def run_model_years() -> int:
    con = connect()
    counts = con.execute("""
        select m.cluster_id, c.model_year, count(*) as n
        from ml.cluster_members m join marts.fct_complaints c using (odino)
        group by 1, 2""").df()
    out = []
    for cid, g in counts.groupby("cluster_id"):
        r = elevated_years(g.set_index("model_year").n)
        r.insert(0, "cluster_id", cid)
        out.append(r)
    res = pd.concat(out, ignore_index=True)
    con.register("my_df", res)
    con.execute("create or replace table ml.cluster_model_years as select * from my_df")
    con.close()
    named = res[res.is_elevated].groupby("cluster_id").size()
    log.info("model years: %d clusters, %d name at least one elevated year (mean %.1f of %.1f years in span)",
             res.cluster_id.nunique(), len(named), named.mean(), res.groupby("cluster_id").size().mean())
    return len(named)


def evaluate_model_years(con) -> pd.DataFrame:
    """How well each method names the years the matching recall actually covers.

    cluster_span: every year from the cluster's first to last model year (before)
    binomial:     the paper's elevated years, or the span if none is significant (after)
    """
    best = con.execute("""
        select r.cluster_id, c.min_model_year, c.max_model_year,
               list(distinct v.model_year) as actual
        from ml.cluster_recall_match r
        join ml.clusters c using (cluster_id)
        join marts.bridge_recall_vehicle v
          on v.campno = r.campno and v.make = c.make and v.model = c.model
        where r.rank = 1 and r.is_match and v.model_year >= ?
        group by 1, 2, 3""", [config.MIN_MODEL_YEAR]).df()
    named = con.execute("""select cluster_id, list(model_year) as years
                           from ml.cluster_model_years where is_elevated group by 1""").df()
    named = dict(zip(named.cluster_id, named.years))
    rows = []
    for r in best.itertuples():
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
