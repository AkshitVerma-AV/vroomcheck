-- One row per bulletin x vehicle x component (the TSV fans out multi-valued fields).
with typed as (
    select
        trim(NHTSA_ID)                                 as nhtsa_id,
        trim(TSB_DOCUMENT_ID)                          as tsb_document_id,
        {{ nhtsa_date('MFR_COMM_DATE') }}              as communication_date,
        trim(COMM_TYPE)                                as communication_type,
        {{ canonical_make(norm_name('src.MAKE')) }}        as make,
        {{ canonical_model(canonical_make(norm_name('src.MAKE')), norm_model('src.MODEL')) }} as model,
        {{ model_year('MODEL_YEAR') }}                 as model_year,
        {{ norm_name('NHTSA_COMPONENTS') }}            as component_path,
        trim(split_part({{ norm_name('NHTSA_COMPONENTS') }}, ':', 1)) as component_top,
        trim(MFR_COMPONENT_SYSTEM)                     as mfr_component_system,
        trim(SUMMARY)                                  as summary
    from {{ source('raw', 'raw_tsbs') }} as src
    where NHTSA_ID is not null and regexp_matches(trim(NHTSA_ID), '^[0-9]+$')
)
select t.*, {{ is_passenger_vehicle('t.make', 't.model') }} as is_passenger_vehicle
from typed t
