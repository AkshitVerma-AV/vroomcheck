-- One row per NHTSA defect investigation that touches at least one passenger vehicle.
-- An investigation can lead to several recalls (see bridge_investigation_recall);
-- campno is the first of them that is a recall in fct_recalls.
with i as (
    select * from {{ ref('stg_investigations') }}
    where is_passenger_vehicle
),
recalls as (
    select action_number, min(campno) as campno, count(distinct campno) as n_recalls
    from i
    where campno in (select campno from {{ ref('fct_recalls') }})
    group by action_number
)
select
    i.action_number,
    min(i.investigation_type)                           as investigation_type,
    min(i.opened_date)                                  as opened_date,
    max(i.closed_date)                                  as closed_date,
    min(r.campno)                                       as campno,
    coalesce(min(r.n_recalls), 0)                       as n_recalls,
    -- 77 investigations span several manufacturers; min() always picks the same one
    min(i.mfr_name)                                     as mfr_name,
    {{ string_agg_distinct('i.make', ', ') }}           as makes,
    {{ string_agg_distinct('i.model', ', ') }}          as models,
    min(i.model_year)                                   as min_model_year,
    max(i.model_year)                                   as max_model_year,
    {{ string_agg_distinct('i.component_top', ' | ') }} as component_tops,
    min(i.subject)                                      as subject,
    max(i.summary)                                      as summary
from i
left join recalls r using (action_number)
group by i.action_number
