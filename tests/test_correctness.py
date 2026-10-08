import importlib
import json
import os
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

_QUALITY = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "quality")
if _QUALITY not in sys.path:
    sys.path.insert(0, _QUALITY)
cc = importlib.import_module("check_correctness")


class TestClassify(unittest.TestCase):
    def test_compiles(self):
        self.assertEqual(cc.classify("x = 1\n").status, "compiles")

    def test_syntax_error(self):
        c = cc.classify("x = = 1\n")
        self.assertEqual(c.status, "syntax_error")
        self.assertEqual(c.error["stage"], "syntax")
        self.assertEqual(c.error["lineno"], 1)

    def test_compile_error_is_distinct_from_syntax_error(self):
        # Parses fine, but is rejected by the compiler.
        c = cc.classify("return 1\n")
        self.assertEqual(c.status, "compile_error")
        self.assertEqual(c.error["stage"], "compile")

    def test_levels_are_ordered(self):
        self.assertLess(cc.level_of("decompile_error"), cc.level_of("syntax_error"))
        self.assertLess(cc.level_of("syntax_error"), cc.level_of("compile_error"))
        self.assertLess(cc.level_of("compile_error"), cc.level_of("compiles"))
        self.assertIsNone(cc.level_of(cc.SOURCE_ERROR))


class TestExtractUnits(unittest.TestCase):
    SOURCE = textwrap.dedent('''\
        from __future__ import annotations
        import os

        @deco
        def f(a):
            return a

        class C(Base):
            """doc"""
            def m(self):
                return 1

            def m(self):
                return 2

        class D: pass
        ''')

    def test_unit_ids(self):
        ids = [u.id for u in cc.extract_units(self.SOURCE)]
        self.assertEqual(ids, [
            "Import: import os",
            "def f",
            'class C/Expr: """doc"""',
            "class C/def m",
            "class C/def m#2",
            "class D",
        ])

    def test_unit_ids_do_not_depend_on_line_numbers(self):
        moved = "\n\n# comment\n" + self.SOURCE.replace("import os\n", "import os\n\n\n")
        moved = moved.replace("from __future__ import annotations\n", "", 1)
        moved = "from __future__ import annotations\n" + moved
        self.assertEqual([u.id for u in cc.extract_units(self.SOURCE)],
                         [u.id for u in cc.extract_units(moved)])

    def test_long_statement_ids_are_truncated(self):
        ids = [u.id for u in cc.extract_units("x = " + "1 + " * 40 + "1\n")]
        self.assertEqual(len(ids), 1)
        self.assertTrue(ids[0].startswith("Assign: x = 1 + "))
        self.assertTrue(ids[0].endswith("..."))

    def test_units_carry_future_imports_and_decorators(self):
        units = {u.id: u.source for u in cc.extract_units(self.SOURCE)}
        self.assertTrue(units["def f"].startswith("from __future__ import annotations\n"))
        self.assertIn("@deco\n", units["def f"])
        self.assertIn("class C:\n", units["class C/def m"])
        for src in units.values():
            compile(src, "<unit>", "exec")


