"""Worker-shared, expiring, owner-bound report snapshots. No database writes."""
import hashlib
import json
import secrets
import os
from pathlib import Path
import re
import tempfile
import time
from .errors import ReportError


class Snapshots:
    """Shared by server workers; exports accept only an owner-bound saved model."""

    def __init__(self, root, error_type=ReportError):
        self.root = Path(root)
        self.error_type = error_type

    def folder(self, owner):
        digest = hashlib.sha256(json.dumps(owner).encode()).hexdigest()
        return self.root / digest

    def put(self, owner, model):
        token, now = secrets.token_urlsafe(24), time.time()
        folder = self.folder(owner)
        folder.mkdir(parents=True, exist_ok=True)
        for path in folder.glob('*.json'):
            try:
                if path.stat().st_mtime < now - 600:
                    path.unlink(missing_ok=True)
            except FileNotFoundError:
                pass
        files = sorted(folder.glob('*.json'), key=lambda path: path.name)
        if len(files) >= 40:
            files[0].unlink(missing_ok=True)
        fd, temporary = tempfile.mkstemp(dir=folder, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump({'expires': now + 600, 'owner': owner, 'model': model}, stream, ensure_ascii=False)
            os.replace(temporary, folder / (token + '.json'))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return token

    def get(self, owner, token):
        if not re.fullmatch(r'[A-Za-z0-9_-]{32}', token):
            raise self.error_type('REPORT_EXPIRED', 'Previzualizarea a expirat. Generați din nou raportul.', 409)
        path = self.folder(owner) / (token + '.json')
        try:
            row = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            row = None
        if not row or row['expires'] <= time.time() or tuple(row['owner']) != owner:
            raise self.error_type('REPORT_EXPIRED', 'Previzualizarea a expirat. Generați din nou raportul.', 409)
        return row['model']
