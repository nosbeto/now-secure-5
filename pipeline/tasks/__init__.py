from pipeline.tasks.build_sql import BuildSqlTask
from pipeline.tasks.flatten import FlattenTask
from pipeline.tasks.load_duckdb import LoadDuckDbTask
from pipeline.tasks.permission_meta import PermissionMetaTask
from pipeline.tasks.to_jsonl import ToJsonlTask
from pipeline.tasks.unzip import UnzipTask

# The task registry, in run order. To add a task: create tasks/<name>.py with a Task subclass and append it here.
TASKS = [
    UnzipTask(),
    ToJsonlTask(),
    LoadDuckDbTask(),
    FlattenTask(),
    PermissionMetaTask(),
    BuildSqlTask(),
]
