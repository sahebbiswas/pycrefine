"""Diagnostics: places the decompiler could not reconstruct are recorded, or raised in strict mode."""
import io
import os
import sys
import unittest
from unittest.mock import patch

import pycrefine
from pycrefine import (DecompilationError, Decompiler39, Diagnostic, DecompilerGeneric, get_decompiler,
                       main)
from pycrefine import diagnostics as dg

from .test_helpers import _compile

MATCH_SRC = "def f(x):\n    match x:\n        case [a, b]:\n            return a\n    return 'other'\n"
CLEAN_SRC = "def f(x):\n    return x + 1\n"


def _decompiler(src, strict=False):
    pyc = _compile(src)
    try:
        return get_decompiler(pyc, strict=strict)
    finally:
        os.unlink(pyc)


class TestDiagnosticValue(unittest.TestCase):
    def test_str_names_kind_place_and_version(self):
        d = Diagnostic(dg.UNSUPPORTED_OPCODE, "no handler", (3, 12), opcode="FOO", offset=8, code_name="f")
        self.assertEqual(str(d), "unsupported_opcode: no handler [in f at FOO (offset 8); Python 3.12]")

    def test_as_dict_is_json_friendly(self):
        d = Diagnostic(dg.STACK_UNDERFLOW, "empty", (3, 9))
        self.assertEqual(d.as_dict()["python_version"], "3.9")
        self.assertIsNone(d.as_dict()["opcode"])

    def test_comment_block(self):
        self.assertEqual(dg.comment_block([]), "")
        block = dg.comment_block([Diagnostic(dg.STACK_UNDERFLOW, "empty", (3, 9))])
        self.assertTrue(block.startswith("# pycrefine: 1 place could not"))
        self.assertTrue(all(line.startswith("#") for line in block.splitlines()))

    def test_comment_block_keeps_every_line_a_comment(self):
        """A line break inside a reason or code name must not leave source outside a comment."""
        for brk in ("\n", "\r\n", "\r", "\x0c", "\x85", "\u2028"):
            with self.subTest(brk=repr(brk)):
                d = Diagnostic(dg.UNSUPPORTED_OPCODE, f"first{brk}import os; os.system('x')", (3, 9),
                               code_name=f"f{brk}g")
                block = dg.comment_block([d])
                self.assertTrue(all(line.startswith("#") for line in block.splitlines()), block)
                compile(block, "<block>", "exec")  # nothing but comments: no statements
                import ast
                self.assertEqual(ast.parse(block).body, [])
        single = dg.comment_block([Diagnostic(dg.STACK_UNDERFLOW, "empty", (3, 9))])
        self.assertEqual(single.splitlines()[1], "#   - " + str(Diagnostic(dg.STACK_UNDERFLOW, "empty", (3, 9))))

    def test_kinds_are_unique_and_listed(self):
        self.assertEqual(len(set(dg.KINDS)), len(dg.KINDS))


class TestRecording(unittest.TestCase):
    def _dec(self, strict=False):
        return Decompiler39(compile("pass", "<t>", "exec"), strict=strict)

    def test_clean_code_has_no_diagnostics(self):
        dec = _decompiler(CLEAN_SRC)
        dec.decompile()
        self.assertEqual(dec.diagnostics, [])

    def test_stack_underflow_is_recorded_and_falls_back(self):
        dec = self._dec()
        self.assertEqual(dec._pop("FALLBACK", "needed an operand"), "FALLBACK")
        self.assertEqual([d.kind for d in dec.diagnostics], [dg.STACK_UNDERFLOW])

    def test_pop_with_operand_records_nothing(self):
        dec = self._dec()
        dec.stack.append("x")
        self.assertEqual(dec._pop("FALLBACK", "needed an operand"), "x")
        self.assertEqual(dec.diagnostics, [])

    def test_identical_reports_are_deduplicated(self):
        dec = self._dec()
        for _ in range(3):
            dec._pop("?", "needed an operand")
        self.assertEqual(len(dec.diagnostics), 1)

    def test_strict_raises_at_first_report(self):
        dec = self._dec(strict=True)
        with self.assertRaises(DecompilationError) as cm:
            dec._pop("?", "needed an operand")
        self.assertEqual(cm.exception.diagnostic.kind, dg.STACK_UNDERFLOW)
        self.assertIsInstance(cm.exception, ValueError)

    def test_nested_decompilers_share_the_list(self):
        dec = self._dec(strict=True)
        sub = self._dec()
        dec._share_diagnostics_with(sub)
        self.assertIs(sub.diagnostics, dec.diagnostics)
        self.assertTrue(sub.strict)


