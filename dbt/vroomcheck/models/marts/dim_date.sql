-- Calendar dimension, one row per day, so month / quarter / year logic has one definition.
-- Covers the oldest investigation (1970s) through the end of 2030.
select
    d::date                         as date_day,
    date_trunc('month', d)::date    as month_start,
    date_trunc('quarter', d)::date  as quarter_start,
    year(d)                         as year,
    quarter(d)                      as quarter,
    month(d)                        as month,
    monthname(d)                    as month_name,
    isodow(d)                       as iso_day_of_week,
    isodow(d) >= 6                  as is_weekend
from range(date '1970-01-01', date '2031-01-01', interval 1 day) as t(d)
