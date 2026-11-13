'''Run named analysis queries from sql/queries/*.sql against the analysis database (always read-only).

    uv run python -m pipeline.queries list                    # every query name, by file
    uv run python -m pipeline.queries run                     # run every query
    uv run python -m pipeline.queries run tier0_              # only queries whose name starts with tier0_
    uv run python -m pipeline.queries run --file profiling    # only the queries in sql/queries/profiling.sql
    uv run python -m pipeline.queries sql "SELECT count(*) FROM tier0"    # one-off query
    uv run python -m pipeline.queries shape static.application_overprivileged_check   # outline raw JSON

Each statement in a query file needs a `-- name: <unique_name>` line and must end with a semicolon at the
end of a line. Helper tables must be CREATE TEMP TABLE, because the database is opened read-only.
'''
import argparse
import json
import logging
import re
import sys
from pathlib import Path

import duckdb

from pipeline.config import Config

logger = logging.getLogger('queries')


class QueryError(Exception):
    pass


class NamedQuery:
    '''One `-- name:` statement from a query file.'''

    def __init__(self, name: str, sql: str, source: Path):
        self.name = name
        self.sql = sql
        self.source = source

    @property
    def is_create(self) -> bool:
        return re.sub(r'--[^\n]*', '', self.sql).strip().upper().startswith('CREATE')


class QueryLibrary:
    '''Every named statement in the .sql files of [paths].queries_dir.'''

    NAME_RE = re.compile(r'^--\s*name:\s*(\S+)', re.MULTILINE)

    def __init__(self, queries_dir: Path):
        if not queries_dir.is_dir():
            raise QueryError(f'queries_dir not found: {queries_dir}')
        self.queries_dir = queries_dir
        self.queries = self._load()

    def _load(self) -> list[NamedQuery]:
        queries, seen = [], {}
        for path in sorted(self.queries_dir.glob('*.sql')):
            for query in self._parse(path):
                if query.name in seen:
                    raise QueryError(f'Query name {query.name!r} is used in both {seen[query.name].name} and {path.name}')
                seen[query.name] = path
                queries.append(query)
        if not queries:
            raise QueryError(f'No named queries in {self.queries_dir}')
        return queries

    def _parse(self, path: Path) -> list[NamedQuery]:
        parsed = []
        for chunk in re.split(r';\s*$', path.read_text(encoding='utf-8'), flags=re.MULTILINE):
            sql = '\n'.join(line for line in chunk.splitlines() if not line.startswith('-- ====')).strip()
            if not re.sub(r'--[^\n]*', '', sql).strip():
                continue  # comments only (file header, section banner)
            match = self.NAME_RE.search(sql)
            if not match:
                raise QueryError(f'Statement without a `-- name:` line in {path.name}:\n{sql[:200]}')
            parsed.append(NamedQuery(match.group(1), sql, path))
        return parsed

    def select(self, prefix: str = '', file_stem: str | None = None) -> list[NamedQuery]:
        pool = self.queries
        if file_stem is not None:
            pool = [q for q in pool if q.source.stem == file_stem]
            if not pool:
                stems = sorted({q.source.stem for q in self.queries})
                raise QueryError(f'No query file {file_stem}.sql in {self.queries_dir} (files: {stems})')
        if not any(q.name.startswith(prefix) for q in pool):
            raise QueryError(f'No query names start with {prefix!r} (see `list`)')
        # CREATE statements always run, so a filtered run still has the helper tables later queries use.
        return [q for q in pool if q.name.startswith(prefix) or q.is_create]


class QueryRunner:
    '''Prints query results from a read-only connection to the analysis database.'''

    def __init__(self, db_file: Path):
        if not db_file.is_file():
            raise QueryError(f'Database not found: {db_file} (run `uv run python -m pipeline` first)')
        self.db_file = db_file

    def run(self, queries: list[tuple[str, str]]):
        con = duckdb.connect(str(self.db_file), read_only=True)
        try:
            for name, sql in queries:
                try:
                    result = con.sql(sql)
                except duckdb.CatalogException as e:
                    raise QueryError(f'{name}: {e} (is the table built? run `uv run python -m pipeline`)') from e
                except duckdb.Error as e:
                    raise QueryError(f'{name}: {e}') from e
                print(f'\n### {name}')
                if result is None:
                    print('(ok)')
                else:
                    result.show(max_rows=80, max_width=250)
        except QueryError:
            raise
        except Exception as e:
            logger.error(f'Query run failed: {e}')
            raise
        finally:
            con.close()


