import json
from pathlib import Path

import duckdb
import pandas as pd

from pipeline.config import Config
from pipeline.tasks.base import Task, TaskError

# Tidy tables written into the analysis database, as {table: [(column, type), ...]}. Only what Tier 0 (declared but
# unused permission) and Tier 1 (not justified by the app's category) need. Keyed by `id` (= assessments.id), Android only.
TABLES = {
    'app': [
        ('id', 'INTEGER'), ('package_key', 'VARCHAR'), ('title', 'VARCHAR'), ('publisher_name', 'VARCHAR'),
        ('group_name', 'VARCHAR'), ('version_code', 'BIGINT'), ('sha256', 'VARCHAR'),
        ('target_sdk', 'INTEGER'), ('min_sdk', 'INTEGER'),
        ('has_static', 'BOOLEAN'), ('has_dynamic', 'BOOLEAN'), ('has_overprivileged_check', 'BOOLEAN'),
        ('overprivileged_vulnerable', 'BOOLEAN'), ('on_play_store', 'BOOLEAN'),
        ('extra_not_granted', 'INTEGER'), ('dynamic_permissions_match', 'BOOLEAN'),
    ],
    'app_permission': [('id', 'INTEGER'), ('perm', 'VARCHAR'), ('is_extra', 'BOOLEAN')],
}

class AppFlattener:
    '''Turns one app's raw analysis JSON into rows for each tidy table. Rows accumulate in `self.rows`.'''

    def __init__(self):
        self.rows = {name: [] for name in TABLES}

    def add(self, app_id: int, meta: dict, analysis: dict):
        task = self._dict(analysis.get('task'), 'task')
        s = (task.get('static') or {}).get('result')
        d = (task.get('dynamic') or {}).get('result')
        has_static, has_dynamic = isinstance(s, dict), isinstance(d, dict)
        s, d = (s if has_static else {}), (d if has_dynamic else {})
        self._app(app_id, meta, s, d, has_static, has_dynamic)

    def _app(self, app_id: int, meta: dict, s: dict, d: dict, has_static: bool, has_dynamic: bool):
        static_summary = s.get('run_summary') or {}
        dynamic_summary = d.get('run_summary') or {}
        dynamic_apk = dynamic_summary.get('apk_info') or {}

        op = s.get('application_overprivileged_check')
        granted = (op or {}).get('granted_permissions') or []
        extra = set((op or {}).get('extra_permissions') or [])
        for perm in granted:
            self.rows['app_permission'].append((app_id, perm, perm in extra))
        dynamic_perms = dynamic_apk.get('permissions')
        permissions_match = (set(dynamic_perms) == set(granted)) if op is not None and dynamic_perms is not None else None

        self.rows['app'].append((
            app_id, meta['package_key'], meta['title'], meta['publisher_name'], meta['group_name'],
            (static_summary.get('apk_info') or {}).get('versionCode'),
            (static_summary.get('hashes') or {}).get('sha256'),
            dynamic_apk.get('targetSDK'), dynamic_apk.get('minSDK'),
            has_static, has_dynamic, op is not None,
            (op.get('vulnerable') is True) if op is not None else None,
            dynamic_summary.get('apk_available_on_play_store') is True if has_dynamic else None,
            len(extra - set(granted)) if op is not None else None,
            permissions_match,
        ))

    @staticmethod
    def _dict(value, path: str) -> dict:
        if not isinstance(value, dict):
            raise TaskError(f'{path}: expected an object, got {type(value).__name__}')
        return value


