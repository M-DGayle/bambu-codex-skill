"""Private setup and Studio credential import. Never prints access codes."""
import argparse
import getpass
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import venv

from common import atomic_json, config, state_dir

ROOT = Path(__file__).resolve().parent


def configure_discovered(alias: str, serial: str, host: str, certificate_sha256: str,
                         allow_control: bool, confirmed: bool) -> dict:
    from common import require_write
    from printer import discovery, lan_address, probe_certificate, saved_devices
    require_write(confirmed)
    if not re.fullmatch('[a-z][a-z0-9-]{0,40}', alias):
        raise ValueError('Alias must use lowercase letters, digits and hyphens.')
    host = lan_address(host)
    observed = discovery(3)['devices']
    if not any(d['serial'] == serial and d['host'] == host for d in observed):
        raise ValueError('Device must match a fresh LAN announcement.')
    code = saved_devices().get(serial)
    if not isinstance(code, str) or not code:
        raise ValueError('No saved Studio access code for this serial. Use interactive setup.')
    actual = probe_certificate(host)
    if actual['sha256'] != certificate_sha256.lower():
        raise ValueError('Certificate changed since inspection.')
    current = config()
    printers = current.setdefault('printers', {})
    if alias in printers:
        raise ValueError('Alias already configured; use interactive setup to review replacement.')
    printers[alias] = {'host': host, 'serial': serial, 'access_code': code,
                       'certificate_sha256': actual['sha256'], 'allow_control': allow_control,
                       'trust_method': 'explicit first-use pin of discovered LAN device'}
    atomic_json(state_dir() / 'config.json', current)
    return {'configured': alias, 'host': host, 'allow_control': allow_control,
            'credential_source': 'Bambu Studio local saved access code', 'trust_method': printers[alias]['trust_method']}


def mcp_config(root: Path, runtime: Path) -> dict:
    return {'mcpServers': {'bambu': {'command': str(runtime), 'args': [str(root / 'server.py')]}}}


def setup_mcp(replace: bool = False) -> dict:
    runtime_dir = state_dir() / 'venv'
    runtime = runtime_dir / ('Scripts/python.exe' if sys.platform == 'win32' else 'bin/python')
    launch = mcp_config(ROOT, runtime)
    destination = ROOT / '.mcp.json'
    if destination.exists() and json.loads(destination.read_text()) != launch and not replace:
        raise ValueError('Different .mcp.json exists; review before --replace-config.')
    if not runtime.is_file():
        venv.EnvBuilder(with_pip=True).create(runtime_dir)
    subprocess.run([str(runtime), '-m', 'pip', 'install', '-r', str(ROOT / 'requirements.txt')], check=True)
    atomic_json(destination, launch)
    return {'runtime': str(runtime), 'mcp_config': str(destination)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mcp', action='store_true')
    parser.add_argument('--replace-config', action='store_true')
    parser.add_argument('--studio-path')
    parser.add_argument('--printer', action='store_true', help='Interactive credential setup; access code input is hidden')
    args = parser.parse_args()
    if sys.version_info < (3, 12):
        parser.error('Python 3.12 or newer is required.')
    if args.mcp:
        print(json.dumps(setup_mcp(args.replace_config), indent=2))
    if args.studio_path:
        path = Path(args.studio_path).expanduser().resolve(strict=True)
        current = config()
        current['studio_path'] = str(path)
        atomic_json(state_dir() / 'config.json', current)
        print('Studio executable configured.')
    if args.printer:
        from printer import lan_address, probe_certificate, saved_devices
        alias = input('Printer alias (e.g. workshop): ').strip()
        if not re.fullmatch('[a-z][a-z0-9-]{0,40}', alias):
            parser.error('Invalid alias.')
        host = lan_address(input('LAN IP: ').strip())
        serial = input('Printer serial: ').strip()
        if not re.fullmatch('[A-Za-z0-9-]{5,64}', serial):
            parser.error('Invalid serial.')
        cert = probe_certificate(host)
        print('Certificate SHA-256:', cert['sha256'])
        if input('Trust this printer certificate after checking its identity? [yes/no]: ') != 'yes':
            parser.error('Certificate not trusted.')
        code = saved_devices().get(serial)
        if code and input('Use this printer\'s saved Studio access code? [yes/no]: ') != 'yes':
            code = None
        code = code or getpass.getpass('LAN access code (hidden): ')
        if not code:
            parser.error('Access code required.')
        allow_control = input('Enable write tools for explicitly requested actions? [yes/no]: ') == 'yes'
        current = config()
        printers = current.setdefault('printers', {})
        if alias in printers and input('Replace the existing alias configuration? [yes/no]: ') != 'yes':
            parser.error('Existing configuration preserved.')
        printers[alias] = {'host': host, 'serial': serial, 'access_code': code,
                           'certificate_sha256': cert['sha256'], 'allow_control': allow_control,
                           'trust_method': 'interactive first-use pin'}
        atomic_json(state_dir() / 'config.json', current)
        print('Printer configuration saved. No printer commands executed.')
    if not any((args.mcp, args.studio_path, args.printer)):
        parser.print_help()


if __name__ == '__main__':
    main()
