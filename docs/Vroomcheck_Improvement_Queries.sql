-- Vroomcheck: showing the ESV26-289 improvements in Snowflake
-- Read-only queries. Run them in a Snowsight worksheet (cursor in a query, Cmd+Enter).
-- ML.CLUSTER_MONTHLY keeps both signals for every cluster and month:
--   POISSON_SPIKE  = the original detector (before)
--   PAPER_ELEVATED = the paper's sustained-elevation flag (after)
-- so the back-test can be recomputed for every detector from one table.

USE WAREHOUSE VROOMCHECK_WH;


-- 1. The back-test, recomputed live for all four detectors
--    (same clusters, same recalls, same 24-month window, only the alert rule changes)
WITH recall_scope AS (
    SELECT campno, recall_date
    FROM VROOMCHECK.MARTS.FCT_RECALLS
    WHERE recall_date >= '2017-01-01'
      AND min_model_year >= 2015
      AND recall_date <= (SELECT MAX(month) FROM VROOMCHECK.ML.CLUSTER_MONTHLY)
),
best_cluster AS (
    SELECT campno, cluster_id
    FROM VROOMCHECK.ML.CLUSTER_RECALL_MATCH
    WHERE is_match
    QUALIFY ROW_NUMBER() OVER (PARTITION BY campno
                               ORDER BY match_method = 'cited' DESC, match_score DESC, cluster_id) = 1
),
detectors AS (
    SELECT column1 AS detector, column2 AS sort_order
    FROM VALUES ('poisson (before)', 1), ('paper', 2), ('either', 3), ('combined', 4)
),
per_recall AS (
    SELECT d.detector, d.sort_order, r.campno, r.recall_date,
           SUM(IFF(cm.month < DATE_TRUNC('month', r.recall_date), cm.n, 0)) AS pre_recall_complaints,
           MIN(IFF(cm.month < DATE_TRUNC('month', r.recall_date)
                   AND cm.month >= DATEADD(month, -24, DATE_TRUNC('month', r.recall_date))
                   AND CASE d.detector
                           WHEN 'poisson (before)' THEN cm.poisson_spike
                           WHEN 'paper'            THEN cm.paper_elevated
                           WHEN 'either'           THEN cm.poisson_spike OR cm.paper_elevated
                           ELSE                         cm.poisson_spike AND cm.paper_elevated
                       END,
                   cm.month, NULL)) AS first_alert
    FROM recall_scope r
    JOIN best_cluster b USING (campno)
    JOIN VROOMCHECK.ML.CLUSTER_MONTHLY cm USING (cluster_id)
    CROSS JOIN detectors d
    GROUP BY 1, 2, 3, 4
)
SELECT detector,
       COUNT_IF(pre_recall_complaints >= 5)                              AS detectable_recalls,
       COUNT_IF(pre_recall_complaints >= 5 AND first_alert IS NOT NULL)  AS flagged_early,
       ROUND(100 * flagged_early / detectable_recalls, 1)                AS pct_flagged_early,
       ROUND(MEDIAN(IFF(pre_recall_complaints >= 5 AND first_alert IS NOT NULL,
                        DATEDIFF(day, DATEADD(month, 1, first_alert), recall_date) / 30.44, NULL)), 1)
                                                                         AS median_lead_months
FROM per_recall
GROUP BY detector, sort_order
ORDER BY sort_order;


