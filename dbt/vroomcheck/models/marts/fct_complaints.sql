-- One row per complaint (ODINO), restricted to the project scope:
-- passenger vehicles (not tires / child seats / equipment, motorcycles, RVs or commercial
-- trucks), model year >= 2015, received >= 2015.
with base as (
    select c.*, o.system_group, o.severity_weight, coalesce(o.in_scope, true) as component_in_scope
    from {{ ref('stg_complaints') }} c
    left join {{ ref('component_ontology') }} o using (component_top)
    where c.product_type = 'V'
      and c.is_passenger_vehicle
      and c.model_year >= {{ var('min_model_year') }}
      and year(c.received_date) >= {{ var('min_received_year') }}
      and c.make is not null and c.model is not null
),
-- most severe component; 31k complaints tie on severity, so break ties alphabetically
-- and take both columns from the same row to keep runs reproducible
primary_component as (
    select odino, component_top as primary_component, system_group as primary_system
    from base
    qualify row_number() over (partition by odino
                               order by coalesce(severity_weight, 0.3) desc, component_top nulls last) = 1
),
per_complaint as (
    select
        odino,
        min(make)                           as make,
        min(model)                          as model,
        min(model_year)                     as model_year,
        min(mfr_name)                       as mfr_name,
        min(received_date)                  as received_date,
        min(fail_date)                      as fail_date,
        {{ dbt.bool_or('is_crash') }}       as is_crash,
        {{ dbt.bool_or('is_fire') }}        as is_fire,
        coalesce(max(injured), 0)           as injured,
        coalesce(max(deaths), 0)            as deaths,
        {{ dbt.bool_or('harm_counts_implausible') }} as harm_counts_implausible,
        {{ dbt.bool_or('medical_attention') }} as medical_attention,
        {{ dbt.bool_or('towed') }}          as towed,
        max(miles)                          as miles,
        max(vehicle_speed)                  as vehicle_speed,
        min(vin_prefix)                     as vin_prefix,
        min(state)                          as state,
        min(complaint_source)               as complaint_source,
        min(fuel_type)                      as fuel_type,
        max(narrative)                      as narrative,
        count(*)                            as n_components,
        {{ string_agg_distinct('component_top', ' | ') }}  as component_tops,
        {{ string_agg_distinct('component_path', ' | ') }} as component_paths,
        {{ dbt.bool_or('component_in_scope') }} as in_scope
    from base
    group by odino
)
select
    c.*,
    p.primary_component,
    p.primary_system,
    md5(c.make || '|' || c.model || '|' || c.model_year::varchar) as vehicle_key,
    date_trunc('month', c.received_date)::date                as received_month,
    -- simple harm score used to prioritise clusters (crash / fire / injury / death)
    1 + 3 * c.is_crash::int + 3 * c.is_fire::int + 2 * (c.injured > 0)::int + 5 * (c.deaths > 0)::int
      + 1 * c.towed::int                                       as harm_score,
    -- recall campaign the owner explicitly cites, e.g. "NHTSA Campaign Number: 25V869000"
    {{ regex_first_match('c.narrative', '[0-9]{2}V[0-9]{3}000') }} as referenced_campno,
    -- "remedy not available" call-centre template: about a known recall's repair delay,
    -- not a new failure, so it is excluded from defect detection
    (c.narrative ilike '%part to do the recall repair was not%'
     or c.narrative ilike '%exceeded a reasonable amount of time for the recall%'
     or (c.narrative ilike '%received notification of%campaign%' and c.narrative ilike '%not experienced a failure%'))
                                                               as is_remedy_complaint
from per_complaint c
join primary_component p using (odino)
where c.in_scope
  and c.narrative is not null and length(c.narrative) >= 20
