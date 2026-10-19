"""Unit tests for the lint ratchet (quality/check_lint.py); pylint itself is not run."""
import contextlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "quality", "check_lint.py")
_spec = importlib.util.spec_from_file_location("check_lint", _PATH)
cl = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(cl)


def msg(symbol="bare-except", obj="C.f", path="src/pycrefine/a.py", line=1, text="boom"):
    return {"symbol": symbol, "obj": obj, "path": path, "line": line, "message": text,
            "type": "warning"}


class TestKeys(unittest.TestCase):
    def test_key_ignores_line_numbers_and_message_text(self):
        self.assertEqual(cl.finding_key(msg(line=3, text="x")), cl.finding_key(msg(line=90, text="y")))

    def test_module_level_finding_uses_placeholder_object(self):
        self.assertTrue(cl.finding_key(msg(obj="")).endswith("::<module>"))

    def test_cyclic_import_is_keyed_by_message(self):
        a = msg(symbol="cyclic-import", obj="", text="Cyclic import (a -> b)")
        b = msg(symbol="cyclic-import", obj="", text="Cyclic import (a -> c)")
        self.assertNotEqual(cl.finding_key(a), cl.finding_key(b))

    def test_project_wide_findings_ignore_the_module_they_are_reported_on(self):
        a = msg(symbol="cyclic-import", obj="", path="src/pycrefine/a.py", text="Cyclic import (a -> b)")
        b = msg(symbol="cyclic-import", obj="", path="src/pycrefine/z.py", text="Cyclic import (a -> b)")
        self.assertEqual(cl.finding_key(a), cl.finding_key(b))

    def test_duplicate_code_key_ignores_line_ranges_but_not_content(self):
        text = "Similar lines in 2 files\n==a:[{}:{}]\n==b:[{}:{}]\n    x = 1"
        moved = msg(symbol="duplicate-code", obj="", text=text.format(10, 20, 5, 15))
        shifted = msg(symbol="duplicate-code", obj="", text=text.format(30, 40, 25, 35))
        other = msg(symbol="duplicate-code", obj="", text=text.format(10, 20, 5, 15).replace("x = 1", "y = 2"))
        self.assertEqual(cl.finding_key(moved), cl.finding_key(shifted))
        self.assertNotEqual(cl.finding_key(moved), cl.finding_key(other))

    def test_windows_paths_are_normalised(self):
        self.assertTrue(cl.finding_key(msg(path="src\\pycrefine\\a.py")).startswith("src/pycrefine/a.py"))

    def test_counts_are_grouped_and_sorted(self):
        counts = cl.count_findings([msg(), msg(line=2), msg(obj="C.g")])
        self.assertEqual(counts["src/pycrefine/a.py::bare-except::C.f"], 2)
        self.assertEqual(list(counts), sorted(counts))


class TestOutputEscaping(unittest.TestCase):
    def test_multiline_text_cannot_start_a_workflow_command(self):
        line = cl._one_line("first\n::error::injected")
        self.assertNotIn("\n", line)
        self.assertFalse(line.startswith("::"))

    def test_command_data_is_escaped(self):
        self.assertEqual(cl._escape_command("50%\r\nnext"), "50%25%0D%0Anext")
        self.assertEqual(cl._escape_command("a:b,c", prop=True), "a%3Ab%2Cc")

    def test_annotations_are_escaped_on_github_actions(self):
        out = io.StringIO()
        bad = msg(text="line one\n::warning::injected", line=7)
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), contextlib.redirect_stdout(out):
            cl._annotate([bad], {cl.finding_key(bad): (0, 1)})
        self.assertEqual(len(out.getvalue().splitlines()), 1)
        self.assertIn("%0A::warning::injected", out.getvalue())


    def test_only_the_excess_occurrences_are_annotated(self):
        existing, added = msg(line=3), msg(line=50)
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"GITHUB_ACTIONS": "true"}), contextlib.redirect_stdout(out):
            cl._annotate([existing, added], {cl.finding_key(added): (1, 2)})
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        self.assertIn("line=50", lines[0])