class TestCompare(unittest.TestCase):
    def _result(self, path, status, units=None):
        r = cc.EntryResult(path, "source" if units is not None else "pyc", status)
        r.units = {k: {"status": v} for k, v in (units or {}).items()}
        return r

    def _baseline(self, entries):
        return {"schema_version": cc.SCHEMA_VERSION, "entries": entries}

    def _changes(self, changes):
        return {(c["path"], c["unit"], c["change"]) for c in changes}

    def test_unchanged(self):
        base = self._baseline({"a.pyc": {"kind": "pyc", "status": "compiles"}})
        self.assertEqual(cc.compare([self._result("a.pyc", "compiles")], base), [])

    def test_entry_regression_and_improvement(self):
        base = self._baseline({
            "a.pyc": {"kind": "pyc", "status": "compiles"},
            "b.pyc": {"kind": "pyc", "status": "syntax_error"},
        })
        changes = cc.compare([self._result("a.pyc", "compile_error"),
                              self._result("b.pyc", "compiles")], base)
        self.assertEqual(self._changes(changes), {
            ("a.pyc", None, "regressed"),
            ("b.pyc", None, "improved"),
        })

    def test_unit_regression_inside_failing_file(self):
        base = self._baseline({"a.py": {"kind": "source", "status": "syntax_error",
                                        "units": {"def f": "compiles", "def g": "syntax_error"}}})
        changes = cc.compare([self._result("a.py", "syntax_error",
                                           {"def f": "syntax_error", "def g": "compiles"})], base)
        self.assertEqual(self._changes(changes), {
            ("a.py", "def f", "regressed"),
            ("a.py", "def g", "improved"),
        })

    def test_new_missing_and_unscorable(self):
        base = self._baseline({
            "a.py": {"kind": "source", "status": "compiles", "units": {"def gone": "compiles"}},
            "b.pyc": {"kind": "pyc", "status": "compiles"},
        })
        changes = cc.compare([self._result("a.py", "compiles", {"def kept": "compiles"}),
                              self._result("c.pyc", "compiles")], base)
        self.assertEqual(self._changes(changes), {
            ("a.py", "def gone", "missing"),
            ("a.py", "def kept", "new"),
            ("b.pyc", None, "missing"),
            ("c.pyc", None, "new"),
        })
        report = cc.build_report([], changes, None)
        self.assertEqual(report["result"], "fail")  # missing records fail

    def test_scored_record_becoming_source_error_fails(self):
        base = self._baseline({"a.py": {"kind": "source", "status": "compiles",
                                        "units": {"def f": "compiles"}}})
        changes = cc.compare([self._result("a.py", cc.SOURCE_ERROR, {})], base)
        self.assertEqual(self._changes(changes), {("a.py", None, "lost")})
        self.assertEqual(cc.build_report([], changes, None)["result"], "fail")

    def test_duplicate_label_count_change_is_ambiguous(self):
        # A new "x = 1" inserted before the old one takes over its id.
        base = self._baseline({"a.py": {"kind": "source", "status": "syntax_error",
                                        "units": {"Assign: x = 1": "compiles"}}})
        changes = cc.compare([self._result("a.py", "syntax_error",
                                           {"Assign: x = 1": "compiles",
                                            "Assign: x = 1#2": "syntax_error"})], base)
        self.assertIn(("a.py", "Assign: x = 1", "ambiguous"), self._changes(changes))
        self.assertEqual(cc.build_report([], changes, None)["result"], "fail")

    def test_new_and_unscorable_do_not_fail(self):
        base = self._baseline({"a.py": {"kind": "source", "status": cc.SOURCE_ERROR}})
        changes = cc.compare([self._result("a.py", "compiles", {}),
                              self._result("c.pyc", "compiles")], base)
        self.assertEqual(self._changes(changes), {
            ("a.py", None, "unscorable"),
            ("c.pyc", None, "new"),
        })
        self.assertEqual(cc.build_report([], changes, None)["result"], "pass")

    def test_missing_unit_fails(self):
        base = self._baseline({"a.py": {"kind": "source", "status": "syntax_error",
                                        "units": {"If: if x:": "compiles"}}})
        changes = cc.compare([self._result("a.py", "syntax_error",
                                           {"If: if y:": "syntax_error"})], base)
        self.assertEqual(self._changes(changes), {
            ("a.py", "If: if x:", "missing"),
            ("a.py", "If: if y:", "new"),
        })
        self.assertEqual(cc.build_report([], changes, None)["result"], "fail")


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_check_entry_source(self):
        src = self.dir / "mod.py"
        src.write_text("import os\n\ndef f(a, b):\n    return a + b\n")
        with tempfile.TemporaryDirectory() as work:
            r = cc.check_entry(src, work)
        self.assertEqual(r.status, "compiles")
        self.assertEqual(set(r.units), {"Import: import os", "def f"})
        self.assertTrue(all(u["status"] == "compiles" for u in r.units.values()))
        self.assertTrue(r.units["def f"]["ast_equal"])

    def test_check_entry_honours_coding_cookie(self):
        src = self.dir / "latin.py"
        src.write_bytes(b"# -*- coding: latin-1 -*-\nname = '\xe9t\xe9'\n")
        with tempfile.TemporaryDirectory() as work:
            r = cc.check_entry(src, work)
        self.assertEqual(r.status, "compiles")
        self.assertEqual(set(r.units), {"Assign: name = '\u00e9t\u00e9'"})

    def test_check_entry_unknown_encoding_is_source_error(self):
        src = self.dir / "bogus.py"
        src.write_bytes(b"# -*- coding: no-such-codec -*-\nx = 1\n")
        with tempfile.TemporaryDirectory() as work:
            r = cc.check_entry(src, work)
        self.assertEqual(r.status, cc.SOURCE_ERROR)
        self.assertEqual(r.error["stage"], "source")

    def test_check_entry_source_error_is_not_scored(self):
        src = self.dir / "bad.py"
        src.write_text("def (:\n")
        with tempfile.TemporaryDirectory() as work:
            r = cc.check_entry(src, work)
        self.assertEqual(r.status, cc.SOURCE_ERROR)
        self.assertEqual(r.units, {})

    def _run_main(self, *args):
        from io import StringIO
        from contextlib import redirect_stdout, redirect_stderr
        out = StringIO()
        with redirect_stdout(out), redirect_stderr(StringIO()):
            code = cc.main([str(a) for a in args])
        return code, out.getvalue()

    def test_main_gates_on_baseline(self):
        bad = self.dir / "bad.pyc"
        bad.write_bytes(b"\x00" * 4)          # too short: decompile_error
        baseline = self.dir / "baseline.json"

        code, _ = self._run_main(bad, "--baseline", baseline, "--update-baseline")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(baseline.read_text())["entries"][cc._rel(bad)]["status"],
                         "decompile_error")

        data = json.loads(baseline.read_text())
        data["entries"][cc._rel(bad)]["status"] = "compiles"
        baseline.write_text(json.dumps(data))
        code, out = self._run_main(bad, "--baseline", baseline, "--json")
        self.assertEqual(code, 1)
        report = json.loads(out)
        self.assertEqual(report["result"], "fail")
        self.assertEqual(report["summary"]["changes"], {"regressed": 1})
        self.assertEqual(report["entries"][0]["behavior"], "not_evaluated")

    def test_main_missing_baseline_is_config_error(self):
        src = self.dir / "mod.py"
        src.write_text("x = 1\n")
        code, _ = self._run_main(src, "--baseline", self.dir / "nope.json")
        self.assertEqual(code, 2)
        code, _ = self._run_main(src, "--no-baseline")
        self.assertEqual(code, 0)

    def test_main_rejects_baseline_for_other_python_version(self):
        src = self.dir / "mod.py"
        src.write_text("x = 1\n")
        baseline = self.dir / "baseline.json"
        self._run_main(src, "--baseline", baseline, "--update-baseline")
        data = json.loads(baseline.read_text())
        data["python_version"] = "2.7"
        baseline.write_text(json.dumps(data))
        code, _ = self._run_main(src, "--baseline", baseline)
        self.assertEqual(code, 2)

    def test_default_corpus_matches_committed_baseline(self):
        if not cc.default_baseline_path().exists():
            self.skipTest(f"no committed baseline for Python {cc.python_tag()}")
        code, out = self._run_main("--json")
        report = json.loads(out)
        failures = [c for c in report["changes"] if c["change"] in cc.FAILING_CHANGES]
        self.assertEqual(failures, [])
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