@unittest.skipUnless(sys.version_info >= (3, 10), "match statements need Python 3.10+")
class TestUnsupportedConstruct(unittest.TestCase):
    def test_match_is_reported_not_silent(self):
        dec = _decompiler(MATCH_SRC)
        dec.decompile()
        self.assertTrue(dec.diagnostics)
        self.assertTrue(all(d.kind in dg.KINDS for d in dec.diagnostics))

    def test_strict_raises_for_match(self):
        dec = _decompiler(MATCH_SRC, strict=True)
        with self.assertRaises(DecompilationError):
            dec.decompile()


ASYNC_SRC = "async def f():\n    return 1\n"
ASYNC_GEN_SRC = "async def f():\n    yield 1\n"
ASYNC_METHOD_SRC = "class A:\n    async def f(self):\n        return 1\n"
CONVERSION_IN_COMPREHENSION_SRC = "def f(y):\n    return [f'{x!r}' for x in y]\n"
CONVERSION_IN_COMPREHENSION_FILTER_SRC = "def f(y):\n    return [x for x in y if f'{x!r}']\n"
COMBINED_CONDITION_SRC = "def f(a, b):\n    if a > 1 and b < 2:\n        return 1\n    return 2\n"


class TestAsyncDef(unittest.TestCase):
    """An `async def` is written as a plain def, so it must be reported even with no await in it."""

    def test_async_def_without_await_is_reported(self):
        for name, src in (("coroutine", ASYNC_SRC), ("async generator", ASYNC_GEN_SRC),
                          ("method", ASYNC_METHOD_SRC)):
            with self.subTest(name):
                dec = _decompiler(src)
                dec.decompile()
                # Other constructs in the function may be reported as well (3.11 also flags
                # the async generator's wrapper opcode), so look for this report in particular.
                reasons = [d.reason for d in dec.diagnostics if d.kind == dg.UNSUPPORTED_VERSION_CONSTRUCT]
                self.assertTrue(any("async def" in reason for reason in reasons), reasons)

    def test_strict_raises_for_async_def_without_await(self):
        for name, src in (("coroutine", ASYNC_SRC), ("async generator", ASYNC_GEN_SRC),
                          ("method", ASYNC_METHOD_SRC)):
            with self.subTest(name):
                with self.assertRaises(DecompilationError) as cm:
                    _decompiler(src, strict=True).decompile()
                self.assertEqual(cm.exception.diagnostic.kind, dg.UNSUPPORTED_VERSION_CONSTRUCT)

    def test_plain_and_generator_functions_are_not_reported(self):
        for src in (CLEAN_SRC, "def g():\n    yield 1\n"):
            dec = _decompiler(src)
            dec.decompile()
            self.assertEqual(dec.diagnostics, [])


