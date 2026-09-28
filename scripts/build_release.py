"""Build a portable allowlisted ZIP with per-file hashes; never include private launch/state files."""
import hashlib
import json
from pathlib import Path
import zipfile

from validate_package import ROOT, release_files, validate


def main():
    validate()
    output = ROOT / 'dist'
    output.mkdir(exist_ok=True)
    archive_path = output / 'bambu-bridge-v0.1.0-rc.1.zip'
    manifest = {}
    with zipfile.ZipFile(archive_path, 'w', compression=zipfile.ZIP_DEFLATED) as archive:
        for path in release_files():
            relative = path.relative_to(ROOT).as_posix()
            data = path.read_bytes()
            archive.writestr('bambu-bridge/' + relative, data)
            manifest[relative] = hashlib.sha256(data).hexdigest()
        archive.writestr('bambu-bridge/MANIFEST.sha256.json', json.dumps(manifest, indent=2) + '\n')
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        for name, digest in manifest.items():
            assert hashlib.sha256(archive.read('bambu-bridge/' + name)).hexdigest() == digest
        assert not any(n.endswith('/.mcp.json') or '/config.json' in n or '/.git/' in n for n in archive.namelist())
    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    (output / 'SHA256SUMS.txt').write_text(f'{digest}  {archive_path.name}\n', encoding='utf-8')
    print(json.dumps({'archive': str(archive_path), 'files': len(manifest), 'sha256': digest}, indent=2))


if __name__ == '__main__':
    main()
