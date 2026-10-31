"""The command line: program name, --version, and errors for files that cannot be read (#130, #153)."""
import contextlib
import errno
import io
import os
import py_compile
import sys
import tempfile
import unittest
from unittest.mock import patch

import pycrefine
from pycrefine.cli import main


def _run(*argv):
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with patch("sys.argv", ["anything.py", *argv]), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            main()
        except SystemExit as exit_:
            code = exit_.code or 0
    return code, out.getvalue(), err.getvalue()


class TestCommandLine(unittest.TestCase):

    def test_version_option_prints_the_version(self):
        code, out, _ = _run("--version")
        self.assertEqual(code, 0)
        self.assertEqual(out.strip(), f"pycrefine {pycrefine.__version__}")

    def test_program_name_does_not_depend_on_how_it_was_started(self):
        code, out, _ = _run("--help")
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("usage: pycrefine "), out[:60])
        self.assertIn("--version", out)

    def test_missing_file_is_a_one_line_error(self):
        code, out, err = _run("/nonexistent/nope.pyc")
        self.assertEqual(code, 1)
        self.assertEqual(out, "")
        reason = os.strerror(errno.ENOENT)  # the wording depends on the operating system and its language
        self.assertEqual(err.strip(), f"Error: cannot read /nonexistent/nope.pyc: {reason}")

    def test_directory_is_a_one_line_error(self):
        with tempfile.TemporaryDirectory() as folder:
            code, _, err = _run(folder)
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("Error: cannot read "), err)
        self.assertEqual(len(err.strip().splitlines()), 1, err)
        self.assertNotIn("Traceback", err)

    def test_source_file_is_reported_as_not_a_pyc(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "module.py")
            with open(path, "w", encoding="utf-8") as handle:
                handle.write("import os\n\n\ndef main():\n    print('this is source code')\n")
            code, _, err = _run(path)
        self.assertEqual(code, 1)
        self.assertIn("is not a .pyc file", err)
        self.assertNotIn("Python 3.15", err)
        self.assertNotIn("magic number: 0x", err)

    def test_short_file_is_still_reported_as_too_short(self):
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "tiny.pyc")
            with open(path, "wb") as handle:
                handle.write(b"x = 1\n")
            code, _, err = _run(path)
        self.assertEqual(code, 1)
        self.assertIn("too short", err)

    @unittest.skipUnless((3, 9) <= sys.version_info[:2] <= (3, 14), "the host's .pyc files are not supported")
    def test_failure_to_write_to_stdout_is_not_reported_as_a_read_error(self):
        class ClosedPipe(io.StringIO):
            def write(self, text):
                raise BrokenPipeError(32, "Broken pipe")

        err = io.StringIO()
        with tempfile.TemporaryDirectory() as folder:
            path = os.path.join(folder, "mod.pyc")
            py_compile.compile(self._source(folder), cfile=path, doraise=True)
            with patch("sys.argv", ["pycrefine", path]), contextlib.redirect_stdout(ClosedPipe()), \
                    contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
                main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("could not write the decompiled source to standard output", err.getvalue())
        self.assertNotIn("cannot read", err.getvalue())

    def test_failure_to_write_survives_a_missing_null_device(self):
        class ClosedPipe(io.StringIO):
            def write(self, text):
                raise BrokenPipeError(32, "Broken pipe")

            def fileno(self):
                return 1

        err = io.StringIO()
        with patch("sys.argv", ["pycrefine", "--version"]), contextlib.redirect_stdout(ClosedPipe()), \
                contextlib.redirect_stderr(err), patch("os.open", side_effect=OSError("no null device")), \
                self.assertRaises(SystemExit) as raised:
            main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("could not write the version to standard output", err.getvalue())

    def test_failure_to_write_the_version_is_a_one_line_error(self):
        class ClosedPipe(io.StringIO):
            def write(self, text):
                raise BrokenPipeError(32, "Broken pipe")

        err = io.StringIO()
        with patch("sys.argv", ["pycrefine", "--version"]), contextlib.redirect_stdout(ClosedPipe()), \
                contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as raised:
            main()
        self.assertEqual(raised.exception.code, 1)
        self.assertIn("could not write the version to standard output", err.getvalue())

    @staticmethod
    def _source(folder):
        path = os.path.join(folder, "mod.py")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write("x = 1\n")
        return path


if __name__ == "__main__":
    unittest.main()
