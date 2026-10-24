-- primary_system must be the ontology system group of primary_component (both come from the same row).
select f.odino, f.primary_component, f.primary_system, o.system_group
from {{ ref('fct_complaints') }} f
join {{ ref('component_ontology') }} o on o.component_top = f.primary_component
where f.primary_system is distinct from o.system_group
