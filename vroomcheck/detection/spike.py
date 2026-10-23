"""Flag clusters whose complaint volume is rising abnormally fast.

The test is causal (month t only uses data up to month t), which is what
lets us back-test against historical recall dates.

For each cluster and month t:
    recent    = complaints in months t-2..t
    baseline  = mean monthly complaints in months t-14..t-3
    growth    = how fast the rest of that vehicle line is growing (fleet effect:
                a newly launched model gets more complaints of every kind)
    expected  = 3 * max(baseline, floor) * growth
    spike  <=> recent >= MIN_RECENT, recent/expected >= MIN_RATIO and
               Poisson P(X >= recent | expected) < P_MAX
"""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd
from scipy.stats import poisson

from vroomcheck import config
from vroomcheck.db import connect

log = logging.getLogger(__name__)

# tuned on the back-test (see README: 6/2.5/1e-3 flagged 29%, 4/2.0/1e-2 flagged ~51%)
MIN_RECENT = 4
MIN_RATIO = 2.0
P_MAX = 1e-2
BASE_FLOOR = 0.25
ACTIVE_WINDOW_MONTHS = 6


def _monthly(con) -> tuple[pd.DataFrame, pd.DataFrame, pd.Timestamp]:
    end = con.execute("""select date_trunc('month', max(received_date)) - interval 1 month
                         from marts.fct_complaints""").fetchone()[0]
    cl = con.execute("""
        select m.cluster_id, c.received_month as month, count(*) n
        from ml.cluster_members m join marts.fct_complaints c using (odino)
        group by 1, 2""").df()
    line = con.execute("""
        select make, model, received_month as month, count(*) n
        from marts.fct_complaints where not is_remedy_complaint group by 1, 2, 3""").df()
    return cl, line, pd.Timestamp(end)


def score_cluster(counts: pd.Series, line_counts: pd.Series) -> pd.DataFrame:
    """counts / line_counts: monthly series on the same complete month index."""
    other = (line_counts - counts).clip(lower=0)
    recent = counts.rolling(3, min_periods=1).sum()
    base = counts.shift(3).rolling(12, min_periods=1).mean().fillna(0)
    o_recent = other.rolling(3, min_periods=1).sum() / 3
    o_base = other.shift(3).rolling(12, min_periods=1).mean().fillna(0)
    growth = ((o_recent + 1) / (o_base + 1)).clip(0.5, 4.0)
    expected = 3 * np.maximum(base, BASE_FLOOR) * growth
    p = poisson.sf(recent - 1, expected)
    ratio = recent / expected
    # no verdict until a full baseline exists (3-month window + 12-month baseline)
    warm = np.arange(len(counts)) >= 14
    spike = (recent >= MIN_RECENT) & (ratio >= MIN_RATIO) & (p < P_MAX) & warm
    return pd.DataFrame({"n": counts, "recent_3m": recent, "expected_3m": expected,
                         "ratio": ratio, "p_value": p, "is_spike": spike})


def run_spike_detection() -> int:
    con = connect()
    cl, line, end = _monthly(con)
    meta = con.execute("select cluster_id, make, model from ml.clusters").df().set_index("cluster_id")
    months = pd.date_range(f"{config.MIN_RECEIVED_YEAR}-01-01", end, freq="MS")
    line_idx = line.set_index(["make", "model", "month"]).n
    out = []
    for cid, g in cl.groupby("cluster_id"):
        make, model = meta.loc[cid, ["make", "model"]]
        counts = g.set_index(pd.to_datetime(g.month)).n.reindex(months, fill_value=0).astype(float)
        lc = line_idx.loc[(make, model)]
        lc.index = pd.to_datetime(lc.index)
        lc = lc.reindex(months, fill_value=0).astype(float)
        s = score_cluster(counts, lc)
        s["cluster_id"] = cid
        out.append(s.rename_axis("month").reset_index())
    monthly = pd.concat(out, ignore_index=True)
    con.register("mon", monthly)
    con.execute("create or replace table ml.cluster_monthly as select * from mon")
    con.close()
    n_spiking = monthly.loc[monthly.is_spike, "cluster_id"].nunique()
    log.info("scored %d clusters over %d months; %d have spiked at least once",
             monthly.cluster_id.nunique(), len(months), n_spiking)
    return n_spiking


CITED_MIN_SHARE = 0.2   # cluster is linked to a recall its own complaints cite this often
NON_DEFECT_RE = r"attached document|document complaint|police report|motor company|owns|owned|filed"


