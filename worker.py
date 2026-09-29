"""A single persistent Studio CLI job. Never starts a development server."""
import json
from pathlib import Path
import subprocess
import sys
import time
import uuid
import zipfile

from common import atomic_json, read_json, state_dir
from studio import archive_members


def verify_slice(path: Path) -> dict:
    if not path.is_file():
        return {'verified': False, 'reason': 'Expected sliced.3mf was not produced.'}
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive_members(archive)
            gcode = [m for m in members if m.filename.endswith('.gcode') and m.file_size > 0]
            if not gcode:
                return {'verified': False, 'reason': 'Export contains no nonempty G-code members.'}
            if archive.testzip() is not None:
                return {'verified': False, 'reason': 'Export CRC validation failed.'}
        return {'verified': True, 'gcode_members': [m.filename for m in gcode],
                'physical_print_validated': False}
    except (ValueError, OSError, zipfile.BadZipFile):
        return {'verified': False, 'reason': 'Export is not a valid bounded 3MF archive.'}


def run(job_id: str) -> None:
    if str(uuid.UUID(job_id)) != job_id:
        raise ValueError('Invalid job ID.')
    directory = state_dir() / 'jobs' / job_id
    spec = read_json(directory / 'spec.json')
    status = {'job_id': job_id, 'status': 'running', 'directory': str(directory), 'started_at': time.time()}
    process = None
    try:
        if (directory / 'cancel').exists():
            status['status'] = 'cancelled'
            return
        kwargs = {'creationflags': subprocess.CREATE_NO_WINDOW} if sys.platform == 'win32' else {}
        # --datadir prevents this CLI instance forwarding arguments into the open GUI.
        datadir = directory / 'studio-data'
        datadir.mkdir()
        atomic_json(datadir / 'BambuStudio.conf', {'app': {'single_instance': 'false'}})
        args = [spec['executable'], '--datadir', str(datadir), *spec['arguments']]
        with (directory / 'stdout.log').open('wb') as out, (directory / 'stderr.log').open('wb') as err:
            process = subprocess.Popen(args, cwd=directory, stdin=subprocess.DEVNULL, stdout=out, stderr=err,
                                       shell=False, **kwargs)
            status['pid'] = process.pid
            atomic_json(directory / 'status.json', status)
            deadline = time.monotonic() + spec['timeout_seconds']
            while process.poll() is None:
                if (directory / 'cancel').exists() or time.monotonic() > deadline:
                    process.kill()
                    process.wait(timeout=10)
                    status['status'] = 'cancelled' if (directory / 'cancel').exists() else 'timed_out'
                    break
                time.sleep(0.2)
            else:
                status['exit_code'] = process.returncode
                status['status'] = 'completed' if process.returncode == 0 else 'failed'
        if spec['expect_toolpaths']:
            options = spec['arguments']
            output = Path(options[options.index('--outputdir') + 1]) / 'sliced.3mf'
            status['output_path'] = str(output)
            status['slice_validation'] = verify_slice(output)
            if status['status'] == 'completed' and not status['slice_validation']['verified']:
                status['status'] = 'failed_output_validation'
    except Exception as exc:
        if process and process.poll() is None:
            process.kill()
            process.wait(timeout=10)
        status.update(status='failed', error_type=type(exc).__name__)
    finally:
        status['finished_at'] = time.time()
        atomic_json(directory / 'status.json', status)


if __name__ == '__main__':
    run(sys.argv[1])
