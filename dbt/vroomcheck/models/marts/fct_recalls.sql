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
        any_value(mfr_name)                             as mfr_name,
        string_agg(distinct make, ', ' order by make)   as makes,
        string_agg(distinct component_top, ' | ' order by component_top)  as component_tops,
        string_agg(distinct component_path, ' | ' order by component_path) as component_paths,
        max(potentially_affected)                       as potentially_affected,
        any_value(influenced_by)                        as influenced_by,
        max(defect_summary)                             as defect_summary,
        max(consequence_summary)                        as consequence_summary,
        max(remedy_summary)                             as remedy_summary,
        bool_or(do_not_drive)                           as do_not_drive,
        bool_or(park_outside)                           as park_outside,
        min(model_year)                                 as min_model_year,
        max(model_year)                                 as max_model_year,
        count(distinct make || '|' || model || '|' || coalesce(model_year::varchar, '')) as n_vehicles
    from r
    group by campno
),
-- A recall can come out of several investigations (see bridge_investigation_recall);
-- investigation_id is the one NHTSA opened first, matching investigation_opened.
inv as (
    select campno,
           first(action_number order by opened_date nulls last, action_number) as investigation_id,
           min(opened_date)                                                   as investigation_opened,
           count(*)                                                           as n_investigations
    from (
        select action_number, campno, min(opened_date) as opened_date
        from {{ ref('stg_investigations') }}
        where campno is not null and is_passenger_vehicle
        group by 1, 2
    )
    group by campno
)
select c.*, inv.investigation_id, inv.investigation_opened,
       coalesce(inv.n_investigations, 0) as n_investigations
from campaign c
left join inv using (campno)
