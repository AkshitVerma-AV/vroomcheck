"""Daily Vroomcheck pipeline.

NHTSA republishes its flat files every day, so the DAG runs daily: download
only what changed, reload raw, gate on data quality, rebuild the dbt models,
score the fleet-normalised indicator from ESV26-289, embed new complaints,
recluster, find each cluster's affected model years, rescore spikes, back-test
and refresh the briefs for the top emerging defects.

The project is mounted at /opt/vroomcheck (see airflow/docker-compose.yml).
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta

from airflow import DAG
from airflow.operators.bash import BashOperator
from airflow.operators.python import PythonOperator

PROJECT = "/opt/vroomcheck"
sys.path.insert(0, PROJECT)

DBT = f"dbt --no-use-colors {{cmd}} --project-dir {PROJECT}/dbt/vroomcheck --profiles-dir {PROJECT}/dbt/vroomcheck"


def _call(module: str, fn: str, *args):
    def run():
        import importlib

        return getattr(importlib.import_module(module), fn)(*args)
    return run


default_args = {"owner": "vroomcheck", "retries": 2, "retry_delay": timedelta(minutes=10)}

with DAG(
    dag_id="vroomcheck_daily",
    description="NHTSA complaints -> warehouse -> defect clusters -> cited early warnings",
    start_date=datetime(2026, 10, 1),
    schedule="0 6 * * *",          # NHTSA files refresh overnight
    catchup=False,
    max_active_runs=1,
    default_args=default_args,
    tags=["vroomcheck", "nhtsa"],
) as dag:
    download = PythonOperator(task_id="download_nhtsa", python_callable=_call(
        "vroomcheck.ingestion.download", "download_all"))
    load = PythonOperator(task_id="load_raw", python_callable=_call(
        "vroomcheck.ingestion.load_raw", "load_all"))
    dq = PythonOperator(task_id="quality_gate", python_callable=_call(
        "vroomcheck.ingestion.quality_checks", "run_checks"))
    dbt_seed = BashOperator(task_id="dbt_seed", bash_command=DBT.format(cmd="seed"))
    dbt_run = BashOperator(task_id="dbt_run", bash_command=DBT.format(cmd="run"))
    dbt_test = BashOperator(task_id="dbt_test", bash_command=DBT.format(cmd="test"))
    indicator = PythonOperator(task_id="paper_indicator", python_callable=_call(
        "vroomcheck.detection.indicator", "run_indicator"))
    embed_recalls = PythonOperator(task_id="embed_recalls", python_callable=_call(
        "vroomcheck.detection.embed", "embed_recalls"))
    embed_complaints = PythonOperator(task_id="embed_complaints", python_callable=_call(
        "vroomcheck.detection.embed", "embed_complaints"))
    cluster = PythonOperator(task_id="cluster", python_callable=_call(
        "vroomcheck.detection.cluster", "run_clustering"))
    model_years = PythonOperator(task_id="model_years", python_callable=_call(
        "vroomcheck.detection.model_years", "run_model_years"))
    spikes = PythonOperator(task_id="spike_detection", python_callable=_call(
        "vroomcheck.detection.spike", "run_spike_detection"))
    match = PythonOperator(task_id="match_recalls", python_callable=_call(
        "vroomcheck.detection.spike", "match_recalls"))
    alerts = PythonOperator(task_id="build_alerts", python_callable=_call(
        "vroomcheck.detection.spike", "build_alerts"))
    backtest = PythonOperator(task_id="backtest", python_callable=_call(
        "vroomcheck.detection.backtest", "run_backtest"))
    briefs = PythonOperator(task_id="agent_briefs", python_callable=_call(
        "vroomcheck.agents.graph", "generate_top_briefs", 10))

    download >> load >> dq >> dbt_seed >> dbt_run >> dbt_test
    # DuckDB allows one writer at a time, so ML steps run sequentially
    # (on Snowflake the two embedding tasks can run in parallel).
    dbt_test >> indicator >> embed_recalls >> embed_complaints >> cluster >> model_years
    model_years >> spikes >> match >> alerts >> backtest >> briefs
