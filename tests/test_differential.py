"""The differential behavior check (quality/check_differential.py) and its baselines."""
import importlib.util
import json
import os
import sys
import tempfile
import textwrap
import unittest
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

    BASE = {"fixtures": {"a": "equivalent", "b": "mismatch", "c": "syntax_error"}}

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

    def test_baseline_keeps_issues_only_for_fixtures_that_still_fail(self):
        previous = {"issues": {"b": [7], "a": [9]}}
        results = [result("a", "equivalent"), result("b", "mismatch"), result("z", "mismatch")]
        baseline = cd.to_baseline(results, previous)
        self.assertEqual(baseline["issues"], {"b": [7]})
        self.assertEqual(list(baseline["fixtures"]), ["a", "b", "z"])


@unittest.skipUnless(os.name == "posix" or sys.platform == "win32", "needs a subprocess")
class TestEndToEnd(unittest.TestCase):
    """Runs the real harness on throwaway fixtures, with the interpreter running the tests."""

    def check(self, source, name="case"):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / f"{name}.py"
            path.write_text(textwrap.dedent(source), encoding="utf-8")
            return cd.run_fixtures(sys.executable, [path])[0]

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


class TestFixturesAndBaselines(unittest.TestCase):

    def fixtures(self):
        return {p.stem for p in cd.DEFAULT_FIXTURES.glob("*.py")}

    def test_there_are_enough_fixtures(self):
        self.assertGreaterEqual(len(self.fixtures()), 20)

    def test_every_baseline_covers_exactly_the_fixtures(self):
        baselines = sorted(cd.BASELINE_DIR.glob("py*.json"))
        self.assertTrue(baselines)
        for path in baselines:
            with self.subTest(baseline=path.name):
                data = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(data["schema_version"], cd.SCHEMA_VERSION)
                self.assertEqual(f"py{data['python_version']}.json", path.name)
                self.assertEqual(set(data["fixtures"]), self.fixtures())
                for status in data["fixtures"].values():
                    self.assertIn(status, cd.LEVELS)

    def test_every_failing_fixture_names_an_issue(self):
        """A gap the harness finds is tracked in an issue, and the baseline says which."""
        for path in sorted(cd.BASELINE_DIR.glob("py*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            issues = data.get("issues", {})
            failing = {n for n, s in data["fixtures"].items() if s != "equivalent"}
            with self.subTest(baseline=path.name):
                self.assertEqual(set(issues), failing)
                for numbers in issues.values():
                    self.assertTrue(numbers)
                    self.assertTrue(all(isinstance(n, int) and n > 0 for n in numbers))

    def test_the_baseline_for_this_python_matches_a_real_run_of_a_fixture(self):
        baseline = json.loads(cd.default_baseline_path().read_text(encoding="utf-8"))
        name = "expr_arithmetic"
        (r,) = cd.run_fixtures(sys.executable, [cd.DEFAULT_FIXTURES / f"{name}.py"])
        self.assertEqual(r.status, baseline["fixtures"][name], r.as_dict())


if __name__ == "__main__":
    unittest.main()
