"""Step 3 of ingestion: data-quality gate on the raw layer.

Results are appended to raw._dq_results so the dashboard can show data health
over time. A failing `critical` check stops the pipeline before dbt runs.

The same 19 checks run on either warehouse; only the SQL dialect differs:

    python -m vroomcheck.ingestion.quality_checks                     # DuckDB (default)
    python -m vroomcheck.ingestion.quality_checks --target snowflake  # Snowflake RAW
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass
from typing import Callable

from vroomcheck import config
from vroomcheck.ingestion.sources import DATASETS

log = logging.getLogger(__name__)

# Unicode space separators DuckDB's trim() strips; Snowflake's trim() only strips
# what it is given (same list as the snowflake__ws_trim dbt macro).
_ZS = [32, 160, 5760, 8192, 8193, 8194, 8195, 8196, 8197, 8198, 8199, 8200, 8201, 8202, 8239, 8287, 12288]


@dataclass(frozen=True)
class Dialect:
    trim: Callable[[str], str]
    to_date: Callable[[str], str]           # YYYYMMDD text -> date, null if unparseable
    tomorrow: str
    starts_like_campno: Callable[[str], str]
    reject_key: Callable[[str], str]        # how the loader names a dataset in _load_rejects
    now: str
    placeholder: str


DIALECTS = {
    "duckdb": Dialect(
        trim=lambda e: f"trim({e})",
        to_date=lambda c: f"try_strptime({c}, '%Y%m%d')",
        tomorrow="current_date + interval 1 day",
        starts_like_campno=lambda c: f"regexp_matches({c}, '^[0-9]{{2}}[VEICT][0-9]{{3}}')",
        reject_key=lambda name: name,
        now="current_timestamp::timestamp",
        placeholder="?",
    ),
    "snowflake": Dialect(
        trim=lambda e: f"trim({e}, {' || '.join(f'chr({c})' for c in _ZS)})",
        to_date=lambda c: f"try_to_date({c}, 'YYYYMMDD')",
        tomorrow="dateadd(day, 1, current_date())",
        starts_like_campno=lambda c: f"regexp_instr({c}, '^[0-9]{{2}}[VEICT][0-9]{{3}}') = 1",
        reject_key=lambda name: name.upper(),
        now="current_timestamp()::timestamp_ntz",
        placeholder="%s",
    ),
}


@dataclass
class Check:
    dataset: str
    name: str
    sql: str            # returns a single number
    min_ok: float | None = None
    max_ok: float | None = None
    critical: bool = True


def _checks(d: Dialect) -> list[Check]:
    checks: list[Check] = []
    for key, ds in DATASETS.items():
        t = f"raw.{ds.name}"
        pk = ds.primary_key[0]
        checks.append(Check(key, "row_count", f"select count(*) from {t}", min_ok=1))
        checks.append(Check(key, f"{pk}_null_pct",
                            f"select 100.0 * count(case when {pk} is null or {d.trim(pk)} = '' then 1 end) "
                            f"/ nullif(count(*),0) from {t}", max_ok=0.5))
        for dc in ds.date_columns[:1]:
            checks.append(Check(key, f"{dc}_unparseable_pct",
                                f"select 100.0 * count(case when {d.to_date(dc)} is null then 1 end) "
                                f"/ nullif(count(*),0) from {t}", max_ok=5))
        checks.append(Check(key, "reject_pct",
                            f"select 100.0 * (select count(*) from raw._load_rejects where dataset = '{d.reject_key(ds.name)}') "
                            f"/ nullif((select count(*) from {t}),0)", max_ok=1, critical=False))
    checks += [
        Check("complaints", "narrative_missing_pct",
              f"select 100.0 * count(case when cdescr is null or length({d.trim('cdescr')}) < 20 then 1 end) "
              "/ count(*) from raw.raw_complaints", max_ok=5, critical=False),
        Check("complaints", "future_received_dates",
              f"select count(*) from raw.raw_complaints where {d.to_date('ldate')} > {d.tomorrow}", max_ok=0),
        Check("recalls", "campno_format_bad_pct",
              f"select 100.0 * count(case when not {d.starts_like_campno('campno')} then 1 end) "
              "/ count(*) from raw.raw_recalls", max_ok=1, critical=False),
    ]
    return checks


def _connect(target: str):
    if target == "snowflake":
        from vroomcheck.ingestion.load_raw_snowflake import connect
    else:
        from vroomcheck.db import connect_duckdb as connect
    return connect()


def run_checks(raise_on_fail: bool = True, target: str = "duckdb") -> list[dict]:
    d = DIALECTS[target]
    con = _connect(target)
    try:
        cur = con.cursor()
        cur.execute("""create table if not exists raw._dq_results
                       (run_at timestamp, dataset varchar, check_name varchar, value double,
                        min_ok double, max_ok double, passed boolean, critical boolean)""")
        run_at = cur.execute(f"select {d.now}").fetchone()[0]  # one timestamp per gate run
        results = []
        for c in _checks(d):
            val = cur.execute(c.sql).fetchone()[0]
            val = float(val) if val is not None else 0.0
            ok = (c.min_ok is None or val >= c.min_ok) and (c.max_ok is None or val <= c.max_ok)
            results.append(dict(dataset=c.dataset, check_name=c.name, value=val, min_ok=c.min_ok,
                                max_ok=c.max_ok, passed=ok, critical=c.critical))
            log.info("%-15s %-28s %12.2f %s", c.dataset, c.name, val, "PASS" if ok else "FAIL")
        p = d.placeholder
        cur.executemany(f"insert into raw._dq_results values ({', '.join([p] * 8)})",
                        [[run_at, r["dataset"], r["check_name"], r["value"], r["min_ok"],
                          r["max_ok"], r["passed"], r["critical"]] for r in results])
    finally:
        con.close()
    failed = [r for r in results if r["critical"] and not r["passed"]]
    if failed and raise_on_fail:
        raise RuntimeError(f"critical data-quality checks failed on {target}: {failed}")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=list(DIALECTS), default=config.BACKEND)
    run_checks(target=ap.parse_args().target)
