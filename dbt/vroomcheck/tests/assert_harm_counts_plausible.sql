-- Implausible self-reported injury / death counts must have been nulled in staging.
select odino, injured, deaths
from {{ ref('fct_complaints') }}
where injured > {{ var('max_plausible_injured') }}
   or deaths > {{ var('max_plausible_deaths') }}
   or injured < 0 or deaths < 0
