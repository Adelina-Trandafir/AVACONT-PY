"""Worker-shared, expiring, owner-bound report snapshots. No database writes.

Nothing stays on the disk after its time: every `put` sweeps the expired files of ALL owners (at most once a
minute per process) and the folders left empty, and `release` removes one snapshot as soon as its viewer closes."""
import hashlib
import json
import logging
import secrets
import os
from pathlib import Path
import re
import tempfile
import time
from .errors import ReportError

logger = logging.getLogger(__name__)

LIFETIME = 600           # seconds a snapshot may be used
SWEEP_EVERY = 60         # seconds between two sweeps of the whole root, per process
_TOKEN = re.compile(r'[A-Za-z0-9_-]{32}')


class Snapshots:
    """Shared by server workers; exports accept only an owner-bound saved model."""

    def __init__(self, root, error_type=ReportError):
        self.root = Path(root)
        self.error_type = error_type
        self._swept = 0.0

    def folder(self, owner):
        digest = hashlib.sha256(json.dumps(owner).encode()).hexdigest()
        return self.root / digest

    def sweep(self, now=None, force=False):
        """Deletes every snapshot (and temporary file) older than LIFETIME, of every owner, and the owner folders
        left empty. Throttled: a call within SWEEP_EVERY of the last one does nothing unless `force`."""
        now = time.time() if now is None else now
        if not force and now - self._swept < SWEEP_EVERY:
            return
        self._swept = now
        if not self.root.is_dir():
            return
        for folder in self.root.iterdir():
            if not folder.is_dir():
                continue
            for path in folder.iterdir():
                try:
                    if path.suffix in ('.json', '.tmp') and path.stat().st_mtime < now - LIFETIME:
                        path.unlink(missing_ok=True)
                except FileNotFoundError:
                    pass   # another worker removed it first
                except OSError:
                    logger.warning('report snapshot sweep: cannot remove %s', path.name, exc_info=True)
            self._remove_if_empty(folder)

    @staticmethod
    def _remove_if_empty(folder):
        try:
            folder.rmdir()   # refused by the system while the folder holds a file
        except OSError:
            pass   # not empty (the normal case of a folder in use) or already gone

    def put(self, owner, model):
        token, now = secrets.token_urlsafe(24), time.time()
        self.sweep(now)
        folder = self.folder(owner)
        for path in folder.glob('*.json') if folder.is_dir() else ():
            try:
                if path.stat().st_mtime < now - LIFETIME:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
        for attempt in (1, 2):
            folder.mkdir(parents=True, exist_ok=True)
            files = sorted(folder.glob('*.json'), key=lambda path: path.name)
            if len(files) >= 40:
                files[0].unlink(missing_ok=True)
            try:
                fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
                break
            except FileNotFoundError:
                if attempt == 2:   # a sweep of another worker removed the empty folder twice in a row
                    raise
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'expires': now + LIFETIME, 'owner': owner, 'model': model}, stream, ensure_ascii=False)
            os.replace(temporary, folder / (token + '.json'))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return token

    def release(self, owner, token):
        """Removes one snapshot of this owner now (the viewer was closed). A missing or foreign token is not an
        error: the aim, that nothing stays, is already met."""
        if not _TOKEN.fullmatch(token):
            raise self.error_type('REPORT_EXPIRED', 'Previzualizarea a expirat. Generați din nou raportul.', 409)
        folder = self.folder(owner)
        (folder / (token + '.json')).unlink(missing_ok=True)
        self._remove_if_empty(folder)

    def get(self, owner, token):
        if not _TOKEN.fullmatch(token):
            raise self.error_type('REPORT_EXPIRED', 'Previzualizarea a expirat. Generați din nou raportul.', 409)
        path = self.folder(owner) / (token + '.json')
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            row = None
        if not row or row['expires'] <= time.time() or tuple(row['owner']) != owner:
            raise self.error_type('REPORT_EXPIRED', 'Previzualizarea a expirat. Generați din nou raportul.', 409)
        return row['model']
