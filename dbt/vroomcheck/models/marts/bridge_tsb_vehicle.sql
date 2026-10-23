select distinct
    nhtsa_id,
    md5(make || '|' || model || '|' || model_year::varchar) as vehicle_key,
    make, model, model_year, component_top
from {{ ref('stg_tsbs') }}
where model_year >= {{ var('min_model_year') }}
  and make is not null and model is not null
