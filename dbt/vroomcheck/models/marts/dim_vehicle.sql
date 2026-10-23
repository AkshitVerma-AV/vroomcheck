-- Conformed vehicle dimension: every (make, model, model year) seen in complaints or recalls.
with keys as (
    select vehicle_key, make, model, model_year from {{ ref('fct_complaints') }}
    union
    select vehicle_key, make, model, model_year from {{ ref('bridge_recall_vehicle') }}
    where model_year >= {{ var('min_model_year') }}
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
)
select k.vehicle_key, k.make, k.model, k.model_year,
       coalesce(c.n_complaints, 0) as n_complaints,
       coalesce(c.n_crashes, 0)    as n_crashes,
       coalesce(c.n_fires, 0)      as n_fires,
       coalesce(c.n_injured, 0)    as n_injured,
       coalesce(c.n_deaths, 0)     as n_deaths,
       coalesce(r.n_recalls, 0)    as n_recalls
from (select distinct * from keys) k
left join cmpl c using (vehicle_key)
left join rcl r using (vehicle_key)
