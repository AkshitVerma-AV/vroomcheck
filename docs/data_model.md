# Vroomcheck data model

The warehouse has four layers. Each layer is a schema in the same DuckDB file
(`warehouse/vroomcheck.duckdb`). The same dbt project builds the same
schemas in Snowflake when run with `VC_DBT_TARGET=snowflake`.

| Layer | Schema | Built by | Purpose |
|---|---|---|---|
| Bronze | `raw` | `vroomcheck/ingestion/load_raw.py` | NHTSA flat files as published, all VARCHAR, plus lineage columns `_source_file` and `_loaded_at`. Also holds `_dq_results` and `_load_rejects`. |
| Silver | `staging` | dbt views `stg_*` | Typed dates and integers, normalised make/model names, component path split into top-level component. |
| Gold | `marts` | dbt tables | Star schema: facts, bridges and conformed dimensions, restricted to the project scope. |
| ML | `ml` | `vroomcheck/detection/*` | Embeddings, clusters, monthly spike scores, recall matches, alerts and back-test results. |
| Ref | `ref` | dbt seeds | `component_ontology`, `non_passenger_makes`. |

## Gold layer (star schema)

```mermaid
erDiagram
    dim_vehicle ||--o{ fct_complaints : "vehicle_key"
    dim_vehicle ||--o{ bridge_recall_vehicle : "vehicle_key"
    dim_vehicle ||--o{ bridge_tsb_vehicle : "vehicle_key"
    dim_vehicle ||--o{ bridge_investigation_vehicle : "vehicle_key"
    dim_component ||--o{ bridge_complaint_component : "component_top"
    fct_complaints ||--o{ bridge_complaint_component : "odino"
    fct_recalls ||--o{ bridge_recall_vehicle : "campno"
    fct_tsbs ||--o{ bridge_tsb_vehicle : "nhtsa_id"
    fct_investigations ||--o{ bridge_investigation_vehicle : "action_number"
    fct_investigations |o--o| fct_recalls : "campno (investigation led to recall)"
    fct_complaints }o--o| fct_recalls : "referenced_campno (owner cites recall)"

    dim_vehicle {
        varchar vehicle_key PK "md5(make|model|year)"
        varchar make
        varchar model
        int model_year
        int n_complaints
        int n_recalls
    }
    dim_component {
        varchar component_top PK
        varchar system_group "ontology"
        double severity_weight
        bool in_scope
    }
    fct_complaints {
        varchar odino PK "NHTSA complaint number"
        varchar vehicle_key FK
        date received_date
        date fail_date
        varchar primary_component
        bool is_crash
        bool is_fire
        int injured
        int deaths
        int harm_score
        varchar narrative "free text, the early signal"
        varchar referenced_campno FK "recall cited in the narrative"
        bool is_remedy_complaint "repair-delay template, excluded from detection"
    }
    fct_recalls {
        varchar campno PK "e.g. 24V123000"
        date recall_date "Part 573 report date"
        varchar component_tops
        bigint potentially_affected
        varchar defect_summary
        varchar consequence_summary
        varchar remedy_summary
        varchar investigation_id FK
    }
    fct_investigations {
        varchar action_number PK "PE / EA / DP / RQ"
        date opened_date
        date closed_date
        varchar campno FK
        varchar subject
    }
    fct_tsbs {
        varchar nhtsa_id PK
        varchar tsb_document_id
        date communication_date
        varchar communication_type
        varchar summary
    }
```

**Grain**
* `fct_complaints`: one row per complaint (ODINO). In the raw file a complaint
  that names three components appears as three rows. The extra rows go into
  `bridge_complaint_component`.
* `fct_recalls`: one row per recall campaign (CAMPNO). The affected vehicles
  go into `bridge_recall_vehicle`, one row per campaign × make/model/year × component.
* The vehicle key is `md5(make|model|model_year)`. Complaints, recalls, TSBs
  and investigations all join on it after name normalisation (`norm_name` macro).

**Scope rules** (pitch slide 8), applied in `fct_complaints`:
* product type is vehicle (`PROD_TYPE = 'V'`), model year 2015 or later, received 2015 or later
* complaints about tires, child seats or equipment only are dropped, using `component_ontology.in_scope`
* RVs, motorcycles and heavy trucks are dropped, using the `non_passenger_makes` seed

## ML layer

```mermaid
erDiagram
    fct_complaints ||--|| complaint_embeddings : odino
    fct_recalls ||--|| recall_embeddings : campno
    clusters ||--o{ cluster_members : cluster_id
    fct_complaints ||--o| cluster_members : odino
    clusters ||--o{ cluster_monthly : cluster_id
    clusters ||--o{ cluster_recall_match : cluster_id
    fct_recalls ||--o{ cluster_recall_match : campno
    clusters ||--|| alerts : cluster_id
    fct_recalls ||--o| backtest_results : campno

    complaint_embeddings { varchar odino PK
        float384 embedding }
    clusters { varchar cluster_id PK
        varchar make
        varchar model
        int size
        varchar keywords "c-TF-IDF label"
        float384 centroid }
    cluster_monthly { varchar cluster_id
        date month
        int n
        double expected_3m
        double p_value
        bool is_spike }
    alerts { varchar cluster_id PK
        varchar status "EMERGING / RECALLED / WATCH"
        double priority }
    backtest_results { varchar campno PK
        date recall_date
        date first_alert_month
        double lead_months }
```

## Data quality

| Where | Check |
|---|---|
| Ingestion gate (`raw._dq_results`) | row counts, primary-key nulls, unparseable dates, load rejects, missing narratives, future dates, CAMPNO format |
| dbt tests | unique and not-null keys on every fact and dimension, referential integrity between bridges and facts, accepted investigation types, investigation close date not before open date (warn), complaint received no more than 2 years before its model year (warn) |
