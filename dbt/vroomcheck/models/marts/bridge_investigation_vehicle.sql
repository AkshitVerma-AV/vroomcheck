-- Investigation x passenger vehicle x component.
select distinct
    action_number,
    md5(make || '|' || model || '|' || model_year::varchar) as vehicle_key,
    make, model, model_year, component_top
from {{ ref('stg_investigations') }}
where is_passenger_vehicle
  and make is not null and model is not null and model_year is not null
