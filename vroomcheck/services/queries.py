"""Read-side service layer shared by the Streamlit dashboard, the MCP server and
the agents. Every function opens a read-only warehouse connection, so many
consumers can query while no pipeline run is writing.
"""
from __future__ import annotations

import json
from contextlib import contextmanager

import numpy as np
import pandas as pd
import requests

from vroomcheck import config
from vroomcheck.db import connect, table_exists

BRIEF_DIR = config.WAREHOUSE_PATH.parent / "briefs"


@contextmanager
def ro():
    con = connect(read_only=True)
    try:
        yield con
    finally:
        con.close()


def _vec_literal(v: np.ndarray) -> str:
    return "[" + ",".join(f"{x:.6f}" for x in v) + f"]::float[{config.EMBED_DIM}]"


# ------------------------------------------------------------------ overview
def layer_counts() -> pd.DataFrame:
    tables = [
        ("raw", "raw_complaints"), ("raw", "raw_recalls"), ("raw", "raw_investigations"), ("raw", "raw_tsbs"),
        ("marts", "fct_complaints"), ("marts", "fct_recalls"), ("marts", "fct_investigations"),
        ("marts", "fct_tsbs"), ("marts", "dim_vehicle"), ("marts", "dim_component"),
        ("ml", "complaint_embeddings"), ("ml", "recall_embeddings"), ("ml", "clusters"),
        ("ml", "cluster_members"), ("ml", "alerts"), ("ml", "backtest_results"),
        ("ml", "vehicle_component_indicator"), ("ml", "indicator_events"), ("ml", "cluster_model_years"),
    ]
    rows = []
    with ro() as con:
        existing = {(s, t) for s, t in con.execute(
            "select table_schema, table_name from information_schema.tables").fetchall()}
        for s, t in tables:
            n = con.execute(f"select count(*) from {s}.{t}").fetchone()[0] if (s, t) in existing else None
            rows.append({"layer": s, "table": t, "rows": n})
    return pd.DataFrame(rows)


def dq_results() -> pd.DataFrame:
    with ro() as con:
        return con.execute("""select * from raw._dq_results
                              qualify run_at = max(run_at) over () order by dataset, check_name""").df()


def data_freshness() -> dict:
    with ro() as con:
        return con.execute("""select (select max(received_date) from marts.fct_complaints) as latest_complaint,
                                     (select max(recall_date) from marts.fct_recalls)       as latest_recall,
                                     (select max(_loaded_at) from raw.raw_complaints)      as loaded_at""").df().iloc[0].to_dict()


# ------------------------------------------------------------------ alerts / clusters
def list_alerts(status: str | None = "EMERGING", make: str | None = None, limit: int = 50) -> pd.DataFrame:
    where, params = ["1=1"], []
    if status:
        where.append("status = ?"); params.append(status)
    if make:
        where.append("make = ?"); params.append(make.upper())
    with ro() as con:
        return con.execute(f"""select * from ml.alerts where {' and '.join(where)}
                               order by priority desc nulls last limit {int(limit)}""", params).df()


def get_cluster(cluster_id: str) -> dict | None:
    with ro() as con:
        df = con.execute("""select a.*, c.cohesion, c.component_purity, c.first_received, c.last_received
                            from ml.alerts a join ml.clusters c using (cluster_id)
                            where cluster_id = ?""", [cluster_id]).df()
    return None if df.empty else df.iloc[0].to_dict()


def cluster_centroid(cluster_id: str) -> np.ndarray:
    with ro() as con:
        v = con.execute("select centroid from ml.clusters where cluster_id = ?", [cluster_id]).fetchone()[0]
    return np.asarray(v, dtype=np.float32)


def cluster_monthly(cluster_id: str) -> pd.DataFrame:
    with ro() as con:
        return con.execute("select * from ml.cluster_monthly where cluster_id = ? order by month",
                           [cluster_id]).df()


def cluster_complaints(cluster_id: str, k: int = 8, harmful_extra: int = 4) -> pd.DataFrame:
    """Most representative complaints (closest to centroid) plus the most harmful ones."""
    with ro() as con:
        return con.execute(f"""
            with m as (
                select c.odino, c.received_date, c.model_year, c.primary_component, c.is_crash, c.is_fire,
                       c.injured, c.deaths, c.miles, c.harm_score, c.narrative, m.centroid_similarity
                from ml.cluster_members m join marts.fct_complaints c using (odino)
                where m.cluster_id = ?
            ),
            rep as (select *, 'representative' as why from m order by centroid_similarity desc limit {int(k)}),
            harm as (select *, 'highest harm' as why from m where odino not in (select odino from rep)
                     and harm_score > 1 order by harm_score desc, centroid_similarity desc limit {int(harmful_extra)})
            select * from rep union all select * from harm
        """, [cluster_id]).df()


