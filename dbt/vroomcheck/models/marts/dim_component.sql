-- Component ontology: NHTSA top-level component -> system group, severity weight, scope flag.
-- Components seen in the data but missing from the seed are surfaced as 'Unmapped'.
with observed as (
    select component_top, count(*) as n_complaint_rows
    from {{ ref('stg_complaints') }}
    where component_top is not null
    group by 1
)
select
    coalesce(o.component_top, s.component_top) as component_top,
    coalesce(s.system_group, 'Unmapped')       as system_group,
    coalesce(s.severity_weight, 0.3)           as severity_weight,
    coalesce(s.in_scope, true)                 as in_scope,
    coalesce(o.n_complaint_rows, 0)            as n_complaint_rows
from observed o
full outer join {{ ref('component_ontology') }} s using (component_top)
