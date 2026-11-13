-- Visualization candidates: queries that return interesting data we may want to chart.
-- Run with:  uv run python -m pipeline.queries run viz_
--
-- Found a query worth visualizing? Add it here: give it a unique `-- name: viz_...` line, a one-line
-- comment saying what it shows, and end it with a semicolon at the end of a line. See "Add a query" in the README.
-- The three examples below relate Tier 0 to the app's target SDK level (target_sdk comes from the dynamic
-- scan, so it is NULL for apps without one).


-- name: viz_tier0_apps_by_target_sdk
-- How many apps target each SDK level, and how many of them are Tier 0.
-- count(DISTINCT ...) because the join repeats an app once per unused permission.
SELECT a.target_sdk,
       count(DISTINCT a.id) AS apps,
       count(DISTINCT t.id) AS tier0_apps
FROM app a
LEFT JOIN tier0 t USING (id)
WHERE a.has_overprivileged_check
GROUP BY 1
ORDER BY 1 NULLS LAST;

-- name: viz_tier0_pairs_by_level
-- Unused dangerous vs signature permissions, split at targetSDK 29 (Android 10, scoped storage).
SELECT CASE WHEN a.target_sdk IS NULL THEN 'unknown'
            WHEN a.target_sdk >= 29 THEN '29+'
            ELSE 'below 29' END AS target_sdk_band,
       count(*) FILTER (t.protection_level = 'dangerous') AS dangerous_pairs,
       count(*) FILTER (t.protection_level = 'signature') AS signature_pairs,
       count(DISTINCT t.id) AS tier0_apps
FROM tier0 t
JOIN app a USING (id)
GROUP BY 1
ORDER BY 1;

-- name: viz_unused_storage_by_target_sdk
-- Apps with an unused READ_EXTERNAL_STORAGE (the most common Tier 0 permission), by target SDK level.
SELECT a.target_sdk, count(*) AS apps
FROM tier0 t
JOIN app a USING (id)
WHERE t.perm = 'android.permission.READ_EXTERNAL_STORAGE'
GROUP BY 1
ORDER BY 1 NULLS LAST;
