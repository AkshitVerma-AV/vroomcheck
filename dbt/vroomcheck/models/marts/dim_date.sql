-- Calendar dimension, one row per day, so month / quarter / year logic has one definition.
-- Covers the oldest investigation (1970s) through the end of 2030.
with days as (
    {{ date_spine_days('1970-01-01', '2031-01-01') }}
)
select
    d                               as date_day,
    date_trunc('month', d)::date    as month_start,
    date_trunc('quarter', d)::date  as quarter_start,
    year(d)                         as year,
    quarter(d)                      as quarter,
    month(d)                        as month,
    {{ month_name('d') }}           as month_name,
    {{ iso_day_of_week('d') }}      as iso_day_of_week,
    {{ iso_day_of_week('d') }} >= 6 as is_weekend
from days
