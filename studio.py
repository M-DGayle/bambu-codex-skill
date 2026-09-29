"""Bambu Studio profiles, projects, preferences and isolated CLI jobs."""
from __future__ import annotations

import copy
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import time
import uuid
import zipfile

from defusedxml import ElementTree
import psutil

from common import artifact_dir, atomic_json, config, read_json, redact, require_write, sha256, state_dir, SECRET


def capabilities() -> dict:
    """Report this connector's implemented backends, not inferred GUI state."""
    return {
        'saved_projects': {'read': True, 'edit_copy': True, 'slice_cli': True},
        'open_project': {
            'read': False, 'update': False, 'save': False, 'slice': False,
            'backend': None, 'status': 'unsupported',
            'project_identity': None, 'unsaved_changes': None,
            'reason': 'This bridge has no in-process Bambu Studio project API. File edits do not update the open project.',
        },
        'computer_use_fallback': False,
        'next_step': 'Live editing requires a native Studio integration with project identity, checkpoint, mutation and readback support.',
        'source_notes': 'references/live-project.md',
    }


def studio_executable() -> Path:
    setting = os.environ.get('BAMBU_STUDIO_PATH') or config().get('studio_path')
    if setting:
        path = Path(setting).expanduser().resolve()
        if not path.is_file():
            raise ValueError('Configured Bambu Studio executable does not exist.')
        return path
    candidates = []
    if sys.platform == 'win32':
        import winreg
        for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
            for key in (r'SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
                        r'SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall'):
                try:
                    with winreg.OpenKey(hive, key) as parent:
                        for index in range(winreg.QueryInfoKey(parent)[0]):
                            try:
                                with winreg.OpenKey(parent, winreg.EnumKey(parent, index)) as item:
                                    if 'Bambu Studio' in winreg.QueryValueEx(item, 'DisplayName')[0]:
                                        candidates.append(Path(winreg.QueryValueEx(item, 'InstallLocation')[0]) / 'bambu-studio.exe')
                            except OSError:
                                pass
                except OSError:
                    pass
        candidates.append(Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Bambu Studio/bambu-studio.exe')
    elif sys.platform == 'darwin':
        candidates.append(Path('/Applications/BambuStudio.app/Contents/MacOS/BambuStudio'))
    for name in ('bambu-studio', 'BambuStudio'):
        found = shutil.which(name)
        if found:
            candidates.append(Path(found))
    for process in psutil.process_iter(['name', 'exe']):
        if 'bambu' in (process.info['name'] or '').lower() and process.info['exe']:
            candidates.append(Path(process.info['exe']))
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise ValueError('Bambu Studio not found. Run setup or set BAMBU_STUDIO_PATH.')


def data_directory() -> Path:
    custom = config().get('studio_data_path')
    if custom:
        return Path(custom).expanduser().resolve()
    if sys.platform == 'win32':
        return Path(os.environ.get('APPDATA', str(Path.home() / 'AppData/Roaming'))) / 'BambuStudio'
    if sys.platform == 'darwin':
        return Path.home() / 'Library/Application Support/BambuStudio'
    return Path(os.environ.get('XDG_CONFIG_HOME', str(Path.home() / '.config'))) / 'BambuStudio'


def roots() -> list[Path]:
    executable = studio_executable()
    locations = [executable.parent / 'resources/profiles', executable.parent.parent / 'Resources/profiles',
                 data_directory() / 'system', data_directory() / 'user']
    locations.extend(Path(p).expanduser() for p in config().get('profile_roots', []))
    return [p.resolve() for p in locations if p.is_dir()]


def profile_files():
    seen = set()
    for root in roots():
        for path in root.rglob('*.json'):
            if path.resolve() in seen or not any(p in ('machine', 'process', 'filament') for p in path.parts):
                continue
            seen.add(path.resolve())
            try:
                data = read_json(path)
                if 'name' in data:
                    yield path, data
            except (ValueError, OSError, UnicodeError):
                continue


def profiles(query: str = '', kind: str = '', offset: int = 0, limit: int = 100) -> dict:
    if kind not in ('', 'machine', 'process', 'filament') or offset < 0 or not 1 <= limit <= 250:
        raise ValueError('Invalid kind or pagination.')
    values = [{'path': str(p), 'name': d['name'], 'type': d.get('type'), 'inherits': d.get('inherits')}
              for p, d in profile_files() if query.casefold() in str(d['name']).casefold()
              and (not kind or kind == d.get('type') or kind in p.parts)]
    return {'total': len(values), 'profiles': values[offset:offset + limit]}


def profile_read(path: str, resolve_inheritance: bool = False) -> dict:
    source = Path(path).expanduser().resolve(strict=True)
    data = read_json(source)
    chain = [str(source)]
    if resolve_inheritance:
        index = list(profile_files())
        visited = set()

        def resolve(current: Path, item: dict, depth: int = 0) -> dict:
            if current in visited or depth > 30:
                raise ValueError('Profile inheritance cycle or excessive depth.')
            visited.add(current)
            parent = item.get('inherits')
            if not parent:
                return copy.deepcopy(item)
            candidates = [(p, d) for p, d in index if d.get('name') == parent and d.get('type') == item.get('type')]
            siblings = [(p, d) for p, d in candidates if p.parent == current.parent]
            candidates = siblings or candidates
            if len(candidates) != 1:
                raise ValueError(f'Inherited profile {parent!r} is missing or ambiguous; select matching profile roots.')
            base_path, base_data = candidates[0]
            chain.append(str(base_path))
            return {**resolve(base_path, base_data, depth + 1), **item}

        data = resolve(source, data)
        data.pop('inherits', None)
    return {'path': str(source), 'sha256': sha256(source), 'inheritance_chain': chain, 'settings': redact(data)}


def settings_catalog(query: str = '', offset: int = 0, limit: int = 100) -> dict:
    if offset < 0 or not 1 <= limit <= 250:
        raise ValueError('Invalid pagination.')
    catalog = {}
    for _, profile in profile_files():
        for key, value in profile.items():
            if query.casefold() not in key.casefold() or SECRET.search(key):
                continue
            entry = catalog.setdefault(key, {'key': key, 'types': set(), 'examples': []})
            entry['types'].add(type(value).__name__)
            if len(entry['examples']) < 2 and value not in entry['examples']:
                entry['examples'].append(value if len(str(value)) < 300 else '<long value; read exact profile>')
    values = [{**d, 'types': sorted(d['types'])} for _, d in sorted(catalog.items())]
    return {'total': len(values), 'settings': values[offset:offset + limit],
            'scope': 'Keys present in installed profiles; examples are not schema constraints or model compatibility guarantees.'}


def json_update(path: str, changes: dict, remove: list[str], expected_sha256: str,
                confirmed: bool, overwrite: bool = False, resolve_inheritance: bool = False) -> dict:
    require_write(confirmed)
    source = Path(path).expanduser().resolve(strict=True)
    if source.name.lower() == 'bambustudio.conf':
        raise ValueError('Use preferences_update for Studio preferences.')
    if sha256(source) != expected_sha256:
        raise ValueError('Source changed since inspection.')
    data = profile_read(str(source), resolve_inheritance)['settings']
    if any(SECRET.search(k) for k in set(changes) | set(remove)) or '<redacted>' in json.dumps(data):
        raise ValueError('Credential-bearing JSON is not a profile; use the private setup workflow.')
    data.update(changes)
    for key in remove:
        data.pop(key, None)
    output = artifact_dir()
    backup = output / ('original-' + source.name)
    shutil.copy2(source, backup)
    destination = source if overwrite else output / source.name
    if overwrite:
        assert_studio_closed()
        if sha256(source) != expected_sha256:
            raise ValueError('Source changed before write.')
    atomic_json(destination, data)
    return {'path': str(destination), 'sha256': sha256(destination), 'backup': str(backup),
            'validation': 'JSON syntax only; Bambu Studio must validate setting semantics.'}


def assert_studio_closed() -> None:
    for process in psutil.process_iter(['name']):
        name = (process.info['name'] or '').lower()
        if name in ('bambu-studio', 'bambu-studio.exe', 'bambustudio', 'bambustudio.exe'):
            raise ValueError('Close Bambu Studio before overwriting its files; it can overwrite external changes.')


def preferences_read() -> dict:
    path = data_directory() / 'BambuStudio.conf'
    return {'path': str(path), 'sha256': sha256(path), 'settings': redact(read_json(path))}


def preferences_update(section: str, changes: dict, remove: list[str], expected_sha256: str, confirmed: bool) -> dict:
    require_write(confirmed)
    assert_studio_closed()
    path = data_directory() / 'BambuStudio.conf'
    if sha256(path) != expected_sha256:
        raise ValueError('Studio preferences changed since inspection.')
    data = read_json(path)
    if SECRET.search(section) or any(SECRET.search(k) for k in set(changes) | set(remove)):
        raise ValueError('Use the private setup workflow for credentials.')
    if section not in data or not isinstance(data[section], dict):
        raise ValueError('Select an existing object section.')
    data[section].update(changes)
    for key in remove:
        data[section].pop(key, None)
    backup = artifact_dir() / 'BambuStudio.conf.backup'
    shutil.copy2(path, backup)
    # Studio accepts checksum-free JSON. Its next save will regenerate its checksum comment.
    atomic_json(path, data)
    return {'path': str(path), 'backup': str(backup), 'sha256': sha256(path), 'requires_studio_restart': True}


MAX_ARCHIVE = 2 * 1024**3
MAX_TEXT = 16 * 1024**2


def archive_members(archive: zipfile.ZipFile) -> list[zipfile.ZipInfo]:
    members = archive.infolist()
    if len(members) > 10000 or sum(x.file_size for x in members) > MAX_ARCHIVE:
        raise ValueError('Project exceeds archive limits.')
    names = set()
    for entry in members:
        p = PurePosixPath(entry.filename)
        if p.is_absolute() or '..' in p.parts or '\\' in entry.filename or ':' in entry.filename or entry.filename in names:
            raise ValueError('Unsafe or duplicate ZIP member.')
        if entry.flag_bits & 1:
            raise ValueError('Encrypted project members are unsupported.')
        names.add(entry.filename)
    return members


def project_inspect(path: str) -> dict:
    source = Path(path).expanduser().resolve(strict=True)
    with zipfile.ZipFile(source) as archive:
        members = archive_members(archive)
        configs = {}
        for item in members:
            if item.filename.startswith('Metadata/') and item.filename.endswith(('.config', '.json')) and item.file_size <= MAX_TEXT:
                text = archive.read(item).decode('utf-8-sig')
                try:
                    configs[item.filename] = redact(json.loads(text))
                except ValueError:
                    configs[item.filename] = {'format': 'xml_or_text', 'bytes': item.file_size}
        return {'path': str(source), 'sha256': sha256(source), 'members': [{'name': e.filename, 'bytes': e.file_size} for e in members],
                'settings': configs, 'has_toolpaths': any(e.filename.endswith('.gcode') and e.file_size for e in members)}


def project_read_member(path: str, member: str) -> dict:
    with zipfile.ZipFile(Path(path).expanduser()) as archive:
        archive_members(archive)
        if archive.getinfo(member).file_size > MAX_TEXT:
            raise ValueError('Member exceeds text limit.')
        text = archive.read(member).decode('utf-8-sig')
    return {'member': member, 'text': text}


def project_update(path: str, settings: dict, remove_settings: list[str], text_members: dict[str, str],
                   expected_sha256: str, confirmed: bool, target: str = 'saved_file') -> dict:
    # Reject an unsupported target before filesystem access or artifact creation.
    if target == 'open_project':
        raise ValueError('Live project editing is unsupported: no native Studio integration is connected. '
                         'No file was edited. Do not substitute a saved-file copy or computer-use automation.')
    if target != 'saved_file':
        raise ValueError('Target must be saved_file or open_project.')
    require_write(confirmed)
    source = Path(path).expanduser().resolve(strict=True)
    if sha256(source) != expected_sha256:
        raise ValueError('Project changed since inspection.')
    destination = artifact_dir() / source.name
    replacements = dict(text_members)
    with zipfile.ZipFile(source) as original:
        members = archive_members(original)
        names = {x.filename for x in members}
        key = 'Metadata/project_settings.config'
        if settings or remove_settings:
            if key in replacements:
                raise ValueError('Use settings or text replacement for project settings, not both.')
            if key not in names or original.getinfo(key).file_size > MAX_TEXT:
                raise ValueError('Project has no bounded Bambu JSON settings member.')
            data = json.loads(original.read(key))
            data.update(settings)
            for setting in remove_settings:
                data.pop(setting, None)
            replacements[key] = json.dumps(data, indent=2, allow_nan=False)
        for name, text in replacements.items():
            if name not in names or not name.endswith(('.config', '.json', '.xml', '.model')):
                raise ValueError('Only existing XML/JSON project members can be replaced.')
            if len(text.encode()) > MAX_TEXT:
                raise ValueError('Replacement exceeds text limit.')
            if text.lstrip().startswith('<'):
                ElementTree.fromstring(text)
            else:
                json.loads(text)
        removed = []
        # Any edited geometry/settings invalidates cached G-code and slice previews.
        with zipfile.ZipFile(destination, 'x', compression=zipfile.ZIP_DEFLATED) as output:
            for item in members:
                if replacements and (item.filename.endswith(('.gcode', '.gcode.md5'))
                                     or re.match(r'^Metadata/plate_\d+\.(png|json)$', item.filename)
                                     or item.filename == 'Metadata/slice_info.config'):
                    removed.append(item.filename)
                    continue
                if item.filename in replacements:
                    output.writestr(item, replacements[item.filename].encode())
                else:
                    with original.open(item) as read, output.open(item, 'w') as write:
                        shutil.copyfileobj(read, write, 1024 * 1024)
    return {'path': str(destination), 'sha256': sha256(destination), 'original_preserved': True,
            'target': 'saved_file', 'open_project_updated': False,
            'removed_stale_slice_members': removed, 'requires_reslice': True,
            'validation': 'Archive and XML/JSON syntax checked; geometry and setting semantics require Studio validation.'}


def open_project(path: str, confirmed: bool) -> dict:
    require_write(confirmed)
    source = Path(path).expanduser().resolve(strict=True)
    if source.suffix.lower() not in ('.3mf', '.stl', '.step', '.stp', '.obj', '.gcode'):
        raise ValueError('Unsupported model/project extension.')
    process = subprocess.Popen([str(studio_executable()), str(source)], shell=False)
    return {'status': 'open_requested', 'pid': process.pid, 'path': str(source), 'ui_load_confirmed': False,
            'existing_session_targeted': False, 'live_settings_verified': False}


def start_job(arguments: list[str], confirmed: bool, timeout_seconds: int = 600,
              expect_toolpaths: bool = False) -> dict:
    require_write(confirmed)
    if not arguments or len(arguments) > 500 or any(not isinstance(a, str) or '\x00' in a for a in arguments):
        raise ValueError('Provide a bounded list of literal CLI arguments.')
    if not 1 <= timeout_seconds <= 3600:
        raise ValueError('Timeout must be 1-3600 seconds.')
    # Full Studio CLI access is privileged, not a filesystem sandbox.
    job_id = str(uuid.uuid4())
    directory = state_dir() / 'jobs' / job_id
    directory.mkdir(parents=True)
    spec = {'job_id': job_id, 'executable': str(studio_executable()), 'arguments': arguments,
            'timeout_seconds': timeout_seconds, 'expect_toolpaths': expect_toolpaths}
    atomic_json(directory / 'spec.json', spec)
    atomic_json(directory / 'status.json', {'job_id': job_id, 'status': 'queued', 'directory': str(directory)})
    kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
    try:
        with (directory / 'worker.log').open('wb') as log:
            subprocess.Popen([sys.executable, str(Path(__file__).with_name('worker.py')), job_id],
                             stdin=subprocess.DEVNULL, stdout=log, stderr=log, shell=False, **kwargs)
    except OSError:
        atomic_json(directory / 'status.json', {'job_id': job_id, 'status': 'failed_to_launch'})
        raise
    return job_status(job_id)


def job_status(job_id: str) -> dict:
    if str(uuid.UUID(job_id)) != job_id:
        raise ValueError('Invalid job ID.')
    directory = state_dir() / 'jobs' / job_id
    status = read_json(directory / 'status.json')
    status['logs'] = [str(p) for p in directory.glob('*.log')]
    return status


def job_cancel(job_id: str, confirmed: bool) -> dict:
    require_write(confirmed)
    current = job_status(job_id)
    if current['status'] in ('queued', 'running'):
        (state_dir() / 'jobs' / job_id / 'cancel').touch()
    return {**current, 'cancellation_requested': current['status'] in ('queued', 'running')}


def slice_project(inputs: list[str], machine: str | None, process: str | None, filaments: list[str],
                  overrides: dict, plate: int, confirmed: bool, timeout_seconds: int = 600) -> dict:
    require_write(confirmed)
    if not inputs or plate < 0:
        raise ValueError('Provide model/project inputs and a nonnegative plate index.')
    paths = [Path(p).expanduser().resolve(strict=True) for p in inputs]
    if any(p.suffix.lower() not in ('.3mf', '.stl', '.obj', '.step', '.stp') for p in paths):
        raise ValueError('Unsupported input format.')
    if any(p.suffix.lower() != '.3mf' for p in paths) and not (machine and process and filaments):
        raise ValueError('Mesh/STEP slicing requires explicit machine, process and filament profiles.')
    output = artifact_dir()
    arguments = ['--slice', str(plate), '--outputdir', str(output), '--export-3mf', 'sliced.3mf', '--debug', '2']
    settings_files = []
    for label, path in [('machine', machine), ('process', process)]:
        if path:
            resolved = profile_read(path, True)['settings']
            target = output / f'{label}.json'
            atomic_json(target, resolved)
            settings_files.append(str(target))
    if settings_files:
        arguments += ['--load-settings', ';'.join(settings_files)]
    filament_files = []
    for index, path in enumerate(filaments):
        target = output / f'filament-{index}.json'
        atomic_json(target, profile_read(path, True)['settings'])
        filament_files.append(str(target))
    if filament_files:
        arguments += ['--load-filaments', ';'.join(filament_files)]
    reserved = {'slice', 'outputdir', 'export-3mf', 'export-settings', 'export-slicedata',
                'load-settings', 'load-filaments', 'load-slicedata', 'datadir', 'pipe'}
    for key, value in overrides.items():
        option = key.replace('_', '-')
        if not re.fullmatch('[a-z][a-z0-9-]*', option) or option in reserved:
            raise ValueError('Invalid/reserved setting override. Use studio_run for advanced CLI operations.')
        if isinstance(value, (dict, list)):
            raise ValueError('CLI overrides must be scalar values; use profiles for arrays/objects.')
        arguments.append('--' + option + '=' + (str(int(value)) if isinstance(value, bool) else str(value)))
    # Copy inputs so CLI preparation cannot modify the original projects.
    for index, path in enumerate(paths):
        target = output / f'input-{index}{path.suffix}'
        shutil.copy2(path, target)
        arguments.append(str(target))
    return {**start_job(arguments, True, timeout_seconds, True), 'output_directory': str(output)}
