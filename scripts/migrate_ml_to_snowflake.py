"""One-time move of the ML layer's expensive inputs from DuckDB to Snowflake.

Copies the complaint and recall embeddings (351K + 13.7K MiniLM vectors), the
HDBSCAN clusters and their members, and the agents' briefs into VROOMCHECK.ML.
Everything downstream (spike scores, recall matches, alerts, back-test and the
ESV26-289 indicator, events and model years) is then recomputed inside
Snowflake by the normal pipeline steps. Moving instead of recomputing keeps the
clusters, and so the "before" results, exactly as they were.

Vectors travel as Parquet: DuckDB writes the file, PUT uploads it to an internal
stage, COPY INTO loads it, and a cast turns each array into VECTOR(FLOAT, 384).

    python scripts/migrate_ml_to_snowflake.py
"""
from __future__ import annotations

import json
import logging
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from vroomcheck import config  # noqa: E402
from vroomcheck.db import connect_duckdb, connect_snowflake  # noqa: E402
from vroomcheck.detection.cluster import save_clusters  # noqa: E402

log = logging.getLogger("migrate")
STAGE = "ml.ml_stage"


def _move_vectors(src, dst, table: str, id_col: str, tmp: Path) -> int:
    f = tmp / f"{table}.parquet"
    src.execute(f"""copy (select {id_col}, embedding::float[] as embedding, model, embedded_at
                          from ml.{table}) to '{f.as_posix()}' (format parquet)""")
    dst.execute(f"put 'file://{f.as_posix()}' @{STAGE}/{table}/ auto_compress = false overwrite = true")
    dst.execute(f"""create or replace transient table ml._{table}_load
                    ({id_col} varchar, embedding array, model varchar, embedded_at timestamp_ntz)""")
    dst.execute(f"""copy into ml._{table}_load from (
                        select $1:{id_col}::varchar, $1:embedding::array, $1:model::varchar,
                               $1:embedded_at::timestamp_ntz
                        from @{STAGE}/{table}/)
                    file_format = (type = parquet)""")
    dst.execute(f"""create or replace table ml.{table}
                    ({id_col} varchar, embedding vector(float, {config.EMBED_DIM}),
                     model varchar, embedded_at timestamp_ntz) as
                    select {id_col}, embedding::vector(float, {config.EMBED_DIM}), model, embedded_at
                    from ml._{table}_load""")
    dst.execute(f"drop table ml._{table}_load")
    dst.execute(f"remove @{STAGE}/{table}/")
    return dst.execute(f"select count(*) from ml.{table}").fetchone()[0]


def main() -> None:
    src = connect_duckdb(read_only=True)
    dst = connect_snowflake()
    dst.execute(f"create stage if not exists {STAGE}")
    with tempfile.TemporaryDirectory() as tmp:
        for table, id_col in (("recall_embeddings", "campno"), ("complaint_embeddings", "odino")):
            n = _move_vectors(src, dst, table, id_col, Path(tmp))
            log.info("ml.%s: %s rows", table, f"{n:,}")

    clusters = src.execute("select * exclude (centroid), centroid::float[] as centroid from ml.clusters").df()
    clusters["centroid"] = clusters.centroid.map(lambda v: [float(x) for x in v])
    members = src.execute("select * from ml.cluster_members").df()
    save_clusters(dst, clusters, members)
    log.info("ml.clusters: %s rows, ml.cluster_members: %s rows", f"{len(clusters):,}", f"{len(members):,}")

    from vroomcheck.services import queries as q
    briefs = sorted((config.WAREHOUSE_PATH.parent / "briefs").glob("*.json"))
    for p in briefs:
        q.save_brief(p.stem, json.loads(p.read_text()))
    log.info("ml.briefs: %d briefs", len(briefs))
    src.close()
    dst.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("snowflake").setLevel(logging.WARNING)
    main()
