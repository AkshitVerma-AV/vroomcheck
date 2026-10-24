-- One row per investigation x vehicle x component.
with typed as (
    select
        {{ ws_trim("ACTION_NUMBER") }}                            as action_number,
        left({{ ws_trim("ACTION_NUMBER") }}, 2)                   as investigation_type,  -- PE, EA, DP, RQ, AQ
        {{ norm_name('src.MAKE') }}    as make_raw,
        {{ norm_model('src.MODEL') }} as model_raw,
        {{ model_year('YEAR') }}                       as model_year,
        {{ norm_name('COMPNAME') }}                    as component_path,
        {{ ws_trim("split_part(" ~ norm_name('COMPNAME') ~ ", ':', 1)") }} as component_top,
        {{ norm_name('MFR_NAME') }}                    as mfr_name,
        {{ nhtsa_date('ODATE') }}                      as opened_date,
        {{ nhtsa_date('CDATE') }}                      as closed_date,
        nullif({{ ws_trim("CAMPNO") }}, '')                       as campno,
        {{ ws_trim("SUBJECT") }}                                  as subject,
        {{ ws_trim("SUMMARY") }}                                  as summary
    from {{ source('raw', 'raw_investigations') }} as src
    where ACTION_NUMBER is not null
),
{{ canonical_vehicles('typed') }}
