-- Complaint x every recall campaign number the owner cites in the narrative
-- (fct_complaints.referenced_campno keeps only the first vehicle campaign).
-- Campaign types: V vehicle, E equipment, I/C child restraint, T tire.
with cited as (
    select odino,
           unnest(list_distinct(regexp_extract_all(narrative, '\d{2}[VEICT]\d{3}000'))) as campno
    from {{ ref('fct_complaints') }}
)
select c.odino,
       c.campno,
       r.campno is not null as recall_in_marts  -- false for non-vehicle campaigns and typos
from cited c
left join {{ ref('fct_recalls') }} r using (campno)
