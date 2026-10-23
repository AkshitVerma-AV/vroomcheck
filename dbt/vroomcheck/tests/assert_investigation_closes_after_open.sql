-- Source-data anomaly check: NHTSA has a handful of historical investigations whose close
-- date precedes the open date. We do not own the source, so this warns instead of failing.
{{ config(severity='warn') }}
select action_number, opened_date, closed_date
from {{ ref('fct_investigations') }}
where closed_date < opened_date
