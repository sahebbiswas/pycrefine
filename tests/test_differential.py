"""The differential behavior check (quality/check_differential.py) and its baselines."""
import importlib.util
import json
import os
import sys
import tempfile
import textwrap
import unittest
import unittest.mock
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_differential", _ROOT / "quality" / "check_differential.py")
cd = importlib.util.module_from_spec(_spec)
sys.modules["check_differential"] = cd
_spec.loader.exec_module(cd)


def result(name, status):
    return cd.FixtureResult(name, status)


class TestCompare(unittest.TestCase):

    def obs(self, **overrides):
        values = dict(exception=None, stdout="out\n", stderr="", result="1")
        values.update(overrides)
        return cd.Observation(**values)

    def test_identical_runs_have_no_mismatch(self):
        self.assertEqual(cd.compare(self.obs(), self.obs()), [])

    def test_each_field_is_reported(self):
        a = self.obs()
        b = self.obs(exception="ValueError: x", stdout="other\n", stderr="e\n", result="2")
        self.assertEqual([m["field"] for m in cd.compare(a, b)], ["exception", "stdout", "stderr", "result"])

    def test_mismatch_carries_both_values(self):
        (m,) = cd.compare(self.obs(result="1"), self.obs(result="2"))
        self.assertEqual((m["original"], m["decompiled"]), ("1", "2"))

    def test_long_captures_are_clipped(self):
        long = "x" * (cd.MAX_CAPTURE + 50)
        self.assertIn("50 more characters", cd._clip(long))
        self.assertIsNone(cd._clip(None))


class TestBaselineDiff(unittest.TestCase):

    BASE = {"entries": {"a": "equivalent", "b": "mismatch", "c": "syntax_error"}}

    def kinds(self, results):
        return {c["name"]: c["kind"] for c in cd.diff_baseline(results, self.BASE)}

    def test_no_change(self):
        same = [result("a", "equivalent"), result("b", "mismatch"), result("c", "syntax_error")]
        self.assertEqual(cd.diff_baseline(same, self.BASE), [])

    def test_a_lower_rung_regresses(self):
        kinds = self.kinds([result("a", "mismatch"), result("b", "decompile_error"),
                            result("c", "syntax_error")])
        self.assertEqual(kinds, {"a": "regressed", "b": "regressed"})

    def test_a_higher_rung_improves(self):
        kinds = self.kinds([result("a", "equivalent"), result("b", "equivalent"),
                            result("c", "mismatch")])
        self.assertEqual(kinds, {"b": "improved", "c": "improved"})

    def test_new_and_missing_fixtures_fail(self):
        kinds = self.kinds([result("a", "equivalent"), result("b", "mismatch"), result("d", "equivalent")])
        self.assertEqual(kinds, {"d": "new", "c": "missing"})
        self.assertTrue({"new", "missing"} <= set(cd.FAILING_CHANGES))

    def test_a_fixture_that_cannot_run_any_more_is_lost(self):
        self.assertEqual(self.kinds([result("a", cd.SOURCE_ERROR), result("b", "mismatch"),
                                     result("c", "syntax_error")]), {"a": "lost"})

    def test_improvements_do_not_fail(self):
        self.assertNotIn("improved", cd.FAILING_CHANGES)

    def test_baseline_lists_every_entry_sorted(self):
        results = [result("z", "mismatch"), result("a", "equivalent")]
        baseline = cd.to_baseline(results)
        self.assertEqual(list(baseline["entries"]), ["a", "z"])
        self.assertEqual(baseline["python_version"], cd.python_tag())

    def test_compile_only_entries_use_the_compiles_rung(self):
        self.assertLess(cd.level_of("syntax_error"), cd.level_of("compiles"))
        self.assertLess(cd.level_of("compiles"), cd.level_of("mismatch"))
        self.assertEqual(self.kinds([result("a", "equivalent"), result("b", "mismatch"),
                                     result("c", "compiles")]), {"c": "improved"})


@unittest.skipUnless(os.name == "posix" or sys.platform == "win32", "needs a subprocess")
class TestSourceErrorsAreNotScored(unittest.TestCase):

    def test_baseline_leaves_out_fixtures_that_did_not_run(self):
        baseline = cd.to_baseline([result("a", "equivalent"), result("b", cd.SOURCE_ERROR)])
        self.assertEqual(baseline["entries"], {"a": "equivalent"})

    def test_a_fixture_that_never_ran_is_not_new(self):
        base = {"entries": {"a": "equivalent"}}
        self.assertEqual(cd.diff_baseline([result("a", "equivalent"), result("b", cd.SOURCE_ERROR)], base), [])

    def test_a_scored_fixture_that_stops_running_is_still_lost(self):
        base = {"entries": {"a": "equivalent"}}
        (change,) = cd.diff_baseline([result("a", cd.SOURCE_ERROR)], base)
        self.assertEqual(change["kind"], "lost")