def similar_recalls(vec: np.ndarray, k: int = 5, make: str | None = None) -> pd.DataFrame:
    # passenger-vehicle recalls only (bridge_recall_vehicle already excludes RVs, trucks, motorcycles)
    flt = "where r.campno in (select campno from marts.bridge_recall_vehicle)"
    flt += " and r.makes ilike '%' || ? || '%'" if make else ""
    params = [make] if make else []
    with ro() as con:
        return con.execute(f"""
            select r.campno, r.recall_date, r.makes, r.component_tops, r.potentially_affected,
                   r.defect_summary, r.consequence_summary, r.remedy_summary, r.investigation_id,
                   array_cosine_similarity(e.embedding, {_vec_literal(vec)}) as similarity
            from ml.recall_embeddings e join marts.fct_recalls r using (campno)
            {flt}
            order by similarity desc limit {int(k)}
        """, params).df()


def cluster_recall_matches(cluster_id: str) -> pd.DataFrame:
    with ro() as con:
        return con.execute("""select m.*, r.defect_summary from ml.cluster_recall_match m
                              join marts.fct_recalls r using (campno)
                              where cluster_id = ? order by rank""", [cluster_id]).df()


def related_tsbs(make: str, model: str, y0: int, y1: int, component: str | None, limit: int = 200) -> pd.DataFrame:
    with ro() as con:
        return con.execute(f"""
            select distinct t.nhtsa_id, t.tsb_document_id, t.communication_date, t.communication_type,
                   t.component_tops, t.summary
            from marts.bridge_tsb_vehicle b join marts.fct_tsbs t using (nhtsa_id)
            where b.make = ? and b.model = ? and b.model_year between ? and ?
              and (? is null or b.component_top = ?)
              and t.communication_type not ilike '%emission%'
            order by t.communication_date desc limit {int(limit)}
        """, [make, model, y0, y1, component, component]).df()


def related_investigations(make: str, model: str, y0: int, y1: int) -> pd.DataFrame:
    with ro() as con:
        return con.execute("""
            select distinct i.action_number, i.investigation_type, i.opened_date, i.closed_date, i.campno,
                   i.component_tops, i.subject, left(i.summary, 1200) as summary
            from marts.bridge_investigation_vehicle b join marts.fct_investigations i using (action_number)
            where b.make = ? and b.model = ? and b.model_year between ? and ?
            order by i.opened_date desc
        """, [make, model, y0, y1]).df()


def search_complaints(query: str, make: str | None = None, model: str | None = None, k: int = 10) -> pd.DataFrame:
    from vroomcheck.detection.embed import embed_texts

    vec = embed_texts([query.lower()])[0]
    where, params = ["1=1"], []
    if make:
        where.append("c.make = ?"); params.append(make.upper())
    if model:
        where.append("c.model = ?"); params.append(model.upper())
    with ro() as con:
        return con.execute(f"""
            select c.odino, c.make, c.model, c.model_year, c.received_date, c.primary_component,
                   left(c.narrative, 500) as narrative,
                   array_cosine_similarity(e.embedding, {_vec_literal(vec)}) as similarity,
                   m.cluster_id
            from ml.complaint_embeddings e join marts.fct_complaints c using (odino)
            left join ml.cluster_members m using (odino)
            where {' and '.join(where)}
            order by similarity desc limit {int(k)}
        """, params).df()


# ------------------------------------------------------------------ VIN check (competency Q2)
def decode_vin(vin: str) -> dict:
    r = requests.get(config.VPIC_DECODE_URL.format(vin=vin.strip()), timeout=20)
    r.raise_for_status()
    res = r.json()["Results"][0]
    return {"vin": vin.upper(), "make": (res.get("Make") or "").upper(), "model": (res.get("Model") or "").upper(),
            "model_year": int(res["ModelYear"]) if res.get("ModelYear") else None,
            "body_class": res.get("BodyClass"), "plant": res.get("PlantCountry"),
            "error_code": res.get("ErrorCode"), "error_text": res.get("ErrorText")}


def live_recalls(make: str, model: str, year: int) -> list[dict]:
    try:
        r = requests.get(config.RECALLS_API_URL, params={"make": make, "model": model, "modelYear": year},
                         timeout=20)
        r.raise_for_status()
        return r.json().get("results", [])
    except Exception:
        return []


def _resolve_model(con, make: str, model: str) -> str:
    """vPIC model names can differ slightly from NHTSA complaint names (e.g. 'SILVERADO' vs 'SILVERADO 1500')."""
    hit = con.execute("select model from marts.dim_vehicle where make = ? and model = ? limit 1",
                      [make, model]).fetchone()
    if hit:
        return hit[0]
    hit = con.execute("""select model, sum(n_complaints) n from marts.dim_vehicle
                         where make = ? and (model like ? || '%' or ? like model || '%')
                         group by 1 order by n desc limit 1""", [make, model, model]).fetchone()
    return hit[0] if hit else model


