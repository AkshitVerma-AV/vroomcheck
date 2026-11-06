# Vroomcheck data model

The warehouse has four layers. Each layer is a schema in the same DuckDB file
(`warehouse/vroomcheck.duckdb`). The same dbt project builds the same
schemas in Snowflake when run with `VC_DBT_TARGET=snowflake`.

| Layer | Schema | Built by | Purpose |
|---|---|---|---|
| Bronze | `raw` | `vroomcheck/ingestion/load_raw.py` | NHTSA flat files as published, all VARCHAR, plus lineage columns `_source_file` and `_loaded_at`. Also holds `_dq_results` and `_load_rejects`. |
| Silver | `staging` | dbt views `stg_*` | Typed dates and integers, canonical make/model names, component path split into top-level component, `is_passenger_vehicle` flag. |
| Gold | `marts` | dbt tables | Star schema: facts, bridges and conformed dimensions, restricted to the project scope. |
| ML | `ml` | `vroomcheck/detection/*` | Embeddings, clusters, monthly spike scores, recall matches, alerts and back-test results. |
| Ref | `ref` | dbt seeds | `component_ontology`, `passenger_makes`, `non_passenger_models`, `make_aliases`, `model_aliases`. |

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
    fct_investigations ||--o{ bridge_investigation_recall : "action_number"
    fct_recalls ||--o{ bridge_investigation_recall : "campno (investigation led to recall)"
    fct_complaints ||--o{ bridge_complaint_recall_citation : "odino"
    fct_recalls |o--o{ bridge_complaint_recall_citation : "campno (owner cites recall)"
    dim_date ||--o{ fct_complaints : "received_date"
    dim_date ||--o{ fct_recalls : "recall_date"

    dim_vehicle {
        varchar vehicle_key PK "md5(make|model|year)"
        varchar make
        varchar model
        int model_year
        bool in_model_year_scope "model_year >= 2015"
        bigint n_complaints
        bigint n_recalls
        bigint n_tsbs
        bigint n_investigations
    }
    dim_date {
        date date_day PK
        date month_start
        int year
        int quarter
        int month
    }
    bridge_investigation_recall {
        varchar action_number FK
        varchar campno FK
    }
    bridge_complaint_recall_citation {
        varchar odino FK
        varchar campno "every campaign cited in the narrative"
        bool recall_in_marts
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
        bool harm_counts_implausible "self-reported counts nulled in staging"
        varchar narrative "free text, the early signal"
        varchar referenced_campno "first vehicle recall cited in the narrative"
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
        varchar investigation_id FK "first investigation opened"
        int n_investigations
    }
    fct_investigations {
        varchar action_number PK "PE / EA / DP / RQ"
        date opened_date
        date closed_date
        varchar campno FK "first resulting recall"
        int n_recalls
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
  `bridge_complaint_component`. `primary_component` is the most severe of them
  (ties broken alphabetically) and `primary_system` is that component's group.
* `fct_recalls`: one row per recall campaign (CAMPNO). The affected vehicles
  go into `bridge_recall_vehicle`, one row per campaign × make/model/year × component.
* The vehicle key is `md5(make|model|model_year)`. Complaints, recalls, TSBs
  and investigations all join on it after name normalisation (`norm_name`,
  `norm_model`, and the `make_aliases` / `model_aliases` seeds, e.g. `GLA250` → `GLA 250`).
  `dim_vehicle` holds every vehicle any fact or bridge references, and every
  `vehicle_key` is tested against it.
* Investigations and recalls are many-to-many (`bridge_investigation_recall`).
  `fct_recalls.investigation_id` / `fct_investigations.campno` keep the first link
  for convenience.

**Scope rules** (pitch slide 8). One vehicle-type rule for every table, computed once in staging
as `is_passenger_vehicle` (`macros/vehicle_scope.sql`):
* the make is on the `passenger_makes` allow-list (current and defunct passenger brands;
  RV, bus, trailer, motorcycle and truck makers are not on it), and
* the model does not match `non_passenger_models` (motorcycles, medium-duty trucks and stripped
  chassis sold under a passenger make, e.g. Honda Gold Wing, BMW R 1250 GS, Ford F-53 / F-650).

Model-year window, per table:
* `fct_complaints`: product type vehicle (`PROD_TYPE = 'V'`), model year 2015 or later, received
  2015 or later; complaints about tires, child seats or equipment only are dropped using
  `component_ontology.in_scope`.
* `fct_tsbs` / `bridge_tsb_vehicle`: model year 2015 or later.
* `fct_recalls`, `fct_investigations` and their bridges: all model years (the VIN check and the
  investigation timeline need older vehicles). `dim_vehicle.in_model_year_scope` marks MY2015+.

Self-reported injury counts above 20 and death counts above 10 (owners type the field maximum, 99,
in protest) are treated as unknown in staging and flagged with `harm_counts_implausible`.

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
    fct_complaints ||--o{ vehicle_component_indicator : "make, model, component"
    vehicle_component_indicator ||--o{ indicator_events : "make, model, component"
    clusters ||--o{ cluster_model_years : cluster_id

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
        bool poisson_spike
        double p_indicator
        bool paper_elevated
        bool is_spike "per config.DETECTOR"
        varchar detector }
    alerts { varchar cluster_id PK
        varchar status "EMERGING / RECALLED / WATCH"
        varchar affected_model_years
        double priority }
    backtest_results { varchar campno PK
        varchar detector
        date recall_date
        date first_alert_month
        double lead_months }
    vehicle_component_indicator { varchar make
        varchar model
        varchar component
        date month
        double n_12m
        double cohens_h
        double d_indicator
        double p_indicator
        bool is_elevated }
    indicator_events { varchar make
        varchar model
        varchar component
        date start_month
        date end_month
        double event_complaints }
    cluster_model_years { varchar cluster_id
        int model_year
        int n
        double p_value
        bool is_elevated }
```

The ML layer lives in Snowflake schema `VROOMCHECK.ML`, with embeddings and cluster
centroids stored as `VECTOR(FLOAT, 384)`. Embeddings, clusters, members and briefs are
permanent tables; everything that can be rebuilt from them is transient.

`vehicle_component_indicator`, `indicator_events` and `cluster_model_years` implement
the method of ESV26-289 (Jones et al., *A Check Engine Light for the Automotive Industry*):
the fleet-normalised D/P indicator with its 6-month sustained-elevation test, event
merging, and the binomial test for affected model years. `ml.detector_comparison`
holds the before/after back-test from `python -m vroomcheck.detection.compare`, and
`ml_before` keeps the original detector's results.

## Data quality

| Where | Check |
|---|---|
| Ingestion gate (`raw._dq_results`) | row counts, primary-key nulls, unparseable dates, load rejects, missing narratives, future dates, CAMPNO format |
| dbt tests | unique and not-null keys on every fact, dimension and seed; referential integrity from every bridge to its fact and to `dim_vehicle`, between investigations and recalls, and from fact dates to `dim_date`; harm counts within plausible limits; accepted investigation types (warn); investigation close date not before open date (warn); complaint received no more than 2 years before its model year (warn); failure date not after received date (warn); near-duplicate model names that should be aliased (warn); cited vehicle campaigns that do not exist (warn) |
