"""Launch an explicitly selected bridge-enabled Studio build without replacing an existing installation."""
import argparse
import os
from pathlib import Path
import subprocess
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import native_client


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--executable', type=Path, required=True)
    parser.add_argument('--data-dir', type=Path, required=True)
    parser.add_argument('--model', type=Path)
    args = parser.parse_args()
    executable = args.executable.resolve(strict=True)
    directory = args.data_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    native_client.root().mkdir(parents=True, exist_ok=True)
    command = [str(executable), '--datadir', str(directory)]
    if args.model:
        command.append(str(args.model.resolve(strict=True)))
    process = subprocess.Popen(command, env={**os.environ, 'BAMBU_BRIDGE_NATIVE_DIR': str(native_client.root())})
    print(f'Launch requested for process {process.pid}; verify studio_live_sessions and studio_live_read before claiming connection.')


if __name__ == '__main__':
    main()
