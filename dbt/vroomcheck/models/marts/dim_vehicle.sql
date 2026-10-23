-- Conformed vehicle dimension: every passenger (make, model, model year) referenced by any
-- fact or bridge, so every vehicle_key in the marts resolves here. Model-year scope is an
-- attribute (in_model_year_scope) rather than a filter, because recalls and investigations
-- keep older model years for the VIN check and the investigation timeline.
with keys as (
    select vehicle_key, make, model, model_year from {{ ref('fct_complaints') }}
    union
    select vehicle_key, make, model, model_year from {{ ref('bridge_recall_vehicle') }}
    union
    select vehicle_key, make, model, model_year from {{ ref('bridge_tsb_vehicle') }}
    union
    select vehicle_key, make, model, model_year from {{ ref('bridge_investigation_vehicle') }}
),
cmpl as (
    select vehicle_key, count(*) as n_complaints,
           sum(is_crash::int) as n_crashes, sum(is_fire::int) as n_fires,
           sum(injured) as n_injured, sum(deaths) as n_deaths
    from {{ ref('fct_complaints') }} group by 1
),
rcl as (
    select vehicle_key, count(distinct campno) as n_recalls
    from {{ ref('bridge_recall_vehicle') }} group by 1
),
tsb as (
    select vehicle_key, count(distinct nhtsa_id) as n_tsbs
    from {{ ref('bridge_tsb_vehicle') }} group by 1
),
inv as (
    select vehicle_key, count(distinct action_number) as n_investigations
    from {{ ref('bridge_investigation_vehicle') }} group by 1
)
select k.vehicle_key, k.make, k.model, k.model_year,
       k.model_year >= {{ var('min_model_year') }} as in_model_year_scope,
       coalesce(c.n_complaints, 0)::bigint     as n_complaints,
       coalesce(c.n_crashes, 0)::bigint        as n_crashes,
       coalesce(c.n_fires, 0)::bigint          as n_fires,
       coalesce(c.n_injured, 0)::bigint        as n_injured,
       coalesce(c.n_deaths, 0)::bigint         as n_deaths,
       coalesce(r.n_recalls, 0)::bigint        as n_recalls,
       coalesce(t.n_tsbs, 0)::bigint           as n_tsbs,
       coalesce(i.n_investigations, 0)::bigint as n_investigations
from keys k
left join cmpl c using (vehicle_key)
left join rcl r using (vehicle_key)
left join tsb t using (vehicle_key)
left join inv i using (vehicle_key)