class TestCommandLine(unittest.TestCase):

    def run_main(self, *argv):
        from contextlib import redirect_stderr, redirect_stdout
        from io import StringIO
        out, err = StringIO(), StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cd.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_unknown_fixture_is_a_configuration_error(self):
        code, _, err = self.run_main("no_such_fixture", "--no-baseline")
        self.assertEqual(code, 2)
        self.assertIn("no entry", err)

    def test_relative_fixture_directory_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "plain.py").write_text("print('hi')\nRESULT = 3\n", encoding="utf-8")
            old = os.getcwd()
            os.chdir(tmp)
            try:
                code, out, _ = self.run_main("--corpus", ".", "--no-baseline")
            finally:
                os.chdir(old)
        self.assertEqual(code, 0, out)
        self.assertIn("equivalent", out)

    def test_partial_baseline_update_is_rejected_before_running_anything(self):
        with unittest.mock.patch.object(cd, "run_entries", side_effect=AssertionError("ran")):
            code, _, err = self.run_main("--update-baseline", "--category", "stdlib")
        self.assertEqual(code, 2)
        self.assertIn("records every entry", err)

    def test_one_report_cannot_serve_several_interpreters(self):
        code, _, err = self.run_main("--python", "a", "--python", "b", "--report", "r.json")
        self.assertEqual(code, 2)
        self.assertIn("one at a time", err)

    def test_one_baseline_cannot_serve_several_interpreters(self):
        code, _, _ = self.run_main("--python", "a", "--python", "b", "--baseline", "b.json")
        self.assertEqual(code, 2)


class TestWorkerEnvironment(unittest.TestCase):

    def run_fixture(self, source):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp, "f.py")
            path.write_text(source, encoding="utf-8")
            done = cd.run_worker(sys.executable, "run", str(path), 30, tmp)
        return json.loads(done.stdout)

    def test_the_harness_sets_hash_seed_and_utf8(self):
        seen = self.run_fixture("import os, sys\nprint(os.environ.get('PYTHONHASHSEED'), sys.flags.utf8_mode)\n")
        self.assertEqual(seen["stdout"].split(), ["0", "1"])

    def test_a_fixture_cannot_import_the_harness(self):
        seen = self.run_fixture("import check_differential\n")
        self.assertIn("ModuleNotFoundError", seen["exception"])


class TestEndToEnd(unittest.TestCase):
    """Runs the real harness on throwaway fixtures, with the interpreter running the tests."""

    def check(self, source, name="case", **options):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{name}.py"
            path.write_text(textwrap.dedent(source), encoding="utf-8")
            entry = cd.Entry(id=name, category="feature", path=path, **options)
            return cd.run_entries(sys.executable, [entry])[0]

    def test_simple_program_is_equivalent(self):
        r = self.check("""
            def add(a, b):
                return a + b
            print(add(1, 2))
            RESULT = add(3, 4)
        """)
        self.assertEqual((r.status, r.mismatches), ("equivalent", []))

    def test_uncaught_exception_is_compared(self):
        r = self.check("print('before')\nraise ValueError('nope')\n")
        self.assertEqual(r.status, "equivalent")

    def test_source_that_does_not_run_is_not_scored(self):
        r = self.check("def broken(:\n")
        self.assertEqual(r.status, cd.SOURCE_ERROR)

    def test_a_runtime_syntax_error_is_compared_not_skipped(self):
        r = self.check("raise SyntaxError('at run time')\n")
        self.assertEqual(r.status, "equivalent")

    def test_a_runaway_fixture_is_stopped(self):
        original = cd.RUN_TIMEOUT
        cd.RUN_TIMEOUT = 2
        try:
            r = self.check("while True:\n    pass\n")
        finally:
            cd.RUN_TIMEOUT = original
        # Both runs time out the same way, so the observations agree; what matters is that
        # the harness returned instead of hanging.
        self.assertIn(r.status, ("equivalent", "mismatch", "decompile_error"))

    def test_run_is_isolated_from_the_callers_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "env.py"
            path.write_text("import os\nRESULT = (sorted(k for k in os.environ if k.startswith('PYCREFINE')), "
                            "os.getcwd() == os.path.realpath(os.getcwd()))\n", encoding="utf-8")
            previous = os.environ.get("PYCREFINE_SECRET")
            os.environ["PYCREFINE_SECRET"] = "x"
            try:
                observed = cd.observe(sys.executable, str(path), tmp)
            finally:
                if previous is None:
                    del os.environ["PYCREFINE_SECRET"]
                else:
                    os.environ["PYCREFINE_SECRET"] = previous
            self.assertTrue(observed.result.startswith("([],"), observed.result)


@unittest.skipUnless(os.name == "posix" or sys.platform == "win32", "needs a subprocess")
class TestOptions(unittest.TestCase):

    def check(self, source, **options):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "case.py"
            path.write_text(textwrap.dedent(source), encoding="utf-8")
            entry = cd.Entry(id="case", category="feature", path=path, **options)
            return cd.run_entries(sys.executable, [entry])[0]

    def test_a_compile_only_entry_is_never_run(self):
        r = self.check("raise SystemExit('would stop a run')\n", equivalence="compile_only")
        self.assertEqual(r.status, "compiles")

    def test_optimized_compilation_drops_asserts_from_the_original_run_too(self):
        r = self.check("""
            def f(n):
                assert n > 0, "positive"
                return n
            print(f(-1))
        """, optimize=2)
        self.assertEqual(r.status, "equivalent", r.as_dict())


if __name__ == "__main__":
    unittest.main()
