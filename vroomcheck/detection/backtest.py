"""Competency question 04: how early would we have flagged a past recall?

For each in-scope historical recall we take the complaint cluster that best
matches it (same vehicle line, highest similarity) and check whether the
causal spike test fired *before* the recall's Part 573 report date.

Success targets from the pitch:
    >= 50 % of past recalls flagged before the recall date
    >= 3 months median early-warning lead time

Known limitation (stated in the demo): clusters are built on the full history,
so cluster membership itself uses some post-recall information. The spike
test that produces the alert date is strictly causal.
"""
from __future__ import annotations

import logging

from vroomcheck import config
from vroomcheck.db import connect

log = logging.getLogger(__name__)

BACKTEST_FROM = "2017-01-01"
MIN_PRE_RECALL_COMPLAINTS = 5
# an alert only counts as early warning if it fired within this window before the recall
LOOKBACK_MONTHS = 24


def run_backtest() -> dict:
    con = connect()
    con.execute(f"""
        create or replace table ml.backtest_results as
        with recall_scope as (
            select r.campno, r.recall_date, r.component_tops, r.defect_summary,
                   r.investigation_id, r.investigation_opened, r.influenced_by
            from marts.fct_recalls r
            where r.recall_date >= date '{BACKTEST_FROM}'
              and r.recall_date <= (select max(month) from ml.cluster_monthly)
              and r.min_model_year >= {config.MIN_MODEL_YEAR}
        ),
        best_cluster as (
            select m.campno, m.cluster_id, m.match_score, m.similarity
            from ml.cluster_recall_match m
            where m.is_match
            qualify row_number() over (partition by m.campno
                                       order by m.match_method = 'cited' desc, m.match_score desc, m.cluster_id) = 1
        ),
        pre as (
            select b.campno, b.cluster_id,
                   sum(cm.n) filter (where cm.month < date_trunc('month', r.recall_date)) as pre_recall_complaints,
                   min(cm.month) filter (where cm.is_spike and cm.month < date_trunc('month', r.recall_date)
                                        and cm.month >= date_trunc('month', r.recall_date) - interval {LOOKBACK_MONTHS} month)
                       as first_alert_month
            from best_cluster b
            join recall_scope r using (campno)
            join ml.cluster_monthly cm using (cluster_id)
            group by 1, 2
        )
        select (select any_value(detector) from ml.cluster_monthly) as detector,
               r.campno, r.recall_date, r.component_tops, r.influenced_by,
               r.investigation_id, r.investigation_opened,
               b.cluster_id, b.match_score, p.pre_recall_complaints, p.first_alert_month,
               b.cluster_id is not null
                 and coalesce(p.pre_recall_complaints, 0) >= {MIN_PRE_RECALL_COMPLAINTS} as detectable,
               p.first_alert_month is not null as flagged_before_recall,
               date_diff('day', p.first_alert_month + interval 1 month, r.recall_date) / 30.44 as lead_months,
               case when r.investigation_opened is not null and p.first_alert_month is not null
                    then date_diff('day', p.first_alert_month + interval 1 month, r.investigation_opened) / 30.44
               end as lead_vs_investigation_months
        from recall_scope r
        left join best_cluster b using (campno)
        left join pre p using (campno, cluster_id)
    """)
    s = con.execute("""
        select any_value(detector)                                   as detector,
               count(*)                                              as recalls_in_scope,
               count(*) filter (where cluster_id is not null)       as recalls_with_cluster,
               count(*) filter (where detectable)                   as recalls_detectable,
               count(*) filter (where detectable and flagged_before_recall) as flagged_early,
               median(lead_months) filter (where detectable and flagged_before_recall) as median_lead_months,
               median(lead_vs_investigation_months) filter (where detectable and flagged_before_recall)
                                                                    as median_lead_vs_investigation
        from ml.backtest_results
    """).df().iloc[0].to_dict()
    s["pct_flagged_of_detectable"] = 100 * s["flagged_early"] / max(s["recalls_detectable"], 1)
    con.close()
    log.info("backtest: %s", s)
    return s


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(run_backtest())