-- 2. Recalls the original detector missed but the paper's method caught early
WITH recall_scope AS (
    SELECT campno, recall_date, component_tops
    FROM VROOMCHECK.MARTS.FCT_RECALLS
    WHERE recall_date >= '2017-01-01' AND min_model_year >= 2015
      AND recall_date <= (SELECT MAX(month) FROM VROOMCHECK.ML.CLUSTER_MONTHLY)
),
best_cluster AS (
    SELECT campno, cluster_id
    FROM VROOMCHECK.ML.CLUSTER_RECALL_MATCH
    WHERE is_match
    QUALIFY ROW_NUMBER() OVER (PARTITION BY campno
                               ORDER BY match_method = 'cited' DESC, match_score DESC, cluster_id) = 1
),
per_recall AS (
    SELECT r.campno, r.recall_date, r.component_tops, b.cluster_id,
           SUM(IFF(cm.month < DATE_TRUNC('month', r.recall_date), cm.n, 0)) AS pre_recall_complaints,
           MIN(IFF(cm.poisson_spike AND cm.month < DATE_TRUNC('month', r.recall_date)
                   AND cm.month >= DATEADD(month, -24, DATE_TRUNC('month', r.recall_date)),
                   cm.month, NULL)) AS poisson_first_alert,
           MIN(IFF(cm.paper_elevated AND cm.month < DATE_TRUNC('month', r.recall_date)
                   AND cm.month >= DATEADD(month, -24, DATE_TRUNC('month', r.recall_date)),
                   cm.month, NULL)) AS paper_first_alert
    FROM recall_scope r
    JOIN best_cluster b USING (campno)
    JOIN VROOMCHECK.ML.CLUSTER_MONTHLY cm USING (cluster_id)
    GROUP BY 1, 2, 3, 4
)
SELECT p.campno, p.recall_date, c.make, c.model, p.component_tops, p.pre_recall_complaints,
       p.paper_first_alert::date                                                       AS paper_first_alert,
       ROUND(DATEDIFF(day, DATEADD(month, 1, p.paper_first_alert), p.recall_date) / 30.44, 1)
                                                                                       AS months_of_warning
FROM per_recall p
JOIN VROOMCHECK.ML.CLUSTERS c USING (cluster_id)
WHERE p.pre_recall_complaints >= 5
  AND p.poisson_first_alert IS NULL
  AND p.paper_first_alert IS NOT NULL
ORDER BY p.pre_recall_complaints DESC
LIMIT 15;


-- 3. Alert workload today: EMERGING clusters (alerting in the last 6 months, no recall) per detector
WITH end_month AS (SELECT MAX(month) AS m FROM VROOMCHECK.ML.CLUSTER_MONTHLY),
candidates AS (           -- not paperwork, not already matched to a recall
    SELECT cluster_id FROM VROOMCHECK.ML.ALERTS WHERE status IN ('EMERGING', 'WATCH')
),
last_flag AS (
    SELECT cm.cluster_id,
           MAX(IFF(cm.poisson_spike, cm.month, NULL))                          AS poisson_last,
           MAX(IFF(cm.paper_elevated, cm.month, NULL))                         AS paper_last,
           MAX(IFF(cm.poisson_spike OR cm.paper_elevated, cm.month, NULL))     AS either_last,
           MAX(IFF(cm.poisson_spike AND cm.paper_elevated, cm.month, NULL))    AS combined_last
    FROM VROOMCHECK.ML.CLUSTER_MONTHLY cm JOIN candidates USING (cluster_id)
    GROUP BY 1
)
SELECT COUNT_IF(poisson_last  >= DATEADD(month, -6, e.m)) AS emerging_poisson_before,
       COUNT_IF(paper_last    >= DATEADD(month, -6, e.m)) AS emerging_paper,
       COUNT_IF(either_last   >= DATEADD(month, -6, e.m)) AS emerging_either,
       COUNT_IF(combined_last >= DATEADD(month, -6, e.m)) AS emerging_combined
FROM last_flag CROSS JOIN end_month e;


