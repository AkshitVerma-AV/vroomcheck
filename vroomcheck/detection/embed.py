"""Embed complaint narratives and recall defect descriptions.

Vectors are stored in the warehouse as fixed-size FLOAT[384] arrays so that
similarity search can run in SQL (array_cosine_similarity) for the RAG step,
the MCP server and the dashboard. Embedding is incremental: only complaints
and recalls without a vector are processed.
"""
from __future__ import annotations

import logging
from functools import lru_cache

import numpy as np
import pyarrow as pa

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


def _to_arrow(ids: list[str], vecs: np.ndarray, id_col: str) -> pa.Table:
    flat = pa.array(vecs.reshape(-1), type=pa.float32())
    emb = pa.FixedSizeListArray.from_arrays(flat, config.EMBED_DIM)
    return pa.table({id_col: pa.array(ids), "embedding": emb})


def _scope_filter() -> str:
    if config.FOCUS_MAKES:
        makes = ", ".join(f"'{m}'" for m in config.FOCUS_MAKES)
        return f"and c.make in ({makes})"
    return ""


def embed_complaints(chunk: int = 20000) -> int:
    con = connect()
    con.execute(f"""create table if not exists ml.complaint_embeddings
                    (odino varchar primary key, embedding float[{config.EMBED_DIM}],
                     model varchar, embedded_at timestamp)""")
    # Only models with enough complaints are clustered, so only those are embedded.
    todo = con.execute(f"""
        with eligible as (
            select make, model from marts.fct_complaints where not is_remedy_complaint
            group by 1, 2 having count(*) >= {config.MIN_COMPLAINTS_PER_MODEL})
        select c.odino, c.narrative
        from marts.fct_complaints c
        join eligible using (make, model)
        anti join ml.complaint_embeddings e on e.odino = c.odino
        where not c.is_remedy_complaint {_scope_filter()}
        order by c.received_date
    """).fetchall()
    log.info("%s complaints to embed", f"{len(todo):,}")
    done = 0
    for i in range(0, len(todo), chunk):
        part = todo[i:i + chunk]
        ids = [r[0] for r in part]
        vecs = embed_texts([clean_narrative(r[1]) for r in part])
        tbl = _to_arrow(ids, vecs, "odino")
        con.register("emb_batch", tbl)
        con.execute(f"""insert into ml.complaint_embeddings
                        select odino, embedding::float[{config.EMBED_DIM}], ?, current_timestamp
                        from emb_batch""", [config.EMBED_MODEL])
        con.unregister("emb_batch")
        done += len(part)
        log.info("embedded %s / %s", f"{done:,}", f"{len(todo):,}")
    con.close()
    return done


def embed_recalls() -> int:
    con = connect()
    con.execute(f"""create table if not exists ml.recall_embeddings
                    (campno varchar primary key, embedding float[{config.EMBED_DIM}],
                     model varchar, embedded_at timestamp)""")
    todo = con.execute("""
        select r.campno, coalesce(r.defect_summary, '') || ' ' || coalesce(r.consequence_summary, '')
        from marts.fct_recalls r
        anti join ml.recall_embeddings e on e.campno = r.campno
        where r.defect_summary is not null
    """).fetchall()
    log.info("%s recalls to embed", f"{len(todo):,}")
    if todo:
        vecs = embed_texts([clean_recall(r[1]) for r in todo])
        con.register("emb_batch", _to_arrow([r[0] for r in todo], vecs, "campno"))
        con.execute(f"""insert into ml.recall_embeddings
                        select campno, embedding::float[{config.EMBED_DIM}], ?, current_timestamp
                        from emb_batch""", [config.EMBED_MODEL])
    con.close()
    return len(todo)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    embed_recalls()
    embed_complaints()
