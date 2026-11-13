import base64
import binascii
import re
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path

import duckdb

from pipeline.config import Config
from pipeline.tasks.base import Task, TaskError

# The Android framework declares every platform permission, with its protectionLevel, in this one manifest.
# Gitiles serves it base64-encoded when asked for ?format=TEXT.
AOSP_MANIFEST_URL = (
    'https://android.googlesource.com/platform/frameworks/base/+/refs/tags/{tag}/core/res/AndroidManifest.xml?format=TEXT'
)
ANDROID_NS = '{http://schemas.android.com/apk/res/android}'
TAG_PATTERN = re.compile(r'^android-\d+(\.\d+)*_r\d+$')
MIN_PERMISSIONS = 100  # a real framework manifest declares hundreds; fewer means a broken or wrong file

COLUMNS = [
    ('perm', 'VARCHAR'), ('protection_level', 'VARCHAR'), ('protection_flags', 'VARCHAR'),
    ('defined_in', 'VARCHAR'), ('apps', 'INTEGER'), ('apps_extra', 'INTEGER'),
]


class ProtectionLevel:
    '''Reduces an android:protectionLevel string ('signature|privileged|appop') to its base level.'''

    # Base levels Android defines; signatureOrSystem is the pre-API 23 name for signature|privileged.
    BASE_LEVELS = {'normal': 'normal', 'dangerous': 'dangerous', 'signature': 'signature',
                   'signatureOrSystem': 'signature', 'internal': 'internal'}
    UNKNOWN = 'unknown'  # not declared by the framework: vendor or app-defined names, typos, made-up names

    @classmethod
    def base(cls, flags: str) -> str:
        # Flags are not always listed base-first ('module|signature|role'), so take the first base level present.
        for flag in flags.split('|'):
            if flag in cls.BASE_LEVELS:
                return cls.BASE_LEVELS[flag]
        raise TaskError(f'protectionLevel {flags!r} has no base level ({sorted(cls.BASE_LEVELS)})')


class AospManifest:
    '''The framework AndroidManifest.xml at one pinned AOSP tag, downloaded once and cached under external_dir.'''

    TIMEOUT_S = 60

    def __init__(self, tag: str, external_dir: Path, logger):
        if not TAG_PATTERN.match(tag):
            raise TaskError(f'[permissions].aosp_tag must look like android-14.0.0_r1, got {tag!r}')
        self.tag = tag
        self.cache_file = external_dir / 'aosp' / tag / 'AndroidManifest.xml'
        self.logger = logger

    def protection_levels(self) -> dict[str, str]:
        '''{permission name: raw protectionLevel flags}. A missing attribute means normal, as on Android.'''
        try:
            if not self.cache_file.is_file():
                self._download()
            else:
                self.logger.info(f'Using cached {self.cache_file}')
            levels = self._parse(self.cache_file.read_bytes())
        except TaskError:
            raise
        except Exception as e:
            raise TaskError(f'Could not read the AOSP manifest for {self.tag}: {e}') from e
        self.logger.info(f'AOSP {self.tag} declares {len(levels):,} permissions')
        return levels

    def _download(self):
        url = AOSP_MANIFEST_URL.format(tag=self.tag)
        self.logger.info(f'Downloading {url}')
        request = urllib.request.Request(url, headers={'User-Agent': 'now-secure-pipeline'})
        try:
            with urllib.request.urlopen(request, timeout=self.TIMEOUT_S) as response:
                encoded = response.read()
            content = base64.b64decode(encoded, validate=False)
        except urllib.error.HTTPError as e:
            raise TaskError(f'Download failed ({e.code}) for {url}: check [permissions].aosp_tag') from e
        except (urllib.error.URLError, TimeoutError) as e:
            raise TaskError(f'Download failed for {url}: {e} (needs network access the first time)') from e
        except binascii.Error as e:
            raise TaskError(f'{url} did not return base64 text: {e}') from e
        self._parse(content)  # never cache a file we cannot use
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        partial = self.cache_file.with_suffix('.xml.partial')
        partial.write_bytes(content)
        partial.replace(self.cache_file)
        self.logger.info(f'Cached {self.cache_file}')

    def _parse(self, content: bytes) -> dict[str, str]:
        try:
            root = ET.fromstring(content)
        except ET.ParseError as e:
            raise TaskError(f'AOSP manifest for {self.tag} is not valid XML: {e} (delete {self.cache_file} to re-download)') from e
        levels = {}
        for element in root.iter('permission'):
            name = element.get(f'{ANDROID_NS}name')
            if name:
                levels[name] = element.get(f'{ANDROID_NS}protectionLevel') or 'normal'
        if len(levels) < MIN_PERMISSIONS:
            raise TaskError(f'AOSP manifest for {self.tag} declares only {len(levels)} permissions (delete {self.cache_file} to re-download)')
        return levels


