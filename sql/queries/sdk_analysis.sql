-- SDK analysis: how Tier 0 relates to the Android SDK level an app targets.
-- Run with:  uv run python -m pipeline.queries run sdk_
--
-- This file is a starting point: copy a query, give it a new unique `-- name:` (keep the sdk_ prefix),
-- and end it with a semicolon at the end of a line. See "Add a query" in the README.
-- target_sdk comes from the dynamic scan, so it is NULL for apps without one.


-- name: sdk_tier0_apps_by_target_sdk
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

-- name: sdk_tier0_pairs_by_level
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

-- name: sdk_unused_storage_by_target_sdk
-- Apps with an unused READ_EXTERNAL_STORAGE (the most common Tier 0 permission), by target SDK level.
SELECT a.target_sdk, count(*) AS apps
FROM tier0 t
JOIN app a USING (id)
WHERE t.perm = 'android.permission.READ_EXTERNAL_STORAGE'
GROUP BY 1
ORDER BY 1 NULLS LAST;
