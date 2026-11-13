-- Tier 0 report queries (see sql/tiers/README.md).
-- Run with:  uv run python -m pipeline.queries run tier0_
--
-- tier0_* queries read the raw permission tables; tier0_table_* read the tier0 table built by build_sql.


-- ==== Tier 0. Declared but unused ===================================================

-- name: tier0_overview
WITH per_app AS (
    SELECT a.id, a.overprivileged_vulnerable AS vuln,
           count(p.perm) AS n_granted, count(p.perm) FILTER (p.is_extra) AS n_extra
    FROM app a LEFT JOIN app_permission p USING (id)
    WHERE a.has_overprivileged_check
    GROUP BY ALL
)
SELECT count(*) AS apps, count(*) FILTER (vuln) AS vulnerable,
       avg(n_extra) AS avg_extra, avg(n_granted) AS avg_granted,
       count(*) FILTER (n_extra > 0) AS any_extra
FROM per_app;

-- name: tier0_top_extra_permissions
SELECT perm, count(DISTINCT id) AS apps FROM app_permission WHERE is_extra GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 40;

-- name: tier0_sanity_checks
-- extra must be a subset of granted, and granted should equal the manifest permissions seen by the dynamic scan.
SELECT count(*) FILTER (extra_not_granted = 0) AS extra_subset_of_granted,
       count(*) FILTER (dynamic_permissions_match) AS dynamic_equals_granted,
       count(dynamic_permissions_match) AS has_dynamic_permissions
FROM app
WHERE has_overprivileged_check;

-- name: tier0_dangerous_rate
SELECT count(DISTINCT p.id) FILTER (p.is_extra) AS apps_with_extra_any,
       count(DISTINCT p.id) FILTER (p.is_extra AND m.protection_level = 'dangerous') AS apps_with_extra_dangerous,
       count(DISTINCT p.id) FILTER (m.protection_level = 'dangerous') AS apps_granted_dangerous,
       (SELECT count(*) FROM app WHERE has_overprivileged_check) AS apps_with_check
FROM app_permission p JOIN permission_meta m USING (perm);

-- name: tier0_top_dangerous_extra
SELECT p.perm, count(DISTINCT p.id) AS apps
FROM app_permission p JOIN permission_meta m USING (perm)
WHERE p.is_extra AND m.protection_level = 'dangerous'
GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT 12;

-- name: tier0_permission_levels
-- Every declared permission by Android protection level (unknown = not declared by the framework).
SELECT protection_level, count(*) AS permissions, count(*) FILTER (apps_extra > 0) AS unused_somewhere
FROM permission_meta GROUP BY 1 ORDER BY 1;


-- ==== Tier 0 table (built by build_sql) ==========================================

-- name: tier0_table_headline
-- Denominator = apps with the over-privilege check.
SELECT count(DISTINCT id) AS tier0_apps,
       count(DISTINCT id) FILTER (protection_level = 'dangerous') AS dangerous_only_apps,
       count(DISTINCT id) FILTER (perm <> 'android.permission.READ_EXTERNAL_STORAGE') AS excl_read_storage,
       (SELECT count(*) FROM app WHERE has_overprivileged_check) AS denominator,
       count(*) AS tier0_pairs
FROM tier0;

-- name: tier0_table_by_level
SELECT protection_level, count(*) AS pairs, count(DISTINCT id) AS apps
FROM tier0 GROUP BY 1 ORDER BY 1;

-- name: tier0_table_by_group
SELECT perm_group, count(*) AS pairs, count(DISTINCT id) AS apps
FROM tier0 GROUP BY 1 ORDER BY 3 DESC, 1;

-- name: tier0_table_top_permissions
SELECT perm, protection_level, count(DISTINCT id) AS apps
FROM tier0 GROUP BY ALL ORDER BY apps DESC, perm LIMIT 20;

-- name: tier0_table_extra_not_tier0
-- Extra permissions that are NOT Tier 0, by why (normal, or unknown = vendor/app-defined/typo).
SELECT m.protection_level, count(*) AS pairs, count(DISTINCT p.id) AS apps
FROM app_permission p JOIN permission_meta m USING (perm)
WHERE p.is_extra AND m.protection_level NOT IN ('dangerous', 'signature')
GROUP BY 1 ORDER BY 2 DESC;
