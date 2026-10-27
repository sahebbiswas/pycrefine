"""No silent catch-alls: failures become diagnostics, specific errors, or a distinct exit status."""
import ast
import io
import os
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pycrefine
from pycrefine import (DecompilationError, Decompiler311Plus, Decompiler39, DecompilerGeneric, get_decompiler,
                       main)
from pycrefine import diagnostics as dg
from pycrefine.cli import EXIT_INTERNAL_ERROR

from .test_helpers import _compile

SRC_DIR = Path(pycrefine.__file__).parent


def _broad_handlers():
    """(path, line, kind, has_reason) for every bare `except:` or `except Exception` in the package."""
    found = []
    for path in sorted(SRC_DIR.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        lines = text.splitlines()
        for node in ast.walk(ast.parse(text)):
            if not isinstance(node, ast.ExceptHandler):
                continue
            names = []
            if node.type is None:
                names = ["bare"]
            else:
                elts = node.type.elts if isinstance(node.type, ast.Tuple) else [node.type]
                names = [e.id for e in elts if isinstance(e, ast.Name) and e.id in ("Exception", "BaseException")]
            if names:
                found.append((path.relative_to(SRC_DIR), node.lineno, names[0], "# broad:" in lines[node.lineno - 1]))
    return found


class TestNoSilentCatchAll(unittest.TestCase):
    def test_no_bare_except(self):
        self.assertEqual([h for h in _broad_handlers() if h[2] == "bare"], [])

    def test_every_broad_except_says_why(self):
        missing = [h for h in _broad_handlers() if not h[3]]
        self.assertEqual(missing, [], "add a `# broad: <reason>` comment or narrow the exception")

    def test_the_few_broad_handlers_are_the_expected_ones(self):
        places = sorted({str(h[0]) for h in _broad_handlers()})
        self.assertEqual(places, ["cli.py", "loader.py"])


class TestExceptionTable(unittest.TestCase):
    @unittest.skipUnless(sys.version_info >= (3, 11), "exception tables exist from 3.11")
    def test_decode_failure_is_reported_not_swallowed(self):
        dec = Decompiler311Plus(compile("try:\n    pass\nexcept ValueError:\n    pass\n", "<t>", "exec"))
        with patch("dis.Bytecode", side_effect=ValueError("bad table")):
            self.assertIsNone(dec._exception_entries())
        self.assertEqual([d.kind for d in dec.diagnostics], [dg.INTERNAL_ERROR])
        self.assertIn("bad table", dec.diagnostics[0].reason)

    @unittest.skipUnless(sys.version_info >= (3, 11), "exception tables exist from 3.11")
    def test_decode_failure_raises_in_strict_mode(self):
        dec = Decompiler311Plus(compile("pass", "<t>", "exec"), strict=True)
        with patch("dis.Bytecode", side_effect=ValueError("bad table")):
            with self.assertRaises(DecompilationError):
                dec._exception_entries()

    SOURCES = {
        "try/except": "def f():\n    try:\n        g()\n    except ValueError:\n        h()\n",
        "with": "def f():\n    with open('x') as fh:\n        fh.read()\n",
    }

    @unittest.skipUnless(sys.version_info >= (3, 11), "exception tables exist from 3.11")
    def test_every_pass_reads_the_table_through_the_shared_path(self):
        # A table that cannot be decoded must come out as one diagnostic however many passes
        # read it, not as a raw error from whichever pass reads it first.
        for name, src in self.SOURCES.items():
            with self.subTest(name):
                pyc = _compile(src)
                try:
                    dec = get_decompiler(pyc)
                    with patch("dis.Bytecode", side_effect=ValueError("bad table")):
                        dec.decompile()
                finally:
                    os.unlink(pyc)
                # One report per code object (the module and `f`), however many passes read it.
                self.assertEqual({d.kind for d in dec.diagnostics}, {dg.INTERNAL_ERROR})
                names = [d.code_name for d in dec.diagnostics]
                self.assertEqual(sorted(names), sorted(set(names)), names)
                self.assertEqual(len(names), 2)
                self.assertTrue(all("bad table" in d.reason for d in dec.diagnostics))

    @unittest.skipUnless(sys.version_info >= (3, 11), "exception tables exist from 3.11")
    def test_an_unreadable_table_stops_a_strict_decompile_with_a_decompilation_error(self):
        for name, src in self.SOURCES.items():
            with self.subTest(name):
                pyc = _compile(src)
                try:
                    dec = get_decompiler(pyc, strict=True)
                    with patch("dis.Bytecode", side_effect=ValueError("bad table")):
                        with self.assertRaises(DecompilationError):
                            dec.decompile()
                finally:
                    os.unlink(pyc)

    @unittest.skipUnless(sys.version_info >= (3, 14), "the COPY 1 / LOAD_SPECIAL `with` preamble is 3.14 bytecode")
    def test_a_strict_failure_in_the_314_with_resync_is_not_swallowed(self):
        # DecompilationError is a ValueError, and the with re-sync in py314.py catches ValueError
        # around its own bookkeeping. The table read must sit outside that handler.
        real = Decompiler311Plus._exception_entries
        raised = []

        def fail_in_py314(dec):
            if os.path.basename(sys._getframe(1).f_code.co_filename) == "py314.py":
                diagnostic = dg.Diagnostic(dg.INTERNAL_ERROR, "bad table", tuple(dec.target_version))
                raised.append(diagnostic)
                raise DecompilationError(diagnostic)
            return real(dec)

        pyc = _compile(self.SOURCES["with"])
        try:
            dec = get_decompiler(pyc, strict=True)
            with patch.object(Decompiler311Plus, "_exception_entries", fail_in_py314):
                with self.assertRaises(DecompilationError):
                    dec.decompile()
        finally:
            os.unlink(pyc)
        self.assertTrue(raised, "the with re-sync never read the table")

    def test_old_versions_have_no_table_and_no_diagnostic(self):
        dec = Decompiler39(compile("pass", "<t>", "exec"), target_version=(3, 9))
        with patch("dis.Bytecode", side_effect=AssertionError("must not be consulted")):
            self.assertIsNone(dec._exception_entries())
        self.assertEqual(dec.diagnostics, [])


class TestSliceEvaluation(unittest.TestCase):
    @staticmethod
    def _loads():
        return [i for i in dg_instructions(compile("y", "<t>", "exec")) if i.opname.startswith("LOAD_")][:1]

    def test_sub_decompiler_failure_is_reported(self):
        dec = DecompilerGeneric(compile("pass", "<t>", "exec"))
        loads = self._loads()
        with patch.object(DecompilerGeneric, "_handle_instruction", side_effect=IndexError("boom")):
            result = dec._eval_comp_slice(loads)
        self.assertIsNone(result)
        self.assertEqual([d.kind for d in dec.diagnostics], [dg.INTERNAL_ERROR])
        self.assertIn("boom", dec.diagnostics[0].reason)

    def test_strict_mode_raises(self):
        dec = DecompilerGeneric(compile("pass", "<t>", "exec"), strict=True)
        loads = self._loads()
        with patch.object(DecompilerGeneric, "_handle_instruction", side_effect=IndexError("boom")):
            with self.assertRaises(DecompilationError):
                dec._eval_comp_slice(loads)


def dg_instructions(code):
    dec = DecompilerGeneric(code)
    dec.decompile()
    return dec.instructions


class TestLiteralFallbacks(unittest.TestCase):
    def _dec(self, strict=False):
        return Decompiler39(compile("pass", "<t>", "exec"), target_version=(3, 9), strict=strict)

    def test_non_literal_key_tuple_is_reported(self):
        dec = self._dec()
        dec.stack.extend(["1", "2", "(a, b"])
        instr = pycrefine.BytecodeInstruction(156, "BUILD_CONST_KEY_MAP", 2, 2, 0, None, False)
        dec._op_build_const_key_map(instr)
        self.assertEqual([d.kind for d in dec.diagnostics], [dg.AMBIGUOUS_EXPRESSION])

    def test_literal_key_tuple_is_silent(self):
        dec = self._dec()
        dec.stack.extend(["1", "2", "('a', 'b')"])
        instr = pycrefine.BytecodeInstruction(156, "BUILD_CONST_KEY_MAP", 2, 2, 0, None, False)
        dec._op_build_const_key_map(instr)
        self.assertEqual(dec.diagnostics, [])
        self.assertEqual(dec.stack[-1], "{'a': 1, 'b': 2}")

    def test_key_tuple_failure_raises_in_strict_mode(self):
        dec = self._dec(strict=True)
        dec.stack.extend(["1", "2", "(a, b"])
        instr = pycrefine.BytecodeInstruction(156, "BUILD_CONST_KEY_MAP", 2, 2, 0, None, False)
        with self.assertRaises(DecompilationError):
            dec._op_build_const_key_map(instr)


class TestLoaderCauses(unittest.TestCase):
    def test_error_lists_why_each_offset_failed(self):
        magic = importlib_magic()
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            f.write(magic + struct.pack("<III", 0, 0, 0) + b"\xff" * 32)
            path = f.name
        try:
            with self.assertRaises(ValueError) as cm:
                get_decompiler(path)
        finally:
            os.unlink(path)
        message = str(cm.exception)
        self.assertIn("Could not find valid marshal code object", message)
        self.assertIn("marshal at offset 16:", message)
        self.assertIn("parser at offset 4:", message)


def importlib_magic():
    import importlib.util
    return importlib.util.MAGIC_NUMBER


class TestCliTopLevel(unittest.TestCase):
    def _run(self, argv, side_effect):
        out, err = io.StringIO(), io.StringIO()
        code = 0
        with patch("pycrefine.cli.get_decompiler", side_effect=side_effect), \
                patch("sys.argv", ["pycrefine", "x.pyc"] + argv), patch("sys.stdout", out), patch("sys.stderr", err):
            try:
                main()
            except SystemExit as e:
                code = e.code
        return code, err.getvalue()

    def test_internal_failure_has_its_own_exit_status_and_no_traceback(self):
        code, err = self._run([], RuntimeError("kaboom"))
        self.assertEqual(code, EXIT_INTERNAL_ERROR)
        self.assertIn("Internal error: RuntimeError: kaboom", err)
        self.assertIn("--debug", err)
        self.assertIn("github.com/sahebbiswas/pycrefine/issues", err)
        self.assertNotIn("Traceback", err)

    def test_debug_prints_the_traceback(self):
        code, err = self._run(["--debug"], RuntimeError("kaboom"))
        self.assertEqual(code, EXIT_INTERNAL_ERROR)
        self.assertIn("Traceback", err)

    def test_unreadable_input_is_an_ordinary_error(self):
        code, err = self._run([], FileNotFoundError(2, "No such file or directory"))
        self.assertEqual(code, 1)
        self.assertTrue(err.startswith("Error:"))


if __name__ == "__main__":
    unittest.main()
