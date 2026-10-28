import zipfile

from pipeline.config import Config
from pipeline.tasks.base import Task, TaskError


class UnzipTask(Task):
    '''Extract every .zip in [paths].raw_dir into [paths].interim_dir.'''

    name = 'unzip'

    def run(self, config: Config):
        raw_dir = config.path('raw_dir')
        interim_dir = config.path('interim_dir')
        if not raw_dir.is_dir():
            raise TaskError(f'raw_dir does not exist: {raw_dir}. Download the group .zip files and place them there (see README "Getting the data")')

        zip_files = sorted(raw_dir.glob('*.zip'))
        if not zip_files:
            raise TaskError(f'No .zip files found in {raw_dir}. Download the group .zip files and place them there (see README "Getting the data")')

        interim_dir.mkdir(parents=True, exist_ok=True)
        for zip_file in zip_files:
            self.logger.info(f'Unzipping {zip_file.name}')
            try:
                with zipfile.ZipFile(zip_file) as archive:
                    archive.extractall(interim_dir)
            except zipfile.BadZipFile as e:
                raise TaskError(f'Corrupt zip file {zip_file}: {e}') from e
        self.logger.info(f'Extracted {len(zip_files)} archives into {interim_dir}')
