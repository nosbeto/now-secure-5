# PJG Tiers: Tier 0 and Tier 1

This folder holds the SQL that turns the scan data into the **Privilege Justification Gap (PJG)**
tiers from the proposal (*Closing the Privilege Justification Gap*, A. De Armas). The project
covers two tiers:

| Tier | Question | Status |
|---|---|---|
| **Tier 0** | Does the app declare a sensitive permission it never uses? | **Built:** `tier0` table, rebuilt on every pipeline run |
| **Tier 1** | Is that unused permission also unjustified for what the app is for? | **Not started.** Scaffold below |

Tiers 2–3 (runtime evidence, data leaving the device) and the Phase 5 models are out of scope.

For how to run the pipeline, query the database or add your own views and queries, see the
[root README](../../README.md).

---

## How the pieces fit

```
app_permission ──┐
                 ├─(is_extra AND dangerous/signature)──► tier0   (app × unused permission)
permission_meta ─┤                                         │
perm_group ──────┘                                         │
                                                           ▼
app_store + category_justification ─────────────────────► tier1   (Tier 0 pairs judged unjustified)
```

- **Unit of analysis:** an **app × permission** pair. Roll up to one row per app when reporting
  (the example view `sql/views/tier0_by_app.sql` does this for Tier 0).
- **Tier 1 is a filter on Tier 0**, not a separate population: every Tier 1 pair is a Tier 0 pair.
- **Population:** 157 Android apps have an assessment. **153** of them have the static
  over-privilege check, and only those can be scored. The other 4 are "not measurable", not
  "clean".
- `package_key` is unique across the 157 apps, so it's a safe join key, including for store data.

**Files, run in name order by the `build_sql` task:**

| File | Builds |
|---|---|
| `10_tier0.sql` | `tier0` (table) |
| `20_tier1.sql` | *to add:* `tier0_judged`, `tier1` |

---

## Tier 0: Declared but unused

### Definition

An app × permission pair is Tier 0 when:

1. the app declares the permission (`application_overprivileged_check.granted_permissions`), **and**
2. NowSecure's static analysis lists it as unused (`extra_permissions` → `app_permission.is_extra`), **and**
3. Android classes the permission as **dangerous or signature-level**.

### How it's built

Three pipeline tasks, all part of `uv run python -m pipeline`:

1. **`flatten`** writes `app` and `app_permission` from the raw scan JSON.
2. **`permission_meta`** gives every permission name its Android protection level. That level is
   set by Android, not by the app, so it isn't in the scans. The task:
   - queries `app_permission` for every permission name (341 today);
   - looks each one up in Android's own framework manifest
     (`frameworks/base/core/res/AndroidManifest.xml` from the Android Open Source Project), at the
     version set in `config.default.toml` (`[permissions] aosp_tag = 'android-14.0.0_r1'`). The
     file is downloaded once into `data/external/aosp/<tag>/` and cached;
   - reduces each `protectionLevel` (e.g. `signature|privileged|appop`) to its base level:
     `dangerous`, `signature`, `internal` or `normal`. Names Android doesn't declare (vendor or
     app-defined permissions, typos like `READ_EXTERNAL_STORAGEE`) become `unknown`.
3. **`build_sql`** loads the hand-written `seeds/perm_group.csv` (a group for each dangerous or
   signature permission, which Tier 1 joins on), then runs `10_tier0.sql`:

```sql
CREATE OR REPLACE TABLE tier0 AS
SELECT p.id, a.package_key, p.perm, g.perm_group, m.protection_level, m.protection_flags
FROM app_permission p
JOIN app a USING (id)
JOIN permission_meta m USING (perm)
JOIN perm_group g USING (perm)
WHERE p.is_extra AND m.protection_level IN ('dangerous', 'signature')
ORDER BY p.id, p.perm;
```

### Outputs

| Table | One row per | Columns |
|---|---|---|
| `permission_meta` | permission declared by any app | `perm`, `protection_level`, `protection_flags` (raw AOSP string), `defined_in` (AOSP tag), `apps`, `apps_extra` |
| `perm_group` | dangerous/signature permission | `perm`, `perm_group` (from `seeds/perm_group.csv`) |
| `tier0` | app × unused permission | `id`, `package_key`, `perm`, `perm_group`, `protection_level`, `protection_flags` |
| `tier0_by_app` (view) | app with the check | Tier 0 counts and groups per app, `is_tier0` |

