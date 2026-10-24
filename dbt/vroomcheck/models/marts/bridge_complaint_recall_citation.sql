-- Complaint x every recall campaign number the owner cites in the narrative
-- (fct_complaints.referenced_campno keeps only the first vehicle campaign).
-- Campaign types: V vehicle, E equipment, I/C child restraint, T tire.
with cited as (
    {{ regex_match_rows(ref('fct_complaints'), 'odino', 'narrative', '[0-9]{2}[VEICT][0-9]{3}000', 'campno') }}
)
select c.odino,
       c.campno,
       r.campno is not null as recall_in_marts  -- false for non-vehicle campaigns and typos
from cited c
left join {{ ref('fct_recalls') }} r using (campno)