-- 4. Feature B: affected model years, before (cluster span) vs after (binomial test)
--    scored against the model years each matched recall actually covers
WITH best AS (
    SELECT cluster_id, campno FROM VROOMCHECK.ML.CLUSTER_RECALL_MATCH WHERE rank = 1 AND is_match
),
actual AS (
    SELECT DISTINCT b.cluster_id, v.model_year
    FROM best b
    JOIN VROOMCHECK.ML.CLUSTERS c USING (cluster_id)
    JOIN VROOMCHECK.MARTS.BRIDGE_RECALL_VEHICLE v
      ON v.campno = b.campno AND v.make = c.make AND v.model = c.model
    WHERE v.model_year >= 2015
),
named AS (
    SELECT cluster_id, COUNT_IF(is_elevated) AS n_elevated
    FROM VROOMCHECK.ML.CLUSTER_MODEL_YEARS GROUP BY 1
),
predicted AS (
    SELECT 'cluster span (before)' AS method, y.cluster_id, y.model_year
    FROM VROOMCHECK.ML.CLUSTER_MODEL_YEARS y
    WHERE y.cluster_id IN (SELECT cluster_id FROM actual)
    UNION ALL
    SELECT 'binomial test (after)', y.cluster_id, y.model_year
    FROM VROOMCHECK.ML.CLUSTER_MODEL_YEARS y JOIN named n USING (cluster_id)
    WHERE y.cluster_id IN (SELECT cluster_id FROM actual)
      AND (y.is_elevated OR n.n_elevated = 0)          -- no significant year: fall back to the span
),
per_cluster AS (
    SELECT p.method, p.cluster_id, COUNT(*) AS years_named, COUNT(a.model_year) AS years_right
    FROM predicted p LEFT JOIN actual a USING (cluster_id, model_year)
    GROUP BY 1, 2
),
recall_years AS (SELECT cluster_id, COUNT(*) AS years_in_recall FROM actual GROUP BY 1)
SELECT method,
       COUNT(*)                                          AS recalled_clusters,
       ROUND(AVG(years_named), 1)                        AS avg_years_named,
       ROUND(AVG(years_right / years_named), 3)          AS precision,
       ROUND(AVG(years_right / years_in_recall), 3)      AS recall
FROM per_cluster JOIN recall_years USING (cluster_id)
GROUP BY method
ORDER BY method DESC;


-- 5. Why normalise: raw complaint volume vs the paper's fleet-normalised indicator (ENGINE, latest month)
--    The F-150 gets the most complaints overall, but a normal share of them are about the engine,
--    so it is not flagged. The Escape's engine share is almost three times the fleet's, so it is.
SELECT make, model,
       ROUND(n_12m / NULLIF(share, 0))  AS all_complaints_last_12m,
       n_12m                            AS engine_complaints_last_12m,
       ROUND(share, 3)                  AS engine_share,
       ROUND(baseline, 3)               AS fleet_engine_share,
       ROUND(p_indicator, 2)            AS p_indicator,
       is_elevated
FROM VROOMCHECK.ML.VEHICLE_COMPONENT_INDICATOR
WHERE component = 'ENGINE'
  AND month = (SELECT MAX(month) FROM VROOMCHECK.ML.VEHICLE_COMPONENT_INDICATOR)
  AND (make, model) IN (('FORD', 'F-150'), ('JEEP', 'WRANGLER'), ('HONDA', 'CR-V'),
                        ('FORD', 'ESCAPE'), ('KIA', 'SOUL'), ('HYUNDAI', 'SONATA'))
ORDER BY all_complaints_last_12m DESC;


-- 6. Feature C: the largest merged defect events
SELECT make, model, component, start_month, end_month, flagged_months,
       ROUND(peak_p_indicator, 2) AS peak_p_indicator, event_complaints
FROM VROOMCHECK.ML.INDICATOR_EVENTS
ORDER BY event_complaints DESC
LIMIT 10;


-- 7. The stored before/after table written by python -m vroomcheck.detection.compare
SELECT detector, flagged_early, pct_flagged_of_detectable, median_lead_months,
       emerging_alerts, alert_months_per_1000, precision_proxy_pct, detect_seconds
FROM VROOMCHECK.ML.DETECTOR_COMPARISON;


-- 8. Latency: how long the paper's SQL took inside Snowflake (from query history)
SELECT start_time, ROUND(total_elapsed_time / 1000, 1) AS seconds, LEFT(query_text, 80) AS query
FROM TABLE(VROOMCHECK.INFORMATION_SCHEMA.QUERY_HISTORY_BY_USER(USER_NAME => CURRENT_USER(), RESULT_LIMIT => 10000))
WHERE (query_text ILIKE '%create or replace transient table ml.vehicle_component_indicator%'
   OR query_text ILIKE '%create or replace transient table ml.cluster_model_years%'
   OR query_text ILIKE '%create or replace transient table ml.indicator_events%')
  AND query_text NOT ILIKE '%query_history%'
ORDER BY start_time DESC
LIMIT 10;
