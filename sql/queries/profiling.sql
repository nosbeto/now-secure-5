-- Profiling: what is in the database, and how complete it is.
-- Run with:  uv run python -m pipeline.queries run raw_     (or p1_, or no prefix for everything)
--
-- Every statement needs a `-- name: <unique_name>` line and must end with a semicolon at the end of a line.
-- The database is opened read-only, so any helper table must be CREATE TEMP TABLE.


-- ==== 0. What is in the database (raw tables) ========================================

-- name: raw_platform_counts
SELECT platform_type, has_assessment, count(*) AS n
FROM assessments
GROUP BY ALL ORDER BY 1, 2;

-- name: raw_group_counts
SELECT group_name, count(*) AS n, count(*) FILTER (has_assessment) AS with_assessment
FROM assessments
GROUP BY 1 ORDER BY 1;

-- name: raw_static_task_keys
SELECT k AS task, count(*) AS apps
FROM (SELECT unnest(json_keys(analysis -> 'task' -> 'static' -> 'result')) AS k FROM assessment_raw)
GROUP BY 1 ORDER BY 1;

-- name: raw_dynamic_task_keys
SELECT k AS task, count(*) AS apps
FROM (SELECT unnest(json_keys(analysis -> 'task' -> 'dynamic' -> 'result')) AS k FROM assessment_raw)
GROUP BY 1 ORDER BY 1;

-- name: raw_findings_android
-- NowSecure's own scored checks, as a cross-check for the raw task data.
SELECT f.check_id, count(*) AS n, count(*) FILTER (f.affected) AS affected
FROM findings f JOIN assessments a ON a.id = f.assessment_id
WHERE a.platform_type = 'android'
GROUP BY 1 ORDER BY 3 DESC, 1
LIMIT 40;

-- name: raw_finding_descriptions
SELECT check_id, any_value(json_extract_string("check", '$.description'))[:300] AS description
FROM findings
WHERE check_id = 'application_overprivileged_check'
GROUP BY 1 ORDER BY 1;


-- ==== Coverage and data quality =============================================

-- name: p1_coverage
SELECT count(*) AS apps,
       count(*) FILTER (has_static) AS has_static,
       count(*) FILTER (has_dynamic) AS has_dynamic,
       count(*) FILTER (has_static AND has_dynamic) AS has_both,
       count(*) FILTER (has_overprivileged_check) AS has_overprivileged,
       count(*) FILTER (on_play_store) AS on_play_store,
       count(DISTINCT package_key) AS distinct_packages
FROM app;

-- name: p1_duplicate_packages
SELECT package_key, count(*) AS n, list(group_name ORDER BY group_name) AS groups
FROM app GROUP BY 1 HAVING n > 1 ORDER BY 1;
