import argparse
import logging
import platform
import sys

from pipeline.run_log import RunLog
from pipeline.runner import PipelineRunner
from pipeline.tasks import TASKS

logger = logging.getLogger('pipeline')


def parse_args():
    parser = argparse.ArgumentParser(
        prog='python3 -m pipeline',
        description='Run pipeline tasks. With no task names, runs all tasks in order.',
    )
    parser.add_argument('tasks', nargs='*', help='Task names to run (default: all)')
    parser.add_argument('--list', action='store_true', help='List available tasks and exit')
    return parser.parse_args()


def main():
    RunLog.configure_console()
    args = parse_args()
    try:
        runner = PipelineRunner(TASKS)
        if args.list:
            for task in runner.tasks:
                print(f'{task.name:<12} {(task.__doc__ or "").strip()}')
            return

        log_file = RunLog.start_file()
        logger.info(f'Logging this run to {log_file}')
        logger.debug(f'argv={sys.argv} python={platform.python_version()} platform={platform.platform()}')
        runner.run(args.tasks)
    except Exception as e:
        logger.error(str(e))
        logger.debug('Traceback:', exc_info=True)  # file only; the terminal stays readable
        sys.exit(1)


if __name__ == '__main__':
    main()
