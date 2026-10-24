{# Vehicle naming and scope rules shared by every staging model, so complaints,
   recalls, investigations and bulletins resolve to the same vehicle and are
   kept or dropped by exactly the same rule. Written as plain joins so the same
   SQL runs on DuckDB and Snowflake. #}

{# NHTSA marks superseded product records with a "REDUNDANT " prefix; they are the same vehicle. #}
{% macro norm_model(col) -%}
    nullif(regexp_replace({{ norm_name(col) }}, '^REDUNDANT ', ''), '')
{%- endmacro %}

{# Continues a WITH clause after a CTE (typed_cte) that has make_raw / model_raw columns
   and ends with the final SELECT. Output: every typed column, plus
     make, model            canonical names (make_aliases / model_aliases seeds)
     is_passenger_vehicle   make is on passenger_makes and the model does not match
                            non_passenger_models (motorcycles, medium-duty trucks,
                            stripped chassis sold under a passenger make)
   Usage:  with typed as (...), {{ canonical_vehicles('typed') }} #}
{% macro canonical_vehicles(typed_cte) -%}
_vehicle_named as (
    select t.*, coalesce(ma.make, t.make_raw) as make
    from {{ typed_cte }} t
    left join {{ ref('make_aliases') }} ma on ma.alias = t.make_raw
),
_vehicle_canonical as (
    select n.* exclude (make_raw, model_raw), coalesce(mo.model, n.model_raw) as model
    from _vehicle_named n
    left join {{ ref('model_aliases') }} mo on mo.make = n.make and mo.alias = n.model_raw
),
_vehicle_excluded as (
    select distinct v.make, v.model
    from (select distinct make, model from _vehicle_canonical) v
    join {{ ref('non_passenger_models') }} x on x.make = v.make and v.model like x.model_pattern
)
select c.*,
       coalesce(pm.make is not null and e.make is null, false) as is_passenger_vehicle
from _vehicle_canonical c
left join {{ ref('passenger_makes') }} pm on pm.make = c.make
left join _vehicle_excluded e on e.make = c.make and e.model = c.model
{%- endmacro %}