class TableWriter:
    '''Bulk-loads accumulated rows into typed DuckDB tables through pandas (far faster than executemany).'''

    PANDAS_DTYPES = {'INTEGER': 'Int64', 'BIGINT': 'Int64', 'DOUBLE': 'Float64', 'BOOLEAN': 'boolean', 'VARCHAR': 'string'}

    def __init__(self, con):
        self.con = con

    def write(self, name: str, rows: list[tuple]):
        columns = TABLES[name]
        ddl = ', '.join(f'{col} {kind}' for col, kind in columns)
        self.con.execute(f'CREATE OR REPLACE TABLE {name} ({ddl})')
        if not rows:
            return
        try:
            frame = pd.DataFrame({
                col: pd.array([self._coerce(row[i], kind) for row in rows], dtype=self.PANDAS_DTYPES[kind])
                for i, (col, kind) in enumerate(columns)
            })
        except (TypeError, ValueError) as e:
            raise TaskError(f'Could not build table {name}: {e}') from e
        self.con.register('flatten_rows', frame)
        try:
            self.con.execute(f'INSERT INTO {name} SELECT * FROM flatten_rows')
        finally:
            self.con.unregister('flatten_rows')

    @staticmethod
    def _coerce(value, kind: str):
        if value is None:
            return None
        if kind == 'VARCHAR':
            return value if isinstance(value, str) else json.dumps(value)
        if kind in ('INTEGER', 'BIGINT'):
            return int(value)
        if kind == 'DOUBLE':
            return float(value)
        return bool(value)


class FlattenTask(Task):
    '''Flatten the raw Android scan JSON in assessment_raw into tidy tables (+ Parquet copies in [paths].tidy_dir).'''

    name = 'flatten'
    PROGRESS_EVERY = 25

    def run(self, config: Config):
        duckdb_file = config.path('duckdb_file')
        tidy_dir = config.path('tidy_dir')
        if not duckdb_file.is_file():
            raise TaskError(f'duckdb_file not found: {duckdb_file} (run the load_duckdb task first)')

        con = duckdb.connect(str(duckdb_file))
        try:
            missing = {'assessments', 'assessment_raw'} - {r[0] for r in con.execute('SHOW TABLES').fetchall()}
            if missing:
                raise TaskError(f'{duckdb_file} has no {sorted(missing)} table(s) (run the load_duckdb task first)')
            flattener = self._flatten(con)
            self._write(con, flattener)
            self._export(con, tidy_dir)
        except TaskError:
            raise
        except Exception as e:
            self.logger.error(f'Aborting flatten: {e}')
            raise TaskError(f'Flatten failed: {e}') from e
        finally:
            con.close()

    def _flatten(self, con) -> AppFlattener:
        flattener = AppFlattener()
        result = con.execute('''
            SELECT a.id, a.package_key, a.title, a.publisher_name, a.group_name, r.analysis
            FROM assessments a
            JOIN assessment_raw r ON r.assessment_id = a.id
            WHERE a.platform_type = 'android'
            ORDER BY a.id
        ''')
        count = 0
        while (row := result.fetchone()) is not None:
            app_id, package_key, title, publisher_name, group_name, analysis = row
            meta = {'package_key': package_key, 'title': title, 'publisher_name': publisher_name, 'group_name': group_name}
            try:
                flattener.add(app_id, meta, json.loads(analysis))
            except (TaskError, json.JSONDecodeError) as e:
                raise TaskError(f'App id {app_id} ({package_key}): {e}') from e
            count += 1
            if count % self.PROGRESS_EVERY == 0:
                self.logger.info(f'Flattened {count} apps')
        if count == 0:
            raise TaskError('No Android apps found in assessment_raw')
        self.logger.info(f'Flattened {count} Android apps')
        return flattener

    def _write(self, con, flattener: AppFlattener):
        # One transaction: a failure leaves the previous tidy tables untouched.
        writer = TableWriter(con)
        con.begin()
        try:
            for name in TABLES:
                writer.write(name, flattener.rows[name])
            con.commit()
        except Exception:
            con.rollback()
            raise
        for name in TABLES:
            self.logger.info(f'{name}: {con.execute(f"SELECT count(*) FROM {name}").fetchone()[0]:,} rows')

    def _export(self, con, tidy_dir: Path):
        tidy_dir.mkdir(parents=True, exist_ok=True)
        for name in TABLES:
            target = str(tidy_dir / f'{name}.parquet').replace("'", "''")
            con.execute(f"COPY {name} TO '{target}' (FORMAT parquet)")
        self.logger.info(f'Parquet copies of {len(TABLES)} tables written to {tidy_dir}')
