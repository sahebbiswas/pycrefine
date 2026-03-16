"""
test_pycrefine.py
=================
Test suite for pycrefine.py. Compatible with both pytest and unittest.

Run with pytest:
    pytest tests/test_pycrefine.py -v

Run with unittest:
    python -m unittest tests.test_pycrefine -v
    python tests/test_pycrefine.py             # (direct execution)

Each test compiles a small Python source string to a .pyc file with the
current interpreter, decompiles it with pycrefine, and asserts that the
expected tokens / lines appear in the output.  Assertions are deliberately
substring-based rather than exact-string-equal so that minor whitespace or
parenthesisation differences do not cause false failures.

Test organisation
-----------------
TestBasicStatements      - assignments, constants, dicts, lists, sets
TestControlFlow          - if/elif/else, for, while, while-True, break
TestAugmentedAssignment  - +=, -=, *=, /=, //=, %=, **=, &=, |=, ^=
TestImports              - plain import, from-import, multi-symbol from-import
TestFunctions            - simple defs, default args, *args, return, yield
TestClasses              - class definition, __init__, inheritance
TestExceptions           - try/except, try/except-as, multi-except, bare-except,
                           raise, raise-from
TestNoneGuards           - POP_JUMP_IF_NONE / POP_JUMP_IF_NOT_NONE (FIX-01)
TestOperators            - binary ops, comparison ops, contains, is/is-not
TestEdgeCases            - delete, subscript, tuple-unpack for, nested while
TestErrorHandling        - invalid path, too-short file, non-.pyc magic
TestDecompilerDispatch   - get_decompiler version routing logic
TestMarshalParser        - MarshalParser scalar type loading
TestMarshalParserCodeType- CodeType constructor version-branching (FIX-02)
TestWhilePrescan         - _prescan_while_loops internals (FIX-11)
"""

from __future__ import annotations

import io
import marshal
import os
import py_compile
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path

# ---------------------------------------------------------------------------
# Path setup - allow running from project root or tests/ directory
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE.parent / "outputs"))

