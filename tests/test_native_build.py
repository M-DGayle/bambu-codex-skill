from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from build_native_windows import relocate_dependencies


class DependencyRelocationTests(unittest.TestCase):
    def test_relocates_metadata_keeps_original_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            path = root / 'target.cmake'
            original = 'set(LIB "D:/a/Studio/deps/build/BambuStudio_dep/usr/local/lib/freetype.lib")\n'
            path.write_text(original)
            unrelated = root / 'notes.txt'
            unrelated.write_text(original)
            self.assertEqual(relocate_dependencies(root), ['target.cmake'])
            self.assertIn(root.as_posix() + '/lib/freetype.lib', path.read_text())
            self.assertEqual(path.with_name('target.cmake.bridge-original').read_text(), original)
            self.assertEqual(unrelated.read_text(), original)
            self.assertEqual(relocate_dependencies(root), [])


if __name__ == '__main__':
    unittest.main()
