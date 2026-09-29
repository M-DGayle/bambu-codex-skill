"""Configure/build the optional Studio integration in separate build/install directories."""
import argparse
from pathlib import Path
import re
import subprocess
import sys

from prepare_native_source import prepare


def relocate_dependencies(directory):
    """Repair upstream CI's absolute prefix in an extracted dependency bundle."""
    prefix = re.compile(r'[A-Za-z]:/[^";\s]*?/BambuStudio_dep/usr/local')
    changed = []
    for path in directory.rglob('*'):
        if not path.is_file() or path.suffix not in ('.cmake', '.pc'):
            continue
        original = path.read_text(encoding='utf-8')
        updated = prefix.sub(lambda _: directory.as_posix(), original)
        if updated == original:
            continue
        backup = path.with_name(path.name + '.bridge-original')
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
        path.write_text(updated, encoding='utf-8')
        changed.append(str(path.relative_to(directory)))
    return changed


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--dependencies', type=Path, required=True)
    parser.add_argument('--build-dir', type=Path, required=True)
    parser.add_argument('--install-dir', type=Path, required=True)
    parser.add_argument('--sdk', type=Path, required=True)
    parser.add_argument('--pkg-config', type=Path, required=True)
    parser.add_argument('--cmake', default='cmake')
    parser.add_argument('--generator', default='Visual Studio 18 2026')
    parser.add_argument('--jobs', type=int, default=4)
    args = parser.parse_args()
    if sys.platform != 'win32' or not 1 <= args.jobs <= 16:
        parser.error('This builder requires Windows and 1 to 16 compiler jobs.')
    source = args.source.resolve(strict=True)
    destination = args.install_dir.resolve()
    marker = destination / '.bambu-bridge-native-build'
    if destination.exists() and any(destination.iterdir()) and not marker.exists():
        parser.error('Choose an empty install directory; an existing stock Studio installation must not be overwritten.')
    destination.mkdir(parents=True, exist_ok=True)
    marker.write_text('Optional native bridge build; runtime acceptance must be verified separately.\n')
    prepare(source)
    dependencies = args.dependencies.resolve(strict=True)
    relocated = relocate_dependencies(dependencies)
    print(f'Relocated {len(relocated)} dependency metadata files; originals retained beside them.')
    subprocess.run([args.cmake, '-S', str(source), '-B', str(args.build_dir.resolve()), '-G', args.generator,
        '-A', 'x64', '-DBBL_RELEASE_TO_PUBLIC=1', '-DBBL_INTERNAL_TESTING=0', '-DCMAKE_BUILD_TYPE=Release',
        '-DCMAKE_POLICY_VERSION_MINIMUM=3.5', '-DSLIC3R_MSVC_COMPILE_PARALLEL=OFF',
        '-DCMAKE_PREFIX_PATH=' + str(dependencies),
        '-DCMAKE_INSTALL_PREFIX=' + str(destination), '-DWIN10SDK_PATH=' + str(args.sdk.resolve(strict=True)),
        '-DPKG_CONFIG_EXECUTABLE=' + str(args.pkg_config.resolve(strict=True))], check=True)
    subprocess.run([args.cmake, '--build', str(args.build_dir.resolve()), '--target', 'install', '--config', 'Release',
        '--parallel', '4', '--', '/p:UseMultiToolTask=true', '/p:MultiProcessorCompilation=true',
        '/p:CL_MPCount=' + str(args.jobs), '/p:EnforceProcessCountAcrossBuilds=true'], check=True)
    print('Build/install completed. Verify an actual native session before using it for a user project.')


if __name__ == '__main__':
    main()
