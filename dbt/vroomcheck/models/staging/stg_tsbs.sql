-- One row per bulletin x vehicle x component (the TSV fans out multi-valued fields).
select
    trim(NHTSA_ID)                                 as nhtsa_id,
    trim(TSB_DOCUMENT_ID)                          as tsb_document_id,
    {{ nhtsa_date('MFR_COMM_DATE') }}              as communication_date,
    trim(COMM_TYPE)                                as communication_type,
    {{ norm_name('MAKE') }}                        as make,
    {{ norm_name('MODEL') }}                       as model,
    {{ model_year('MODEL_YEAR') }}                 as model_year,
    {{ norm_name('NHTSA_COMPONENTS') }}            as component_path,
    trim(split_part({{ norm_name('NHTSA_COMPONENTS') }}, ':', 1)) as component_top,
    trim(MFR_COMPONENT_SYSTEM)                     as mfr_component_system,
    trim(SUMMARY)                                  as summary
from {{ source('raw', 'raw_tsbs') }}
where NHTSA_ID is not null and regexp_matches(trim(NHTSA_ID), '^[0-9]+$')
