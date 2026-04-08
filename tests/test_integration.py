import os
import tempfile
import unittest

from .test_helpers import _run39_full_impl, assert_contains, decompile


class TestEdgeCases(unittest.TestCase):
    def test_empty_module(self):
        """An effectively empty module (just a pass) must not crash."""
        out = decompile("pass\n")
        self.assertIsInstance(out, str)

    def test_delete_name(self):
        out = decompile("x = 1\ndel x\n")
        assert_contains(out, "del x")

    def test_nested_while_no_extra_body_block(self):
        """
        Regression: nested while loops must not emit the inner loop body
        twice due to dup-condition drain miscounting.
        """
        src = (
            "i = 0\nwhile i < 3:\n    j = 0\n"
            "    while j < 2:\n        j += 1\n    i += 1\n"
        )
        out = decompile(src)
        self.assertEqual(
            out.count("j += 1"), 1,
            f"'j += 1' appears {out.count('j += 1')} times:\n{out}",
        )

    def test_real_module_pattern(self):
        src = (
            "import os\nimport sys\n\n"
            "DEBUG = False\n\n"
            "def process(items, verbose=False):\n"
            "    result = []\n"
            "    for item in items:\n"
            "        if item > 0:\n"
            "            result.append(item)\n"
            "    return result\n\n"
            "class Worker:\n"
            "    def __init__(self, name):\n"
            "        self.name = name\n"
            "        self.count = 0\n"
        )
        out = decompile(src)
        assert_contains(
            out,
            "import os", "import sys", "DEBUG = False",
            "def process(", "for item in items:", "result.append",
            "class Worker:", "def __init__(", "self.name = name",
        )

    def test_class_inheritance_round_trip(self):
        out = decompile("class MyError(Exception):\n    pass\n")
        assert_contains(out, "MyError")

    def test_function_with_loop_and_try(self):
        src = (
            "def safe_parse(items):\n"
            "    results = []\n"
            "    for item in items:\n"
            "        try:\n"
            "            results.append(int(item))\n"
            "        except ValueError:\n"
            "            pass\n"
            "    return results\n"
        )
        out = decompile(src)
        assert_contains(
            out, "def safe_parse(", "for item in items:", "except ValueError:"
        )

    def test_deeply_nested_functions(self):
        src = (
            "def outer(x):\n"
            "    def middle(y):\n"
            "        def inner(z):\n"
            "            return z * 2\n"
            "        return inner(y)\n"
            "    return middle(x)\n"
        )
        out = decompile(src)
        assert_contains(out, "def outer(", "def middle(", "def inner(")


