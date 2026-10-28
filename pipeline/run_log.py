import logging
import sys
from datetime import datetime
from pathlib import Path

from pipeline.config import PROJECT_ROOT

LOG_DIR = PROJECT_ROOT / 'logs'
LOG_FORMAT = '%(asctime)s %(levelname)s [%(name)s] %(message)s'


class RunLog:
    '''Sends INFO+ to the terminal and DEBUG+ (including tracebacks) to logs/pipeline_<timestamp>.log.'''

    @staticmethod
    def configure_console():
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT, stream=sys.stderr)

    @staticmethod
    def start_file(log_dir: Path = LOG_DIR) -> Path:
        try:
            log_dir.mkdir(parents=True, exist_ok=True)
            log_file = log_dir / f'pipeline_{datetime.now():%Y%m%d_%H%M%S}.log'
            handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
            handler.setLevel(logging.DEBUG)
            handler.setFormatter(logging.Formatter(LOG_FORMAT))

            root = logging.getLogger()
            root.setLevel(logging.DEBUG)
            for existing in root.handlers:
                if not isinstance(existing, logging.FileHandler):
                    existing.setLevel(logging.INFO)  # keep the terminal at INFO
            root.addHandler(handler)
            return log_file
        except OSError as e:
            logging.getLogger('pipeline').error(f'Could not create log file in {log_dir}: {e}')
            raise
