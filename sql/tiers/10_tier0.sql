-- Tier 0: declared but unused.
-- An app x permission pair is Tier 0 when NowSecure's static check lists the permission in
-- extra_permissions (app_permission.is_extra) and the Android framework declares it dangerous or
-- signature-level (permission_meta, built by the permission_meta task from the pinned AOSP manifest).
-- perm_group comes from the hand-written seeds/perm_group.csv; Tier 1 joins on it.
--
-- Run by the build_sql task after the seeds are loaded. Stored as a table (not a view) so it stays in
-- the database and gets a Parquet copy in [paths].tidy_dir.

CREATE OR REPLACE TABLE tier0 AS
SELECT p.id, a.package_key, p.perm, g.perm_group, m.protection_level, m.protection_flags
FROM app_permission p
JOIN app a USING (id)
JOIN permission_meta m USING (perm)
JOIN perm_group g USING (perm)
WHERE p.is_extra AND m.protection_level IN ('dangerous', 'signature')
ORDER BY p.id, p.perm;
