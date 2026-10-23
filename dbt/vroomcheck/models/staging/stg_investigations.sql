-- One row per investigation x vehicle x component.
select
    trim(ACTION_NUMBER)                            as action_number,
    left(trim(ACTION_NUMBER), 2)                   as investigation_type,  -- PE, EA, DP, RQ, AQ
    {{ norm_name('MAKE') }}                        as make,
    {{ norm_name('MODEL') }}                       as model,
    {{ model_year('YEAR') }}                       as model_year,
    {{ norm_name('COMPNAME') }}                    as component_path,
    trim(split_part({{ norm_name('COMPNAME') }}, ':', 1)) as component_top,
    {{ norm_name('MFR_NAME') }}                    as mfr_name,
    {{ nhtsa_date('ODATE') }}                      as opened_date,
    {{ nhtsa_date('CDATE') }}                      as closed_date,
    nullif(trim(CAMPNO), '')                       as campno,
    trim(SUBJECT)                                  as subject,
    trim(SUMMARY)                                  as summary
from {{ source('raw', 'raw_investigations') }}
where ACTION_NUMBER is not null
