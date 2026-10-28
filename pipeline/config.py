import logging
import tomllib
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_FILE = PROJECT_ROOT / 'config.default.toml'
LOCAL_CONFIG_FILE = PROJECT_ROOT / 'config.local.toml'

logger = logging.getLogger('config')


class ConfigError(Exception):
    pass


class Config:
    '''Pipeline settings: committed defaults with optional per-user overrides merged on top.'''

    def __init__(self, data: dict):
        self._data = data

    @classmethod
    def load(cls, local_file: Path = LOCAL_CONFIG_FILE) -> 'Config':
        try:
            if not DEFAULT_CONFIG_FILE.is_file():
                raise ConfigError(f'Default config missing: {DEFAULT_CONFIG_FILE}')
            data = cls._read(DEFAULT_CONFIG_FILE)

            if local_file.is_file():
                cls._merge(data, cls._read(local_file), local_file)
                logger.info(f'Applied overrides from {local_file}')
            else:
                logger.info('No config.local.toml found; using defaults')
            return cls(data)
        except ConfigError:
            raise
        except Exception as e:
            logger.error(f'Failed to load config: {e}')
            raise ConfigError(f'Failed to load config: {e}') from e

    @staticmethod
    def _read(path: Path) -> dict:
        try:
            with path.open('rb') as f:
                return tomllib.load(f)
        except tomllib.TOMLDecodeError as e:
            raise ConfigError(f'Invalid TOML in {path}: {e}') from e

    @staticmethod
    def _merge(base: dict, overrides: dict, source: Path):
        # Only keys that exist in the defaults may be overridden, so typos fail fast.
        for section, values in overrides.items():
            if section not in base or not isinstance(values, dict):
                raise ConfigError(f'Unknown section [{section}] in {source}')
            unknown = set(values) - set(base[section])
            if unknown:
                raise ConfigError(f'Unknown keys in [{section}] of {source}: {sorted(unknown)}')
            base[section].update(values)

    def path(self, key: str) -> Path:
        '''Return [paths].<key> as an absolute Path (relative values resolve from the project root).'''
        try:
            value = self._data['paths'][key]
        except KeyError:
            raise ConfigError(f'[paths].{key} is not defined in {DEFAULT_CONFIG_FILE.name}') from None
        if not isinstance(value, str) or not value.strip():
            raise ConfigError(f'[paths].{key} must be a non-empty string')
        path = Path(value).expanduser()
        return path if path.is_absolute() else (PROJECT_ROOT / path).resolve()
