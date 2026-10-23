-- Two model names for the same make and year that differ only by spaces or hyphens
-- (e.g. E-GOLF vs EGOLF) split one vehicle into two keys. Add the pair to model_aliases.
{{ config(severity='warn') }}
with v as (
    select make, model, model_year, regexp_replace(model, '[\s-]', '', 'g') as squashed
    from {{ ref('dim_vehicle') }}
)
select a.make, a.model, b.model as variant, a.model_year
from v a
join v b on a.make = b.make and a.model_year = b.model_year
        and a.squashed = b.squashed and a.model < b.model
