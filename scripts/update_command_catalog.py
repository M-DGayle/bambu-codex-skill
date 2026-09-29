"""Extract command names and field names (not implementation code) from pinned upstream Studio source."""
import concurrent.futures
import json
from pathlib import Path
import re
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]


def get(url):
    with urlopen(Request(url, headers={'User-Agent': 'bambu-bridge-catalog'}), timeout=30) as response:
        return response.read().decode()


def main():
    revision = json.loads(get('https://api.github.com/repos/bambulab/BambuStudio/commits/master'))['sha']
    tree = json.loads(get(f'https://api.github.com/repos/bambulab/BambuStudio/git/trees/{revision}?recursive=1'))
    paths = [e['path'] for e in tree['tree'] if e['path'].endswith('.cpp') and
             ('/DeviceCore/' in e['path'] or e['path'].endswith('/DeviceManager.cpp'))]
    assignment = re.compile(r'\b(\w+)\["(\w+)"\]\["command"\]\s*=\s*"([^"]+)"')
    commands = {}

    def extract(path):
        source = get(f'https://raw.githubusercontent.com/bambulab/BambuStudio/{revision}/{path}')
        entries = []
        for match in assignment.finditer(source):
            variable, section, command = match.groups()
            before = source.rfind('\n}', 0, match.start())
            after = source.find('\n}', match.end())
            block = source[max(0, before):after if after >= 0 else len(source)]
            fields = sorted(set(re.findall(r'\b' + re.escape(variable) + r'\["' + re.escape(section) + r'"\]\["([^"]+)"\]', block)) - {'command', 'sequence_id'})
            entries.append({'section': section, 'command': command, 'observed_fields': fields,
                            'source': f'https://github.com/bambulab/BambuStudio/blob/{revision}/{path}#L{source[:match.start()].count(chr(10)) + 1}'})
        return entries

    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        for entries in pool.map(extract, paths):
            for entry in entries:
                key = (entry['section'], entry['command'])
                if key in commands:
                    commands[key]['observed_fields'] = sorted(set(commands[key]['observed_fields'] + entry['observed_fields']))
                else:
                    commands[key] = entry
    data = {'upstream_revision': revision,
            'notice': 'Factual command/field index extracted from upstream source. Not a complete protocol schema. Fields may be conditional; follow the linked implementation and verify exact model/firmware. No command in this catalog grants authorization to execute it.',
            'commands': [commands[k] for k in sorted(commands)]}
    target = ROOT / 'references/commands.json'
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'commands': len(commands), 'revision': revision}))


if __name__ == '__main__':
    main()
