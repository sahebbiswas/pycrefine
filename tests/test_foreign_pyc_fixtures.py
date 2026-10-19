"""The committed foreign-version .pyc fixtures must match their manifest."""
import importlib.util
import json
import os
import unittest

from pycrefine import _get_python_version_from_magic

_FIXTURES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")
_DIR = os.path.join(_FIXTURES, "foreign_pyc")


def _load_regenerate():
    spec = importlib.util.spec_from_file_location("foreign_pyc_regenerate",
                                                  os.path.join(_FIXTURES, "regenerate_foreign_pyc.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestForeignPycManifest(unittest.TestCase):
    def test_manifest_matches_files_on_disk(self):
        regen = _load_regenerate()
        self.assertEqual(regen.check(), 0)

    def test_magic_numbers_match_recorded_python_version(self):
        with open(os.path.join(_DIR, "manifest.json")) as f:
            manifest = json.load(f)
        self.assertTrue(manifest["files"])
        for name, entry in manifest["files"].items():
            with self.subTest(name=name):
                major, minor = (int(p) for p in entry["python"].split("."))
                self.assertEqual(_get_python_version_from_magic(entry["magic"]), entry["python"])
                self.assertIn(f"cpython-{major}{minor}", name)


if __name__ == "__main__":
    unittest.main()
