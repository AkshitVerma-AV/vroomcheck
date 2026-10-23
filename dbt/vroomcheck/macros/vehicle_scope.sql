{# Vehicle naming and scope rules shared by every staging model, so complaints,
   recalls, investigations and bulletins resolve to the same vehicle and are
   kept or dropped by exactly the same rule.

   Pass table-qualified column references (e.g. 'src.MAKE', 't.make'): the seed tables
   have make / model columns too, and an unqualified name inside these correlated
   subqueries would bind to the seed instead of the row being classified. #}

{# NHTSA marks superseded product records with a "REDUNDANT " prefix; they are the same vehicle. #}
{% macro norm_model(col) -%}
    nullif(regexp_replace({{ norm_name(col) }}, '^REDUNDANT ', ''), '')
{%- endmacro %}

{# Map known spelling variants ("MERCEDES BENZ") to one name, via the make_aliases seed. #}
{% macro canonical_make(make) -%}
    coalesce((select _ma.make from {{ ref('make_aliases') }} _ma where _ma.alias = {{ make }}), {{ make }})
{%- endmacro %}

{# Map known model spelling variants ("EGOLF") to one name, via the model_aliases seed. #}
{% macro canonical_model(make, model) -%}
    coalesce((select _mo.model from {{ ref('model_aliases') }} _mo
              where _mo.make = {{ make }} and _mo.alias = {{ model }}), {{ model }})
{%- endmacro %}

{# Passenger vehicle = an allow-listed passenger make, minus the motorcycles, medium-duty
   trucks and stripped chassis those makes also sell (non_passenger_models seed). #}
{% macro is_passenger_vehicle(make, model) -%}
    coalesce(
        {{ make }} in (select _pm.make from {{ ref('passenger_makes') }} _pm)
        and not exists (select 1 from {{ ref('non_passenger_models') }} _np
                        where _np.make = {{ make }} and {{ model }} like _np.model_pattern),
        false)
{%- endmacro %}