class TestErrorHandling(unittest.TestCase):
    def test_nonexistent_file_raises(self):
        with self.assertRaises((FileNotFoundError, OSError)):
            from pycrefine import get_decompiler
            get_decompiler("/nonexistent/path/file.pyc")

    def test_too_short_file_raises(self):
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            f.write(b"\x00" * 4)
            path = f.name
        try:
            with self.assertRaisesRegex(ValueError, "too short"):
                from pycrefine import get_decompiler
                get_decompiler(path)
        finally:
            os.unlink(path)

    def test_invalid_magic_raises(self):
        """A file with an unrecognised magic number raises ValueError with a descriptive message."""
        import struct
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            f.write(struct.pack("<I", 0xDEADBEEF))
            f.write(b"\x00" * 12)
            path = f.name
        try:
            with self.assertRaisesRegex(ValueError, "Invalid or unsupported Python magic number"):
                from pycrefine import get_decompiler
                get_decompiler(path)
        finally:
            os.unlink(path)

    def test_invalid_magic_inferred_version_in_error(self):
        """ValueError for a version-valid but host-incompatible magic number includes the version name."""
        import importlib.util
        import struct
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            # 3310 is Python 3.4. This is unsupported.
            f.write(struct.pack("<I", 3310))
            f.write(b"\x00" * 12)
            path = f.name

        host_magic = int.from_bytes(importlib.util.MAGIC_NUMBER, "little")
        if (host_magic & 0xFFFF) != 3310:
            try:
                with self.assertRaisesRegex(ValueError, "Input file appears to be from Python 3.4"):
                    from pycrefine import get_decompiler
                    get_decompiler(path)
            finally:
                os.unlink(path)
        else:
            os.unlink(path)

    def test_invalid_marshal_data_includes_version_in_error(self):
        """ValueError for corrupted marshal data includes the inferred version name."""
        import struct
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            f.write(struct.pack("<I", 3495))
            f.write(b"\x00" * 12)
            f.write(b"GARBAGE")
            path = f.name
        try:
            with self.assertRaisesRegex(ValueError, "Inferred version: Python 3.12"):
                from pycrefine import get_decompiler
                get_decompiler(path)
        finally:
            os.unlink(path)

    def test_decompile_returns_string(self):
        out = decompile("x = 1\n")
        self.assertIsInstance(out, str)

    def test_decompile_does_not_crash_on_complex_source(self):
        """Decompiling a moderately complex file must not raise."""
        src = (
            "import os\nfrom sys import argv\n\n"
            "class Base:\n    def method(self):\n        return 1\n\n"
            "class Child(Base):\n"
            "    def method(self):\n"
            "        i = 0\n"
            "        while i < 10:\n"
            "            i += 1\n"
            "        return i\n\n"
            "def process(items):\n"
            "    try:\n"
            "        for item in items:\n"
            "            if item:\n"
            "                print(item)\n"
            "    except Exception:\n"
            "        pass\n"
        )
        out = decompile(src)
        self.assertIn("class Base", out)

    def test_empty_decompiler_output_emits_warning(self):
        """If decompile() returns an empty string, main() prints a warning to stderr."""
        import io
        from unittest.mock import MagicMock, patch

        from pycrefine import main

        mock_dec = MagicMock()
        mock_dec.decompile.return_value = ""

        with patch("pycrefine.get_decompiler", return_value=mock_dec), \
                patch("sys.argv", ["pycrefine", "dummy.pyc"]), \
                patch("sys.stderr", new_callable=io.StringIO) as mock_stderr, \
                patch("sys.exit") as mock_exit:

            main()

            self.assertIn("Warning: Decompiler returned no source code", mock_stderr.getvalue())
            mock_exit.assert_called_with(1)


