-- Recall campaign x affected passenger vehicle (make, model, model year) x component.
select distinct
    campno,
    md5(make || '|' || model || '|' || model_year::varchar) as vehicle_key,
    make,
    model,
    model_year,
    component_top,
    component_path
from {{ ref('stg_recalls') }}
where recall_type = 'V'
  and is_passenger_vehicle
  and make is not null and model is not null and model_year is not null
