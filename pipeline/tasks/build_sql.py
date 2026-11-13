import csv
from pathlib import Path

import duckdb

from pipeline.config import Config
from pipeline.tasks.base import Task, TaskError

# Seed CSVs in [paths].seeds_dir, as {table: {column: type}}. Every CSV in the folder must be listed here, and the
# header must match the columns in this order.
SEEDS = {
    'perm_group': {'perm': 'VARCHAR', 'perm_group': 'VARCHAR'},
}

# Tables the tier SQL must create. Each one gets a Parquet copy in [paths].tidy_dir.
TIER_TABLES = ('tier0',)

# Protection levels (from permission_meta) that make an unused permission Tier 0. Must match sql/tiers/10_tier0.sql.
TIER0_LEVELS = ('dangerous', 'signature')


def sql_string(value: str) -> str:
    '''Quote a value as a SQL string literal.'''
    escaped = value.replace("'", "''")
    return f"'{escaped}'"


class SeedLoader:
    '''Loads every seed CSV into a typed table of the same name.'''

    def __init__(self, con, seeds_dir: Path):
        self.con = con
        self.seeds_dir = seeds_dir

    def load(self) -> dict[str, int]:
        try:
            if not self.seeds_dir.is_dir():
                raise TaskError(f'seeds_dir not found: {self.seeds_dir}')
            found = {path.stem for path in self.seeds_dir.glob('*.csv')}
            unknown, missing = found - set(SEEDS), set(SEEDS) - found
            if unknown:
                raise TaskError(f'Unknown seed file(s) in {self.seeds_dir}: {sorted(unknown)} (declare them in SEEDS)')
            if missing:
                raise TaskError(f'Missing seed file(s) in {self.seeds_dir}: {sorted(missing)}')
            return {name: self._load_one(name, columns) for name, columns in SEEDS.items()}
        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f'Could not load seeds from {self.seeds_dir}: {e}') from e

    def _load_one(self, name: str, columns: dict[str, str]) -> int:
        path = self.seeds_dir / f'{name}.csv'
        with path.open(newline='', encoding='utf-8') as f:
            header = next(csv.reader(f), None)
        if header != list(columns):
            raise TaskError(f'{path.name}: header must be {list(columns)}, got {header}')
        spec = ', '.join(f'{sql_string(col)}: {sql_string(kind)}' for col, kind in columns.items())
        self.con.execute(
            f'CREATE OR REPLACE TABLE {name} AS '
            f'SELECT * FROM read_csv({sql_string(str(path))}, header = true, columns = {{{spec}}})'
        )
        rows = self.con.execute(f'SELECT count(*) FROM {name}').fetchone()[0]
        if rows == 0:
            raise TaskError(f'{path.name} has no rows')
        return rows


class PermGroupValidator:
    '''Checks seeds/perm_group.csv is well-formed and gives a group to every permission that can be Tier 0.'''

    def __init__(self, con):
        self.con = con

    def validate(self):
        try:
            duplicates = self._list('SELECT perm FROM perm_group GROUP BY perm HAVING count(*) > 1')
            if duplicates:
                raise TaskError(f'perm_group.csv: duplicate perm rows: {duplicates}')
            bad = self._list('''
                SELECT coalesce(perm, '<empty perm>') FROM perm_group
                WHERE perm IS NULL OR perm_group IS NULL OR perm_group <> upper(perm_group)
            ''')
            if bad:
                raise TaskError(f'perm_group.csv: rows need a perm and an UPPERCASE perm_group: {bad}')
            levels = ', '.join(sql_string(level) for level in TIER0_LEVELS)
            missing = self._list(f'''
                SELECT perm FROM permission_meta
                WHERE protection_level IN ({levels}) AND perm NOT IN (SELECT perm FROM perm_group)
                ORDER BY perm
            ''')
            if missing:
                raise TaskError(f'perm_group.csv has no group for these {"/".join(TIER0_LEVELS)} permissions: {missing}')
        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f'Could not validate perm_group: {e}') from e

    def _list(self, sql: str) -> list[str]:
        return [row[0] for row in self.con.execute(sql).fetchall()]


