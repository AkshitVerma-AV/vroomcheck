-- Complaint x component (a complaint can name several components).
select distinct
    c.odino,
    c.component_path,
    c.component_top,
    o.system_group
from {{ ref('stg_complaints') }} c
join {{ ref('fct_complaints') }} f using (odino)
left join {{ ref('component_ontology') }} o using (component_top)
where c.component_path is not null
