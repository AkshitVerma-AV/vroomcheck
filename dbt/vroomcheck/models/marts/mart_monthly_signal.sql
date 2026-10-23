-- Monthly complaint volume per vehicle model line x component: the input to
-- the component-level spike view in the dashboard.
select
    make,
    model,
    primary_component           as component_top,
    received_month,
    count(*)                    as n_complaints,
    sum(is_crash::int)          as n_crashes,
    sum(is_fire::int)           as n_fires,
    sum(injured)                as n_injured,
    sum(deaths)                 as n_deaths,
    sum(harm_score)             as harm_points
from {{ ref('fct_complaints') }}
group by all
