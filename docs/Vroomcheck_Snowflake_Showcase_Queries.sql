-- Vroomcheck: showcase queries for Snowflake (database VROOMCHECK, schema MARTS)
-- Each query joins the Kimball star schema to answer one question.
-- Run in a Snowsight worksheet: put the cursor in a query and press Cmd+Enter.

USE WAREHOUSE VROOMCHECK_WH;


-- 1. Drill-across scorecard: every business process side by side for the same car.
--    The conformed dim_vehicle key is what lets four fact tables line up.
WITH c AS (
    SELECT vehicle_key,
           COUNT(*)                                     AS complaints,
           SUM(CASE WHEN is_crash THEN 1 ELSE 0 END)    AS crashes,
           SUM(CASE WHEN is_fire  THEN 1 ELSE 0 END)    AS fires,
           SUM(deaths)                                  AS deaths
    FROM VROOMCHECK.MARTS.FCT_COMPLAINTS
    GROUP BY vehicle_key
),
r AS (SELECT vehicle_key, COUNT(DISTINCT campno)        AS recalls
      FROM VROOMCHECK.MARTS.BRIDGE_RECALL_VEHICLE GROUP BY vehicle_key),
i AS (SELECT vehicle_key, COUNT(DISTINCT action_number) AS investigations
      FROM VROOMCHECK.MARTS.BRIDGE_INVESTIGATION_VEHICLE GROUP BY vehicle_key),
t AS (SELECT vehicle_key, COUNT(DISTINCT nhtsa_id)      AS bulletins
      FROM VROOMCHECK.MARTS.BRIDGE_TSB_VEHICLE GROUP BY vehicle_key)
SELECT v.make, v.model, v.model_year,
       c.complaints, c.crashes, c.fires, c.deaths,
       COALESCE(r.recalls, 0)        AS recalls,
       COALESCE(i.investigations, 0) AS investigations,
       COALESCE(t.bulletins, 0)      AS bulletins
FROM VROOMCHECK.MARTS.DIM_VEHICLE v
JOIN c      ON c.vehicle_key = v.vehicle_key
LEFT JOIN r ON r.vehicle_key = v.vehicle_key
LEFT JOIN i ON i.vehicle_key = v.vehicle_key
LEFT JOIN t ON t.vehicle_key = v.vehicle_key
ORDER BY c.complaints DESC
LIMIT 10;


-- 2. Which components hurt people most? Complaints joined to the component dimension.
SELECT d.system_group,
       d.component_top,
       d.severity_weight,
       COUNT(*)                                                             AS complaints,
       ROUND(100.0 * SUM(CASE WHEN f.is_crash THEN 1 ELSE 0 END) / COUNT(*), 1) AS crash_pct,
       ROUND(100.0 * SUM(CASE WHEN f.is_fire  THEN 1 ELSE 0 END) / COUNT(*), 1) AS fire_pct,
       SUM(f.deaths)                                                        AS deaths,
       ROUND(AVG(f.harm_score), 2)                                          AS avg_harm_score
FROM VROOMCHECK.MARTS.FCT_COMPLAINTS f
JOIN VROOMCHECK.MARTS.DIM_COMPONENT  d ON d.component_top = f.primary_component
GROUP BY d.system_group, d.component_top, d.severity_weight
ORDER BY deaths DESC, complaints DESC
LIMIT 10;


-- 3. Role-playing date dimension: one calendar, three roles, one row per year.
WITH c AS (SELECT d.year AS yr, COUNT(*) AS complaints
           FROM VROOMCHECK.MARTS.FCT_COMPLAINTS f
           JOIN VROOMCHECK.MARTS.DIM_DATE d ON d.date_day = f.received_date GROUP BY d.year),
     r AS (SELECT d.year AS yr, COUNT(*) AS recalls
           FROM VROOMCHECK.MARTS.FCT_RECALLS f
           JOIN VROOMCHECK.MARTS.DIM_DATE d ON d.date_day = f.recall_date GROUP BY d.year),
     i AS (SELECT d.year AS yr, COUNT(*) AS investigations_opened
           FROM VROOMCHECK.MARTS.FCT_INVESTIGATIONS f
           JOIN VROOMCHECK.MARTS.DIM_DATE d ON d.date_day = f.opened_date GROUP BY d.year)
SELECT c.yr AS year, c.complaints, r.recalls, i.investigations_opened
FROM c
LEFT JOIN r ON r.yr = c.yr
LEFT JOIN i ON i.yr = c.yr
ORDER BY year;


-- 4. The early-warning question: how many complaints arrived BEFORE each recall,
--    for the same car and the same component, and how much warning did they give?
SELECT r.campno,
       r.recall_date,
       b.make, b.model,
       b.component_top,
       COUNT(DISTINCT f.odino)              AS complaints_before_recall,
       MIN(f.received_date)                 AS first_complaint,
       r.recall_date - MIN(f.received_date) AS days_of_warning
