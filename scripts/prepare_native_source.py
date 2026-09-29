"""Apply the optional GUI-thread bridge to the exact reviewed upstream source tree."""
import argparse
import hashlib
from pathlib import Path
import shutil

REVISION = 'da8b44ee34dd349f2ae0df3f1cbae366df482354'
APP_HASH = '0f42076fe00edee900f860acfdb11f8760daabd473470a80287a62130221e71f'
CMAKE_HASH = '6870963349fb55f9353600d9ede9b7b98925ccfc2aecba9cccdae1336600e884'
STARTUP_HOOK = '            start_codex_studio_bridge();\n            this->post_init();\n            finish_codex_studio_bridge_startup();'


def prepare(source: Path):
    root = Path(__file__).resolve().parents[1]
    app_path = source / 'src/slic3r/GUI/GUI_App.cpp'
    cmake_path = source / 'src/slic3r/CMakeLists.txt'
    app = app_path.read_text(encoding='utf-8')
    cmake = cmake_path.read_text(encoding='utf-8')
    original_app = app.removeprefix('#include "StudioBridge.hpp"\n').replace(
        'int GUI_App::OnExit()\n{\n    stop_codex_studio_bridge();', 'int GUI_App::OnExit()\n{').replace(
        '            this->post_init();\n            start_codex_studio_bridge();', '            this->post_init();').replace(
        STARTUP_HOOK, '            this->post_init();')
    original_cmake = cmake.replace('    GUI/StudioBridge.cpp\n    GUI/StudioBridge.hpp\n', '')
    if hashlib.sha256(original_app.encode()).hexdigest() != APP_HASH or hashlib.sha256(original_cmake.encode()).hexdigest() != CMAKE_HASH:
        raise ValueError(f'Unexpected upstream source. Use reviewed revision {REVISION}; local changes were preserved.')
    if STARTUP_HOOK not in app:
        app = original_app
        assert app.count('int GUI_App::OnExit()\n{') == 1
        assert app.count('            this->post_init();') == 1
        app = '#include "StudioBridge.hpp"\n' + app
        app = app.replace('int GUI_App::OnExit()\n{', 'int GUI_App::OnExit()\n{\n    stop_codex_studio_bridge();')
        app = app.replace('            this->post_init();', STARTUP_HOOK)
        app_path.write_text(app, encoding='utf-8')
    if 'GUI/StudioBridge.cpp' not in cmake:
        assert cmake.count('set(SLIC3R_GUI_SOURCES\n') == 1
        cmake_path.write_text(cmake.replace('set(SLIC3R_GUI_SOURCES\n', 'set(SLIC3R_GUI_SOURCES\n    GUI/StudioBridge.cpp\n    GUI/StudioBridge.hpp\n'), encoding='utf-8')
    for name in ('StudioBridge.cpp', 'StudioBridge.hpp'):
        shutil.copy2(root / 'native' / name, source / 'src/slic3r/GUI' / name)
    print('Native bridge source installed; compilation and runtime verification are still required.')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', type=Path)
    prepare(parser.parse_args().source.resolve(strict=True))