class TestPylintTimeout(unittest.TestCase):
    def test_a_hung_pylint_is_a_tool_failure(self):
        import subprocess
        hung = subprocess.TimeoutExpired(cmd="pylint", timeout=1)
        with mock.patch.object(cl.subprocess, "run", side_effect=hung), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cl.run_pylint(["src/pycrefine"])
        self.assertEqual(caught.exception.code, 2)

    def test_both_pylint_calls_are_bounded(self):
        import subprocess
        done = subprocess.CompletedProcess([], 0, stdout="[]", stderr="")
        with mock.patch.object(cl.subprocess, "run", return_value=done) as run:
            cl.run_pylint(["src/pycrefine"])
            cl.pylint_version()
        self.assertEqual(len(run.call_args_list), 2)
        for call in run.call_args_list:
            self.assertIsNotNone(call.kwargs.get("timeout"))


class TestCompare(unittest.TestCase):
    def test_equal_counts_pass(self):
        self.assertEqual(cl.compare({"a": 2}, {"a": 2}), {"increased": {}, "decreased": {}})

    def test_new_key_and_larger_count_are_increases(self):
        diff = cl.compare({"a": 3, "b": 1}, {"a": 2})
        self.assertEqual(diff["increased"], {"a": (2, 3), "b": (0, 1)})

    def test_fewer_findings_are_reported_as_decreases(self):
        diff = cl.compare({"a": 1}, {"a": 2, "gone": 1})
        self.assertEqual(diff["decreased"], {"a": (2, 1), "gone": (1, 0)})
        self.assertEqual(diff["increased"], {})


class TestMain(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.baseline = Path(self.tmp.name) / "baseline.json"

    def _run(self, messages, *args):
        patches = (mock.patch.object(cl, "run_pylint", return_value=messages),
                   mock.patch.object(cl, "pylint_version", return_value="9.9.9"))
        out = io.StringIO()
        with patches[0], patches[1], contextlib.redirect_stdout(out):
            rc = cl.main(["--baseline", str(self.baseline), *args])
        return rc, out.getvalue()

    def _write(self, findings):
        cl.write_baseline(self.baseline, findings, "9.9.9")

    def test_passes_when_findings_match_baseline(self):
        self._write(cl.count_findings([msg()]))
        self.assertEqual(self._run([msg(line=40)])[0], 0)

    def test_fails_on_a_new_finding(self):
        self._write(cl.count_findings([msg()]))
        rc, out = self._run([msg(), msg(obj="C.new")])
        self.assertEqual(rc, 1)
        self.assertIn("C.new", out)

    def test_missing_baseline_is_an_error(self):
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(self._run([msg()])[0], 2)

    def test_update_creates_the_first_baseline(self):
        self.assertEqual(self._run([msg()], "--update-baseline")[0], 0)
        self.assertEqual(json.loads(self.baseline.read_text())["findings"], cl.count_findings([msg()]))

    def test_update_refuses_a_scoped_target(self):
        self._write(cl.count_findings([msg(), msg(obj="C.g")]))
        before = self.baseline.read_text()
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            self._run([msg()], "--update-baseline", "src/pycrefine/a.py")
        self.assertEqual(caught.exception.code, 2)
        self.assertEqual(self.baseline.read_text(), before)

    def test_update_lowers_but_refuses_to_raise(self):
        self._write(cl.count_findings([msg(), msg(line=2)]))
        self.assertEqual(self._run([msg()], "--update-baseline")[0], 0)
        self.assertEqual(sum(json.loads(self.baseline.read_text())["findings"].values()), 1)
        rc, _ = self._run([msg(), msg(obj="C.new")], "--update-baseline")
        self.assertEqual(rc, 1)
        self.assertEqual(sum(json.loads(self.baseline.read_text())["findings"].values()), 1)

    def test_allow_increase_accepts_new_findings(self):
        self._write({})
        self.assertEqual(self._run([msg()], "--update-baseline", "--allow-increase")[0], 0)
        self.assertEqual(self._run([msg()])[0], 0)


class TestCommittedBaseline(unittest.TestCase):
    def test_baseline_file_is_well_formed(self):
        data = json.loads(cl.DEFAULT_BASELINE.read_text(encoding="utf-8"))
        self.assertEqual(data["schema_version"], cl.SCHEMA_VERSION)
        self.assertTrue(all(isinstance(n, int) and n > 0 for n in data["findings"].values()))
        self.assertEqual(list(data["findings"]), sorted(data["findings"]))


if __name__ == "__main__":
    unittest.main()
