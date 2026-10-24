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
per_complaint as (
    select
        odino,
        any_value(make)                     as make,
        any_value(model)                    as model,
        any_value(model_year)               as model_year,
        any_value(mfr_name)                 as mfr_name,
        min(received_date)                  as received_date,
        min(fail_date)                      as fail_date,
        bool_or(is_crash)                   as is_crash,
        bool_or(is_fire)                    as is_fire,
        coalesce(max(injured), 0)           as injured,
        coalesce(max(deaths), 0)            as deaths,
        bool_or(harm_counts_implausible)    as harm_counts_implausible,
        bool_or(medical_attention)          as medical_attention,
        bool_or(towed)                      as towed,
        max(miles)                          as miles,
        max(vehicle_speed)                  as vehicle_speed,
        any_value(vin_prefix)               as vin_prefix,
        any_value(state)                    as state,
        any_value(complaint_source)         as complaint_source,
        any_value(fuel_type)                as fuel_type,
        max(narrative)                      as narrative,
        count(*)                            as n_components,
        string_agg(distinct component_top, ' | ' order by component_top) as component_tops,
        string_agg(distinct component_path, ' | ' order by component_path) as component_paths,
        -- most severe component; 31k complaints tie on severity, so break ties alphabetically
        -- and take both columns from the same row to keep runs reproducible
        first(component_top order by coalesce(severity_weight, 0.3) desc, component_top nulls last)
                                            as primary_component,
        first(system_group order by coalesce(severity_weight, 0.3) desc, component_top nulls last)
                                            as primary_system,
        bool_or(component_in_scope)         as in_scope
    from base
    group by odino
)
select
    *,
    md5(make || '|' || model || '|' || model_year::varchar) as vehicle_key,
    date_trunc('month', received_date)::date                as received_month,
    -- simple harm score used to prioritise clusters (crash / fire / injury / death)
    1 + 3 * is_crash::int + 3 * is_fire::int + 2 * (injured > 0)::int + 5 * (deaths > 0)::int
      + 1 * towed::int                                       as harm_score,
    -- recall campaign the owner explicitly cites, e.g. "NHTSA Campaign Number: 25V869000"
    nullif(regexp_extract(narrative, '(\d{2}V\d{3}000)', 1), '') as referenced_campno,
    -- "remedy not available" call-centre template: about a known recall's repair delay,
    -- not a new failure, so it is excluded from defect detection
    (narrative ilike '%part to do the recall repair was not%'
     or narrative ilike '%exceeded a reasonable amount of time for the recall%'
     or (narrative ilike '%received notification of%campaign%' and narrative ilike '%not experienced a failure%'))
                                                             as is_remedy_complaint
from per_complaint
where in_scope
  and narrative is not null and length(narrative) >= 20
