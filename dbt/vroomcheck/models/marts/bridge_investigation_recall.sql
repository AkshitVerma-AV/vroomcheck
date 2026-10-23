-- Investigation x the recall campaign(s) it led to. Many-to-many: one investigation can
-- produce several recalls and one recall can follow several investigations.
select distinct
    i.action_number,
    i.campno
from {{ ref('stg_investigations') }} i
where i.campno is not null
  and i.action_number in (select action_number from {{ ref('fct_investigations') }})
  and i.campno in (select campno from {{ ref('fct_recalls') }})