class ShapeInspector:
    '''Prints a truncated outline of one raw scan task, to see its JSON structure without dumping megabytes.'''

    KINDS = ('static', 'dynamic', 'yaapStatic')

    def __init__(self, db_file: Path, max_depth: int = 5):
        if not db_file.is_file():
            raise QueryError(f'Database not found: {db_file}')
        self.db_file = db_file
        self.max_depth = max_depth

    def show(self, spec: str, offset: int = 20):
        kind, _, task = spec.partition('.')
        if kind not in self.KINDS or not re.fullmatch(r'\w+', task):
            raise QueryError(f'Expected <{"|".join(self.KINDS)}>.<task_name>, got {spec!r}')
        con = duckdb.connect(str(self.db_file), read_only=True)
        try:
            # Pick a mid-sized example (OFFSET into the largest) so the outline is representative.
            row = con.execute(f'''
                WITH x AS (
                    SELECT a.package_key, json_extract(r.analysis, '$.task.{kind}.result.{task}') AS v
                    FROM assessment_raw r JOIN assessments a ON a.id = r.assessment_id
                    WHERE a.platform_type = 'android'
                )
                SELECT package_key, v FROM x WHERE v IS NOT NULL
                ORDER BY length(CAST(v AS VARCHAR)) DESC LIMIT 1 OFFSET ?
            ''', [offset]).fetchone()
            if row is None:
                raise QueryError(f'No Android app has {spec}')
            print(f'===== {spec} ({row[0]})')
            print(json.dumps(self._outline(json.loads(row[1])), indent=1, ensure_ascii=False)[:3500])
        except QueryError:
            raise
        except Exception as e:
            logger.error(f'Shape inspection of {spec} failed: {e}')
            raise
        finally:
            con.close()

    def _outline(self, value, depth: int = 0):
        if isinstance(value, dict):
            if depth >= self.max_depth:
                return '{...}'
            return {k: self._outline(v, depth + 1) for k, v in list(value.items())[:25]}
        if isinstance(value, list):
            return [f'len={len(value)}', self._outline(value[0], depth + 1)] if value else []
        if isinstance(value, str):
            return value[:80]
        return value


class Cli:
    def __init__(self):
        config = Config.load()
        self.db_file = config.path('duckdb_file')
        self.queries_dir = config.path('queries_dir')

    def main(self, argv: list[str]) -> int:
        parser = argparse.ArgumentParser(prog='python -m pipeline.queries', description=__doc__,
                                         formatter_class=argparse.RawDescriptionHelpFormatter)
        sub = parser.add_subparsers(dest='command', required=True)
        sub.add_parser('list', help='list query names by file')
        run = sub.add_parser('run', help='run named queries from sql/queries/')
        run.add_argument('prefix', nargs='?', default='', help='only run queries whose name starts with this')
        run.add_argument('--file', dest='file_stem', help='only run queries from sql/queries/<FILE>.sql')
        one_off = sub.add_parser('sql', help='run one SQL statement given on the command line')
        one_off.add_argument('statement')
        shape = sub.add_parser('shape', help='outline the JSON shape of raw scan tasks')
        shape.add_argument('specs', nargs='+', metavar='kind.task')
        args = parser.parse_args(argv)
        try:
            if args.command == 'list':
                for query in QueryLibrary(self.queries_dir).queries:
                    print(f'{query.source.name:<18} {query.name}')
            elif args.command == 'run':
                selected = QueryLibrary(self.queries_dir).select(args.prefix, args.file_stem)
                QueryRunner(self.db_file).run([(q.name, q.sql) for q in selected])
            elif args.command == 'sql':
                QueryRunner(self.db_file).run([('sql', args.statement)])
            else:
                inspector = ShapeInspector(self.db_file)
                for spec in args.specs:
                    inspector.show(spec)
            return 0
        except QueryError as e:
            logger.error(str(e))
            return 1


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(levelname)s %(name)s: %(message)s')
    sys.exit(Cli().main(sys.argv[1:]))