class SqlFolderRunner:
    '''Runs every .sql file in one folder in file-name order (10_tier0.sql, 20_..., ...).'''

    def __init__(self, con, sql_dir: Path, required: bool):
        self.con = con
        self.sql_dir = sql_dir
        self.required = required  # sql/tiers must have files; sql/views may be empty

    def files(self) -> list[Path]:
        if not self.sql_dir.is_dir():
            raise TaskError(f'SQL folder not found: {self.sql_dir}')
        files = sorted(self.sql_dir.glob('*.sql'))
        if self.required and not files:
            raise TaskError(f'No .sql files in {self.sql_dir}')
        return files

    def run(self) -> list[str]:
        ran = []
        for path in self.files():
            try:
                self.con.execute(path.read_text(encoding='utf-8'))
            except duckdb.Error as e:
                raise TaskError(f'{path.name} failed: {e}') from e
            ran.append(path.name)
        return ran


class BuildSqlTask(Task):
    '''Load seeds/*.csv, then run sql/tiers/*.sql (tier tables) and sql/views/*.sql (shared views).'''

    name = 'build_sql'
    REQUIRED_TABLES = {'app', 'app_permission', 'permission_meta'}

    def run(self, config: Config):
        duckdb_file = config.path('duckdb_file')
        if not duckdb_file.is_file():
            raise TaskError(f'duckdb_file not found: {duckdb_file} (run the load_duckdb and flatten tasks first)')
        seeds_dir, tidy_dir = config.path('seeds_dir'), config.path('tidy_dir')
        tiers_dir, views_dir = config.path('tiers_dir'), config.path('views_dir')

        con = duckdb.connect(str(duckdb_file))
        try:
            missing = self.REQUIRED_TABLES - {r[0] for r in con.execute('SHOW TABLES').fetchall()}
            if missing:
                raise TaskError(f'{duckdb_file} has no {sorted(missing)} table(s) (run the flatten and permission_meta tasks first)')
            self._build(con, seeds_dir, tiers_dir, views_dir)
            self._export(con, tidy_dir)
            self._report(con)
        except TaskError:
            raise
        except Exception as e:
            self.logger.error(f'Aborting build_sql: {e}')
            raise TaskError(f'build_sql failed: {e}') from e
        finally:
            con.close()

    def _build(self, con, seeds_dir: Path, tiers_dir: Path, views_dir: Path):
        # One transaction: a bad seed or SQL file leaves the previous tables and views untouched.
        con.begin()
        try:
            for name, rows in SeedLoader(con, seeds_dir).load().items():
                self.logger.info(f'Loaded seed {name}: {rows:,} rows')
            PermGroupValidator(con).validate()
            for file_name in SqlFolderRunner(con, tiers_dir, required=True).run():
                self.logger.info(f'Ran tiers/{file_name}')
            missing = set(TIER_TABLES) - {r[0] for r in con.execute('SHOW TABLES').fetchall()}
            if missing:
                raise TaskError(f'The tier SQL did not create {sorted(missing)} (see TIER_TABLES)')
            # Views run after the tiers so they can build on them.
            for file_name in SqlFolderRunner(con, views_dir, required=False).run():
                self.logger.info(f'Ran views/{file_name}')
            con.commit()
        except Exception:
            con.rollback()
            raise

    def _export(self, con, tidy_dir: Path):
        tidy_dir.mkdir(parents=True, exist_ok=True)
        for name in TIER_TABLES:
            con.execute(f'COPY {name} TO {sql_string(str(tidy_dir / f"{name}.parquet"))} (FORMAT parquet)')
        self.logger.info(f'Parquet copies of {", ".join(TIER_TABLES)} written to {tidy_dir}')

    def _report(self, con):
        apps, dangerous_apps, pairs, scored = con.execute('''
            SELECT count(DISTINCT id),
                   count(DISTINCT id) FILTER (protection_level = 'dangerous'),
                   count(*),
                   (SELECT count(*) FROM app WHERE has_overprivileged_check)
            FROM tier0
        ''').fetchone()
        if scored == 0:
            raise TaskError('No app has the over-privilege check, so Tier 0 cannot be measured')
        self.logger.info(
            f'tier0: {pairs:,} pairs in {apps} of {scored} apps ({apps / scored:.1%}); '
            f'{dangerous_apps} apps ({dangerous_apps / scored:.1%}) with a dangerous unused permission'
        )
