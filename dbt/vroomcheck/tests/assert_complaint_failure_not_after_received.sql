-- A failure cannot happen after NHTSA received the complaint about it; source-data anomaly, so warn.
{{ config(severity='warn') }}
select odino, fail_date, received_date
from {{ ref('fct_complaints') }}
where fail_date > received_date
