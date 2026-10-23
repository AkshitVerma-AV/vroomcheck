-- One row per manufacturer communication / service bulletin touching an in-scope
-- (passenger vehicle, model year >= 2015) vehicle.
select
    nhtsa_id,
    any_value(tsb_document_id)                          as tsb_document_id,
    min(communication_date)                             as communication_date,
    any_value(communication_type)                       as communication_type,
    string_agg(distinct make, ', ' order by make)       as makes,
    string_agg(distinct component_top, ' | ' order by component_top) as component_tops,
    max(summary)                                        as summary
from {{ ref('stg_tsbs') }}
where model_year >= {{ var('min_model_year') }}
  and is_passenger_vehicle
group by nhtsa_id
