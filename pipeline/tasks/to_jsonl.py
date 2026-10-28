import json

from pipeline.config import Config
from pipeline.tasks.base import Task, TaskError


class ToJsonlTask(Task):
    '''Combine each group's JSON files in [paths].interim_dir into one JSONL file at [paths].jsonl_file.'''

    name = 'to_jsonl'
    SKIP_DIRS = {'__MACOSX'}

    def run(self, config: Config):
        interim_dir = config.path('interim_dir')
        jsonl_file = config.path('jsonl_file')
        if not interim_dir.is_dir():
            raise TaskError(f'interim_dir does not exist or is not a directory: {interim_dir} (run the unzip task first)')

        group_dirs = sorted(p for p in interim_dir.iterdir() if p.is_dir() and p.name not in self.SKIP_DIRS)
        jsonl_file.parent.mkdir(parents=True, exist_ok=True)

        count = 0
        with jsonl_file.open('w') as out:
            for group_dir in group_dirs:
                for json_file in sorted(group_dir.glob('*.json')):
                    try:
                        records = json.loads(json_file.read_text())
                    except json.JSONDecodeError as e:
                        raise TaskError(f'Invalid JSON in {json_file}: {e}') from e
                    if not isinstance(records, list):
                        raise TaskError(f'Expected a JSON array in {json_file}, got {type(records).__name__}')
                    for record in records:
                        record['_group'] = group_dir.name
                        record['_source_file'] = json_file.name
                        out.write(json.dumps(record) + '\n')
                        count += 1
        self.logger.info(f'Wrote {count} records to {jsonl_file}')
