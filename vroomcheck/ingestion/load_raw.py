"""Step 2 of ingestion: load landing files into the warehouse `raw` schema.

Raw tables keep every column as VARCHAR exactly as NHTSA published it, plus
lineage columns (_source_file, _loaded_at). Rows DuckDB cannot parse are kept
in raw._load_rejects instead of silently dropped.
"""
from __future__ import annotations

import logging
from pathlib import Path

from vroomcheck.config import LANDING_DIR
from vroomcheck.db import connect
from vroomcheck.ingestion.sources import DATASETS, Dataset

log = logging.getLogger(__name__)


def _files_for(ds: Dataset) -> list[Path]:
    folder = LANDING_DIR / ds.name
    return sorted(p for p in folder.glob("*") if p.suffix.lower() in (".txt", ".tsv"))


def load_dataset(con, ds: Dataset) -> int:
    files = _files_for(ds)
    if not files:
        raise FileNotFoundError(f"no landing files for {ds.name}; run download first")
    cols = "{" + ", ".join(f"'{c}': 'VARCHAR'" for c in ds.columns) + "}"
    file_list = "[" + ", ".join(f"'{f.as_posix()}'" for f in files) + "]"
    log.info("loading %s from %d file(s)", ds.name, len(files))
    con.execute(f"""
        create or replace table raw.{ds.name} as
        select *, current_timestamp as _loaded_at
        from read_csv({file_list},
                      delim='\t', header=false, quote='', escape='',
                      columns={cols}, null_padding=true, ignore_errors=true,
                      filename='_source_file',
                      store_rejects=true, rejects_table='_rejects_tmp')
    """)
    rejects = con.execute("select count(*) from _rejects_tmp").fetchone()[0]
    con.execute("""create table if not exists raw._load_rejects
                   (dataset varchar, source_file varchar, line bigint, error_message varchar,
                    logged_at timestamp)""")
    con.execute("delete from raw._load_rejects where dataset = ?", [ds.name])
    if rejects:
        con.execute(f"""
            insert into raw._load_rejects
            select '{ds.name}', s.file_path, e.line, e.error_message, current_timestamp
            from _rejects_tmp e join reject_scans s using (scan_id)""")
    n = con.execute(f"select count(*) from raw.{ds.name}").fetchone()[0]
    log.info("%s: %s rows loaded, %s rejected", ds.name, f"{n:,}", rejects)
    return n


def load_all() -> dict[str, int]:
    con = connect()
    try:
        return {k: load_dataset(con, ds) for k, ds in DATASETS.items()}
    finally:
        con.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(load_all())