class TestVerifyScenesBugs(unittest.TestCase):
    def test_interrupted_if_combination(self):
        src = (
            "def f(in_a):\n"
            "    if in_a != '0':\n"
            "        str_b = random(in_a)\n"
            "        if str_b:\n"
            "            print('good')\n"
            "    return True\n"
        )
        out = decompile(src)
        self.assertIn("str_b = random(in_a)", out)
        self.assertIn("if str_b:", out)
        self.assertNotIn("and str_b", out)

    def test_compound_group_shrink_and_else_emission(self):
        src = (
            "def f(in_a, in_b):\n"
            "    if in_a or in_b:\n"
            "        if in_a and in_b:\n"
            "            delta = min([in_a, in_b])\n"
            "        else:\n"
            "            delta = in_a if in_a else in_b\n"
            "        if int(delta) < 100:\n"
            "            print('bad')\n"
            "    return\n"
        )
        out = decompile(src)
        self.assertIn("if in_a or in_b:", out)
        self.assertIn("if in_a and in_b:", out)
        self.assertIn("else:", out)
        self.assertIn("if int(delta) < 100:", out)

    def test_assert_formatting_api_11(self):
        src = (
            "def f(in_a, in_b, in_c):\n"
            "    if in_c in (2, 3):\n"
            "        if not in_b > 0:\n"
            "            raise AssertionError('bad')\n"
            "        if not in_a < in_c:\n"
            "            raise AssertionError('bad')\n"
        )
        out = decompile(src)
        self.assertNotIn("if raise", out)
        self.assertEqual(out.count("raise AssertionError"), 2)

    def test_nested_empty_except_pass_api_12(self):
        src = (
            "def f(to_input):\n"
            "    value = 0\n"
            "    try:\n"
            "        value += int(to_input)\n"
            "    except Exception:\n"
            "        try:\n"
            "            value += int(to_input[1:])\n"
            "        except Exception:\n"
            "            pass\n"
            "        try:\n"
            "            value += int(to_input[2:])\n"
            "        except Exception:\n"
            "            pass\n"
            "    return value\n"
        )
        out = decompile(src)
        self.assertEqual(out.count("try:"), 3)
        self.assertEqual(out.count("except Exception:"), 3)
        # Ensure return is aligned correctly (not inside except)
        self.assertRegex(out, r"\n    return value\s*$", out)

    def test_build_slice_support(self):
        src = "def f(lst):\n    return lst[1:-1]\n"
        out = decompile(src)
        self.assertIn("lst[1:-1]", out)

    def test_api_20_ternary_with_slice(self):
        from pycrefine import BytecodeInstruction as I
        instructions = [
            I(0, "LOAD_GLOBAL",      0, "print",    0,  None, False),
            I(0, "LOAD_CONST",       1, 'value:',   2,  None, False),
            I(0, "LOAD_FAST",        0, "in_a",     4,  None, False),
            I(0, "COMPARE_OP",       7, "not in",   6,  None, False),
            I(0, "POP_JUMP_IF_FALSE", 0, 18,         8,  None, False),
            I(0, "LOAD_FAST",        0, "in_a",    10,  None, False),
            I(0, "JUMP_FORWARD",     0, 28,        12,  None, False),
            I(0, "LOAD_FAST",        0, "in_a",    18,  None, True),
            I(0, "LOAD_CONST",       2, 6,         20,  None, False),
            I(0, "LOAD_CONST",       3, None,      22,  None, False),
            I(0, "BUILD_SLICE",      2, 2,         24,  None, False),
            I(0, "BINARY_SUBSCR",    0, None,      26,  None, False),
            I(0, "CALL_FUNCTION",    1, 1,         28,  None, True),
            I(0, "POP_TOP",          0, None,      30,  None, False),
            I(0, "LOAD_CONST",       0, None,      32,  None, False),
            I(0, "RETURN_VALUE",     0, None,      34,  None, False),
        ]
        out = _run39_full_impl(instructions)
        expected_pattern = r'print\(\s*in_a\s+if\s+[\'"]value:[\'"]\s+not\s+in\s+in_a\s+else\s+in_a\[6:\]\s*\)'
        self.assertRegex(out, expected_pattern)

    def test_api_21_ternary_in_modulo(self):
        src = (
            "def f(in_a):\n"
            "    if type(in_a) == str:\n"
            "        tstr = 'post: %s' % (in_a if 'value:' not in in_a else in_a[6:])\n"
            "    else:\n"
            "        tstr = 'post: {}'.format(in_a)\n"
            "    return tstr, 'this value'\n"
        )
        out = decompile(src)
        self.assertIn("if 'value:' not in in_a else in_a[6:]", out)
        self.assertIn("'post: %s'", out)

    def test_api_22_ternary_modulo_is_none(self):
        src = (
            "def f(in_a):\n"
            "    print('post %s in input' % ('not found' if in_a is None else 'reset'))\n"
            "    return in_a is not None\n"
        )
        out = decompile(src)
        self.assertIn("'not found' if in_a is None else 'reset'", out)
        self.assertIn("return in_a is not None", out)

    def test_api_23_tuple_unpack_and_ternary(self):
        src = (
            "def f():\n"
            "    in_a, in_b = api_21(None)\n"
            "    print('post %s in input' % ('not found' if in_b is None else 'reset'))\n"
            "    return in_a is not None\n"
        )
        out = decompile(src)
        self.assertIn("in_a, in_b = api_21(None)", out)
        self.assertIn("'not found' if in_b is None else 'reset'", out)

    def test_api_32_print_string_beautification(self):
        import re as _re
        src = (
            "def api_32(in_a):\n"
            "    print(\"This is my Input :\\n %s\" % in_a)\n"
            "    print(\"This is my Input2 : \\'%s\\' and \\\"%s\\\"\" % (in_a, in_a))\n"
            "    return in_a\n"
        )

        # ── core: newline string is collapsed to a single-line literal ──────
        out_core = decompile(src, beautification_level='core')
        # The first print must contain key text and in_a, on one line (triple-quote removed).
        self.assertTrue(
            _re.search(r'print\(.*This is my Input.*in_a.*\)', out_core),
            f"First print not found in core output:\n{out_core}",
        )
        # The line must NOT be split across multiple source lines (no triple-quote).
        matching_lines = [ln for ln in out_core.splitlines()
                          if "This is my Input" in ln and "print" in ln]
        self.assertTrue(matching_lines, "Could not find 'This is my Input' print line in core output")
        # Second print must contain 'This is my Input2' and reference in_a twice.
        self.assertTrue(
            _re.search(r'This is my Input2.*in_a.*in_a', out_core, _re.DOTALL),
            f"Second print not found properly in core output:\n{out_core}",
        )

        # ── none: triple-quote form (or f-string on 3.12+) preserved ────────
        out_none = decompile(src, beautification_level='none')
        self.assertIn("This is my Input", out_none)
        self.assertIn("in_a", out_none)
        self.assertTrue(
            _re.search(r'This is my Input2.*in_a.*in_a', out_none, _re.DOTALL),
            f"Second print not found in none output:\n{out_none}",
        )

    def test_api_32_print_none_preserves_triple_quote(self):
        """Under beautification='none', a newline-containing string stays triple-quoted."""
        import re as _re
        src = (
            "def f(x):\n"
            "    print(\"line1\\nline2\" % x)\n"
        )
        out_none = decompile(src, beautification_level='none')
        # Must contain triple-quoted form (actual newline inside the string literal).
        self.assertTrue(
            _re.search(r'print\(.*""".*\n.*"""', out_none, _re.DOTALL)
            or _re.search(r"print\(.*'''.*\n.*'''", out_none, _re.DOTALL),
            f"Expected triple-quoted string in none output:\n{out_none}",
        )

    def test_api_32_print_core_no_triple_quote(self):
        """Under beautification='core', a newline-containing string must NOT be triple-quoted."""
        import re as _re
        src = (
            "def f(x):\n"
            "    print(\"line1\\nline2\" % x)\n"
        )
        out_core = decompile(src, beautification_level='core')
        # The print statement must be a single line — no triple-quote spanning.
        print_lines = [ln for ln in out_core.splitlines() if "print" in ln]
        self.assertTrue(print_lines, f"No print line found:\n{out_core}")
        self.assertFalse(
            _re.search(r'"""', out_core) or _re.search(r"'''", out_core),
            f"Triple-quoted string leaked into core output:\n{out_core}",
        )

    def test_print_tuple_arg_not_unwrapped(self):
        """print((a, b)) must NOT become print(a, b) — the tuple is a single argument."""
        from pycrefine import post_process_source
        src = "print((a, b))\n"
        out = post_process_source(src, beautification_level='core')
        self.assertNotIn("print(a, b)", out,
                         f"Tuple was incorrectly unwrapped:\n{out}")
        self.assertIn("(a, b)", out)

    def test_print_grouping_parens_unwrapped(self):
        """print((expr)) where inner has no top-level comma should be unwrapped to print(expr)."""
        from pycrefine import post_process_source
        src = 'print(("fmt %s" % x))\n'
        out = post_process_source(src, beautification_level='core')
        self.assertNotIn("print((", out,
                         f"Grouping parens were not removed:\n{out}")

    def test_print_embedded_quotes_safe(self):
        """Triple-quoted string with embedded quotes must produce valid Python."""
        import ast
        from pycrefine import post_process_source
        src = 'print(("""hello "world"\\nfoo""" % x))\n'
        out = post_process_source(src, beautification_level='core')
        try:
            ast.parse(out)
        except SyntaxError as exc:
            self.fail(f"Embedded-quote rewrite produced invalid Python: {exc}\n{out}")