class PermissionMetaTask(Task):
    '''Build permission_meta: every permission in app_permission with its protection level from the pinned AOSP manifest.'''

    name = 'permission_meta'

    def run(self, config: Config):
        duckdb_file = config.path('duckdb_file')
        if not duckdb_file.is_file():
            raise TaskError(f'duckdb_file not found: {duckdb_file} (run the load_duckdb and flatten tasks first)')
        manifest = AospManifest(config.value('permissions', 'aosp_tag'), config.path('external_dir'), self.logger)
        tidy_dir = config.path('tidy_dir')

        con = duckdb.connect(str(duckdb_file))
        try:
            if 'app_permission' not in {r[0] for r in con.execute('SHOW TABLES').fetchall()}:
                raise TaskError(f'{duckdb_file} has no app_permission table (run the flatten task first)')
            rows = self._rows(con, manifest.tag, manifest.protection_levels())
            self._write(con, rows)
            self._export(con, tidy_dir)
        except TaskError:
            raise
        except Exception as e:
            self.logger.error(f'Aborting permission_meta: {e}')
            raise TaskError(f'permission_meta failed: {e}') from e
        finally:
            con.close()

    def _rows(self, con, tag: str, levels: dict[str, str]) -> list[tuple]:
        used = con.execute('''
            SELECT perm, count(DISTINCT id) AS apps, count(DISTINCT id) FILTER (is_extra) AS apps_extra
            FROM app_permission GROUP BY perm ORDER BY perm
        ''').fetchall()
        if not used:
            raise TaskError('app_permission is empty (did flatten find any over-privilege checks?)')
        rows = []
        for perm, apps, apps_extra in used:
            flags = levels.get(perm)
            level = ProtectionLevel.base(flags) if flags is not None else ProtectionLevel.UNKNOWN
            rows.append((perm, level, flags, tag if flags is not None else None, apps, apps_extra))
        return rows

    def _write(self, con, rows: list[tuple]):
        ddl = ', '.join(f'{col} {kind}' for col, kind in COLUMNS)
        con.begin()
        try:
            con.execute(f'CREATE OR REPLACE TABLE permission_meta ({ddl})')
            con.executemany(f'INSERT INTO permission_meta VALUES ({", ".join("?" for _ in COLUMNS)})', rows)
            con.commit()
        except Exception:
            con.rollback()
            raise
        for level, perms, extra in con.execute('''
            SELECT protection_level, count(*), count(*) FILTER (apps_extra > 0)
            FROM permission_meta GROUP BY 1 ORDER BY 1
        ''').fetchall():
            self.logger.info(f'permission_meta: {perms} {level} permissions ({extra} unused in at least one app)')

    def _export(self, con, tidy_dir: Path):
        tidy_dir.mkdir(parents=True, exist_ok=True)
        target = str(tidy_dir / 'permission_meta.parquet').replace("'", "''")
        con.execute(f"COPY permission_meta TO '{target}' (FORMAT parquet)")
        self.logger.info(f'Parquet copy of permission_meta written to {tidy_dir}')
