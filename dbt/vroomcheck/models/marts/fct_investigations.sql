-- One row per NHTSA defect investigation.
select
    action_number,
    any_value(investigation_type)                       as investigation_type,
    min(opened_date)                                    as opened_date,
    max(closed_date)                                    as closed_date,
    any_value(campno)                                   as campno,
    any_value(mfr_name)                                 as mfr_name,
    string_agg(distinct make, ', ' order by make)       as makes,
    string_agg(distinct model, ', ' order by model)     as models,
    min(model_year)                                     as min_model_year,
    max(model_year)                                     as max_model_year,
    string_agg(distinct component_top, ' | ' order by component_top) as component_tops,
    any_value(subject)                                  as subject,
    max(summary)                                        as summary
from {{ ref('stg_investigations') }}
group by action_number
