-- One row per passenger-vehicle recall campaign (CAMPNO), with its earliest linked investigation.
-- Vehicle attributes are aggregated over the campaign's passenger-vehicle rows only, so they
-- agree with bridge_recall_vehicle; all model years are kept (the VIN check needs older ones).
with r as (
    select * from {{ ref('stg_recalls') }}
    where recall_type = 'V' and is_passenger_vehicle
),
campaign as (
    select
        campno,
        min(report_received_date)                       as recall_date,
        min(owner_notify_date)                          as owner_notify_date,
        min(mfr_name)                                   as mfr_name,
        {{ string_agg_distinct('make', ', ') }}         as makes,
        {{ string_agg_distinct('component_top', ' | ') }}  as component_tops,
        {{ string_agg_distinct('component_path', ' | ') }} as component_paths,
        max(potentially_affected)                       as potentially_affected,
        min(influenced_by)                              as influenced_by,
        max(defect_summary)                             as defect_summary,
        max(consequence_summary)                        as consequence_summary,
        max(remedy_summary)                             as remedy_summary,
        {{ dbt.bool_or('do_not_drive') }}               as do_not_drive,
        {{ dbt.bool_or('park_outside') }}               as park_outside,
        min(model_year)                                 as min_model_year,
        max(model_year)                                 as max_model_year,
        count(distinct make || '|' || model || '|' || coalesce(model_year::varchar, '')) as n_vehicles
    from r
    group by campno
),
-- A recall can come out of several investigations (see bridge_investigation_recall);
-- investigation_id is the one NHTSA opened first, matching investigation_opened.
inv_links as (
    select action_number, campno, min(opened_date) as opened_date
    from {{ ref('stg_investigations') }}
    where campno is not null and is_passenger_vehicle
    group by 1, 2
),
inv as (
    select campno,
           action_number                              as investigation_id,
           opened_date                                as investigation_opened,
           count(*) over (partition by campno)        as n_investigations
    from inv_links
    qualify row_number() over (partition by campno order by opened_date nulls last, action_number) = 1
)
select c.*, inv.investigation_id, inv.investigation_opened,
       coalesce(inv.n_investigations, 0) as n_investigations
from campaign c
left join inv using (campno)
