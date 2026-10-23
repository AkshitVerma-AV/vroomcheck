-- One row per complaint x component, typed and normalised.
with typed as (
    select
        trim(CMPLID)                                   as cmplid,
        trim(ODINO)                                    as odino,
        {{ norm_name('MFR_NAME') }}                    as mfr_name,
        {{ canonical_make(norm_name('src.MAKETXT')) }}     as make,
        {{ canonical_model(canonical_make(norm_name('src.MAKETXT')), norm_model('src.MODELTXT')) }} as model,
        {{ model_year('YEARTXT') }}                    as model_year,
        {{ norm_name('COMPDESC') }}                    as component_path,
        trim(split_part({{ norm_name('COMPDESC') }}, ':', 1)) as component_top,
        CRASH = 'Y'                                    as is_crash,
        FIRE = 'Y'                                     as is_fire,
        -- owners sometimes type the field maximum (99) as a protest; implausible counts become unknown
        case when try_cast(INJURED as integer) between 0 and {{ var('max_plausible_injured') }}
             then try_cast(INJURED as integer) end     as injured,
        case when try_cast(DEATHS as integer) between 0 and {{ var('max_plausible_deaths') }}
             then try_cast(DEATHS as integer) end      as deaths,
        coalesce(try_cast(INJURED as integer) > {{ var('max_plausible_injured') }}
                 or try_cast(DEATHS as integer) > {{ var('max_plausible_deaths') }}, false)
                                                       as harm_counts_implausible,
        MEDICAL_ATTN = 'Y'                             as medical_attention,
        VEHICLES_TOWED_YN = 'Y'                        as towed,
        nullif(try_cast(MILES as integer), 0)          as miles,
        nullif(try_cast(VEH_SPEED as integer), 0)      as vehicle_speed,
        {{ nhtsa_date('FAILDATE') }}                   as fail_date,
        {{ nhtsa_date('LDATE') }}                      as received_date,
        {{ nhtsa_date('DATEA') }}                      as added_date,
        nullif(upper(trim(VIN)), '')                   as vin_prefix,
        nullif(trim(STATE), '')                        as state,
        trim(CMPL_TYPE)                                as complaint_source,
        trim(PROD_TYPE)                                as product_type,
        nullif(trim(FUEL_TYPE), '')                    as fuel_type,
        trim(CDESCR)                                   as narrative,
        _source_file
    from {{ source('raw', 'raw_complaints') }} as src
    where CMPLID is not null
)
select t.*, {{ is_passenger_vehicle('t.make', 't.model') }} as is_passenger_vehicle
from typed t
