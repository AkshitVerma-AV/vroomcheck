"""Embed complaint narratives and recall defect descriptions.

The model runs locally (sentence-transformers); the vectors are stored in
Snowflake as VECTOR(FLOAT, 384) so that similarity search runs in SQL
(VECTOR_COSINE_SIMILARITY) for the RAG step, the MCP server and the dashboard.
Embedding is incremental: only complaints and recalls without a vector are
processed.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np
import pandas as pd

from vroomcheck import config
from vroomcheck.db import connect
from vroomcheck.detection.text import clean_narrative, clean_recall

log = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def get_model():
    import torch
    from sentence_transformers import SentenceTransformer

    device = "cuda" if torch.cuda.is_available() else "cpu"
    log.info("loading %s on %s", config.EMBED_MODEL, device)
    return SentenceTransformer(config.EMBED_MODEL, device=device)


def embed_texts(texts: list[str], batch_size: int = config.EMBED_BATCH) -> np.ndarray:
    model = get_model()
    vecs = model.encode(texts, batch_size=batch_size, normalize_embeddings=True,
                        show_progress_bar=len(texts) > 5000, convert_to_numpy=True)
    return vecs.astype(np.float32)


def _scope_filter() -> str:
    if config.FOCUS_MAKES:
        makes = ", ".join(f"'{m}'" for m in config.FOCUS_MAKES)
        return f"and c.make in ({makes})"
    return ""


def insert_vectors(con, table: str, id_col: str, ids: list[str], vecs: np.ndarray, model: str) -> None:
    """Append (id, vector) rows to an ML embedding table, going through a transient staging table."""
    con.write_df(pd.DataFrame({id_col: ids, "embedding": list(vecs.astype(float))}), "ml._emb_batch")
    con.execute(f"""insert into {table} ({id_col}, embedding, model, embedded_at)
                    select {id_col}, embedding::vector(float, {config.EMBED_DIM}), ?, current_timestamp()
                    from ml._emb_batch""", [model])
    con.execute("drop table if exists ml._emb_batch")


def ensure_embedding_table(con, table: str, id_col: str) -> None:
    con.execute(f"""create table if not exists {table}
                    ({id_col} varchar primary key, embedding vector(float, {config.EMBED_DIM}),
                     model varchar, embedded_at timestamp_ntz)""")


def embed_complaints(chunk: int = 20000) -> int:
    con = connect()
    ensure_embedding_table(con, "ml.complaint_embeddings", "odino")
    # Only models with enough complaints are clustered, so only those are embedded.
    todo = con.execute(f"""
        with eligible as (
            select make, model from marts.fct_complaints where not is_remedy_complaint
            group by 1, 2 having count(*) >= {config.MIN_COMPLAINTS_PER_MODEL})
        select c.odino, c.narrative
        from marts.fct_complaints c
        join eligible using (make, model)
        where not c.is_remedy_complaint {_scope_filter()}
          and not exists (select 1 from ml.complaint_embeddings e where e.odino = c.odino)
        order by c.received_date
    """).fetchall()
    log.info("%s complaints to embed", f"{len(todo):,}")
    done = 0
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        vecs = embed_texts([clean_narrative(r[1]) for r in part])
        insert_vectors(con, "ml.complaint_embeddings", "odino", [r[0] for r in part], vecs, config.EMBED_MODEL)
        done += len(part)
        log.info("embedded %s / %s", f"{done:,}", f"{len(todo):,}")
    con.close()
    return done


def embed_recalls() -> int:
    con = connect()
    ensure_embedding_table(con, "ml.recall_embeddings", "campno")
    todo = con.execute("""
        select r.campno, coalesce(r.defect_summary, '') || ' ' || coalesce(r.consequence_summary, '')
        from marts.fct_recalls r
        where r.defect_summary is not null
          and not exists (select 1 from ml.recall_embeddings e where e.campno = r.campno)
    """).fetchall()
    log.info("%s recalls to embed", f"{len(todo):,}")
    if todo:
        vecs = embed_texts([clean_recall(r[1]) for r in todo])
        insert_vectors(con, "ml.recall_embeddings", "campno", [r[0] for r in todo], vecs, config.EMBED_MODEL)
    con.close()
    return len(todo)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    embed_recalls()
    embed_complaints()