class TestFindingsRefinement(unittest.TestCase):
    def test_blank_separator_nested_logic(self):
        src = "def outer(x):\n    if x > 0:\n        print(x)\ndef another():\n    pass\n"
        out = decompile(src)
        self.assertNotRegex(out, r":\s*\n\s*\n\s*if x > 0", f"Unexpected blank line between def and nested if:\n{out}")
        self.assertRegex(out, r"\n\s*\ndef another", f"Missing blank line before top-level def:\n{out}")

    def test_call_function_ex_normalization_starred(self):
        src = "a = [1, 2]\n(lambda x, y: x+y)(*a)\n"
        out = decompile(src)
        self.assertRegex(out, r"lambda.*?\)\s*\(\*a\)")
        self.assertNotIn("('func'", out)

    def test_keyword_call_reconstruction(self):
        src = "def f(a=0, b=0): return a + b\nf(a=1, b=2)\n"
        out = decompile(src)
        compact = out.replace(" ", "")
        self.assertIn("a=1", compact)
        self.assertIn("b=2", compact)

    def test_unpack_sequence_failure_resilience(self):
        src = "a, b = [1, 2]\n"
        out = decompile(src)
        self.assertIn("a, b =", out)

    def test_unpack_sequence_unbalanced(self):
        src = "(a, b), c = [[1, 2], 3]\n"
        out = decompile(src)
        self.assertIn("(a, b), c =", out)


if __name__ == "__main__":
    unittest.main()
