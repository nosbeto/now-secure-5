from pipeline.tasks.load_duckdb import LoadDuckDbTask
from pipeline.tasks.to_jsonl import ToJsonlTask
from pipeline.tasks.unzip import UnzipTask

# The task registry, in run order. To add a task: create tasks/<name>.py with a Task subclass and append it here.
TASKS = [
    UnzipTask(),
    ToJsonlTask(),
    LoadDuckDbTask(),
]
