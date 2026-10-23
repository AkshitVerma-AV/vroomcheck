select distinct
    action_number,
    md5(make || '|' || model || '|' || model_year::varchar) as vehicle_key,
    make, model, model_year, component_top
from {{ ref('stg_investigations') }}
where make is not null and model is not null and model_year is not null
