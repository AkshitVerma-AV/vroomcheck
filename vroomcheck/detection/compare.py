"""Before vs after: the back-test under each detector (config.DETECTOR).

Clusters, recall matches and the back-test window stay fixed; only the signal
that raises an alert changes, so every difference comes from the detector.
"poisson" is the original detector (before); "paper" and "combined" add the
ESV26-289 indicator (after). The first run copies the original results to
ml_before so the before numbers stay available after the comparison.

    python -m vroomcheck.detection.compare                 # all detectors
    python -m vroomcheck.detection.compare --rebuild       # also rerun indicator + model years, timed
"""
from __future__ import annotations

import argparse
import logging
import time

import pandas as pd

from vroomcheck import config
from vroomcheck.db import connect, table_exists
from vroomcheck.detection.backtest import LOOKBACK_MONTHS, run_backtest
from vroomcheck.detection.indicator import run_indicator
from vroomcheck.detection.model_years import evaluate_model_years, run_model_years
from vroomcheck.detection.spike import DETECTORS, build_alerts, run_spike_detection

log = logging.getLogger(__name__)


def _r(x) -> float | None:
    return None if x is None or pd.isna(x) else round(float(x), 1)


def _snapshot_before(con) -> None:
    if table_exists(con, "ml_before", "backtest_results"):
        return
    con.execute("create schema if not exists ml_before")
    for t in ("cluster_monthly", "alerts", "backtest_results"):
        con.execute(f"create table ml_before.{t} as select * from ml.{t}")
    log.info("saved the original detector's results to ml_before")


def _alert_metrics(con) -> dict:
    """Alert burden and a precision proxy for the detector currently in ml.cluster_monthly."""
    m = con.execute(f"""
        with end_m as (select max(month) as e from ml.cluster_monthly),
        first as (select cluster_id, min(month) filter (where is_spike) as first_alert
                  from ml.cluster_monthly group by 1),
        rec as (select cluster_id, recall_date from ml.cluster_recall_match where rank = 1 and is_match)
        select
            (select count(*) from ml.alerts where status = 'EMERGING')                       as emerging_alerts,
            (select 1000.0 * count(*) filter (where is_spike) / count(*) from ml.cluster_monthly)
                                                                                              as alert_months_per_1000,
            count(*) filter (where f.first_alert is not null)                                as clusters_ever_alerted,
            -- old enough that a recall within the window could have happened before the data ends
            count(*) filter (where f.first_alert <= e.e - interval {LOOKBACK_MONTHS} month)  as alerts_old_enough,
            count(*) filter (where f.first_alert <= e.e - interval {LOOKBACK_MONTHS} month
                               and r.recall_date > f.first_alert
                               and r.recall_date <= f.first_alert + interval {LOOKBACK_MONTHS} month)
                                                                                              as alerts_then_recalled
        from first f cross join end_m e left join rec r using (cluster_id)""").df().iloc[0].to_dict()
    m["precision_proxy_pct"] = 100 * m["alerts_then_recalled"] / max(m["alerts_old_enough"], 1)
    return m


def run_comparison(detectors=DETECTORS, rebuild: bool = False) -> pd.DataFrame:
    timings = {}
    if rebuild:
        for name, fn in (("indicator_seconds", run_indicator), ("model_years_seconds", run_model_years)):
            t = time.perf_counter()
            fn()
            timings[name] = round(time.perf_counter() - t, 1)
    con = connect()
    _snapshot_before(con)
    con.close()

    rows = []
    for d in detectors:
        t = time.perf_counter()
        run_spike_detection(d)
        build_alerts()
        detect_seconds = time.perf_counter() - t
        s = run_backtest()
        con = connect()
        m = _alert_metrics(con)
        con.close()
        rows.append({"detector": d, "recalls_detectable": s["recalls_detectable"],
                     "flagged_early": s["flagged_early"],
                     "pct_flagged_of_detectable": _r(s["pct_flagged_of_detectable"]),
                     "median_lead_months": _r(s["median_lead_months"]),
                     "median_lead_vs_investigation": _r(s["median_lead_vs_investigation"]),
                     **{k: _r(v) if isinstance(v, float) else v for k, v in m.items()},
                     "detect_seconds": _r(detect_seconds)})

    # leave the warehouse on the configured detector
    run_spike_detection(config.DETECTOR)
    build_alerts()
    run_backtest()

    df = pd.DataFrame(rows)
    con = connect()
    con.register("cmp_df", df)
    con.execute("create or replace table ml.detector_comparison as select * from cmp_df")
    years = evaluate_model_years(con)
    con.close()

    print("\nDetector comparison (same clusters, same recalls, same back-test window)")
    print(df.set_index("detector").T.to_string())
    if not years.empty:
        print(f"\nAffected model years vs the matched recall ({years.cluster_id.nunique()} recalled clusters)")
        print(years.groupby("method")[["years_named", "precision", "recall", "jaccard"]].mean().round(3).to_string())
    if timings:
        print("\nOne-off build times:", timings)
    return df


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--rebuild", action="store_true", help="rerun the indicator and model years first (timed)")
    run_comparison(rebuild=ap.parse_args().rebuild)