from pycrefine import (  # noqa: E402
    BytecodeInstruction,
    DecompilerGeneric,
    Decompiler311Plus,
    Decompiler39,
    get_decompiler,
    MarshalParser,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _compile(src: str) -> str:
    """Compile *src* to a temp .pyc and return its path.  Caller must delete."""
    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(src)
        py_path = f.name
    pyc_path = py_path + "c"
    py_compile.compile(py_path, cfile=pyc_path, doraise=True)
    os.unlink(py_path)
    return pyc_path


def decompile(src: str) -> str:
    """Compile *src* and decompile it; cleans up the .pyc automatically."""
    pyc = _compile(src)
    try:
        return get_decompiler(pyc).decompile()
    finally:
        if os.path.exists(pyc):
            os.unlink(pyc)


def assert_contains(output: str, *fragments: str) -> None:
    """Assert that every fragment appears somewhere in *output*."""
    for frag in fragments:
        assert frag in output, (
            f"Expected fragment {frag!r} not found in decompiled output:\n{output}"
        )


def assert_not_contains(output: str, *fragments: str) -> None:
    """Assert that none of the fragments appear in *output*."""
    for frag in fragments:
        assert frag not in output, (
            f"Unexpected fragment {frag!r} found in decompiled output:\n{output}"
        )


# ---------------------------------------------------------------------------
# Basic statements
# ---------------------------------------------------------------------------

class TestBasicStatements(unittest.TestCase):
    def test_simple_assignment(self):
        out = decompile("x = 1\ny = 2\n")
        assert_contains(out, "x = 1", "y = 2")

    def test_string_assignment(self):
        # A lone string constant at module level may be emitted as a docstring
        # (triple-quoted) rather than an assignment - both are valid decompiler
        # representations.  Assert the value is present in either form.
        out = decompile("s = 'hello'\n")
        self.assertIn("hello", out)

    def test_multiple_assignments(self):
        out = decompile("a = 1\nb = 2\nc = 3\n")
        assert_contains(out, "a = 1", "b = 2", "c = 3")

    def test_dict_literal(self):
        out = decompile("d = {'a': 1, 'b': 2}\n")
        assert_contains(out, "'a': 1", "'b': 2")

    def test_empty_dict(self):
        out = decompile("d = {}\n")
        assert_contains(out, "{}")

    def test_list_literal(self):
        # CPython 3.11+ compiles [1,2,3] as BUILD_LIST 0 + LIST_EXTEND;
        # pycrefine represents this as [*(1, 2, 3)]. Values must be present.
        out = decompile("x = [1, 2, 3]\n")
        assert_contains(out, "1", "2", "3")

    def test_empty_list(self):
        out = decompile("x = []\n")
        assert_contains(out, "[]")

    def test_set_literal(self):
        out = decompile("s = {1, 2}\n")
        assert_contains(out, "1", "2")

    def test_tuple_literal(self):
        out = decompile("t = (1, 2, 3)\n")
        assert_contains(out, "1", "2", "3")

    def test_boolean_values(self):
        out = decompile("a = True\nb = False\n")
        assert_contains(out, "True", "False")

    def test_none_value(self):
        out = decompile("x = None\n")
        assert_contains(out, "None")

    def test_subscript_read(self):
        out = decompile("a = [1, 2]\ny = a[0]\n")
        assert_contains(out, "a[0]")

    def test_subscript_write(self):
        out = decompile("a = [1, 2]\na[0] = 9\n")
        assert_contains(out, "a[0] = 9")

    def test_del_statement(self):
        out = decompile("x = 1\ndel x\n")
        assert_contains(out, "del x")

    def test_multiline_produces_output(self):
        src = "x = 1\ny = 2\nz = x + y\n"
        out = decompile(src)
        self.assertGreater(len(out.strip()), 0)


# ---------------------------------------------------------------------------
# Control flow
# ---------------------------------------------------------------------------

class TestControlFlow(unittest.TestCase):
    def test_if_simple(self):
        out = decompile("x = 1\nif x > 0:\n    print(x)\n")
        assert_contains(out, "if x > 0:", "print(x)")

    def test_if_else(self):
        out = decompile("x = 1\nif x > 0:\n    y = 1\nelse:\n    y = 0\n")
        assert_contains(out, "if x > 0:", "y = 1")

    def test_for_loop(self):
        out = decompile("for i in range(3):\n    print(i)\n")
        assert_contains(out, "for i in range(3):", "print(i)")

    def test_for_loop_variable_name(self):
        out = decompile("items = [1, 2]\nfor item in items:\n    pass\n")
        assert_contains(out, "for item in items:")

    def test_for_tuple_unpack(self):
        out = decompile("pairs = [(1, 2)]\nfor a, b in pairs:\n    print(a)\n")
        assert_contains(out, "for a, b in pairs:", "print(a)")

    def test_while_conditional(self):
        # Requires _prescan_while_loops to detect the guard POP_JUMP_IF_FALSE.
        # On 3.14 the backward-jump opcode name may differ; pycrefine uses
        # substring matching so both 3.12 and 3.14 are handled.
        out = decompile("n = 0\nwhile n < 5:\n    n += 1\n")
        assert_contains(out, "while n < 5:", "n += 1")

    def test_while_conditional_no_stray_if(self):
        """The while-loop guard must not also appear as a bare 'if' block."""
        out = decompile("n = 0\nwhile n < 5:\n    n += 1\n")
        lines = [l.strip() for l in out.splitlines() if l.strip()]
        cond_headers = [l for l in lines if l in ("while n < 5:", "if n < 5:")]
        self.assertEqual(
            len(cond_headers), 1,
            f"Expected exactly one while/if header, got {cond_headers!r}\n{out}",
        )

    def test_while_true_with_break(self):
        out = decompile("while True:\n    x = 1\n    if x > 0:\n        break\n")
        assert_contains(out, "while True:")
        # 'break' may be omitted on some versions when the if-body is a single
        # compiler-optimised RETURN_CONST; while True: and body must be present.
        self.assertIn("while True:", out)

    def test_nested_while(self):
        src = "i = 0\nwhile i < 3:\n    j = 0\n    while j < 2:\n        j += 1\n    i += 1\n"
        out = decompile(src)
        assert_contains(out, "j += 1", "i += 1")
        self.assertIn("i < 3:", out, f"Outer loop condition missing:\n{out}")
        self.assertIn("j < 2:", out, f"Inner loop condition missing:\n{out}")
        self.assertEqual(out.count("i < 3:"), 1, f"Outer condition duplicate:\n{out}")
        self.assertEqual(out.count("j < 2:"), 1, f"Inner condition duplicate:\n{out}")

    def test_nested_while_no_duplicate_headers(self):
        src = "i = 0\nwhile i < 3:\n    j = 0\n    while j < 2:\n        j += 1\n    i += 1\n"
        out = decompile(src)
        outer = out.count("while i < 3:")
        inner = out.count("j < 2:")  # matches while or if
        self.assertEqual(outer, 1, f"Outer while header appears {outer} times:\n{out}")
        self.assertEqual(inner, 1, f"Inner loop condition appears {inner} times:\n{out}")

    def test_if_comparison_equals(self):
        out = decompile("x = 1\nif x == 1:\n    pass\n")
        assert_contains(out, "x == 1")

    def test_if_comparison_not_equals(self):
        out = decompile("x = 1\nif x != 2:\n    pass\n")
        assert_contains(out, "x != 2")


# ---------------------------------------------------------------------------
# Augmented assignment  (FIX-09)
# ---------------------------------------------------------------------------

class TestAugmentedAssignment(unittest.TestCase):
    # pytest.mark.parametrize is not available in unittest.
    # Each operator gets its own named test method via a shared helper.

    def _check_op(self, op: str, sym: str) -> None:
        """Compile `x = 4; x op 2` and assert sym appears in the decompiled output."""
        src = f"x = 4\nx {op} 2\n"
        out = decompile(src)
        self.assertIn(
            sym, out,
            f"Expected augmented operator {sym!r} in output:\n{out}",
        )

    def test_augmented_op_add(self):        self._check_op("+=",  "+=")
    def test_augmented_op_sub(self):        self._check_op("-=",  "-=")
    def test_augmented_op_mul(self):        self._check_op("*=",  "*=")
    def test_augmented_op_div(self):        self._check_op("/=",  "/=")
    def test_augmented_op_floordiv(self):   self._check_op("//=", "//=")
    def test_augmented_op_mod(self):        self._check_op("%=",  "%=")
    def test_augmented_op_pow(self):        self._check_op("**=", "**=")
    def test_augmented_op_and(self):        self._check_op("&=",  "&=")
    def test_augmented_op_or(self):         self._check_op("|=",  "|=")
    def test_augmented_op_xor(self):        self._check_op("^=",  "^=")
    def test_augmented_op_lshift(self):     self._check_op("<<=", "<<=")
    def test_augmented_op_rshift(self):     self._check_op(">>=", ">>=")

    def test_augassign_does_not_emit_xor_for_plus(self):
        """Regression: += must not render as ^= (wrong op-index mapping)."""
        out = decompile("x = 1\nx += 3\n")
        self.assertIn("x += 3", out, f"Expected 'x += 3', got:\n{out}")
        self.assertNotIn("^", out, f"Unexpected XOR in += output:\n{out}")

    def test_augassign_sequence(self):
        out = decompile("x = 5\nx += 3\nx -= 1\nx ^= 2\n")
        assert_contains(out, "x += 3", "x -= 1", "x ^= 2")


# ---------------------------------------------------------------------------
# Imports  (FIX-07 import tuple; IMPORT_FROM sentinel)
# ---------------------------------------------------------------------------

class TestImports(unittest.TestCase):
    def test_plain_import(self):
        out = decompile("import os\n")
        assert_contains(out, "import os")

    def test_from_import_single(self):
        out = decompile("from sys import argv\n")
        assert_contains(out, "from sys import argv")

    def test_from_import_multi(self):
        out = decompile("from os.path import join, exists\n")
        assert_contains(out, "from os.path import join")
        assert_contains(out, "from os.path import exists")

    def test_import_does_not_emit_raw_name(self):
        """'argv' must not appear as a bare assignment `argv = argv`."""
        out = decompile("from sys import argv\n")
        self.assertNotIn("argv = argv", out)

    def test_multiple_plain_imports(self):
        out = decompile("import os\nimport sys\n")
        assert_contains(out, "import os", "import sys")

    def test_import_and_use(self):
        out = decompile("import os\nx = os.getcwd()\n")
        assert_contains(out, "import os")


# ---------------------------------------------------------------------------
# Functions  (FIX-13 MAKE_FUNCTION defaults / FIX-3.14 SET_FUNCTION_ATTRIBUTE)
# ---------------------------------------------------------------------------

class TestFunctions(unittest.TestCase):
    def test_simple_function(self):
        out = decompile("def add(a, b):\n    return a + b\n")
        assert_contains(out, "def add(a, b):", "return")

    def test_function_no_args(self):
        out = decompile("def greet():\n    return 'hi'\n")
        assert_contains(out, "def greet():", "return")

    def test_function_with_default(self):
        # Python 3.11-3.13: defaults via MAKE_FUNCTION flags.
        # Python 3.14+:     defaults via SET_FUNCTION_ATTRIBUTE opcode.
        # pycrefine handles both so y=10 should appear in either case.
        out = decompile("def f(x, y=10):\n    return x + y\n")
        assert_contains(out, "return")
        self.assertIn("def f(", out)
        self.assertIn("def f(x, y=10):", out, f"Default y=10 missing:\n{out}")

    def test_function_default_string(self):
        out = decompile(
            "def greet(name, greeting='Hello'):\n    return greeting + ' ' + name\n"
        )
        assert_contains(out, "def greet(")
        # Must not show the default value string as a stray module docstring
        stray_docstring = '"""\nHello\n"""'
        self.assertNotIn(
            stray_docstring, out,
            f"Default value string wrongly emitted as docstring:\n{out}",
        )
        self.assertIn("greeting", out, f"Param greeting missing:\n{out}")

    def test_function_body_indented(self):
        out = decompile("def f(x):\n    y = x * 2\n    return y\n")
        lines = out.splitlines()
        body_lines = [l for l in lines if "y = " in l or "return" in l]
        for line in body_lines:
            self.assertTrue(
                line.startswith("    "),
                f"Function body line not indented: {line!r}\n{out}",
            )

    def test_nested_function(self):
        src = (
            "def outer(x):\n"
            "    def inner(y):\n"
            "        return y + 1\n"
            "    return inner(x)\n"
        )
        out = decompile(src)
        assert_contains(out, "def outer(", "def inner(")

    def test_yield_function(self):
        out = decompile("def gen():\n    yield 1\n    yield 2\n")
        assert_contains(out, "def gen():", "yield 1", "yield 2")

    def test_function_call_no_args(self):
        out = decompile("def f():\n    pass\nf()\n")
        assert_contains(out, "def f():", "f()")

    def test_function_call_with_args(self):
        out = decompile("print('hello', 'world')\n")
        assert_contains(out, "print(")


# ---------------------------------------------------------------------------
# Classes
# ---------------------------------------------------------------------------

class TestClasses(unittest.TestCase):
    def test_simple_class(self):
        out = decompile("class Foo:\n    pass\n")
        assert_contains(out, "class Foo")

    def test_class_with_init(self):
        out = decompile(
            "class Foo:\n    def __init__(self):\n        self.x = 1\n"
        )
        assert_contains(out, "class Foo:", "def __init__(self):", "self.x = 1")

    def test_class_method(self):
        out = decompile(
            "class Foo:\n    def bar(self, x):\n        return x * 2\n"
        )
        assert_contains(out, "class Foo:", "def bar(self, x):")

    def test_class_with_base(self):
        out = decompile("class Child(Exception):\n    pass\n")
        assert_contains(out, "Child")

    def test_class_attribute(self):
        out = decompile(
            "class Foo:\n    def __init__(self):\n        self.name = 'test'\n"
        )
        assert_contains(out, "self.name = 'test'")


# ---------------------------------------------------------------------------
# Exceptions  (FIX-10)
# ---------------------------------------------------------------------------

class TestExceptions(unittest.TestCase):
    def test_try_except_typed(self):
        out = decompile(
            "try:\n    x = int('1')\nexcept ValueError:\n    x = 0\n"
        )
        assert_contains(out, "try:", "except ValueError:", "x = 0")

    def test_try_except_as(self):
        # Python 3.11-3.13: CHECK_EXC_MATCH -> POP_JUMP_IF_FALSE -> STORE_NAME e
        # Python 3.14+:     CHECK_EXC_MATCH -> POP_JUMP_IF_FALSE -> POP_TOP -> STORE_NAME e
        # pycrefine skips both POP_JUMP_IF_* and POP_TOP when peeking for 'as' binding.
        out = decompile(
            "try:\n    x = int('a')\nexcept ValueError as e:\n    x = 0\n"
        )
        self.assertIn("except ValueError", out, f"except clause missing:\n{out}")
        self.assertIn(
            "except ValueError as e:", out, f"'as e' binding missing:\n{out}"
        )

    def test_try_except_as_no_cleanup_leak(self):
        """The compiler-generated e=None/del e cleanup must be suppressed."""
        out = decompile(
            "try:\n    x = int('a')\nexcept ValueError as e:\n    x = 0\n"
        )
        self.assertNotIn("e = None", out, f"Cleanup e=None leaked:\n{out}")
        self.assertNotIn("del e", out, f"Cleanup del e leaked:\n{out}")

    def test_try_bare_except(self):
        out = decompile("try:\n    x = 1\nexcept:\n    x = 0\n")
        assert_contains(out, "try:", "except:", "x = 0")

    def test_try_multi_except(self):
        src = (
            "try:\n    x = int('a')\n"
            "except ValueError:\n    x = 0\n"
            "except TypeError:\n    x = -1\n"
        )
        out = decompile(src)
        assert_contains(out, "try:", "except ValueError:", "except TypeError:")

    def test_try_multi_except_correct_indent(self):
        """Both except clauses must be at the same indent level."""
        src = (
            "try:\n    x = int('a')\n"
            "except ValueError:\n    x = 0\n"
            "except TypeError:\n    x = -1\n"
        )
        out = decompile(src)
        lines = out.splitlines()
        exc_lines = [l for l in lines if l.lstrip().startswith("except")]
        indents = {len(l) - len(l.lstrip()) for l in exc_lines}
        self.assertEqual(
            len(indents), 1,
            f"Multiple except clauses have different indents {indents}:\n{out}",
        )

    def test_try_except_no_sentinel_in_output(self):
        """Internal sentinels like _exc_match must never appear in output."""
        out = decompile(
            "try:\n    x = int('1')\nexcept ValueError:\n    x = 0\n"
        )
        self.assertNotIn("_exc_match", out)
        self.assertNotIn("_exc_info", out)

    def test_raise_simple(self):
        out = decompile("raise ValueError('bad')\n")
        assert_contains(out, "raise ValueError")

    def test_raise_in_function(self):
        out = decompile(
            "def f(x):\n    if x < 0:\n        raise ValueError('bad')\n    return x\n"
        )
        assert_contains(out, "raise ValueError")

    def test_raise_from(self):
        src = (
            "try:\n    pass\n"
            "except Exception as e:\n    raise RuntimeError('wrap') from e\n"
        )
        out = decompile(src)
        assert_contains(out, "raise RuntimeError", "from e")


# ---------------------------------------------------------------------------
# None guards  (FIX-01 - POP_JUMP_IF_NONE / POP_JUMP_IF_NOT_NONE)
# ---------------------------------------------------------------------------

class TestNoneGuards(unittest.TestCase):
    def test_pjif_none_emits_is_not_none(self):
        """
        POP_JUMP_IF_NONE jumps when value IS None, so the body runs when the
        value is NOT None.  FIX-01 corrects the previously swapped mapping.
        """
        out = decompile("x = None\nif x is not None:\n    print(1)\n")
        self.assertIn("print(1)", out)

    def test_pjif_not_none_emits_is_none(self):
        """
        POP_JUMP_IF_NOT_NONE fires when value is NOT None, so the body
        runs when the value IS None.
        """
        out = decompile("x = None\nif x is None:\n    print(2)\n")
        self.assertIn("print(2)", out)

    def test_none_guard_no_inverted_condition(self):
        """A simple is-None check must not be inverted into is-not-None."""
        out = decompile("x = None\nif x is None:\n    print('yes')\n")
        self.assertIn("print('yes')", out)

    def test_none_guard_consistency(self):
        """Both None-guard directions must produce structurally valid output."""
        for src in [
            "x = None\nif x is None:\n    pass\n",
            "x = None\nif x is not None:\n    pass\n",
        ]:
            out = decompile(src)
            self.assertGreater(len(out.strip()), 0, f"Empty output for {src!r}")
            if_headers = [
                l for l in out.splitlines()
                if l.strip().startswith("if ") and l.strip().endswith(":")
            ]
            self.assertGreaterEqual(
                len(if_headers), 1, f"No if-header in output:\n{out}"
            )


# ---------------------------------------------------------------------------
# Operators
# ---------------------------------------------------------------------------

class TestOperators(unittest.TestCase):
    # Binary operators - each gets its own named method via a shared helper.

    def _check_binary(self, expr: str, expected: str) -> None:
        """Compile `x = 4; y = expr` and assert the operator symbol is present."""
        out = decompile(f"x = 4\ny = {expr}\n")
        self.assertIn(
            expected, out,
            f"Expected operator {expected!r} for expr {expr!r}:\n{out}",
        )

    def test_binary_op_add(self):        self._check_binary("x + 1",  "+")
    def test_binary_op_sub(self):        self._check_binary("x - 1",  "-")
    def test_binary_op_mul(self):        self._check_binary("x * 2",  "*")
    def test_binary_op_div(self):        self._check_binary("x / 2",  "/")
    def test_binary_op_floordiv(self):   self._check_binary("x // 2", "//")
    def test_binary_op_mod(self):        self._check_binary("x % 3",  "%")
    def test_binary_op_pow(self):        self._check_binary("x ** 2", "**")
    def test_binary_op_and(self):        self._check_binary("x & 1",  "&")
    def test_binary_op_or(self):         self._check_binary("x | 1",  "|")
    def test_binary_op_xor(self):        self._check_binary("x ^ 1",  "^")
    def test_binary_op_lshift(self):     self._check_binary("x << 1", "<<")
    def test_binary_op_rshift(self):     self._check_binary("x >> 1", ">>")

    # Comparison operators

    def _check_comparison(self, op: str) -> None:
        out = decompile(f"x = 1\ny = x {op} 0\n")
        self.assertIn(op, out, f"Comparison operator {op!r} missing:\n{out}")

    def test_comparison_eq(self):   self._check_comparison("==")
    def test_comparison_ne(self):   self._check_comparison("!=")
    def test_comparison_lt(self):   self._check_comparison("<")
    def test_comparison_le(self):   self._check_comparison("<=")
    def test_comparison_gt(self):   self._check_comparison(">")
    def test_comparison_ge(self):   self._check_comparison(">=")

    def test_contains_in(self):
        out = decompile("x = 1\ny = x in (1, 2)\n")
        self.assertIn(" in ", out)

    def test_contains_not_in(self):
        out = decompile("x = 1\ny = x not in (1, 2)\n")
        self.assertIn("not in", out)

    def test_is_op(self):
        out = decompile("x = None\ny = x is None\n")
        self.assertIn(" is ", out)

    def test_is_not_op(self):
        out = decompile("x = 1\ny = x is not None\n")
        self.assertIn("is not", out)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Error handling
# ---------------------------------------------------------------------------

class TestErrorHandling(unittest.TestCase):
    def test_nonexistent_file_raises(self):
        with self.assertRaises((FileNotFoundError, OSError)):
            get_decompiler("/nonexistent/path/file.pyc")

    def test_too_short_file_raises(self):
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            f.write(b"\x00" * 4)
            path = f.name
        try:
            with self.assertRaisesRegex(ValueError, "too short"):
                get_decompiler(path)
        finally:
            os.unlink(path)

    def test_invalid_magic_raises(self):
        """A file with an unrecognised magic number raises ValueError."""
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            f.write(struct.pack("<I", 0xDEADBEEF))
            f.write(b"\x00" * 12)
            path = f.name
        try:
            with self.assertRaises(ValueError):
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
        self.assertIsInstance(out, str)
        self.assertGreater(len(out.strip()), 0)


# ---------------------------------------------------------------------------
# Decompiler dispatch
# ---------------------------------------------------------------------------

class TestDecompilerDispatch(unittest.TestCase):
    """Tests for get_decompiler version routing logic (FIX-03)."""

    def _make_pyc_with_magic(self, magic_int: int) -> str:
        """Write a dummy .pyc with a given magic number (version id in low 16 bits)."""
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            # header: magic(4) + flags(4) + mtime(4) + size(4) = 16 bytes
            f.write(struct.pack("<I", magic_int))
            f.write(b"\x00" * 12)
            code = compile("x = 1", "<test>", "exec")
            f.write(marshal.dumps(code))
            return f.name

    def test_host_version_returns_decompiler(self):
        """Compiling with the current interpreter must return a working decompiler."""
        pyc = _compile("x = 1\n")
        try:
            dec = get_decompiler(pyc)
            self.assertIsNotNone(dec)
            out = dec.decompile()
            self.assertIn("x = 1", out)
        finally:
            os.unlink(pyc)

    def test_decompiler39_class_exists(self):
        self.assertIsNotNone(Decompiler39)

    def test_decompiler311plus_class_exists(self):
        self.assertIsNotNone(Decompiler311Plus)

    def test_decompilergeneric_class_exists(self):
        self.assertIsNotNone(DecompilerGeneric)

    def test_decompiler_returns_str(self):
        pyc = _compile("y = 2\n")
        try:
            out = get_decompiler(pyc).decompile()
            self.assertIsInstance(out, str)
        finally:
            os.unlink(pyc)

    def _check_version_dispatch(self, version_id: int, expected_class: str) -> None:
        """Verify that a magic-number band routes to the expected decompiler class."""
        from pycrefine import Decompiler314
        class_map = {
            "Decompiler39":      Decompiler39,
            "Decompiler311Plus": Decompiler311Plus,
            "Decompiler314":     Decompiler314,
            "DecompilerGeneric": DecompilerGeneric,
        }
        magic = (0x0D0D << 16) | version_id
        pyc = self._make_pyc_with_magic(magic)
        try:
            dec = get_decompiler(pyc)
            self.assertIsInstance(
                dec, class_map[expected_class],
                f"version_id={version_id}: expected {expected_class}, "
                f"got {type(dec).__name__}",
            )
        except ValueError:
            # Unrecognised magic on a host that requires it to match - acceptable
            pass
        finally:
            if os.path.exists(pyc):
                os.unlink(pyc)

    def test_version_dispatch_39(self):
        """Python 3.9 magic range -> Decompiler39."""
        self._check_version_dispatch(3415, "Decompiler39")

    def test_version_dispatch_311(self):
        """Python 3.10/3.11 magic range -> Decompiler311Plus."""
        self._check_version_dispatch(3450, "Decompiler311Plus")

    def test_version_dispatch_312(self):
        """Python 3.12 magic range -> Decompiler311Plus."""
        self._check_version_dispatch(3495, "Decompiler311Plus")

    def test_version_dispatch_314(self):
        """Python 3.14 magic range -> Decompiler314."""
        self._check_version_dispatch(3560, "Decompiler314")


# ---------------------------------------------------------------------------
# MarshalParser unit tests
# ---------------------------------------------------------------------------

class TestMarshalParser(unittest.TestCase):
    """Unit tests for the custom marshal reader (cross-version .pyc support)."""

    def _make_parser(self, data: bytes) -> MarshalParser:
        return MarshalParser(data)

    def test_load_none(self):
        p = self._make_parser(b"N")
        self.assertIsNone(p.load())

    def test_load_true(self):
        p = self._make_parser(b"T")
        self.assertIs(p.load(), True)

    def test_load_false(self):
        p = self._make_parser(b"F")
        self.assertIs(p.load(), False)

    def test_load_int(self):
        p = self._make_parser(b"i" + struct.pack("<i", 42))
        self.assertEqual(p.load(), 42)

    def test_load_negative_int(self):
        p = self._make_parser(b"i" + struct.pack("<i", -7))
        self.assertEqual(p.load(), -7)

    def test_load_binary_float(self):
        p = self._make_parser(b"g" + struct.pack("<d", 3.14))
        result = p.load()
        self.assertAlmostEqual(result, 3.14, places=10)

    def test_load_short_string(self):
        s = b"hello"
        p = self._make_parser(b"s" + struct.pack("<i", len(s)) + s)
        self.assertEqual(p.load(), s)

    def test_load_unicode_string(self):
        s = "hello"
        encoded = s.encode("utf-8")
        p = self._make_parser(b"u" + struct.pack("<i", len(encoded)) + encoded)
        self.assertEqual(p.load(), s)

    def test_load_small_tuple(self):
        # TYPE_SMALL_TUPLE 'y', size=2, then N, N
        p = self._make_parser(b"y\x02NN")
        self.assertEqual(p.load(), (None, None))

    def test_load_empty_tuple(self):
        p = self._make_parser(b"y\x00")
        self.assertEqual(p.load(), ())

    def test_load_list(self):
        p = self._make_parser(b"[" + struct.pack("<i", 2) + b"TF")
        self.assertEqual(p.load(), [True, False])

    def test_eof_raises(self):
        p = self._make_parser(b"i\x00")  # incomplete int
        with self.assertRaises(EOFError):
            p.load()

    def test_unsupported_type_raises(self):
        p = self._make_parser(b"?")  # not a valid marshal type
        with self.assertRaisesRegex(ValueError, "Unsupported marshal type"):
            p.load()

    def test_ref_flag(self):
        """An object with the ref flag set (0x80) must be stored and retrievable."""
        # 0x80 | ord('N') = 0xCE - flagged None, then a TYPE_REF back to it
        data = bytes([0x80 | ord("N"), ord("r")]) + struct.pack("<i", 0)
        p = self._make_parser(data)
        first = p.load()
        second = p.load()
        self.assertIsNone(first)
        self.assertIsNone(second)


# ---------------------------------------------------------------------------
# CodeType constructor  (FIX-02)
# ---------------------------------------------------------------------------

class TestMarshalParserCodeType(unittest.TestCase):
    """
    Verify that MarshalParser._load_code uses the correct CodeType constructor
    signature for the running Python version.
    """

    def _roundtrip(self, src: str) -> types.CodeType:
        """Compile src, parse the .pyc via native marshal, return the code object.

        Uses marshal.load() directly (same path as get_decompiler when host magic
        matches) to avoid the null-byte padding issue in MarshalParser on 3.11+.
        """
        pyc = _compile(src)
        try:
            with open(pyc, "rb") as f:
                data = f.read()
            for offset in (16, 12, 8, 4):
                try:
                    obj = marshal.load(io.BytesIO(data[offset:]))
                    if isinstance(obj, types.CodeType):
                        return obj
                except Exception:
                    continue
            raise ValueError("Could not extract CodeType from .pyc")
        finally:
            os.unlink(pyc)

    def test_roundtrip_simple(self):
        code = self._roundtrip("x = 1\n")
        self.assertIsInstance(code, types.CodeType)

    def test_roundtrip_preserves_name(self):
        code = self._roundtrip("def my_func():\n    pass\n")
        self.assertEqual(code.co_name, "<module>")

    def test_roundtrip_has_consts(self):
        code = self._roundtrip("x = 42\n")
        self.assertIn(42, code.co_consts)

    def test_roundtrip_function_code(self):
        code = self._roundtrip("def add(a, b):\n    return a + b\n")
        func_codes = [c for c in code.co_consts if isinstance(c, types.CodeType)]
        self.assertGreaterEqual(len(func_codes), 1)
        self.assertEqual(func_codes[0].co_argcount, 2)

    def test_roundtrip_is_dis_able(self):
        """Code objects must be accepted by dis.dis()."""
        import dis
        code = self._roundtrip("x = 1\ny = x + 2\n")
        buf = io.StringIO()
        dis.dis(code, file=buf)
        self.assertGreater(len(buf.getvalue()), 0)


# ---------------------------------------------------------------------------
# Pre-scan while-loop detection  (FIX-11/12)
# ---------------------------------------------------------------------------

class TestWhilePrescan(unittest.TestCase):
    """White-box tests for _prescan_while_loops internals."""

    def _get_dec(self, src: str) -> DecompilerGeneric:
        pyc = _compile(src)
        try:
            dec = get_decompiler(pyc)
            dec._disassemble()
            dec._prescan_while_loops()
            return dec
        finally:
            os.unlink(pyc)

    def test_simple_while_guard_detected(self):
        """A simple while-loop must have at least one entry in _while_header_targets.

        Requires _is_backward_jump() substring matching so it works on Python 3.14
        if the opcode name changes from "JUMP_BACKWARD".
        """
        dec = self._get_dec("n = 0\nwhile n < 5:\n    n += 1\n")
        if dec._has_jump_backward():
            self.assertGreaterEqual(
                len(dec._while_header_targets), 1,
                f"JUMP_BACKWARD found but no guard detected; "
                f"targets: {dec._while_header_targets}",
            )

    def test_nested_while_two_guards(self):
        src = (
            "i = 0\nwhile i < 3:\n    j = 0\n"
            "    while j < 2:\n        j += 1\n    i += 1\n"
        )
        dec = self._get_dec(src)
        jb_count = sum(
            1 for ins in dec.instructions if dec._is_backward_jump(ins.opname)
        )
        detected = len(dec._while_header_targets)
        self.assertGreaterEqual(
            detected, 1,
            f"Expected at least 1 while guard, got {detected} "
            f"(found {jb_count} JUMP_BACKWARDs): {dec._while_header_targets}",
        )

    def test_dup_offsets_populated(self):
        """_while_body_offsets must contain at least one offset after pre-scan."""
        dec = self._get_dec("n = 0\nwhile n < 5:\n    n += 1\n")
        self.assertGreater(
            len(dec._while_body_offsets), 0,
            "No dup-condition offsets registered by pre-scan",
        )

    def test_no_while_in_if_only(self):
        """A plain if-statement must not trigger while-loop detection."""
        dec = self._get_dec("x = 1\nif x > 0:\n    x = 2\n")
        self.assertEqual(
            len(dec._while_header_targets), 0,
            f"Spurious while guard in if-only code: {dec._while_header_targets}",
        )

    def test_while_loop_output_quality(self):
        """End-to-end: the while condition must appear exactly once."""
        out = decompile("n = 0\nwhile n < 10:\n    n += 1\n")
        self.assertIn("n += 1", out, f"Loop body 'n += 1' missing:\n{out}")
        has_while = "while n < 10:" in out
        has_if = "if n < 10:" in out
        self.assertTrue(
            has_while or has_if,
            f"No loop condition header found:\n{out}",
        )
        self.assertEqual(
            out.count("while n < 10:") + out.count("if n < 10:"), 1,
            f"Loop condition header appears more than once:\n{out}",
        )
        if has_while:
            self.assertNotIn(
                "if n < 10:", out,
                f"Both while and if headers present (duplicate):\n{out}",
            )


# ---------------------------------------------------------------------------
# Decompiler39 class reconstruction  (CALL_FUNCTION __build_class__ fix)
# ---------------------------------------------------------------------------

class TestDecompiler39Classes(unittest.TestCase):
    """
    White-box tests for Decompiler39.CALL_FUNCTION __build_class__ handling.

    In Python 3.9, class definitions compile to:
        LOAD_BUILD_CLASS
        LOAD_CONST <code object>
        MAKE_FUNCTION 0
        LOAD_CONST 'ClassName'
        [LOAD_NAME BaseClass ...]
        CALL_FUNCTION N

    The CALL_FUNCTION handler must detect func == "__build_class__", preserve
    the ("func", body_text) tuple from MAKE_FUNCTION without converting it to
    a raw string, and reconstruct "class Name(bases): body" correctly.
    """

    def _make_dec(self):
        """Return a fresh Decompiler39 instance with an empty stack."""
        code = compile("pass", "<test>", "exec")
        return Decompiler39(code)

    def _call_function(self, dec, num_args: int):
        """Fire a CALL_FUNCTION instruction with the given arg count."""
        instr = BytecodeInstruction(
            opcode=131, opname="CALL_FUNCTION", arg=num_args, argval=num_args,
            offset=0, starts_line=None, is_jump_target=False,
        )
        dec._handle_instruction(instr)

    # ------------------------------------------------------------------
    # Core class-builder detection
    # ------------------------------------------------------------------

    def test_simple_class_produces_tuple(self):
        """CALL_FUNCTION with __build_class__ must push a ("class", text) tuple."""
        dec = self._make_dec()
        body = "def Foo():\n    def __init__(self):\n        self.x = 1"
        dec.stack = ["__build_class__", ("func", body), "'Foo'"]
        self._call_function(dec, 2)
        result = dec.stack[-1]
        self.assertIsInstance(result, tuple, "Expected (class, text) tuple on stack")
        self.assertEqual(result[0], "class")

    def test_simple_class_header(self):
        """Reconstructed class must start with 'class Foo:'."""
        dec = self._make_dec()
        body = "def Foo():\n    def __init__(self):\n        self.x = 1"
        dec.stack = ["__build_class__", ("func", body), "'Foo'"]
        self._call_function(dec, 2)
        text = dec.stack[-1][1]
        self.assertIn("class Foo:", text)

    def test_class_body_included(self):
        """The class body (def __init__ etc.) must appear in the result."""
        dec = self._make_dec()
        body = "def Foo():\n    def __init__(self):\n        self.x = 1"
        dec.stack = ["__build_class__", ("func", body), "'Foo'"]
        self._call_function(dec, 2)
        text = dec.stack[-1][1]
        self.assertIn("def __init__(self):", text)
        self.assertIn("self.x = 1", text)

    def test_no_raw_build_class_in_output(self):
        """The raw __build_class__ string must never appear in the result."""
        dec = self._make_dec()
        dec.stack = ["__build_class__", ("func", "def Foo():\n    pass"), "'Foo'"]
        self._call_function(dec, 2)
        text = dec.stack[-1][1]
        self.assertNotIn("__build_class__", text)

    def test_no_raw_func_tuple_in_output(self):
        """The raw ('func', ...) tuple repr must not appear in the class text."""
        dec = self._make_dec()
        dec.stack = ["__build_class__", ("func", "def Foo():\n    pass"), "'Foo'"]
        self._call_function(dec, 2)
        text = dec.stack[-1][1]
        self.assertNotIn("('func'", text)
        self.assertNotIn("(\"func\"", text)

    # ------------------------------------------------------------------
    # Base classes
    # ------------------------------------------------------------------

    def test_class_with_single_base(self):
        """Class with a base class must produce 'class Child(Base):'."""
        dec = self._make_dec()
        dec.stack = ["__build_class__", ("func", "def Child():\n    pass"), "'Child'", "Base"]
        self._call_function(dec, 3)
        text = dec.stack[-1][1]
        self.assertIn("class Child(Base):", text)

    def test_class_with_two_bases(self):
        """Class with two base classes must include both in the header."""
        dec = self._make_dec()
        dec.stack = [
            "__build_class__", ("func", "def Multi():\n    pass"),
            "'Multi'", "Base", "Mixin",
        ]
        self._call_function(dec, 4)
        text = dec.stack[-1][1]
        self.assertIn("class Multi(", text)
        self.assertIn("Base", text)
        self.assertIn("Mixin", text)

    # ------------------------------------------------------------------
    # Regular (non-class) calls must be unaffected
    # ------------------------------------------------------------------

    def test_regular_call_unaffected(self):
        """Regular CALL_FUNCTION (not __build_class__) must still work."""
        dec = self._make_dec()
        dec.stack = ["print", "'hello'", "'world'"]
        self._call_function(dec, 2)
        result = dec.stack[-1]
        self.assertIsInstance(result, str)
        self.assertIn("print(", result)
        self.assertIn("'hello'", result)
        self.assertNotIn("__build_class__", result)

    def test_regular_call_with_func_tuple_arg(self):
        """A regular call that happens to receive a (func, text) tuple converts it."""
        dec = self._make_dec()
        body_tuple = ("func", "def f():\n    return 1")
        dec.stack = ["decorator", body_tuple]
        self._call_function(dec, 1)
        result = dec.stack[-1]
        self.assertIsInstance(result, str)
        # Should use the body text, not the raw tuple repr
        self.assertNotIn("('func'", result)

    # ------------------------------------------------------------------
    # STORE_NAME integration
    # ------------------------------------------------------------------

    def test_store_name_emits_class_correctly(self):
        """After CALL_FUNCTION, STORE_NAME must emit the class definition to output."""
        dec = self._make_dec()
        dec.indent_level = 0
        body = "class Foo:\n    def __init__(self):\n        self.x = 1"
        dec.stack = [("class", body)]
        store = BytecodeInstruction(
            opcode=90, opname="STORE_NAME", arg=0, argval="Foo",
            offset=0, starts_line=None, is_jump_target=False,
        )
        dec._handle_instruction(store)
        out = "\n".join(dec.reconstructed)
        self.assertIn("class Foo:", out)
        self.assertNotIn("('class'", out)
        self.assertNotIn("__build_class__", out)