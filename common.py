"""Private state, atomic files, redaction, and persistent write deduplication."""
from __future__ import annotations

import hashlib
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import sqlite3
import time
import uuid


def state_dir() -> Path:
    path = Path(os.environ.get('BAMBU_BRIDGE_HOME', str(Path.home() / '.bambu-bridge'))).expanduser()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def atomic_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        with temporary.open('x', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(value, stream, indent=2, allow_nan=False)
            stream.write('\n')
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def config() -> dict:
    path = state_dir() / 'config.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


SECRET = re.compile(r'access.?code|password|passwd|token|secret|credential|authorization|^helio_pat', re.I)


def redact(value):
    if isinstance(value, dict):
        return {k: '<redacted>' if SECRET.search(k) else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def artifact_dir() -> Path:
    path = state_dir() / 'artifacts' / uuid.uuid4().hex
    path.mkdir(parents=True, exist_ok=False)
    return path


def require_write(confirmed: bool) -> None:
    if confirmed is not True:
        raise ValueError('This write requires confirmed=true and an action within the user-authorized scope.')


def read_json(path: Path) -> dict:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('JSON exceeds the 16 MiB limit.')
    # Studio adds a checksum comment after its otherwise valid JSON preferences.
    text = path.read_text(encoding='utf-8-sig')
    value, end = json.JSONDecoder().raw_decode(text.lstrip())
    if text.lstrip()[end:].strip() and not text.lstrip()[end:].lstrip().startswith('#'):
        raise ValueError('Unexpected content after JSON.')
    if not isinstance(value, dict):
        raise ValueError('Expected a JSON object.')
    return value


@contextmanager
def ledger():
    connection = sqlite3.connect(state_dir() / 'operations.sqlite3', timeout=10)
    try:
        with connection:
            connection.execute('CREATE TABLE IF NOT EXISTS operations (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL, result TEXT NOT NULL)')
            yield connection
    finally:
        connection.close()


def operation_status(request_id: str) -> dict:
    with ledger() as db:
        row = db.execute('SELECT result FROM operations WHERE id=?', (request_id,)).fetchone()
    if not row:
        raise ValueError('Unknown request_id.')
    return json.loads(row[0])


def once(request_id: str, intent: dict, action) -> dict:
    """Reserve BEFORE any I/O. An uncertain request is never replayed automatically."""
    if str(uuid.UUID(request_id)) != request_id:
        raise ValueError('request_id must be a canonical UUID.')
    fingerprint = hashlib.sha256(json.dumps(intent, sort_keys=True, allow_nan=False).encode()).hexdigest()
    pending = {'request_id': request_id, 'status': 'pending_or_unknown', 'created_at': time.time(),
               'physical_execution_confirmed': False}
    with ledger() as db:
        cursor = db.execute('INSERT OR IGNORE INTO operations VALUES (?,?,?)',
                            (request_id, fingerprint, json.dumps(pending)))
        existing = db.execute('SELECT fingerprint,result FROM operations WHERE id=?', (request_id,)).fetchone()
    if not cursor.rowcount:
        if existing[0] != fingerprint:
            raise ValueError('request_id was already used for a different action.')
        return {**json.loads(existing[1]), 'deduplicated': True}
    try:
        result = {**pending, **action()}
    except Exception as exc:
        # Exception text may contain addresses/credentials from a provider. Keep only its type.
        result = {**pending, 'error_type': type(exc).__name__,
                  'message': 'Operation failed or its outcome is unknown. Inspect current state before any new request.'}
    with ledger() as db:
        db.execute('UPDATE operations SET result=? WHERE id=?', (json.dumps(result), request_id))
    return result
