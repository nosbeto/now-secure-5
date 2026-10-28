import json
import os
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import duckdb

from pipeline.config import Config
from pipeline.tasks.base import Task, TaskError

SCHEMA = '''
CREATE TABLE assessments (
    id                       INTEGER PRIMARY KEY,  -- source line number in the JSONL
    group_name               VARCHAR NOT NULL,
    source_file              VARCHAR NOT NULL,
    title                    VARCHAR NOT NULL,
    has_assessment           BOOLEAN NOT NULL,
    ref                      VARCHAR,
    publisher_name           VARCHAR,
    build_version            VARCHAR,
    platform_type            VARCHAR,
    package_key              VARCHAR,
    appstore_application_key VARCHAR,
    created_at               TIMESTAMPTZ,
    latest_analysis_recorded TIMESTAMPTZ,
    score                    DOUBLE,
    finding_count            INTEGER NOT NULL
);
CREATE TABLE findings (
    assessment_id INTEGER NOT NULL REFERENCES assessments (id),
    check_id      VARCHAR NOT NULL,
    impact_type   VARCHAR NOT NULL,
    affected      BOOLEAN NOT NULL,
    cvss          DOUBLE  NOT NULL,
    "check"       JSON
);
CREATE TABLE assessment_raw (
    assessment_id INTEGER NOT NULL REFERENCES assessments (id),
    analysis      JSON NOT NULL
);
'''


class RecordError(TaskError):
    '''A JSONL line that fails validation. `path` points at the offending field.'''

    def __init__(self, path: str, message: str):
        super().__init__(f'{path}: {message}')
        self.path = path


@dataclass
class ParsedAssessment:
    assessment_row: tuple
    finding_rows: list[tuple] = field(default_factory=list)
    analysis_json: str | None = None


class AssessmentParser:
    '''Validates one JSONL line completely before anything is inserted, so a line is never half-loaded.'''

    def parse(self, line_no: int, line: bytes) -> ParsedAssessment:
        if not line.strip():
            raise RecordError('<line>', 'empty line')
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            raise RecordError('<line>', f'invalid JSON: {e}') from e
        self._expect(record, dict, '<line>')

        group_name = self._req(record, '_group', str, '')
        source_file = self._req(record, '_source_file', str, '')
        title = self._req(record, 'title', str, '')
        latest = self._timestamp(record.get('latestAnalysisRecorded'), 'latestAnalysisRecorded')

        assessment = record.get('assessment')
        if assessment is None:
            row = (line_no, group_name, source_file, title, False,
                   None, None, None, None, None, None, None, latest, None, 0)
            return ParsedAssessment(row)
        self._expect(assessment, dict, 'assessment')

        report = self._req(assessment, 'report', dict, 'assessment')
        score = report.get('score')
        if score is not None:
            self._number(score, 'assessment.report.score')
        findings = self._req(report, 'findings', list, 'assessment.report')
        finding_rows = [self._finding(line_no, item, f'assessment.report.findings[{i}]') for i, item in enumerate(findings)]
        analysis = self._req(assessment, 'analysis', dict, 'assessment')

        row = (
            line_no, group_name, source_file, title, True,
            self._req(assessment, 'ref', str, 'assessment'),
            self._req(assessment, 'publisherName', str, 'assessment'),
            self._req(assessment, 'buildVersion', str, 'assessment'),
            self._req(assessment, 'platformType', str, 'assessment'),
            self._req(assessment, 'packageKey', str, 'assessment'),
            self._req(assessment, 'appstoreApplicationKey', str, 'assessment'),
            self._timestamp(self._req(assessment, 'createdAt', str, 'assessment'), 'assessment.createdAt'),
            latest,
            float(score) if score is not None else None,
            len(finding_rows),
        )
        return ParsedAssessment(row, finding_rows, json.dumps(analysis))

    def _finding(self, line_no: int, item, path: str) -> tuple:
        self._expect(item, dict, path)
        check = self._req(item, 'check', dict, path)
        return (
            line_no,
            self._req(item, 'checkId', str, path),
            self._req(item, 'impactType', str, path),
            self._req(item, 'affected', bool, path),
            float(self._number(self._req_present(item, 'cvss', path), f'{path}.cvss')),
            json.dumps(check),
        )

    @staticmethod
    def _join(parent: str, key: str) -> str:
        return f'{parent}.{key}' if parent else key

    def _req_present(self, obj: dict, key: str, parent: str):
        if key not in obj or obj[key] is None:
            raise RecordError(self._join(parent, key), 'missing or null')
        return obj[key]

    def _req(self, obj: dict, key: str, kind: type, parent: str):
        value = self._req_present(obj, key, parent)
        self._expect(value, kind, self._join(parent, key))
        return value

    @staticmethod
    def _expect(value, kind: type, path: str):
        # bool is a subclass of int, so it must never pass as a number (and vice versa).
        if not isinstance(value, kind) or (kind is not bool and isinstance(value, bool)):
            raise RecordError(path, f'expected {kind.__name__}, got {type(value).__name__}')

    @staticmethod
    def _number(value, path: str):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise RecordError(path, f'expected a number, got {type(value).__name__}')
        return value

    @staticmethod
    def _timestamp(value, path: str) -> datetime | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise RecordError(path, f'expected an ISO-8601 string, got {type(value).__name__}')
        try:
            return datetime.fromisoformat(value)
        except ValueError as e:
            raise RecordError(path, f'invalid timestamp {value!r}') from e


