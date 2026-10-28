# NowSecure Implementation Phase — Data Pipeline

A small task runner that prepares the group assessment data:

1. **unzip** — extracts every `.zip` in `data/raw/` into `data/interim/`
2. **to_jsonl** — combines each group's JSON files into `data/processed/assessments.jsonl`, tagging every record with `_group` and `_source_file`
3. **load_duckdb** — ingests the JSONL line by line into `data/processed/assessments.duckdb` for analysis (see [The DuckDB database](#the-duckdb-database))

## Setup

The project uses [uv](https://docs.astral.sh/uv/) to manage Python and dependencies (currently just `duckdb`).

```bash
brew install uv        # or: curl -LsSf https://astral.sh/uv/install.sh | sh
uv sync                # creates .venv/ and installs the exact versions pinned in uv.lock
```

Requires Python 3.11 or newer (uv will download one if needed).

- **Add a dependency:** `uv add <package>`. This updates `pyproject.toml` and `uv.lock`; commit both.
- **After pulling changes:** run `uv sync` again so your environment matches `uv.lock`.

## Getting the data

The data isn't in the repository. The whole `data/` folder is git-ignored, so get the group `.zip` files yourself and place them in `data/raw/`:

```bash
mkdir -p data/raw
# copy the downloaded files here, e.g. data/raw/Group 1.zip ... data/raw/Group 7.zip
```

Everything else under `data/` (`interim/` and `processed/`) is created by the pipeline. If your zips live somewhere else, point `raw_dir` at that folder instead (see [Configuring paths for your machine](#configuring-paths-for-your-machine)).

## Running the pipeline

From the project root (data and config paths always resolve from the project root):

```bash
uv run python -m pipeline                    # run all tasks in order
uv run python -m pipeline load_duckdb        # run only the named task(s)
uv run python -m pipeline to_jsonl load_duckdb
uv run python -m pipeline --list             # show available tasks
```

`uv run` uses the project's `.venv/` automatically. You don't need to activate it.

Named tasks always run in registry order, regardless of the order you type them. The run stops at the first failing task and exits with code 1. An unknown task name fails immediately and lists the valid names.

## Logs

Each run (except `--list`) writes a log file to `logs/pipeline_<YYYYmmdd_HHMMSS>.log` at the project root. The folder is git-ignored.

- The terminal shows INFO and above.
- The log file also records DEBUG messages, the command line, the Python version and the platform. When a run fails, it records the **full traceback**, which the terminal leaves out to stay readable.
- The first line of every run says which log file it's writing to.

Old logs aren't deleted automatically. Clear out `logs/` whenever you like.

## Configuring paths for your machine

Default paths live in `config.default.toml` at the project root (committed):

```toml
[paths]
raw_dir      = 'data/raw'
interim_dir  = 'data/interim'
jsonl_file   = 'data/processed/assessments.jsonl'
duckdb_file  = 'data/processed/assessments.duckdb'
rejects_file = 'data/processed/rejects.jsonl'
```

If your files are in the default locations, you don't need to configure anything.

To change a path, create `config.local.toml` next to it (git-ignored) with **only** the keys you want to override:

```toml
[paths]
raw_dir = '~/Downloads/now_secure_zips'
```

- Relative paths resolve from the project root; absolute and `~` paths are used as-is.
- Keys must already exist in `config.default.toml`, so a typo like `rawdir` stops the run with an error instead of being silently ignored.

## The DuckDB database

`load_duckdb` rebuilds `data/processed/assessments.duckdb` from scratch on every run, with three tables:

| Table | One row per | Key columns |
|---|---|---|
| `assessments` | JSONL line (app) | `id` (= JSONL line number), `group_name`, `source_file`, `title`, `has_assessment`, `platform_type`, `package_key`, `publisher_name`, `created_at`, `score`, `finding_count` |
| `findings` | finding | `assessment_id` → `assessments.id`, `check_id`, `impact_type`, `affected`, `cvss`, `check` (JSON) |
| `assessment_raw` | app with an assessment | `assessment_id` → `assessments.id`, `analysis` (the raw dynamic/static/yaapStatic scan output, as JSON) |

Apps with `assessment: null` in the source are kept in `assessments` with `has_assessment = false` and NULL fields, so you can still see which apps have no analysis. Use `assessment_raw` only when you need the raw scan output: it holds about 99% of the data.

**Querying it** (open it read-only while someone else might be running the pipeline):

```bash
uv run python -c "import duckdb; print(duckdb.connect('data/processed/assessments.duckdb', read_only=True).sql('''
    SELECT a.group_name, f.impact_type, count(*) AS n
    FROM findings f JOIN assessments a ON a.id = f.assessment_id
    WHERE f.affected
    GROUP BY ALL ORDER BY 1, 3 DESC
'''))"
```

Any DuckDB client works too: the `duckdb` CLI, DBeaver, or Jupyter with `duckdb.connect(...)`.

**How ingestion works and handles bad data:**
- **One line at a time:** the JSONL is streamed line by line, so memory is bounded by the largest line (about 35 MB), not the whole file. Progress is logged every 25 lines, and every line is logged with its size and finding count in the log file.
- **Each line is validated before anything is inserted:** valid JSON, required fields present, correct types, parseable timestamps. A line is never half-loaded.
- **Bad lines are quarantined:**
  - Each one is logged as a WARNING with its line number, byte offset and the exact field, for example `assessment.report.findings[12].cvss: expected a number, got str`.
  - It's also written to `data/processed/rejects.jsonl`, so you can jump straight to it.
  - Every good line still loads. Then the task **fails (exit 1)**, so problems are never missed.
  - A clean run deletes any old `rejects.jsonl`.
- **No half-built database:** the database is built in a temporary file and swapped in only when it's complete, so a failed or interrupted run leaves the previous database in place.
- **Windows:** close every connection to the database before re-running `load_duckdb`, because Windows won't let it replace an open file.

## Project layout

```
README.md
config.default.toml        # committed default paths
config.local.toml          # your overrides (git-ignored, optional)
pyproject.toml             # project metadata and dependencies (uv)
uv.lock                    # exact dependency versions (commit it)
.venv/                     # created by uv sync (git-ignored)
logs/                      # one log file per run (git-ignored)
data/                      # data only, no code (git-ignored; you supply data/raw/)
  raw/                     # source .zip files: original inputs, never modified
  interim/                 # extracted files (created by unzip)
  processed/               # assessments.jsonl (to_jsonl), assessments.duckdb + rejects.jsonl (load_duckdb)
pipeline/                  # code: a Python package
  __main__.py              # CLI entry point (uv run python -m pipeline)
  runner.py                # PipelineRunner: selects, times and runs tasks
  run_log.py               # terminal + per-run log file setup
  config.py                # loads config.default.toml + config.local.toml
  tasks/
    __init__.py            # TASKS registry: defines run order
    base.py                # Task base class and TaskError
    unzip.py
    to_jsonl.py
    load_duckdb.py
```

## Adding a task

1. **Create the task** in `pipeline/tasks/<name>.py`:

   ```python
   from pipeline.config import Config
   from pipeline.tasks.base import Task, TaskError


   class MyTask(Task):
       '''One-line description shown by --list.'''

       name = 'my_task'   # the name used on the command line

       def run(self, config: Config):
           src = config.path('jsonl_file')
           if not src.is_file():
               raise TaskError(f'Input not found: {src} (run to_jsonl first)')
           self.logger.info(f'Processing {src}')
           ...
   ```

2. **Register it** in `pipeline/tasks/__init__.py`. Its position in the list is its run order:

   ```python
   from pipeline.tasks.my_task import MyTask

   TASKS = [
       UnzipTask(),
       ToJsonlTask(),
       LoadDuckDbTask(),
       MyTask(),
   ]
   ```

3. **Add any new paths** under `[paths]` in `config.default.toml`, then read them with `config.path('new_key')`. You don't need to change the config code.

4. **Check it**:

   ```bash
   uv run python -m pipeline --list
   uv run python -m pipeline my_task
   ```

### Task conventions

- **Fail fast:** check inputs at the top of `run()` and raise `TaskError` with a message that says what's wrong and how to fix it. The runner logs it with the task name and stops the pipeline.
- **Don't catch and ignore errors:** if you catch an exception, raise a clearer `TaskError` from it (`raise TaskError(...) from e`).
- **Log with `self.logger`** (named `task.<name>`), not `print`.
- **Get every path through `config.path(...)`** and never hardcode one, so the task works on everyone's machine.
- **Use absolute imports** (`from pipeline.… import …`) inside the package.
- **Make tasks safe to re-run:** a task should overwrite its outputs, so running it twice gives the same result.