def match_recalls() -> int:
    """Link each cluster to recalls on the same vehicle line.

    Two signals:
      * explicit  - a share of the cluster's complaints cite the campaign number
      * semantic  - centroid / defect-summary cosine similarity, re-ranked with
                    component agreement and timing. Weights were chosen on the
                    clusters whose complaints cite a campaign (top-1 accuracy
                    27% -> 47%; the right recall is in the top 5 for ~80%).
    """
    con = connect()
    con.execute(f"""
        create or replace table ml.cluster_recall_match as
        with cand as (
            select distinct c.cluster_id, b.campno
            from ml.clusters c
            join marts.bridge_recall_vehicle b
              on b.make = c.make and b.model = c.model
             and b.model_year between c.min_model_year and c.max_model_year
        ), cited as (
            select m.cluster_id, f.referenced_campno as campno,
                   count(*)::double / any_value(c.size) as cited_share
            from ml.cluster_members m
            join marts.fct_complaints f using (odino)
            join ml.clusters c using (cluster_id)
            where f.referenced_campno is not null
            group by 1, 2
        ), scored as (
            select cand.cluster_id, cand.campno, r.recall_date, r.component_tops,
                   array_cosine_similarity(c.centroid, e.embedding) as similarity,
                   array_cosine_similarity(c.centroid, e.embedding)
                     + 0.1 * contains(coalesce(r.component_tops, ''), coalesce(c.top_component, '#'))::int
                     + 0.1 * (r.recall_date between c.first_received - interval 6 month and c.last_received)::int
                       as match_score,
                   coalesce(ci.cited_share, 0) as cited_share
            from cand
            join ml.clusters c using (cluster_id)
            join ml.recall_embeddings e using (campno)
            join marts.fct_recalls r using (campno)
            left join cited ci using (cluster_id, campno)
        )
        select *,
               case when cited_share >= {CITED_MIN_SHARE} then 'cited'
                    when match_score >= {config.RECALL_MATCH_THRESHOLD} then 'semantic' end as match_method,
               cited_share >= {CITED_MIN_SHARE} or match_score >= {config.RECALL_MATCH_THRESHOLD} as is_match,
               row_number() over (partition by cluster_id
                                  order by (cited_share >= {CITED_MIN_SHARE}) desc, match_score desc, campno) as rank
        from scored
        qualify rank <= 5
    """)
    n = con.execute("select count(distinct cluster_id) from ml.cluster_recall_match "
                    "where rank = 1 and is_match").fetchone()[0]
    con.close()
    log.info("%d clusters linked to a recall (cited or semantic score >= %.2f)", n, config.RECALL_MATCH_THRESHOLD)
    return n


def build_alerts() -> int:
    """Current state of every cluster: EMERGING (spiking, no recall), RECALLED or WATCH."""
    con = connect()
    con.execute(f"""
        create or replace table ml.alerts as
        with end_month as (select max(month) as m from ml.cluster_monthly),
        spikes as (
            select cluster_id,
                   min(month) filter (where is_spike) as first_spike_month,
                   max(month) filter (where is_spike) as last_spike_month,
                   max(ratio) filter (where month >= (select m from end_month) - interval {ACTIVE_WINDOW_MONTHS} month)
                       as recent_max_ratio,
                   sum(n) filter (where month > (select m from end_month) - interval 3 month) as last_3m
            from ml.cluster_monthly group by 1
        ),
        best as (
            select * from ml.cluster_recall_match
            where rank = 1 and is_match
        )
        select c.cluster_id, c.make, c.model, c.min_model_year, c.max_model_year, c.size,
               c.top_component, c.top_system, c.keywords, c.n_crash, c.n_fire, c.n_injured, c.n_deaths,
               c.harm_rate, s.first_spike_month, s.last_spike_month, s.recent_max_ratio, s.last_3m,
               b.campno as matched_campno, b.recall_date as matched_recall_date,
               b.match_score as recall_match_score, b.match_method,
               s.last_spike_month >= (select m from end_month) - interval {ACTIVE_WINDOW_MONTHS} month as is_active,
               case
                   -- clusters about paperwork rather than a failure (attachments, police reports, letters)
                   when regexp_matches(c.keywords, '{NON_DEFECT_RE}') then 'NON-DEFECT'
                   when b.campno is not null then 'RECALLED'
                   when s.last_spike_month >= (select m from end_month) - interval {ACTIVE_WINDOW_MONTHS} month
                       then 'EMERGING'
                   else 'WATCH'
               end as status,
               round(ln(1 + coalesce(s.last_3m, 0)) * least(coalesce(s.recent_max_ratio, 1), 10)
                     * c.harm_rate * coalesce(o.severity_weight, 0.5), 3) as priority
        from ml.clusters c
        left join spikes s using (cluster_id)
        left join best b using (cluster_id)
        left join marts.dim_component o on o.component_top = c.top_component
    """)
    n = con.execute("select count(*) from ml.alerts where status = 'EMERGING'").fetchone()[0]
    con.close()
    log.info("%d EMERGING clusters (spiking with no matching recall)", n)
    return n


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_spike_detection()
    match_recalls()
    build_alerts()
