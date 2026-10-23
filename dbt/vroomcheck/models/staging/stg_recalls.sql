-- One row per recall campaign x vehicle x component.
select
    try_cast(RECORD_ID as bigint)                  as record_id,
    trim(CAMPNO)                                   as campno,
    {{ norm_name('MAKETXT') }}                     as make,
    {{ norm_name('MODELTXT') }}                    as model,
    {{ model_year('YEARTXT') }}                    as model_year,
    trim(MFGCAMPNO)                                as mfr_campaign_no,
    {{ norm_name('COMPNAME') }}                    as component_path,
    trim(split_part({{ norm_name('COMPNAME') }}, ':', 1)) as component_top,
    {{ norm_name('MFGNAME') }}                     as mfr_name,
    {{ nhtsa_date('BGMAN') }}                      as mfg_begin_date,
    {{ nhtsa_date('ENDMAN') }}                     as mfg_end_date,
    trim(RCLTYPECD)                                as recall_type,
    try_cast(POTAFF as bigint)                     as potentially_affected,
    {{ nhtsa_date('ODATE') }}                      as owner_notify_date,
    trim(INFLUENCED_BY)                            as influenced_by,
    {{ nhtsa_date('RCDATE') }}                     as report_received_date,
    trim(DESC_DEFECT)                              as defect_summary,
    trim(CONEQUENCE_DEFECT)                        as consequence_summary,
    trim(CORRECTIVE_ACTION)                        as remedy_summary,
    trim(NOTES)                                    as notes,
    upper(trim(DO_NOT_DRIVE)) in ('Y', 'YES')      as do_not_drive,
    upper(trim(PARK_OUTSIDE)) in ('Y', 'YES')      as park_outside,
    _source_file
from {{ source('raw', 'raw_recalls') }}
where CAMPNO is not null
