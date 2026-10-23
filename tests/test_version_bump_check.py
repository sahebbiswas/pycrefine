"""The CI check that every pull request bumps the version by one step (CONTRIBUTING.md)."""
import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / ".github" / "scripts" / "check_version_bump.py"


def _run(old: str, new: str) -> subprocess.CompletedProcess:
    with tempfile.TemporaryDirectory() as tmp:
        old_file, new_file = Path(tmp) / "old.py", Path(tmp) / "new.py"
        old_file.write_text(old, encoding="utf-8")
        new_file.write_text(new, encoding="utf-8")
        return subprocess.run([sys.executable, str(SCRIPT), str(old_file), str(new_file)],
                              capture_output=True, text=True, check=False)


class TestVersionBumpCheck(unittest.TestCase):
    def test_one_step_bumps_pass(self):
        for new, kind in (("1.4.1", "patch"), ("1.5.0", "minor"), ("2.0.0", "major")):
            with self.subTest(new=new):
                result = _run('__version__ = "1.4.0"\n', f'__version__ = "{new}"\n')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn(f"({kind} bump)", result.stdout)

    def test_missing_or_oversized_bumps_fail(self):
        for new in ("1.4.0", "1.4.2", "1.5.1", "1.3.0", "3.0.0"):
            with self.subTest(new=new):
                result = _run('__version__ = "1.4.0"\n', f'__version__ = "{new}"\n')
                self.assertEqual(result.returncode, 1)
                self.assertIn("src/pycrefine/__init__.py", result.stderr)

    def test_malformed_version_files_fail(self):
        for new in ('__version__ = "1.4.1rc1"\n', "__version__ = VERSION\n",
                    '__version__ = "1.4.1"\n__version__ = "1.4.2"\n', "VERSION = 1\n"):
            with self.subTest(new=new):
                result = _run('__version__ = "1.4.0"\n', new)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("error:", result.stderr)

    def test_package_version_is_read_from_the_package(self):
        spec = importlib.util.spec_from_file_location("check_version_bump", SCRIPT)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        import pycrefine
        self.assertEqual(module.read_version(str(ROOT / "src" / "pycrefine" / "__init__.py")),
                         pycrefine.__version__)


if __name__ == "__main__":
    unittest.main()
