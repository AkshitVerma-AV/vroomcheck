"""Load the landing files into Snowflake's RAW schema: the Snowflake twin of load_raw.py.

Same contract as the DuckDB loader: every column VARCHAR exactly as NHTSA published it,
plus lineage columns _SOURCE_FILE and _LOADED_AT. Rows Snowflake cannot parse do not
fail the load; their count and first error per file go to RAW._LOAD_REJECTS.

Files are uploaded to an internal stage (gzip-compressed on the way) and loaded with
COPY INTO. NHTSA files contain literal quotes and backslashes, so the file format turns
off quoting and escaping, matching read_csv(quote='', escape='') in load_raw.py.

A full load ends with the same data-quality gate as the DuckDB pipeline
(quality_checks.py --target snowflake), so a critical failure stops before dbt.

    python -m vroomcheck.ingestion.load_raw_snowflake              # all four datasets
    python -m vroomcheck.ingestion.load_raw_snowflake --only recalls
    python -m vroomcheck.ingestion.load_raw_snowflake --sample 2000
        # test run: the first 2000 lines of each file into RAW._SAMPLE_* tables
"""
from __future__ import annotations

import argparse
import logging
import tempfile
from pathlib import Path

from vroomcheck import config
from vroomcheck.ingestion.load_raw import _files_for
from vroomcheck.ingestion.sources import DATASETS, Dataset

log = logging.getLogger(__name__)
SCHEMA = "RAW"
STAGE = f"{SCHEMA}.NHTSA_STAGE"
FILE_FORMAT = f"{SCHEMA}.NHTSA_TSV"


def connect():
    import snowflake.connector

    auth = ({"private_key_file": config.SNOWFLAKE_PRIVATE_KEY_PATH} if config.SNOWFLAKE_PRIVATE_KEY_PATH
            else {"password": config.SNOWFLAKE_PASSWORD})
    return snowflake.connector.connect(
        account=config.SNOWFLAKE_ACCOUNT, user=config.SNOWFLAKE_USER, role=config.SNOWFLAKE_ROLE,
        warehouse=config.SNOWFLAKE_WAREHOUSE, database=config.SNOWFLAKE_DATABASE, schema=SCHEMA,
        application="vroomcheck", **auth)


def ensure_objects(cur) -> None:
    cur.execute(f"""create file format if not exists {FILE_FORMAT}
                    type = csv field_delimiter = '\\t' skip_header = 0
                    field_optionally_enclosed_by = none escape = none escape_unenclosed_field = none
                    null_if = () empty_field_as_null = true trim_space = false
                    error_on_column_count_mismatch = false encoding = 'UTF8'""")
    cur.execute(f"create stage if not exists {STAGE} file_format = {FILE_FORMAT}")
    cur.execute(f"""create table if not exists {SCHEMA}._LOAD_REJECTS
                    (dataset varchar, source_file varchar, line bigint, error_message varchar,
                     logged_at timestamp_ltz)""")


def load_dataset(cur, ds: Dataset, files: list[Path], table: str, prefix: str) -> int:
    """Replace `table` with the contents of `files`, staged under `prefix`."""
    cols = ", ".join(ds.columns)
    cur.execute(f"""create or replace table {SCHEMA}.{table}
                    ({", ".join(f"{c} varchar" for c in ds.columns)},
                     _SOURCE_FILE varchar, _LOADED_AT timestamp_ltz)""")
    cur.execute(f"remove @{STAGE}/{prefix}/")
    for f in files:
        log.info("uploading %s (%.0f MB)", f.name, f.stat().st_size / 2**20)
        cur.execute(f"put 'file://{f.as_posix()}' @{STAGE}/{prefix}/ auto_compress = true overwrite = true parallel = 8")
    positional = ", ".join(f"${i}" for i in range(1, len(ds.columns) + 1))
    results = cur.execute(f"""
        copy into {SCHEMA}.{table} ({cols}, _SOURCE_FILE, _LOADED_AT)
        from (select {positional}, metadata$filename, current_timestamp() from @{STAGE}/{prefix}/)
        file_format = (format_name = '{FILE_FORMAT}')
        on_error = 'continue' force = true""").fetchall()
    names = [d[0].lower() for d in cur.description]
    cur.execute(f"delete from {SCHEMA}._LOAD_REJECTS where dataset = %s", [table])
    loaded = 0
    for row in results:
        r = dict(zip(names, row))
        loaded += r.get("rows_loaded") or 0
        log.info("%s: %s rows loaded, %s errors", r.get("file"), f"{r.get('rows_loaded') or 0:,}", r.get("errors_seen") or 0)
        if r.get("errors_seen"):
            cur.execute(f"insert into {SCHEMA}._LOAD_REJECTS select %s, %s, %s, %s, current_timestamp()",
                        [table, r.get("file"), r.get("first_error_line"),
                         f"{r['errors_seen']} rows rejected; first: {r.get('first_error')}"])
    n = cur.execute(f"select count(*) from {SCHEMA}.{table}").fetchone()[0]
    log.info("%s: %s rows in table", table, f"{n:,}")
    return n


def _sample_files(ds: Dataset, n_lines: int, tmp: Path) -> list[Path]:
    out = []
    for f in _files_for(ds):
        dest = tmp / ds.name / f.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(f, "rb") as src, open(dest, "wb") as dst:
            for i, line in enumerate(src):
                if i >= n_lines:
                    break
                dst.write(line)
        out.append(dest)
    return out


def load_all(only: str | None = None, sample: int | None = None, run_gate: bool = True) -> dict[str, int]:
    keys = [only] if only else list(DATASETS)
    con = connect()
    try:
        cur = con.cursor()
        ensure_objects(cur)
        counts = {}
        with tempfile.TemporaryDirectory() as tmp:
            for key in keys:
                ds = DATASETS[key]
                if sample:
                    files = _sample_files(ds, sample, Path(tmp))
                    counts[key] = load_dataset(cur, ds, files, f"_SAMPLE_{ds.name.upper()}", f"_sample/{ds.name}")
                else:
                    files = _files_for(ds)
                    if not files:
                        raise FileNotFoundError(f"no landing files for {ds.name}; run download first")
                    counts[key] = load_dataset(cur, ds, files, ds.name.upper(), ds.name)
    finally:
        con.close()
    if run_gate and not sample:
        # same gate as the DuckDB pipeline: a critical failure stops here, before dbt
        from vroomcheck.ingestion.quality_checks import run_checks
        run_checks(target="snowflake")
    return counts


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    ap = argparse.ArgumentParser()
    ap.add_argument("--only", choices=list(DATASETS))
    ap.add_argument("--sample", type=int, help="load only the first N lines of each file into RAW._SAMPLE_* tables")
    a = ap.parse_args()
    print(load_all(a.only, a.sample))
