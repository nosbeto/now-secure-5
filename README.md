# NowSecure Implementation Phase

This repo turns the group NowSecure assessment exports into one DuckDB database. It then builds the
**Privilege Justification Gap (PJG)** tiers on top of it. A small pipeline does the loading and
building. Analysis is plain SQL: you add a `.sql` file and either run it on demand or have the
pipeline build it as a shared view.

- **Tier 0 and Tier 1** (what they are, how they're built, results, the Tier 1 plan):
  [`sql/tiers/README.md`](sql/tiers/README.md)
- **Everything else** (setup, running, querying, contributing): this file

## Contents

- [Quick start](#quick-start)
- [Repository layout](#repository-layout)
- [Getting the data](#getting-the-data)
- [Running the pipeline](#running-the-pipeline)
- [Configuration](#configuration)
- [What's in the database](#whats-in-the-database)
- [Querying the data](#querying-the-data)
- [Contributing analysis](#contributing-analysis): add a query, add a view, add a seed, add a tier
- [Adding a pipeline task](#adding-a-pipeline-task)
- [Logs and troubleshooting](#logs-and-troubleshooting)

---

## Quick start

```bash
brew install uv                    # or: curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync                            # installs Python deps from uv.lock into .venv/
# put the group .zip files in data/raw/ (see "Getting the data")
uv run python -m pipeline          # build everything (~25 s; the first run also downloads one Android file)
uv run python -m pipeline.queries run tier0_      # run the Tier 0 report queries
```

Requires Python 3.11+ (uv downloads one if needed). `uv run` uses the project's `.venv/`
automatically, so you never need to activate it. After pulling changes, run `uv sync` again.
To add a dependency, run `uv add <package>` and commit `pyproject.toml` and `uv.lock`.

## Repository layout

```
README.md                  # this file
config.default.toml        # default paths and settings (committed)
config.local.toml          # your overrides (git-ignored, optional)
pyproject.toml, uv.lock    # dependencies (uv)

pipeline/                  # Python package: the pipeline and the query runner
  __main__.py              # `uv run python -m pipeline`
  queries.py               # `uv run python -m pipeline.queries` (runs sql/queries/)
  config.py, runner.py, run_log.py
  tasks/                   # one file per pipeline task; tasks/__init__.py sets the run order
    unzip.py, to_jsonl.py, load_duckdb.py, flatten.py, permission_meta.py, build_sql.py

sql/                       # all analysis SQL
  tiers/                   # PJG tier tables, built by the pipeline (+ README for Tier 0 / Tier 1)
  views/                   # shared views anyone can add, built by the pipeline after the tiers
  queries/                 # named queries you run on demand; never change the database
                           #   profiling.sql, tier0.sql, sdk_analysis.sql (starter examples)
seeds/                     # hand-maintained lookup CSVs, loaded as tables (e.g. perm_group.csv)

data/                      # git-ignored, nothing here is committed
  raw/                     # the source .zip files (you supply these)
  interim/                 # extracted files
  processed/               # assessments.jsonl, assessments.duckdb, rejects.jsonl, tidy/*.parquet
  external/                # cached outside data (Android permission manifest)
logs/                      # one log file per pipeline run (git-ignored)
```

## Getting the data

The data isn't in the repo. Get the group `.zip` files and put them in `data/raw/`:

```bash
mkdir -p data/raw
# copy the downloaded files here, e.g. data/raw/Group 1.zip ... data/raw/Group 7.zip
```

Everything else under `data/` is created by the pipeline. If your zips live somewhere else, point
`raw_dir` at that folder (see [Configuration](#configuration)).

## Running the pipeline

```bash
uv run python -m pipeline                       # run every task in order
uv run python -m pipeline build_sql             # run only the named task(s)
uv run python -m pipeline flatten build_sql     # named tasks always run in registry order
uv run python -m pipeline --list                # show the tasks
```

| # | Task | What it does | Writes |
|---|---|---|---|
| 1 | `unzip` | extracts every `.zip` in `data/raw/` | `data/interim/` |
| 2 | `to_jsonl` | combines each group's JSON files into one JSONL, tagging each record with `_group` and `_source_file` | `assessments.jsonl` |
| 3 | `load_duckdb` | loads the JSONL into a fresh database (see [bad data](#how-loading-handles-bad-data)) | `assessments.duckdb`: raw tables |
| 4 | `flatten` | extracts the tidy tables from the raw Android scan JSON | `app`, `app_permission` |
| 5 | `permission_meta` | gives every declared permission its Android protection level, from Android's own source at a pinned version | `permission_meta` |
| 6 | `build_sql` | loads `seeds/*.csv`, then runs `sql/tiers/*.sql` and `sql/views/*.sql` in name order | seed tables, `tier0`, views |

- Paths always resolve from the project root, so run commands from there.
- The run stops at the first failing task and exits with code 1. An unknown task name fails
  immediately and lists the valid ones.
- **Which task do I re-run?** If you changed a view or tier SQL file or a seed, run
  `build_sql` (well under a second). If you changed `[permissions].aosp_tag`, run
  `permission_meta build_sql`. If the source zips changed, run the whole pipeline.
- `load_duckdb` rebuilds the database from scratch, which drops every table the later tasks made,
  so tasks 4–6 must run after it. The full pipeline does that for you.

## Configuration

`config.default.toml` (committed) holds the defaults:

```toml
[paths]
raw_dir      = 'data/raw'
interim_dir  = 'data/interim'
jsonl_file   = 'data/processed/assessments.jsonl'
duckdb_file  = 'data/processed/assessments.duckdb'
rejects_file = 'data/processed/rejects.jsonl'
tidy_dir     = 'data/processed/tidy'
seeds_dir    = 'seeds'
tiers_dir    = 'sql/tiers'
views_dir    = 'sql/views'
queries_dir  = 'sql/queries'
external_dir = 'data/external'

[permissions]
aosp_tag = 'android-14.0.0_r1'    # Android version the permission protection levels come from
```

To change something for your machine only, create `config.local.toml` (git-ignored) with **just**
the keys you want to override:

```toml
[paths]
raw_dir = '~/Downloads/now_secure_zips'
```

Relative paths resolve from the project root; absolute and `~` paths are used as-is. Keys must
already exist in `config.default.toml`, so a typo stops the run instead of being ignored.

## What's in the database

Everything lives in `data/processed/assessments.duckdb`. All tidy and tier tables cover **Android
apps only** and are keyed by `id` (= `assessments.id`). `package_key` is unique per app too.

**Raw tables** (`load_duckdb`)

| Table | One row per | Key columns |
|---|---|---|
| `assessments` | app in the group files (194) | `id`, `group_name`, `title`, `has_assessment`, `platform_type`, `package_key`, `publisher_name`, `score` |
| `findings` | NowSecure finding | `assessment_id` → `assessments.id`, `check_id`, `impact_type`, `affected`, `cvss`, `check` (JSON) |
| `assessment_raw` | app with an assessment (166) | `assessment_id`, `analysis`: the whole raw scan as JSON (~99% of the data; single apps reach ~35 MB) |

Query the tidy tables instead of `assessment_raw` when you can. Unnesting big arrays from the raw
JSON inside SQL can run DuckDB out of memory.

**Tidy tables** (`flatten`)

| Table | One row per | Columns |
|---|---|---|
| `app` | Android app (157) | identity (`package_key`, `title`, `publisher_name`, `version_code`, `sha256`), `target_sdk`, `min_sdk`, `has_static`, `has_dynamic`, `has_overprivileged_check`, `overprivileged_vulnerable`, `on_play_store`, sanity checks (`extra_not_granted`, `dynamic_permissions_match`) |
| `app_permission` | app × declared permission | `perm`, `is_extra` (NowSecure says the app never uses it) |

**Permission levels** (`permission_meta`)

| Table | One row per | Columns |
|---|---|---|
| `permission_meta` | permission declared by any app | `protection_level` (`dangerous`, `signature`, `internal`, `normal`, `unknown`), `protection_flags`, `defined_in`, `apps`, `apps_extra` |

**Seeds, tiers and views** (`build_sql`)

| Object | Kind | What |
|---|---|---|
| `perm_group` | table (seed) | group for each dangerous/signature permission, from `seeds/perm_group.csv` |
| `tier0` | table | app × unused dangerous/signature permission ([details](sql/tiers/README.md#tier-0-declared-but-unused)) |
| `tier0_by_app` | view | one row per app with its Tier 0 counts (example view in `sql/views/`) |

Parquet copies of `app`, `app_permission`, `permission_meta` and `tier0` are written to
`data/processed/tidy/`, for pandas, R or anything else that doesn't speak DuckDB.

To see everything that exists right now:

```bash
uv run python -m pipeline.queries sql "SELECT table_name, table_type FROM information_schema.tables ORDER BY 1"
```

## Querying the data

Always open the database **read-only**. A writable connection holds a lock, and the pipeline then
fails with `Could not set lock on file`.

**1. The query runner** (best for anything you want to keep or share):

```bash
uv run python -m pipeline.queries list                     # every named query, by file
uv run python -m pipeline.queries run tier0_               # queries whose name starts with tier0_
uv run python -m pipeline.queries run --file profiling     # every query in sql/queries/profiling.sql
uv run python -m pipeline.queries run                      # everything
uv run python -m pipeline.queries sql "SELECT * FROM tier0_by_app WHERE is_tier0 LIMIT 5"
uv run python -m pipeline.queries shape static.application_overprivileged_check   # outline raw JSON
```

**2. The DuckDB CLI** (quick exploring in the terminal):

```bash
duckdb -readonly data/processed/assessments.duckdb
```

Inside it, `.tables` lists tables, `DESCRIBE tier0;` shows columns, and `.exit` (or Ctrl+D)
quits. If a half-typed query leaves you at a `‣` prompt, press Ctrl+C first. The CLI must be
DuckDB **2.x** (`brew install duckdb`): a database written by 2.x can't be opened by 1.x.

**3. The DuckDB UI** (exploratory analysis in the browser):

```bash
duckdb -readonly -ui data/processed/assessments.duckdb
```

This starts a local web interface at **http://localhost:4213** and opens your browser. It has a
SQL notebook and a table browser that shows columns and quick summaries without writing SQL, so
it's a good way to get to know `tier0`, `tier0_by_app` and `permission_meta`.

- **Keep `-readonly`.** The UI holds the database open for as long as it runs. Read-only lets the
  pipeline rebuild alongside it. Restart the UI afterwards to see rebuilt tables.
- **Stopping:** quit the CLI in the terminal (`.exit`), or run `CALL stop_ui_server();` in it.
- **From an open CLI session:** `CALL start_ui();` starts it (`CALL start_ui_server();` does so
  without opening the browser). Run `SET ui_local_port = 8080;` first to use another port.
- **First start needs internet.** DuckDB installs the `ui` extension, and the interface's files
  come from DuckDB's website (the UI is built by MotherDuck). Queries and data stay on your
  machine.
- **Notebooks aren't saved in the repo.** When an exploration turns into something worth keeping
  or sharing, move it into `sql/queries/` or `sql/views/` (see
  [Contributing analysis](#contributing-analysis)).

**4. Python / Jupyter:**

```python
import duckdb
con = duckdb.connect('data/processed/assessments.duckdb', read_only=True)
df = con.sql('SELECT * FROM tier0_by_app').df()      # pandas DataFrame
```

**5. Parquet, no DuckDB needed:** `pandas.read_parquet('data/processed/tidy/tier0.parquet')`.

DBeaver and other DuckDB clients work too.

## Contributing analysis

You choose between a **query** and a **view**:

| | Query (`sql/queries/`) | View (`sql/views/`) |
|---|---|---|
| What it is | a named `SELECT` you run when you want the answer | a named, reusable result other SQL can `FROM` |
| Built | on demand, by `pipeline.queries` | by the pipeline (`build_sql`), every run |
| Changes the database | never (read-only) | yes: adds the view |
| Use it for | report numbers, exploration, checks | definitions other people will build on |

### Add a query

The query files in `sql/queries/`:

| File | Prefix | What's in it |
|---|---|---|
| `profiling.sql` | `raw_`, `p1_` | what's in the database and how complete it is |
| `tier0.sql` | `tier0_` | the Tier 0 report numbers |
| `sdk_analysis.sql` | `sdk_` | **starter file**: three example `count` queries relating Tier 0 to the app's target SDK level. Copy one to begin. |

1. Add your query to the file for its topic, or create a new file such as
   `sql/queries/publisher_analysis.sql`. The runner picks up every `.sql` file in the folder.
2. Give every statement a `-- name:` line with a **unique** name, and end it with `;` at the end of
   a line. Use one prefix per file (`sdk_`, `publisher_`, …) so the whole group runs together.
   This one is from `sdk_analysis.sql`:

   ```sql
   -- name: sdk_unused_storage_by_target_sdk
   -- Apps with an unused READ_EXTERNAL_STORAGE (the most common Tier 0 permission), by target SDK level.
   SELECT a.target_sdk, count(*) AS apps
   FROM tier0 t
   JOIN app a USING (id)
   WHERE t.perm = 'android.permission.READ_EXTERNAL_STORAGE'
   GROUP BY 1
   ORDER BY 1 NULLS LAST;
   ```

3. Run it: `uv run python -m pipeline.queries run sdk_` (the whole group) or
   `run sdk_unused_storage` (just this one).

Tips:

- **Count apps, not rows, after a join.** `tier0` has one row per app *and* permission, so joining
  `app` to it repeats an app once per unused permission. Use `count(DISTINCT a.id)` for apps, as
  `sdk_tier0_apps_by_target_sdk` does, or start from the one-row-per-app view `tier0_by_app`.
- **Check totals against known numbers:** 153 apps have the over-privilege check, 107 are Tier 0.
- The runner opens the database read-only, so a helper table must be `CREATE TEMP TABLE`. Helper
  `CREATE` statements always run, even when you filter by name, so later queries can use them.

### Add a view

1. Create `sql/views/<name>.sql`. Copy `sql/views/tier0_by_app.sql` as a template:

   ```sql
   -- One row per publisher with its Tier 0 rate.
   CREATE OR REPLACE VIEW publisher_tier0 AS
   SELECT publisher_name, count(*) AS apps, avg(is_tier0::INT) AS tier0_rate
   FROM tier0_by_app
   GROUP BY 1;
   ```

2. Build it: `uv run python -m pipeline build_sql`. The log shows `Ran views/<name>.sql`.
3. Use it anywhere: in a query, the CLI, or Python.

Rules: use `CREATE OR REPLACE VIEW`, and only create views here, not tables. Never change or drop
another object. Files run in **name order** after the tiers, so if your view depends on another
view, prefix the file names with numbers (`10_base.sql`, `20_uses_base.sql`). If a view file
fails, the whole `build_sql` run is rolled back and the error names the file.

### Add a seed (hand-written lookup table)

1. Add `seeds/<table>.csv` with a header row.
2. Declare its columns and types in `SEEDS` in `pipeline/tasks/build_sql.py`:
   `'my_lookup': {'key': 'VARCHAR', 'label': 'VARCHAR'}`. An undeclared CSV stops the run, so
   nothing is loaded by accident.
3. Run `build_sql`. The CSV becomes a table called `<table>` that views and queries can join.

### Add a tier

Tier SQL lives in `sql/tiers/`. It builds tables (with Parquet copies), and the pipeline checks
those tables exist. See [Adding a tier](sql/tiers/README.md#adding-a-tier) and the Tier 1
scaffold there.

## Adding a pipeline task

Add a task when something needs Python: reading new raw JSON fields, calling an outside service,
or heavy processing. For anything SQL can express, use a view or query instead.

1. **Create** `pipeline/tasks/<name>.py`:

   ```python
   from pipeline.config import Config
   from pipeline.tasks.base import Task, TaskError


   class MyTask(Task):
       '''One-line description shown by --list.'''

       name = 'my_task'   # the name used on the command line

       def run(self, config: Config):
           db = config.path('duckdb_file')
           if not db.is_file():
               raise TaskError(f'Database not found: {db} (run load_duckdb first)')
           self.logger.info(f'Processing {db}')
           ...
   ```

2. **Register** it in `pipeline/tasks/__init__.py`. Its position in `TASKS` is its run order:

   ```python
   TASKS = [
       UnzipTask(),
       ToJsonlTask(),
       LoadDuckDbTask(),
       FlattenTask(),
       PermissionMetaTask(),
       MyTask(),          # e.g. a Play Store scrape that build_sql will need
       BuildSqlTask(),
   ]
   ```

3. **New settings** go in `config.default.toml`: paths under `[paths]` (read with
   `config.path('key')`), anything else in its own section (read with
   `config.value('section', 'key')`).
4. **Check it:** `uv run python -m pipeline --list`, then `uv run python -m pipeline my_task`.

**Conventions**

- **Fail fast:** check inputs at the top of `run()` and raise `TaskError` with a message that says
  what's wrong and how to fix it. The runner logs it and stops the pipeline.
- **Never swallow errors:** if you catch an exception, raise a clearer `TaskError` from it
  (`raise TaskError(...) from e`).
- **Log with `self.logger`** (named `task.<name>`), not `print`.
- **Get every path from `config.path(...)`.** Never hardcode one.
- **Safe to re-run:** overwrite outputs, so running twice gives the same result. Write database
  changes in one transaction so a failure leaves the previous state in place.
- **Cache outside data** under `config.path('external_dir')` and don't re-download what's cached.
- Use absolute imports (`from pipeline.… import …`), single quotes and f-strings.

## Logs and troubleshooting

Each pipeline run writes `logs/pipeline_<YYYYmmdd_HHMMSS>.log`. The terminal shows INFO and above.
The file also has DEBUG messages and, when a run fails, the **full traceback**. The first line of
every run names its log file. Old logs aren't deleted automatically.

| Symptom | Fix |
|---|---|
| `Could not set lock on file ... assessments.duckdb` | Something has the database open for writing (often a DuckDB CLI or UI). Quit it (`.exit`) and reopen with `-readonly`. |
| `Table with name X does not exist` | The pipeline hasn't built it yet: run `uv run python -m pipeline`. |
| A database written by DuckDB 2.x won't open | Use DuckDB 2.x everywhere: run `uv sync`, and keep any separate CLI on 2.x. |
| `perm_group.csv has no group for ...` | New data brought a dangerous/signature permission. Add a row to `seeds/perm_group.csv` and re-run `build_sql`. |
| `Download failed ... aosp_tag` | First run needs network access, or the tag in `[permissions].aosp_tag` doesn't exist. |
| Windows: `load_duckdb` can't replace the database | Close every connection to it first. |

### How loading handles bad data

- The JSONL is streamed one line at a time, so memory is bounded by the largest line (~35 MB).
- Each line is fully validated (valid JSON, required fields, types, timestamps) before anything is
  inserted, so a line is never half-loaded.
- A bad line is logged as a WARNING with its line number, byte offset and the exact field (e.g.
  `assessment.report.findings[12].cvss: expected a number, got str`). It's also copied to
  `data/processed/rejects.jsonl`. Every good line still loads, and then the task **fails (exit 1)**
  so problems are never missed. A clean run deletes the old `rejects.jsonl`.
- The database is built in a temporary file and swapped in only when complete, so a failed run
  leaves the previous database in place.
