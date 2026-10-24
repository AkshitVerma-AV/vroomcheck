-- One row per bulletin x vehicle x component (the TSV fans out multi-valued fields).
with typed as (
    select
        {{ ws_trim("NHTSA_ID") }}                                 as nhtsa_id,
        {{ ws_trim("TSB_DOCUMENT_ID") }}                          as tsb_document_id,
        {{ nhtsa_date('MFR_COMM_DATE') }}              as communication_date,
        {{ ws_trim("COMM_TYPE") }}                                as communication_type,
        {{ norm_name('src.MAKE') }}    as make_raw,
        {{ norm_model('src.MODEL') }} as model_raw,
        {{ model_year('MODEL_YEAR') }}                 as model_year,
        {{ norm_name('NHTSA_COMPONENTS') }}            as component_path,
        {{ ws_trim("split_part(" ~ norm_name('NHTSA_COMPONENTS') ~ ", ':', 1)") }} as component_top,
        {{ ws_trim("MFR_COMPONENT_SYSTEM") }}                     as mfr_component_system,
        {{ ws_trim("SUMMARY") }}                                  as summary
    from {{ source('raw', 'raw_tsbs') }} as src
    where NHTSA_ID is not null and {{ regex_like(ws_trim('NHTSA_ID'), '^[0-9]+$') }}
),
{{ canonical_vehicles('typed') }}
