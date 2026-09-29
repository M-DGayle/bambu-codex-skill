"""Check portable source structure and explicit release allowlist; no printer contact."""
import ast
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
TOP = ('AGENTS.md', 'README.md', 'CHANGELOG.md', 'LICENSE', 'SKILL.md', 'requirements.txt',
       'common.py', 'studio.py', 'native_client.py', 'worker.py', 'printer.py', 'server.py', 'bridge_client.py', 'setup_bridge.py')
DIRECTORIES = ('.codex-plugin', 'agents', 'skills', 'references', 'scripts', 'tests', '.github', 'native')


def release_files():
    paths = [ROOT / name for name in TOP]
    for directory in DIRECTORIES:
        paths.extend(p for p in (ROOT / directory).rglob('*') if p.is_file() and '__pycache__' not in p.parts
                     and p.suffix not in ('.pyc', '.pyo'))
    return sorted(set(paths))


def validate():
    assert all((ROOT / name).is_file() for name in TOP), 'Required files missing'
    manifest = json.loads((ROOT / '.codex-plugin/plugin.json').read_text())
    assert manifest['name'] == 'bambu-bridge'
    assert manifest['version'].split('+')[0] == '0.1.0-rc.1'
    assert manifest['mcpServers'] == './.mcp.json'
    skill = (ROOT / 'SKILL.md').read_text(encoding='utf-8')
    assert skill.startswith('---\nname: bambu-bridge\n')
    assert '[TODO' not in skill
    catalog = json.loads((ROOT / 'references/commands.json').read_text())
    assert re.fullmatch('[0-9a-f]{40}', catalog['upstream_revision'])
    assert len(catalog['commands']) >= 60
    count = 0
    for path in release_files():
        text = path.read_text(encoding='utf-8')
        assert not re.search(r'[A-Z]:[\\/]Users[\\/][^\s"\']+', text), f'Private machine path in {path.name}'
        if path.suffix == '.py':
            ast.parse(text, filename=str(path))
        if path.name.endswith('.json'):
            json.loads(text)
        count += 1
    return {'portable_files': count, 'catalog_commands': len(catalog['commands']), 'status': 'passed'}


if __name__ == '__main__':
    print(json.dumps(validate(), indent=2))
