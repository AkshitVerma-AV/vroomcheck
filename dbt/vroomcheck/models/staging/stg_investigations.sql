-- One row per investigation x vehicle x component.
with typed as (
    select
        trim(ACTION_NUMBER)                            as action_number,
        left(trim(ACTION_NUMBER), 2)                   as investigation_type,  -- PE, EA, DP, RQ, AQ
        {{ canonical_make(norm_name('src.MAKE')) }}        as make,
        {{ canonical_model(canonical_make(norm_name('src.MAKE')), norm_model('src.MODEL')) }} as model,
        {{ model_year('YEAR') }}                       as model_year,
        {{ norm_name('COMPNAME') }}                    as component_path,
        trim(split_part({{ norm_name('COMPNAME') }}, ':', 1)) as component_top,
        {{ norm_name('MFR_NAME') }}                    as mfr_name,
        {{ nhtsa_date('ODATE') }}                      as opened_date,
        {{ nhtsa_date('CDATE') }}                      as closed_date,
        nullif(trim(CAMPNO), '')                       as campno,
        trim(SUBJECT)                                  as subject,
        trim(SUMMARY)                                  as summary
    from {{ source('raw', 'raw_investigations') }} as src
    where ACTION_NUMBER is not null
)
select t.*, {{ is_passenger_vehicle('t.make', 't.model') }} as is_passenger_vehicle
from typed t
