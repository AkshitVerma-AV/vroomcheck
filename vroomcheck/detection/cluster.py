"""Group complaints that describe the same failure in different words.

For every vehicle line (make + model, pooling model years, since one defect
usually spans several years) we reduce the narrative embeddings with PCA and
run HDBSCAN. HDBSCAN suits this problem: it does not need the number of
defects in advance and leaves one-off complaints as noise instead of forcing
them into a cluster. Each cluster gets a keyword label via class-based TF-IDF.
"""
from __future__ import annotations

import json
import logging
import re

import numpy as np
import pandas as pd
from sklearn.cluster import HDBSCAN
from sklearn.decomposition import PCA
from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer

from vroomcheck import config
from vroomcheck.db import connect
from vroomcheck.detection.text import LABEL_STOPWORDS, clean_narrative

log = logging.getLogger(__name__)
STOP = list(ENGLISH_STOP_WORDS | LABEL_STOPWORDS)


def _slug(s: str) -> str:
    return re.sub(r"[^A-Z0-9]+", "-", s.upper()).strip("-")


def _embeddings_for(con, make: str, model: str) -> tuple[pd.DataFrame, np.ndarray]:
    df = con.execute("""
        select c.odino, c.received_date, c.model_year, c.primary_component, c.primary_system,
               c.is_crash, c.is_fire, c.injured, c.deaths, c.harm_score, c.narrative,
               e.embedding::array as embedding
        from marts.fct_complaints c join ml.complaint_embeddings e using (odino)
        where c.make = ? and c.model = ? and not c.is_remedy_complaint
        order by c.odino
    """, [make, model]).df()
    X = np.array([json.loads(v) if isinstance(v, str) else v for v in df.pop("embedding")],
                 dtype=np.float32).reshape(-1, config.EMBED_DIM)
    return df, X


def _ctfidf_labels(docs_by_cluster: dict[int, list[str]], top_n: int = 6) -> dict[int, str]:
    keys = list(docs_by_cluster)
    joined = [" ".join(docs_by_cluster[k]) for k in keys]
    if len(joined) < 1:
        return {}
    vec = CountVectorizer(ngram_range=(1, 2), stop_words=STOP, min_df=1, max_features=20000,
                          token_pattern=r"(?u)\b[a-z][a-z]+\b")
    tf = vec.fit_transform(joined).astype(float).toarray()
    tf = tf / np.maximum(tf.sum(axis=1, keepdims=True), 1)
    avg_words = tf.sum() / max(len(keys), 1)
    idf = np.log(1 + avg_words / np.maximum(tf.sum(axis=0), 1e-12))
    scores = tf * idf
    vocab = np.array(vec.get_feature_names_out())
    return {k: ", ".join(vocab[np.argsort(-scores[i])[:top_n]]) for i, k in enumerate(keys)}


def cluster_vehicle_line(con, make: str, model: str) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    df, X = _embeddings_for(con, make, model)
    n = len(df)
    if n < config.MIN_COMPLAINTS_PER_MODEL:
        return None
    Xr = PCA(n_components=min(32, n - 1), random_state=42).fit_transform(X)
    Xr /= np.linalg.norm(Xr, axis=1, keepdims=True) + 1e-9
    min_cluster = max(config.HDBSCAN_MIN_CLUSTER, int(n * 0.004))
    hdb = HDBSCAN(min_cluster_size=min_cluster, min_samples=5, cluster_selection_method="leaf")
    labels = hdb.fit_predict(Xr)
    df["label"] = labels
    df["probability"] = hdb.probabilities_
    if (labels >= 0).sum() == 0:
        return None

    docs = {int(k): [clean_narrative(t, 600) for t in g.narrative]
            for k, g in df[df.label >= 0].groupby("label")}
    kw = _ctfidf_labels(docs)

    rows, members = [], []
    for k, g in df[df.label >= 0].groupby("label"):
        idx = g.index.to_numpy()
        centroid = X[idx].mean(axis=0)
        centroid /= np.linalg.norm(centroid) + 1e-9
        sims = X[idx] @ centroid
        cid = f"{_slug(make)}__{_slug(model)}__{int(k):03d}"
        rows.append(dict(
            cluster_id=cid, make=make, model=model, size=len(g),
            min_model_year=int(g.model_year.min()), max_model_year=int(g.model_year.max()),
            top_component=g.primary_component.mode().iat[0] if g.primary_component.notna().any() else None,
            top_system=g.primary_system.mode().iat[0] if g.primary_system.notna().any() else None,
            component_purity=float(g.primary_component.value_counts(normalize=True).iat[0]),
            keywords=kw.get(int(k), ""),
            first_received=g.received_date.min(), last_received=g.received_date.max(),
            n_crash=int(g.is_crash.sum()), n_fire=int(g.is_fire.sum()),
            n_injured=int(g.injured.sum()), n_deaths=int(g.deaths.sum()),
            harm_rate=float(g.harm_score.mean()),
            cohesion=float(sims.mean()),
            centroid=centroid.astype(np.float32).tolist(),
        ))
        members += [dict(odino=o, cluster_id=cid, probability=float(p), centroid_similarity=float(s))
                    for o, p, s in zip(g.odino, g.probability, sims)]
    return pd.DataFrame(rows), pd.DataFrame(members)


def save_clusters(con, clusters: pd.DataFrame, members: pd.DataFrame) -> None:
    """Write ml.clusters (centroid as VECTOR) and ml.cluster_members."""
    con.write_df(clusters.drop(columns=["built_at"], errors="ignore"), "ml._clusters_stage")
    con.execute(f"""create or replace table ml.clusters as
                    select * exclude (centroid), centroid::vector(float, {config.EMBED_DIM}) as centroid,
                           current_timestamp() as built_at
                    from ml._clusters_stage""")
    con.execute("drop table if exists ml._clusters_stage")
    # permanent, like the embeddings: rebuilding clusters changes every downstream result
    con.write_df(members, "ml.cluster_members", transient=False)


def run_clustering() -> int:
    con = connect()
    groups = con.execute("""
        select c.make, c.model, count(*) n
        from marts.fct_complaints c join ml.complaint_embeddings e using (odino)
        where not c.is_remedy_complaint
        group by 1, 2 having count(*) >= ? order by n desc
    """, [config.MIN_COMPLAINTS_PER_MODEL]).fetchall()
    log.info("clustering %d vehicle lines", len(groups))
    all_c, all_m = [], []
    for i, (make, model, n) in enumerate(groups, 1):
        res = cluster_vehicle_line(con, make, model)
        if res is None:
            continue
        c, m = res
        all_c.append(c)
        all_m.append(m)
        log.info("[%d/%d] %s %s: %d complaints -> %d clusters (%.0f%% clustered)",
                 i, len(groups), make, model, n, len(c), 100 * len(m) / n)
    clusters = pd.concat(all_c, ignore_index=True)
    members = pd.concat(all_m, ignore_index=True)
    save_clusters(con, clusters, members)
    con.close()
    log.info("built %d clusters covering %d complaints", len(clusters), len(members))
    return len(clusters)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_clustering()