def vin_check(vin: str) -> dict:
    v = decode_vin(vin)
    if not v["make"] or not v["model_year"]:
        return {"vehicle": v, "error": "VIN could not be decoded"}
    with ro() as con:
        model = _resolve_model(con, v["make"], v["model"])
        v["warehouse_model"] = model
        recalls = con.execute("""
            select distinct r.campno, r.recall_date, r.component_tops, r.defect_summary, r.consequence_summary,
                   r.remedy_summary, r.do_not_drive, r.park_outside
            from marts.bridge_recall_vehicle b join marts.fct_recalls r using (campno)
            where b.make = ? and b.model = ? and b.model_year = ?
            order by r.recall_date desc""", [v["make"], model, v["model_year"]]).df()
        unrecalled = con.execute("""
            select cluster_id, status, top_component, keywords, size, n_crash, n_fire, n_injured,
                   first_spike_month, last_spike_month, priority
            from ml.alerts
            where make = ? and model = ? and ? between min_model_year and max_model_year
              and status in ('EMERGING', 'WATCH')
            order by (status = 'EMERGING') desc, priority desc nulls last limit 15""",
                                 [v["make"], model, v["model_year"]]).df()
        by_component = con.execute("""
            select primary_component as component, count(*) as complaints,
                   sum(is_crash::int) as crashes, sum(is_fire::int) as fires, sum(injured) as injured
            from marts.fct_complaints where make = ? and model = ? and model_year = ?
            group by 1 order by 2 desc""", [v["make"], model, v["model_year"]]).df()
    return {"vehicle": v, "recalls": recalls, "live_recall_count": len(live_recalls(v["make"], v["model"], v["model_year"])),
            "unrecalled_patterns": unrecalled, "complaints_by_component": by_component}


# ------------------------------------------------------------------ back-test (competency Q4)
def backtest_results() -> pd.DataFrame:
    with ro() as con:
        return con.execute("select * from ml.backtest_results order by recall_date desc").df()


def backtest_summary() -> dict:
    with ro() as con:
        s = con.execute("""
            select count(*) as recalls_in_scope,
                   count(*) filter (where detectable) as recalls_detectable,
                   count(*) filter (where detectable and flagged_before_recall) as flagged_early,
                   median(lead_months) filter (where detectable and flagged_before_recall) as median_lead_months,
                   median(lead_vs_investigation_months) filter (where detectable and flagged_before_recall)
                       as median_lead_vs_investigation_months
            from ml.backtest_results""").df().iloc[0].to_dict()
    s["pct_flagged_of_detectable"] = round(100 * s["flagged_early"] / max(s["recalls_detectable"], 1), 1)
    return s


# ------------------------------------------------------------------ ESV26-289 method
def indicator_series(component: str, vehicles: list[tuple[str, str]]) -> pd.DataFrame:
    """Trailing-year complaints and the paper's P-indicator for some vehicle lines, one component."""
    if not vehicles:
        return pd.DataFrame()
    pairs = " or ".join("(make = ? and model = ?)" for _ in vehicles)
    params = [component] + [x for v in vehicles for x in v]
    with ro() as con:
        return con.execute(f"""
            select make, model, month, n_12m, p_indicator, is_elevated
            from ml.vehicle_component_indicator
            where component = ? and ({pairs})
            order by make, model, month""", params).df()


def indicator_components() -> list[str]:
    with ro() as con:
        return [r[0] for r in con.execute("""select component from ml.vehicle_component_indicator
                                             group by 1 order by sum(n) desc""").fetchall()]


def indicator_vehicles(component: str) -> pd.DataFrame:
    """Vehicle lines with complaints about a component, busiest first, with how often the paper flags them."""
    with ro() as con:
        return con.execute("""
            select make, model, sum(n) as complaints, count(*) filter (where is_elevated) as elevated_months
            from ml.vehicle_component_indicator where component = ?
            group by 1, 2 order by complaints desc""", [component]).df()


def cluster_model_years(cluster_id: str) -> pd.DataFrame:
    with ro() as con:
        return con.execute("select * from ml.cluster_model_years where cluster_id = ? order by model_year",
                           [cluster_id]).df()


def detector_comparison() -> pd.DataFrame:
    with ro() as con:
        if not table_exists(con, "ml", "detector_comparison"):
            return pd.DataFrame()
        return con.execute("select * from ml.detector_comparison").df()


def model_year_accuracy() -> pd.DataFrame:
    from vroomcheck.detection.model_years import evaluate_model_years
    with ro() as con:
        ev = evaluate_model_years(con)
    if ev.empty:
        return ev
    return (ev.groupby("method")[["years_named", "precision", "recall", "jaccard"]].mean().round(3)
              .reset_index())


# ------------------------------------------------------------------ briefs
def load_brief(cluster_id: str) -> dict | None:
    p = BRIEF_DIR / f"{cluster_id}.json"
    return json.loads(p.read_text()) if p.exists() else None


def save_brief(cluster_id: str, brief: dict) -> None:
    BRIEF_DIR.mkdir(parents=True, exist_ok=True)
    (BRIEF_DIR / f"{cluster_id}.json").write_text(json.dumps(brief, indent=2, default=str))