class LoadDuckDbTask(Task):
    '''Ingest [paths].jsonl_file line by line into the DuckDB database at [paths].duckdb_file.'''

    name = 'load_duckdb'
    PROGRESS_EVERY = 25

    def run(self, config: Config):
        jsonl_file = config.path('jsonl_file')
        duckdb_file = config.path('duckdb_file')
        rejects_file = config.path('rejects_file')
        if not jsonl_file.is_file():
            raise TaskError(f'jsonl_file not found: {jsonl_file} (run the to_jsonl task first)')

        duckdb_file.parent.mkdir(parents=True, exist_ok=True)
        rejects_file.parent.mkdir(parents=True, exist_ok=True)
        rejects_file.unlink(missing_ok=True)  # a clean run must leave no stale rejects behind

        tmp_file = duckdb_file.with_name(f'{duckdb_file.name}.tmp')
        self._remove_db(tmp_file)
        try:
            loaded, rejected = self._build(jsonl_file, tmp_file, rejects_file)
            os.replace(tmp_file, duckdb_file)  # atomic: readers never see a half-built database
        except Exception:
            self._remove_db(tmp_file)
            raise
        self.logger.info(f'Database ready at {duckdb_file} ({loaded} line(s) loaded)')

        if rejected:
            raise TaskError(f'{rejected} line(s) rejected and not loaded; see {rejects_file} and the WARNING lines above')

    def _build(self, jsonl_file: Path, db_file: Path, rejects_file: Path) -> tuple[int, int]:
        parser = AssessmentParser()
        loaded = rejected = 0
        start = time.monotonic()
        con = duckdb.connect(str(db_file))
        try:
            con.execute(SCHEMA)
            con.begin()
            offset = 0
            with jsonl_file.open('rb') as src:
                for line_no, line in enumerate(src, start=1):
                    line_offset, offset = offset, offset + len(line)
                    try:
                        parsed = parser.parse(line_no, line)
                    except RecordError as e:
                        rejected += 1
                        self.logger.warning(f'Line {line_no} (byte {line_offset}) rejected: {e}')
                        self._write_reject(rejects_file, line_no, line_offset, e, line)
                        continue

                    self._insert(con, parsed)
                    loaded += 1
                    self.logger.debug(f'Line {line_no}: {len(line):,} bytes, {len(parsed.finding_rows)} findings')
                    if line_no % self.PROGRESS_EVERY == 0:
                        self.logger.info(f'Processed {line_no} lines ({offset / 1e6:,.0f} MB, {time.monotonic() - start:.0f}s)')
            con.commit()
            self._log_counts(con)
            return loaded, rejected
        except Exception as e:
            self.logger.error(f'Aborting load: {e}')
            raise
        finally:
            con.close()

    @staticmethod
    def _insert(con, parsed: ParsedAssessment):
        con.execute(f'INSERT INTO assessments VALUES ({", ".join("?" * len(parsed.assessment_row))})', parsed.assessment_row)
        if parsed.finding_rows:
            con.executemany('INSERT INTO findings VALUES (?, ?, ?, ?, ?, ?)', parsed.finding_rows)
        if parsed.analysis_json is not None:
            con.execute('INSERT INTO assessment_raw VALUES (?, ?)', (parsed.assessment_row[0], parsed.analysis_json))

    def _log_counts(self, con):
        for table in ('assessments', 'findings', 'assessment_raw'):
            count = con.execute(f'SELECT count(*) FROM {table}').fetchone()[0]
            self.logger.info(f'{table}: {count:,} rows')

    @staticmethod
    def _write_reject(rejects_file: Path, line_no: int, offset: int, error: RecordError, line: bytes):
        entry = {
            'line': line_no,
            'byte_offset': offset,
            'field': error.path,
            'error': str(error),
            'preview': line[:200].decode('utf-8', errors='replace'),
        }
        with rejects_file.open('a', encoding='utf-8') as out:
            out.write(json.dumps(entry) + '\n')

    @staticmethod
    def _remove_db(db_file: Path):
        for path in (db_file, db_file.with_name(f'{db_file.name}.wal')):
            path.unlink(missing_ok=True)
