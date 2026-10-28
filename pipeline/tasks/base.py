import logging
from abc import ABC, abstractmethod

from pipeline.config import Config


class TaskError(Exception):
    pass


class Task(ABC):
    '''One pipeline step. Subclasses set `name` and implement `run(config)`.'''

    name: str = ''

    def __init__(self):
        if not self.name:
            raise TypeError(f'{type(self).__name__} must define a non-empty `name`')
        self.logger = logging.getLogger(f'task.{self.name}')

    @abstractmethod
    def run(self, config: Config):
        ...
