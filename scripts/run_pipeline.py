"""Run the full Vroomcheck pipeline locally, in the same task order as the
Airflow DAG (airflow/dags/vroomcheck_dag.py).

    python scripts/run_pipeline.py                 # everything
    python scripts/run_pipeline.py --from embed    # resume from a step
    python scripts/run_pipeline.py --only dbt
"""
from __future__ import annotations

import argparse
import logging
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vroomcheck import config  # noqa: E402

log = logging.getLogger("pipeline")


def dbt(*args: str) -> None:
    exe = Path(sys.executable).with_name("dbt.exe" if sys.platform == "win32" else "dbt")
    cmd = [str(exe), *args, "--project-dir", str(config.DBT_DIR), "--profiles-dir", str(config.DBT_DIR)]
    log.info("$ %s", " ".join(cmd[:2] + list(args)))
    subprocess.run(cmd, check=True, cwd=config.DBT_DIR)


def step_download():
    from vroomcheck.ingestion.download import download_all
    download_all()


def step_load():
    from vroomcheck.ingestion.load_raw import load_all
    load_all()


def step_dq():
    from vroomcheck.ingestion.quality_checks import run_checks
    run_checks()


def step_dbt():
    dbt("seed")
    dbt("run")
    dbt("test")


def step_indicator():
    from vroomcheck.detection.indicator import run_indicator
    run_indicator()


def step_embed():
    from vroomcheck.detection.embed import embed_complaints, embed_recalls
    embed_recalls()
    embed_complaints()


def step_cluster():
    from vroomcheck.detection.cluster import run_clustering
    run_clustering()


def step_model_years():
    from vroomcheck.detection.model_years import run_model_years
    run_model_years()


def step_detect():
    from vroomcheck.detection.spike import build_alerts, match_recalls, run_spike_detection
    run_spike_detection()
    match_recalls()
    build_alerts()


def step_backtest():
    from vroomcheck.detection.backtest import run_backtest
    run_backtest()


def step_briefs():
    from vroomcheck.agents.graph import generate_top_briefs
    generate_top_briefs(5)


STEPS = [("download", step_download), ("load", step_load), ("dq", step_dq), ("dbt", step_dbt),
         ("indicator", step_indicator), ("embed", step_embed), ("cluster", step_cluster),
         ("model_years", step_model_years), ("detect", step_detect),
         ("backtest", step_backtest), ("briefs", step_briefs)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", choices=[s for s, _ in STEPS])
    ap.add_argument("--only", choices=[s for s, _ in STEPS])
    a = ap.parse_args()
    names = [s for s, _ in STEPS]
    todo = STEPS
    if a.only:
        todo = [s for s in STEPS if s[0] == a.only]
    elif a.start:
        todo = STEPS[names.index(a.start):]
    for name, fn in todo:
        t = time.time()
        log.info("=== %s ===", name)
        fn()
        log.info("=== %s done in %.1fs ===", name, time.time() - t)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    main()
