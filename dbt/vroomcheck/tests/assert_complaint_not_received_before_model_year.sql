-- A complaint received more than 2 years before its model year is almost certainly a data-entry error.
{{ config(severity='warn') }}
select odino, model_year, received_date
from {{ ref('fct_complaints') }}
where year(received_date) < model_year - 2
