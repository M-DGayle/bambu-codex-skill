"""Local request/reply transport to an opted-in Studio GUI-thread integration.

No UI automation, process memory access, printer transport or project replacement.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import time
import uuid
import zipfile

import psutil

from common import state_dir, atomic_json, require_write, sha256


def root() -> Path:
    return Path(os.environ.get('BAMBU_BRIDGE_NATIVE_DIR', str(state_dir() / 'native'))).expanduser().resolve()


def _session(session_id: str) -> tuple[Path, dict]:
    canonical = str(uuid.UUID(session_id))
    if canonical != session_id:
        raise ValueError('Expected a canonical native session UUID.')
    directory = root() / canonical
    path = directory / 'session.json'
    if directory.is_symlink() or path.is_symlink() or path.stat().st_size > 4096:
        raise ValueError('Invalid native session metadata.')
    record = json.loads(path.read_text(encoding='utf-8'))
    if record.get('protocol') != 1 or record.get('session_id') != canonical or record.get('backend') != 'studio-native-wx':
        raise ValueError('Incompatible native session.')
    if not isinstance(record.get('token'), str) or len(record['token']) < 64:
        raise ValueError('Missing native session token.')
    process = psutil.Process(record['pid'])
    if not process.is_running() or process.create_time() > record['created_at'] + 1:
        raise ValueError('Native Studio process is gone or PID was reused.')
    return directory, record


def sessions() -> dict:
    found = []
    if root().is_dir():
        for path in root().glob('*/session.json'):
            try:
                _, record = _session(path.parent.name)
                found.append({k: record[k] for k in ('session_id', 'pid', 'backend', 'protocol')})
            except (ValueError, OSError, KeyError, psutil.Error):
                continue
    return {'sessions': found, 'live_backend_available': bool(found),
            'selection': 'Use the exact session_id. Multiple sessions are never selected by filename alone.',
            'setup': 'Run a Studio build containing native/StudioBridge.cpp with BAMBU_BRIDGE_NATIVE_DIR set to the bridge native directory.'}


def _result(directory: Path, session_id: str, request_id: str) -> dict | None:
    response = directory / 'responses' / (request_id + '.json')
    if not response.exists():
        return None
    if response.is_symlink() or response.stat().st_size > 16 * 1024 * 1024:
        raise ValueError('Invalid native response file.')
    value = json.loads(response.read_text(encoding='utf-8'))
    if value.get('session_id') != session_id or value.get('request_id') != request_id:
        raise ValueError('Native response identity mismatch.')
    if not value.get('ok'):
        return {'status': 'rejected_or_rolled_back', 'request_id': request_id, 'session_id': session_id,
                'error': value.get('error', 'Native operation failed'), 'execution_verified': False}
    result = value['result']
    for key in ('path', 'checkpoint_before', 'checkpoint_after'):
        if key in result:
            artifact = Path(result[key]).resolve(strict=True)
            if artifact.parent != directory.resolve() or artifact.suffix != '.3mf':
                raise ValueError('Native checkpoint is outside its session directory.')
            with zipfile.ZipFile(artifact) as archive:
                if '3D/3dmodel.model' not in archive.namelist() or archive.testzip() is not None:
                    raise ValueError('Native checkpoint archive validation failed.')
            result[key + '_sha256'] = sha256(artifact)
    return {'status': 'completed', 'request_id': request_id, 'session_id': session_id, **result}


def operation_status(session_id: str, request_id: str) -> dict:
    if str(uuid.UUID(request_id)) != request_id:
        raise ValueError('Expected a canonical request UUID.')
    if str(uuid.UUID(session_id)) != session_id:
        raise ValueError('Expected a canonical session UUID.')
    directory = root() / session_id
    if directory.is_symlink() or not directory.is_dir():
        raise ValueError('Native session directory is unavailable.')
    return _result(directory, session_id, request_id) or {
        'status': 'pending_or_unknown', 'session_id': session_id, 'request_id': request_id,
        'instruction': 'Do not retry the write with a new UUID. Read this request result again.'}


def request(session_id: str, operation: str, parameters: dict | None = None,
            request_id: str | None = None, timeout_seconds: float = 15) -> dict:
    if operation not in ('read', 'update', 'checkpoint') or not 0 < timeout_seconds <= 60:
        raise ValueError('Invalid native operation or timeout.')
    directory, record = _session(session_id)
    request_id = request_id or str(uuid.uuid4())
    if str(uuid.UUID(request_id)) != request_id:
        raise ValueError('Expected a canonical request UUID.')
    parameters = parameters or {}
    if set(parameters) & {'token', 'session_id', 'request_id', 'operation', 'deadline'}:
        raise ValueError('Native transport fields cannot be overridden.')
    payload = {'session_id': session_id, 'operation': operation, **parameters}
    # A persistent intent record reserves the UUID before the native queue write.
    intent = directory / (request_id + '.intent.json')
    try:
        with intent.open('x', encoding='utf-8') as stream:
            json.dump(payload, stream, allow_nan=False)
    except FileExistsError:
        if json.loads(intent.read_text(encoding='utf-8')) != payload:
            raise ValueError('Native request UUID was already used for a different operation.')
        return operation_status(session_id, request_id)
    queue_path = directory / 'requests' / (request_id + '.json')
    atomic_json(queue_path, {**payload, 'token': record['token'], 'deadline': time.time() + timeout_seconds})
    end = time.monotonic() + timeout_seconds
    while time.monotonic() < end:
        value = _result(directory, session_id, request_id)
        if value is not None:
            return value
        time.sleep(0.1)
    return operation_status(session_id, request_id)


def update(session_id: str, expected_revision: str, changes: dict, request_id: str,
           scope: str = 'process', filament_slot: int | None = None,
           affected_slots: list[int] | None = None, confirmed: bool = False) -> dict:
    require_write(confirmed)
    if scope not in ('process', 'filament') or not changes or len(changes) > 100:
        raise ValueError('Expected process/filament scope and 1 to 100 serialized settings.')
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in changes.items()):
        raise ValueError('Settings must use the serialized strings returned by the native read.')
    if scope == 'filament' and (not filament_slot or not affected_slots):
        raise ValueError('Filament writes require the slot and all slots sharing its preset from a fresh read.')
    return request(session_id, 'update', {'expected_revision': expected_revision, 'changes': changes,
        'scope': scope, 'filament_slot': filament_slot, 'affected_slots': affected_slots or []}, request_id, 60)