FROM VROOMCHECK.MARTS.FCT_RECALLS r
JOIN VROOMCHECK.MARTS.BRIDGE_RECALL_VEHICLE b ON b.campno = r.campno
JOIN VROOMCHECK.MARTS.FCT_COMPLAINTS f
     ON  f.vehicle_key       = b.vehicle_key
     AND f.primary_component = b.component_top
     AND f.received_date     < r.recall_date
GROUP BY r.campno, r.recall_date, b.make, b.model, b.component_top
ORDER BY complaints_before_recall DESC
LIMIT 10;


-- 5. NHTSA's own timeline: from opening an investigation to the recall it led to.
--    PE = preliminary evaluation, EA = engineering analysis, DP = defect petition,
--    RQ = recall query (checks whether an earlier recall was enough, and can lead to a new one).
SELECT i.investigation_type,
       COUNT(*)                                   AS investigation_recall_pairs,
       ROUND(AVG(r.recall_date - i.opened_date))  AS avg_days_to_recall,
       MEDIAN(r.recall_date - i.opened_date)      AS median_days_to_recall
FROM VROOMCHECK.MARTS.BRIDGE_INVESTIGATION_RECALL b
JOIN VROOMCHECK.MARTS.FCT_INVESTIGATIONS i ON i.action_number = b.action_number
JOIN VROOMCHECK.MARTS.FCT_RECALLS        r ON r.campno        = b.campno
GROUP BY i.investigation_type
ORDER BY investigation_recall_pairs DESC;


-- 6. The gap finder: car + component combinations with the most crash or fire
--    complaints that have NO recall for that component (anti-join through the bridge).
WITH c AS (
    SELECT vehicle_key, primary_component,
           COUNT(*)                                                AS complaints,
           SUM(CASE WHEN is_crash OR is_fire THEN 1 ELSE 0 END)    AS crash_or_fire,
           SUM(deaths)                                             AS deaths
    FROM VROOMCHECK.MARTS.FCT_COMPLAINTS
    GROUP BY vehicle_key, primary_component
)
SELECT v.make, v.model, v.model_year, c.primary_component,
       c.complaints, c.crash_or_fire, c.deaths
FROM c
JOIN VROOMCHECK.MARTS.DIM_VEHICLE   v ON v.vehicle_key   = c.vehicle_key
JOIN VROOMCHECK.MARTS.DIM_COMPONENT d ON d.component_top = c.primary_component
LEFT JOIN VROOMCHECK.MARTS.BRIDGE_RECALL_VEHICLE b
       ON b.vehicle_key = c.vehicle_key AND b.component_top = c.primary_component
WHERE b.campno IS NULL
  AND d.in_scope
  AND d.component_top NOT LIKE '%UNKNOWN%'
  AND d.component_top NOT LIKE '%OTHER%'
ORDER BY c.crash_or_fire DESC, c.complaints DESC
LIMIT 10;


-- 7. Owners citing recalls: the fact-to-fact bridge from complaints to recalls.
SELECT r.campno,
       r.recall_date,
       r.component_tops,
       r.potentially_affected,
       COUNT(*) AS citing_complaints
FROM VROOMCHECK.MARTS.BRIDGE_COMPLAINT_RECALL_CITATION b
JOIN VROOMCHECK.MARTS.FCT_RECALLS r ON r.campno = b.campno
GROUP BY r.campno, r.recall_date, r.component_tops, r.potentially_affected
ORDER BY citing_complaints DESC
LIMIT 10;


-- 8. The detection signal: complaints for one model and component, rolled up by year
--    through dim_date. Ford Explorer structure complaints climb for years before
--    recall 26V448000 on 14 Jul 2026.
SELECT d.year,
       SUM(s.n_complaints) AS complaints,
       SUM(s.n_crashes)    AS crashes,
       SUM(s.harm_points)  AS harm_points
FROM VROOMCHECK.MARTS.MART_MONTHLY_SIGNAL s
JOIN VROOMCHECK.MARTS.DIM_DATE d ON d.date_day = s.received_month
WHERE s.make = 'FORD' AND s.model = 'EXPLORER' AND s.component_top = 'STRUCTURE'
GROUP BY d.year
ORDER BY d.year;


-- 9. Bonus: the latest data-quality gate run, stored next to the raw data.
SELECT dataset, check_name, value, passed, critical
FROM VROOMCHECK.RAW._DQ_RESULTS
WHERE run_at = (SELECT MAX(run_at) FROM VROOMCHECK.RAW._DQ_RESULTS)
ORDER BY dataset, check_name;