@unittest.skipUnless(sys.version_info >= (3, 13), "f-string conversions are dropped on 3.13 and later")
class TestSubDecompilersReport(unittest.TestCase):
    """Decompilers of part of a code object, whose output is kept, report to the parent."""

    def test_comprehension_slice_diagnostics_reach_the_parent(self):
        for src in (CONVERSION_IN_COMPREHENSION_SRC, CONVERSION_IN_COMPREHENSION_FILTER_SRC):
            with self.subTest(src=src):
                dec = _decompiler(src)
                dec.decompile()
                self.assertIn(dg.UNSUPPORTED_VERSION_CONSTRUCT, [d.kind for d in dec.diagnostics])

    def test_comprehension_slice_failure_stops_a_strict_run(self):
        for src in (CONVERSION_IN_COMPREHENSION_SRC, CONVERSION_IN_COMPREHENSION_FILTER_SRC):
            with self.subTest(src=src):
                with self.assertRaises(DecompilationError):
                    _decompiler(src, strict=True).decompile()

    def test_a_rejected_slice_leaves_no_diagnostics_behind(self):
        dec = _decompiler(CLEAN_SRC)
        dec.decompile()
        before = list(dec.diagnostics)
        self.assertIsNone(dec._eval_comp_slice([dec.instructions[0], dec.instructions[-1]]))
        self.assertEqual(dec.diagnostics, before)


class TestCombinedConditions(unittest.TestCase):
    def test_the_condition_decompiler_reports_to_the_parent(self):
        calls = []
        original = DecompilerGeneric._share_diagnostics_with

        def spy(parent, sub):
            calls.append((parent, sub))
            return original(parent, sub)

        with patch.object(DecompilerGeneric, "_share_diagnostics_with", spy):
            dec = _decompiler(COMBINED_CONDITION_SRC)
            dec.decompile()
        # A nested function has a code object of its own; the condition decompiler reads the parent's.
        same_code = [(parent, sub) for parent, sub in calls if sub.code_obj is parent.code_obj]
        self.assertTrue(same_code)
        self.assertTrue(all(sub.diagnostics is parent.diagnostics for parent, sub in same_code))


class TestCli(unittest.TestCase):
    def _run(self, argv, src):
        pyc = _compile(src)
        out, err = io.StringIO(), io.StringIO()
        code = 0
        try:
            with patch("sys.argv", ["pycrefine", pyc] + argv), patch("sys.stdout", out), patch("sys.stderr", err):
                try:
                    main()
                except SystemExit as e:
                    code = e.code or 0
        finally:
            os.unlink(pyc)
        return code, out.getvalue(), err.getvalue()

    def test_clean_output_has_no_header(self):
        code, out, err = self._run([], CLEAN_SRC)
        self.assertEqual(code, 0)
        self.assertNotIn("# pycrefine:", out)
        self.assertNotIn("Warning", err)

    def test_strict_flag_reaches_the_decompiler(self):
        with patch("pycrefine.cli.get_decompiler") as gd:
            gd.return_value.decompile.return_value = "x = 1\n"
            gd.return_value.diagnostics = []
            self._run(["--strict"], CLEAN_SRC)
        self.assertTrue(gd.call_args.kwargs["strict"])

    def test_diagnostics_become_warnings_and_header(self):
        diag = Diagnostic(dg.UNSUPPORTED_OPCODE, "no handler", (3, 12), opcode="FOO")
        with patch("pycrefine.cli.get_decompiler") as gd:
            gd.return_value.decompile.return_value = "x = 1\n"
            gd.return_value.diagnostics = [diag]
            code, out, err = self._run([], CLEAN_SRC)
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("# pycrefine: 1 place"))
        self.assertIn("x = 1", out)
        self.assertIn("Warning: unsupported_opcode", err)

    def test_strict_failure_exits_3(self):
        diag = Diagnostic(dg.UNSUPPORTED_OPCODE, "no handler", (3, 12), opcode="FOO")
        with patch("pycrefine.cli.get_decompiler") as gd:
            gd.return_value.decompile.side_effect = DecompilationError(diag)
            code, out, err = self._run(["--strict"], CLEAN_SRC)
        self.assertEqual(code, 3)
        self.assertEqual(out, "")
        self.assertIn("strict mode", err)

    def test_public_names_are_exported(self):
        for name in ("Diagnostic", "DecompilationError"):
            self.assertTrue(hasattr(pycrefine, name))


if __name__ == "__main__":
    unittest.main()
