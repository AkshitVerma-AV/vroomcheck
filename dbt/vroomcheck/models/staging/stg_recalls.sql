-- One row per recall campaign x vehicle x component.
with typed as (
    select
        try_cast(RECORD_ID as bigint)                  as record_id,
        {{ ws_trim("CAMPNO") }}                                   as campno,
        {{ norm_name('src.MAKETXT') }} as make_raw,
        {{ norm_model('src.MODELTXT') }} as model_raw,
        {{ model_year('YEARTXT') }}                    as model_year,
        {{ ws_trim("MFGCAMPNO") }}                                as mfr_campaign_no,
        {{ norm_name('COMPNAME') }}                    as component_path,
        {{ ws_trim("split_part(" ~ norm_name('COMPNAME') ~ ", ':', 1)") }} as component_top,
        {{ norm_name('MFGNAME') }}                     as mfr_name,
        {{ nhtsa_date('BGMAN') }}                      as mfg_begin_date,
        {{ nhtsa_date('ENDMAN') }}                     as mfg_end_date,
        {{ ws_trim("RCLTYPECD") }}                                as recall_type,
        try_cast(POTAFF as bigint)                     as potentially_affected,
        {{ nhtsa_date('ODATE') }}                      as owner_notify_date,
        {{ ws_trim("INFLUENCED_BY") }}                            as influenced_by,
        {{ nhtsa_date('RCDATE') }}                     as report_received_date,
        {{ ws_trim("DESC_DEFECT") }}                              as defect_summary,
        {{ ws_trim("CONEQUENCE_DEFECT") }}                        as consequence_summary,
        {{ ws_trim("CORRECTIVE_ACTION") }}                        as remedy_summary,
        {{ ws_trim("NOTES") }}                                    as notes,
        upper({{ ws_trim("DO_NOT_DRIVE") }}) in ('Y', 'YES')      as do_not_drive,
        upper({{ ws_trim("PARK_OUTSIDE") }}) in ('Y', 'YES')      as park_outside,
        _source_file
    from {{ source('raw', 'raw_recalls') }} as src
    where CAMPNO is not null
),
{{ canonical_vehicles('typed') }}
