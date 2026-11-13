-- Example shared view: one row per app that has the over-privilege check, with its Tier 0 summary.
-- Built by the build_sql task after sql/tiers/, so it can use tier0.
-- Copy this file to add your own view (see "Adding a view" in the README).

CREATE OR REPLACE VIEW tier0_by_app AS
SELECT a.id, a.package_key, a.title, a.publisher_name, a.target_sdk,
       count(t.perm) AS n_unused,
       count(t.perm) FILTER (t.protection_level = 'dangerous') AS n_unused_dangerous,
       count(t.perm) FILTER (t.protection_level = 'signature') AS n_unused_signature,
       list(DISTINCT t.perm_group ORDER BY t.perm_group) FILTER (t.perm IS NOT NULL) AS unused_groups,
       count(t.perm) > 0 AS is_tier0
FROM app a
LEFT JOIN tier0 t USING (id)
WHERE a.has_overprivileged_check
GROUP BY ALL;