Parquet copies: `data/processed/tidy/permission_meta.parquet` and `tier0.parquet`.

### Checks (fail fast)

The run stops with a message naming the problem, and the previous tables stay in place, if:

- `aosp_tag` is malformed, the download fails (e.g. the tag doesn't exist), or the cached file is
  broken. Delete `data/external/aosp/<tag>/` to force a re-download;
- `seeds/perm_group.csv` has a wrong header, a duplicate permission, a missing or lower-case
  group, or **no row for a dangerous or signature permission** that appears in the data. When new
  scans arrive, add the missing rows and re-run `uv run python -m pipeline build_sql`.

### Results (current data, `android-14.0.0_r1`)

| Measure | Value |
|---|---|
| Apps with the check | 153 |
| Apps with **any** unused permission | 137 (89.5%) |
| **Tier 0 apps** (≥1 unused dangerous or signature permission) | **107 (69.9%)**, 283 pairs |
| Apps with ≥1 unused **dangerous** permission | **103 (67.3%)**, 203 pairs |
| Apps with an unused signature permission | 35 (80 pairs); 4 of them have no dangerous one |
| Tier 0 without `READ_EXTERNAL_STORAGE` | 73 apps (61 dangerous-only) |
| Mean granted / mean unused permissions per app | 15.5 / 3.8 |

Top unused permissions: `READ_EXTERNAL_STORAGE` (83 apps), `CAMERA` (21), `READ_CONTACTS` (17),
`SYSTEM_ALERT_WINDOW` (17), `READ_PHONE_STATE` (10), `GET_ACCOUNTS` (9), `ACCESS_FINE_LOCATION` (8),
`REQUEST_INSTALL_PACKAGES` (8).

Reproduce everything with `uv run python -m pipeline.queries run tier0_` (queries in
`sql/queries/tier0.sql`).

### Caveats for the write-up

- **Compare carefully with Felt et al. (2011).** 67% is about double their one-third. They counted
  *any* unused permission using Stowaway's API map. NowSecure's "extra" list also includes
  permissions declared by bundled SDKs it couldn't resolve. Report "any unused" (89.5%) **and**
  "dangerous unused" (67.3%), and say which one is comparable.
- **`READ_EXTERNAL_STORAGE` dominates:** 83 of the 103 dangerous-Tier-0 apps. Report the rate with
  and without it. Many come from SDK manifests merged at build time.
- **Internal consistency holds:** the unused list is a subset of the granted list for all 153 apps,
  and the granted list equals the dynamic scan's manifest permissions in 141/141 apps that have both.
- **The Android version is a choice.** Changing `aosp_tag` can change the numbers, so say which
  tag a reported figure used.
- **Only the core framework manifest is read.** `DOWNLOAD_WITHOUT_NOTIFICATION` (declared by the
  DownloadProvider) and `INSTALL_EXISTING_PACKAGES` come out as `unknown`, so they can't be Tier 0.

---

## Tier 1: Not justified by the app's purpose (scaffold)

> **Status: not started.** This section is the plan. Turn each step into code, tick it off here,
> and move the numbers into a *Results* section like Tier 0's when it's done.

### Definition

A Tier 0 pair whose permission group makes no sense for the app's store category. Example: a
flashlight app with an unused `READ_CONTACTS`.

### What's missing from the data

The scans have `title` and `publisher_name` (in `app`) but **no category, description, installs or
rating**. Tier 1 is the only tier that needs outside data. Only 74 of the 157 apps were on the Play
Store even at scan time (2020–21), so expect many to be gone.

### Steps

- [ ] **1. Play Store scrape → `app_store` table.** A new pipeline task (e.g.
  `pipeline/tasks/scrape_play.py`, after `flatten`; `uv add google-play-scraper`):
  - read `package_key` from `app`;
  - cache **every** response as `data/external/play/<package_key>.json`, never re-request a cached
    package, and pause between requests;
  - write `app_store(package_key, status, category, category_source, installs, score, developer,
    developer_website, scraped_at)` with `status` = `ok | not_found | error`. Record "not found" as
    its own value, not as missing data;
  - **fallback** for `not_found`: pick a category by keyword from `app.title` / `publisher_name`
    and set `category_source = 'title'` (store rows get `'play'`), so the two can be reported apart.
- [ ] **2. Justification matrix → `seeds/category_justification.csv`** with columns
  `category, perm_group, justified, rationale`. Declare it in `SEEDS` in
  `pipeline/tasks/build_sql.py`.
  - `justified` is `true`, `false`, or **empty = cannot judge**. Use empty for multi-purpose
    categories (Social, Communication, Tools), as §9.2 of the proposal says.
  - Only fill in groups that appear in `tier0` (`SELECT DISTINCT perm_group FROM tier0`: 22 today,
    led by STORAGE, CAMERA, CONTACTS, OVERLAY, PHONE, LOCATION, ACCOUNTS, CALENDAR).
- [ ] **3. `20_tier1.sql`** (draft, tested read-only against sample seeds):

  ```sql
  -- Every Tier 0 pair with its judgement (NULL = no store data, or cannot judge)
  CREATE OR REPLACE TABLE tier0_judged AS
  SELECT t.*, s.category, s.category_source, s.status AS store_status, j.justified, j.rationale
  FROM tier0 t
  LEFT JOIN app_store s USING (package_key)
  LEFT JOIN category_justification j ON j.category = s.category AND j.perm_group = t.perm_group;

  CREATE OR REPLACE TABLE tier1 AS
  SELECT * FROM tier0_judged WHERE justified = FALSE;
  ```

  Add `tier0_judged` and `tier1` to `TIER_TABLES` in `build_sql.py` to get Parquet copies.
- [ ] **4. Inter-rater agreement (Cohen's κ).** Draw the sample **once** and commit it as
  `seeds/tier1_label_sample.csv`:

  ```sql
  -- USING SAMPLE runs before a WHERE in the same SELECT, so filter in a subquery if you need to.
  SELECT id, package_key, perm, perm_group FROM tier0 USING SAMPLE 30 ROWS (reservoir, 42);
  ```

  Two teammates label it independently (`seeds/labels_<name>.csv`: `id, perm, justified`).
  Report κ between them and between each rater and `category_justification`.
- [ ] **5. Report queries** in `sql/queries/tier1.sql` (`tier1_*`), for example:

  ```sql
  -- name: tier1_coverage
  SELECT store_status, category_source, justified, count(*) AS pairs, count(DISTINCT id) AS apps
  FROM tier0_judged GROUP BY ALL ORDER BY ALL;

  -- name: tier1_rate
  SELECT count(DISTINCT id) AS tier1_apps,
         count(DISTINCT id) FILTER (category_source = 'play') AS tier1_apps_play_only,
         (SELECT count(DISTINCT id) FROM tier0) AS tier0_apps
  FROM tier1;
  ```

**Done when:** every `tier0` pair has a `store_status`, the Tier 1 rate is reported with
store-derived and title-derived categories shown separately, and κ is reported.

### Later: one score per app

Once Tier 1 exists, add a view (e.g. `sql/views/app_tier.sql`) with one row per app:
`pjg_tier` (0 or NULL), `has_unjustified_pair` (Tier 1 flag) and `has_overprivileged_check`,
so apps that couldn't be measured are kept apart from clean ones.

---

## Adding a tier

1. Add any hand-written lookup CSV to `seeds/` and declare its columns in `SEEDS` in
   `pipeline/tasks/build_sql.py`.
2. Add `NN_<name>.sql` here (numbered so it runs after what it depends on). Use
   `CREATE OR REPLACE TABLE`.
3. Add tables that need a Parquet copy to `TIER_TABLES` in `build_sql.py`.
4. Add report queries to `sql/queries/<tier>.sql`, and document the tier in this README.

## Ground rules for reporting

- **Every reported app needs its evidence trail:** permission → NowSecure `extra` flag → Android
  protection level (with the AOSP tag) → for Tier 1, store category, `category_source` and the
  justification rationale. If a reviewer can't reproduce it from the data and the seeds, it
  doesn't go in.
- **Rates describe this sample (153 Android apps), not the Play Store.**
- **Don't call an app "malicious".** An unused or unjustified permission is over-privilege, not
  proof of misuse.
