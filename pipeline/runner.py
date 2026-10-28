import logging
import time

from pipeline.config import Config
from pipeline.tasks.base import Task

logger = logging.getLogger('pipeline')


class PipelineRunner:
    def __init__(self, tasks: list[Task]):
        names = [task.name for task in tasks]
        duplicates = {name for name in names if names.count(name) > 1}
        if duplicates:
            raise ValueError(f'Duplicate task names in registry: {sorted(duplicates)}')
        self.tasks = tasks

    def select(self, names: list[str]) -> list[Task]:
        '''Return the requested tasks in registry order (all tasks if none are named).'''
        if not names:
            return self.tasks
        valid = [task.name for task in self.tasks]
        unknown = [name for name in names if name not in valid]
        if unknown:
            raise ValueError(f'Unknown task(s): {unknown}. Valid tasks: {valid}')
        return [task for task in self.tasks if task.name in names]

    def run(self, names: list[str]):
        selected = self.select(names)
        config = Config.load()
        for task in selected:
            logger.info(f'Starting task: {task.name}')
            start = time.monotonic()
            try:
                task.run(config)
            except Exception as e:
                # Logged once by the caller; the original traceback stays chained for the log file.
                raise RuntimeError(f'Task {task.name} failed: {e}') from e
            logger.info(f'Finished task: {task.name} ({time.monotonic() - start:.1f}s)')
        logger.info(f'Pipeline complete ({len(selected)} task(s))')
