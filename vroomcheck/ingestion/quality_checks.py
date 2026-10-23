"""Step 3 of ingestion: data-quality gate on the raw layer.

Results are appended to raw._dq_results so the dashboard can show data health
over time. A failing `critical` check stops the pipeline before dbt runs.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from vroomcheck.db import connect
from vroomcheck.ingestion.sources import DATASETS

log = logging.getLogger(__name__)


@dataclass
class Check:
    dataset: str
    name: str
    sql: str            # returns a single number
    min_ok: float | None = None
    max_ok: float | None = None
    critical: bool = True


def _checks() -> list[Check]:
    checks: list[Check] = []
    for key, ds in DATASETS.items():
        t = f"raw.{ds.name}"
        pk = ds.primary_key[0]
        checks.append(Check(key, "row_count", f"select count(*) from {t}", min_ok=1))
        checks.append(Check(key, f"{pk}_null_pct",
                            f"select 100.0 * count(*) filter (where {pk} is null or trim({pk}) = '') "
                            f"/ nullif(count(*),0) from {t}", max_ok=0.5))
        for dc in ds.date_columns[:1]:
            checks.append(Check(key, f"{dc}_unparseable_pct",
                                f"select 100.0 * count(*) filter (where try_strptime({dc}, '%Y%m%d') is null) "
                                f"/ nullif(count(*),0) from {t}", max_ok=5))
        checks.append(Check(key, "reject_pct",
                            f"select 100.0 * (select count(*) from raw._load_rejects where dataset = '{ds.name}') "
                            f"/ nullif((select count(*) from {t}),0)", max_ok=1, critical=False))
    checks += [
        Check("complaints", "narrative_missing_pct",
              "select 100.0 * count(*) filter (where cdescr is null or length(trim(cdescr)) < 20) "
              "/ count(*) from raw.raw_complaints", max_ok=5, critical=False),
        Check("complaints", "future_received_dates",
              "select count(*) from raw.raw_complaints "
              "where try_strptime(ldate,'%Y%m%d') > current_date + interval 1 day", max_ok=0),
        Check("recalls", "campno_format_bad_pct",
              "select 100.0 * count(*) filter (where not regexp_matches(campno, '^[0-9]{2}[VEICT][0-9]{3}')) "
              "/ count(*) from raw.raw_recalls", max_ok=1, critical=False),
    ]
    return checks


def run_checks(raise_on_fail: bool = True) -> list[dict]:
    con = connect()
    con.execute("""create table if not exists raw._dq_results
                   (run_at timestamp, dataset varchar, check_name varchar, value double,
                    min_ok double, max_ok double, passed boolean, critical boolean)""")
    results = []
    run_at = con.execute("select current_timestamp::timestamp").fetchone()[0]  # one timestamp per gate run
    for c in _checks():
        val = con.execute(c.sql).fetchone()[0]
        val = float(val) if val is not None else 0.0
        ok = (c.min_ok is None or val >= c.min_ok) and (c.max_ok is None or val <= c.max_ok)
        results.append(dict(dataset=c.dataset, check_name=c.name, value=val, min_ok=c.min_ok,
                            max_ok=c.max_ok, passed=ok, critical=c.critical))
        con.execute("insert into raw._dq_results values (?,?,?,?,?,?,?,?)",
                    [run_at, c.dataset, c.name, val, c.min_ok, c.max_ok, ok, c.critical])
        log.info("%-15s %-28s %12.2f %s", c.dataset, c.name, val, "PASS" if ok else "FAIL")
    con.close()
    failed = [r for r in results if r["critical"] and not r["passed"]]
    if failed and raise_on_fail:
        raise RuntimeError(f"critical data-quality checks failed: {failed}")
    return results


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    run_checks()
