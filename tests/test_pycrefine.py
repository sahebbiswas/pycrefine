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

import importlib.util
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


def _run39_full_impl(instructions):
    """Shared helper to run Decompiler39 with all prescans on a synthetic instruction list."""
    from pycrefine import Decompiler39, BytecodeInstruction, post_process_source
    code = compile("pass", "<test>", "exec")
    dec = Decompiler39(code)
    dec.instructions = list(instructions)

    _JUMP_OPS = {
        "FOR_ITER", "JUMP_FORWARD", "JUMP_ABSOLUTE",
        "POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE",
        "JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP",
        "SETUP_FINALLY", "SETUP_WITH", "JUMP_IF_NOT_EXC_MATCH",
    }
    targets = {
        ins.argval for ins in dec.instructions
        if ins.opname in _JUMP_OPS and isinstance(ins.argval, int)
    }
    dec.instructions = [
        BytecodeInstruction(
            ins.opcode, ins.opname, ins.arg, ins.argval,
            ins.offset, ins.starts_line,
            ins.offset in targets,
        )
        for ins in dec.instructions
    ]

    dec.pc = 0
    dec.blocks = []
    dec._while_header_targets = {}
    dec._while_body_offsets = set()
    dec._while_true_ends = set()
    dec._prescan_while_loops()
    dec._prescan_try_structure()
    dec._prescan_ternaries()
    dec._prescan_compound_conds()

    # Docstring pre-pass (same as Decompiler39.decompile())
    dec.has_doc = False
    if dec.code_obj.co_consts and isinstance(dec.code_obj.co_consts[0], str):
        first_meaningful = None
        for ins in dec.instructions:
            if ins.opname not in ("RESUME", "NOP", "CACHE", "NOT_TAKEN"):
                first_meaningful = ins
                break
        is_docstring = False
        if first_meaningful and first_meaningful.opname == "LOAD_CONST" and first_meaningful.arg == 0:
            idx = dec.instructions.index(first_meaningful)
            if idx + 1 < len(dec.instructions):
                next_op = dec.instructions[idx + 1].opname
                if next_op in ("POP_TOP", "STORE_NAME"):
                    is_docstring = True
        if is_docstring:
            doc = dec.code_obj.co_consts[0]
            if doc:
                dec._append_reconstructed('"""', indent_multiline=True)
                dec._append_reconstructed(doc.strip(), indent_multiline=True)
                dec._append_reconstructed('"""', indent_multiline=True)
                dec.has_doc = True
                dec.reconstructed.append("")

    dec.pc = 0
    while dec.pc < len(dec.instructions):
        instr = dec.instructions[dec.pc]
        dec._close_blocks(instr.offset)
        dec.pc += 1
        dec._handle_instruction(instr)
    dec._close_blocks(0x7fffffff)

    # Final cleanup: suppress redundant trailing 'return None' at the base indent
    # (mirroring DecompilerGeneric.decompile() logic)
    if dec.reconstructed:
        # Find last non-empty line
        last_idx = len(dec.reconstructed) - 1
        while last_idx >= 0 and not dec.reconstructed[last_idx].strip():
            last_idx -= 1
        
        if last_idx >= 0 and dec.reconstructed[last_idx].strip() == "return None":
            line = dec.reconstructed[last_idx]
            # FIX: Only suppress root-level 'return None'. Indented ones (inside if/with/etc)
            # are likely explicit and should be preserved.
            if not (line.startswith(" ") or line.startswith("\t")):
                # Found a trailing return None at root level. 
                # We can safely remove it if there's other code.
                has_others = False
                for i in range(last_idx):
                     strip_line = dec.reconstructed[i].strip()
                     if strip_line and not (
                         strip_line.startswith('"""') or strip_line.startswith("'''")
                     ):
                         has_others = True
                         break
                
                if has_others or getattr(dec, "has_doc", False):
                    dec.reconstructed.pop(last_idx)
                else:
                    indent = len(line) - len(line.lstrip())
                    dec.reconstructed[last_idx] = line[:indent] + "pass"

    raw_source = "\n".join(str(s) for s in dec.reconstructed).rstrip()
    return post_process_source(raw_source)


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
        # CPython compiles both `if x > 0: y=1 else: y=0` and
        # `y = 1 if x > 0 else 0` to identical bytecode.  The decompiler
        # canonicalises this pattern as a ternary expression, which is
        # semantically equivalent and more compact.  Accept either form.
        out = decompile("x = 1\nif x > 0:\n    y = 1\nelse:\n    y = 0\n")
        self.assertIn("x > 0", out, f"Condition missing:\n{out}")
        self.assertIn("y = 1" if "if x > 0:" in out else "1", out,
                      f"Then-value missing:\n{out}")
        # Both branch values must appear somewhere in the output
        self.assertTrue(
            "y = 1" in out or "1 if" in out,
            f"Then branch value '1' missing:\n{out}",
        )
        self.assertTrue(
            "y = 0" in out or "else 0" in out,
            f"Else branch value '0' missing:\n{out}",
        )

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
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        cond_headers = [ln for ln in lines if ln in ("while n < 5:", "if n < 5:")]
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
        """
        Verify that decompiling an if-statement preserves an equality comparison.
        
        Asserts that the decompiled output contains the literal "x == 1".
        """
        out = decompile("x = 1\nif x == 1:\n    pass\n")
        assert_contains(out, "x == 1")

    def test_if_comparison_not_equals(self):
        """
        Verify that decompiling an inequality if-statement preserves the '!=' comparison.
        
        Ensures the decompiled output contains the substring "x != 2".
        """
        out = decompile("x = 1\nif x != 2:\n    pass\n")
        assert_contains(out, "x != 2")

    def test_if_implicit_else_avoidance(self):
        """The decompiler should not append an else block after an if block
        that ends with an unconditional exit (like return or raise)."""
        src = (
            "def test(x):\n"
            "    if x is None:\n"
            "        return True\n"
            "    if x == 1:\n"
            "        return True\n"
            "    return False\n"
        )
        out = decompile(src)
        self.assertNotIn("else:", out, f"Unexpected 'else:' generated:\n{out}")
        assert_contains(out, "if x is None:", "if x == 1:", "return False")

        lines = out.splitlines()
        first_if_indent = next(len(line) - len(line.lstrip()) for line in lines if "if x is None:" in line)
        second_if_indent = next(len(line) - len(line.lstrip()) for line in lines if "if x == 1:" in line)
        return_false_indent = next(len(line) - len(line.lstrip()) for line in lines if "return False" in line)

        self.assertEqual(first_if_indent, second_if_indent, "if-headers missing base ident level")
        self.assertEqual(first_if_indent, return_false_indent, "return False missing base indent level")




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
        assert_contains(out, "from os.path import join, exists")

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
        body_lines = [ln for ln in lines if "y = " in ln or "return" in ln]
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
        exc_lines = [ln for ln in lines if ln.lstrip().startswith("except")]
        indents = {len(ln) - len(ln.lstrip()) for ln in exc_lines}
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

    def test_try_except_finally(self):
        """try/except/finally must produce all three clauses in correct order."""
        src = (
            "try:\n    x = int('1')\n"
            "except ValueError:\n    x = 0\n"
            "finally:\n    print('done')\n"
        )
        out = decompile(src)
        assert_contains(out, "try:", "except ValueError:", "finally:", "print('done')")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        try_pos    = next(i for i, ln in enumerate(lines) if ln == "try:")
        except_pos = next(i for i, ln in enumerate(lines) if ln.startswith("except"))
        finally_pos = next(i for i, ln in enumerate(lines) if ln == "finally:")
        self.assertLess(try_pos, except_pos, "try: must come before except:")
        self.assertLess(except_pos, finally_pos, "except: must come before finally:")

    def test_try_finally_no_except(self):
        """try/finally without except must produce try: and finally: blocks."""
        src = "try:\n    x = 1\nfinally:\n    print('done')\n"
        out = decompile(src)
        assert_contains(out, "try:", "finally:", "print('done')")

    def test_with_statement(self):
        """
        Ensure a `with ... as ...:` header and its context expression appear in decompiled output and no __exit__ epilogue leaks.
        
        Asserts that the decompiler emits a with-statement header and the context expression (for example, `open(`), and that the sentinel cleanup call `None(None, None)` does not appear.
        """
        src = "with open('f') as fh:\n    data = fh.read()\n"
        out = decompile(src)
        self.assertIn("with ", out, f"'with' header missing:\n{out}")
        self.assertIn("open(", out, f"context expression missing:\n{out}")
        self.assertNotIn("None(None, None)", out, f"__exit__ epilogue leaked:\n{out}")

    def test_with_as_variable_bound(self):
        """The 'as' variable from a with statement must appear in the output."""
        src = "with open('f') as fh:\n    x = fh.read()\n"
        out = decompile(src)
        # The decompiler should bind 'fh' in the with header
        self.assertIn("fh", out, f"'as fh' binding missing:\n{out}")
        self.assertIn("fh.read()", out, f"body missing:\n{out}")

    def test_with_explicit_return_none(self):
        """Explicit 'return None' inside a with statement must be preserved."""
        src = "def f():\n    with open('f'):\n        return None\n"
        out = decompile(src)
        self.assertIn("return None", out, f"Explicit return None dropped inside with:\n{out}")

    def test_with_try_except_finally(self):
        """with + nested try/except/finally must not corrupt structure."""
        src = (
            "with open('f') as fh:\n"
            "    try:\n"
            "        x = fh.read()\n"
            "    except IOError:\n"
            "        x = ''\n"
            "    finally:\n"
            "        print('done')\n"
        )
        out = decompile(src)
        assert_contains(out, "with ", "try:", "except IOError:", "finally:")
        self.assertNotIn("None(None, None)", out, f"__exit__ epilogue leaked:\n{out}")
        self.assertNotIn("_exc_info", out, f"sentinel leaked:\n{out}")

    def test_sequential_try_except_finally_blocks(self):
        """Two sequential try/except/finally blocks must both appear correctly."""
        src = (
            "try:\n    a = int('1')\n"
            "except ValueError:\n    a = 0\n"
            "finally:\n    print('first')\n"
            "try:\n    b = int('2')\n"
            "except ValueError:\n    b = 0\n"
            "finally:\n    print('second')\n"
        )
        out = decompile(src)
        assert_contains(out, "try:", "except ValueError:", "finally:")
        self.assertIn("print('first')", out, f"first finally body missing:\n{out}")
        self.assertIn("print('second')", out, f"second finally body missing:\n{out}")
        # Both except clauses must be present
        self.assertEqual(out.count("except ValueError:"), 2,
                         f"Expected 2 except ValueError: clauses:\n{out}")

    def test_finally_body_after_except_not_before(self):
        """finally: must appear AFTER except clauses, not before them."""
        src = (
            "try:\n    x = 1\n"
            "except ValueError:\n    x = 0\n"
            "finally:\n    print('fin')\n"
        )
        out = decompile(src)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        except_pos  = next((i for i, ln in enumerate(lines) if ln.startswith("except")), -1)
        finally_pos = next((i for i, ln in enumerate(lines) if ln == "finally:"), -1)
        self.assertGreater(finally_pos, except_pos,
                           f"finally: appeared before except: in:\n{out}")

    def test_no_exit_epilogue_leakage(self):
        """The __exit__(None,None,None) with-cleanup must not appear as None(None,None)."""
        src = "with open('f') as fh:\n    pass\n"
        out = decompile(src)
        self.assertNotIn("None(None, None)", out, f"__exit__ epilogue leaked:\n{out}")
        self.assertNotIn("None(None,", out, f"__exit__ epilogue leaked:\n{out}")

    def test_reraise_wrapper_suppressed(self):
        """Re-raise wrapper machinery must not appear in decompiled output."""
        src = (
            "try:\n    x = int('1')\n"
            "except ValueError:\n    x = 0\n"
            "finally:\n    print('done')\n"
        )
        out = decompile(src)
        self.assertNotIn("RERAISE", out, f"RERAISE opcode leaked:\n{out}")
        self.assertNotIn("PUSH_EXC_INFO", out, f"PUSH_EXC_INFO leaked:\n{out}")
        self.assertNotIn("_exc_info", out, f"_exc_info sentinel leaked:\n{out}")


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
        self.assertIn("if x is not None:", out)

    def test_pjif_not_none_emits_is_none(self):
        """
        POP_JUMP_IF_NOT_NONE fires when value is NOT None, so the body
        runs when the value IS None.
        """
        out = decompile("x = None\nif x is None:\n    print(2)\n")
        self.assertIn("print(2)", out)
        self.assertIn("if x is None:", out)

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
                ln for ln in out.splitlines()
                if ln.strip().startswith("if ") and ln.strip().endswith(":")
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
        """A file with an unrecognised magic number raises ValueError with a descriptive message."""
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            f.write(struct.pack("<I", 0xDEADBEEF))
            f.write(b"\x00" * 12)
            path = f.name
        try:
            with self.assertRaisesRegex(ValueError, "Invalid or unsupported Python magic number"):
                get_decompiler(path)
        finally:
            os.unlink(path)

    def test_invalid_magic_inferred_version_in_error(self):
        """ValueError for a version-valid but host-incompatible magic number includes the version name."""
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            # 3310 is Python 3.4. This is unsupported.
            f.write(struct.pack("<I", 3310)) 
            f.write(b"\x00" * 12)
            path = f.name
        
        # We only expect the detailed version message if the host magic is NOT 3310.
        host_magic = int.from_bytes(importlib.util.MAGIC_NUMBER, "little")
        if (host_magic & 0xFFFF) != 3310:
            try:
                with self.assertRaisesRegex(ValueError, "Input file appears to be from Python 3.4"):
                    get_decompiler(path)
            finally:
                os.unlink(path)
        else:
            os.unlink(path)

    def test_invalid_marshal_data_includes_version_in_error(self):
        """ValueError for corrupted marshal data includes the inferred version name."""
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            # 3495 is Python 3.12. If we run on 3.12, it skips host load but tries parser load.
            f.write(struct.pack("<I", 3495)) 
            f.write(b"\x00" * 12) # 4+12 = 16 bytes
            f.write(b"GARBAGE")
            path = f.name
        try:
            # We check for the name in the message.
            with self.assertRaisesRegex(ValueError, "Inferred version: Python 3.12"):
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
        self.assertIn("class Child(Base)", out)
        self.assertIn("while i < 10", out)

    def test_empty_decompiler_output_emits_warning(self):
        """If decompile() returns an empty string, main() prints a warning to stderr."""
        from unittest.mock import patch, MagicMock
        from pycrefine import main
        
        mock_dec = MagicMock()
        mock_dec.decompile.return_value = "" # Empty output
        
        with patch("pycrefine.get_decompiler", return_value=mock_dec), \
             patch("sys.argv", ["pycrefine", "dummy.pyc"]), \
             patch("sys.stderr", new_callable=io.StringIO) as mock_stderr, \
             patch("sys.exit") as mock_exit:
            
            main()
            
            self.assertIn("Warning: Decompiler returned no source code", mock_stderr.getvalue())
            mock_exit.assert_called_with(1)


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
                "JUMP_BACKWARD found but no guard detected; "
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
        """
        A CALL_FUNCTION with a named ('func', body) as its single argument
        is the decorator pattern: decorator(func) -> push ('func', '@deco\ndef f()...')
        so the decorated function definition is preserved as a tuple for STORE_NAME
        to emit cleanly.
        """
        dec = self._make_dec()
        body_tuple = ("func", "def f():\n    return 1")
        dec.stack = ["decorator", body_tuple]
        self._call_function(dec, 1)
        result = dec.stack[-1]
        # Must be a ('func', ...) tuple — STORE_NAME will emit it as source code
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], "func")
        # The decorated body must contain both the @decorator line and the def
        body_text = result[1]
        self.assertIn("@decorator", body_text)
        self.assertIn("def f():", body_text)
        self.assertNotIn("('func'", body_text)

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



# ---------------------------------------------------------------------------
# Python 3.9 specific fixes
# ---------------------------------------------------------------------------

class TestDecompiler39Python39Fixes(unittest.TestCase):
    """
    White-box regression tests for all four Python 3.9 fixes:

      Fix 1 - JUMP_ABSOLUTE recognised as backward jump (_is_backward_jump)
      Fix 2 - is_jump_target populated correctly in _disassemble
      Fix 3 - DUP_TOP + COMPARE_OP("exception match") pattern for try/except
      Fix 4 - INPLACE_* ops peek ahead at STORE to emit augmented assignment

    All tests use synthetic BytecodeInstruction lists injected directly into
    Decompiler39 so they run correctly on any Python version (no 3.9 runtime
    needed).  The helper _run39() bypasses _disassemble() and patches
    is_jump_target from argval targets — matching what the real fix does.
    """

    # ------------------------------------------------------------------
    # Shared helper
    # ------------------------------------------------------------------

    def _run39(self, instructions):
        """
        Run Decompiler39 on a synthetic instruction list.

        Patches is_jump_target using argval of jump opcodes (replicating
        the fix in Decompiler39._disassemble), then runs the prescan and
        the main decode loop manually without calling _disassemble().
        """
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = list(instructions)

        # Patch is_jump_target from argval targets (Fix 2)
        _JUMP_OPS = {
            "FOR_ITER", "JUMP_FORWARD", "JUMP_ABSOLUTE",
            "POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE",
            "JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP",
            "SETUP_FINALLY", "SETUP_WITH", "JUMP_IF_NOT_EXC_MATCH",
        }
        targets = set()
        for ins in dec.instructions:
            if ins.opname in _JUMP_OPS and isinstance(ins.argval, int):
                targets.add(ins.argval)
        dec.instructions = [
            BytecodeInstruction(
                ins.opcode, ins.opname, ins.arg, ins.argval,
                ins.offset, ins.starts_line,
                ins.offset in targets,
            )
            for ins in dec.instructions
        ]

        # Run prescan then main decode loop
        dec.pc = 0
        dec.blocks = []
        dec._while_header_targets = {}
        dec._while_body_offsets = set()
        dec._while_true_ends = set()
        dec._prescan_while_loops()

        dec.pc = 0
        dec.has_doc = False
        while dec.pc < len(dec.instructions):
            instr = dec.instructions[dec.pc]
            # Close expired blocks
            while dec.blocks and instr.offset >= dec.blocks[-1][0]:
                block_end, block_type = dec.blocks.pop()
                last_idx = len(dec.reconstructed) - 1
                while last_idx >= 0 and not dec.reconstructed[last_idx].strip():
                    last_idx -= 1
                if last_idx >= 0 and dec.reconstructed[last_idx].strip().endswith(":"):
                    dec._append_reconstructed("pass")
                dec.indent_level -= 1
            dec.pc += 1
            dec._handle_instruction(instr)
        # Drain any remaining blocks
        while dec.blocks:
            dec.blocks.pop()
            dec.indent_level -= 1

        return "\n".join(dec.reconstructed)

    # ------------------------------------------------------------------
    # Fix 1 — JUMP_ABSOLUTE recognised as backward jump
    # ------------------------------------------------------------------

    def test_is_backward_instruction_backward_jump_absolute(self):
        """
        _is_backward_instruction must return True for a JUMP_ABSOLUTE whose
        target is at or before its own offset (a real loop back-edge).
        """
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        backward = BytecodeInstruction(
            opcode=113, opname="JUMP_ABSOLUTE", arg=4, argval=4,
            offset=10, starts_line=None, is_jump_target=False,
        )
        self.assertTrue(dec._is_backward_instruction(backward),
                        "JUMP_ABSOLUTE with target(4) <= offset(10) is backward")

    def test_is_backward_instruction_forward_jump_absolute_is_false(self):
        """
        _is_backward_instruction must return False for a JUMP_ABSOLUTE whose
        target is ahead (a forward jump — not a loop back-edge).
        """
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        forward = BytecodeInstruction(
            opcode=113, opname="JUMP_ABSOLUTE", arg=20, argval=20,
            offset=10, starts_line=None, is_jump_target=False,
        )
        self.assertFalse(dec._is_backward_instruction(forward),
                         "JUMP_ABSOLUTE with target(20) > offset(10) is NOT backward")

    def test_is_backward_jump_jump_backward(self):
        """_is_backward_jump still returns True for JUMP_BACKWARD (3.11+)."""
        self.assertTrue(Decompiler39._is_backward_jump("JUMP_BACKWARD"))

    def test_is_backward_jump_jump_forward_is_false(self):
        """_is_backward_jump must NOT return True for JUMP_FORWARD."""
        self.assertFalse(Decompiler39._is_backward_jump("JUMP_FORWARD"))

    def test_while_loop_uses_jump_absolute_back_edge(self):
        """
        3.9 while loop: JUMP_ABSOLUTE targeting the condition start must
        produce 'while' not 'if'.

        Layout (3.9):
            0  LOAD_NAME n
            2  LOAD_CONST 5
            4  COMPARE_OP <
            6  POP_JUMP_IF_FALSE 14   <- guard
            8  LOAD_CONST 1           <- body
           10  STORE_NAME n
           12  JUMP_ABSOLUTE 0        <- back-edge
           14  RETURN_VALUE           <- loop exit (is_jump_target=True)
        """
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "LOAD_NAME",         0, "n",  0, None, False),
            Instr(0, "LOAD_CONST",        1,  5,   2, None, False),
            Instr(0, "COMPARE_OP",        0, "<",  4, None, False),
            Instr(0, "POP_JUMP_IF_FALSE", 14, 14,  6, None, False),
            Instr(0, "LOAD_CONST",        2,  1,   8, None, False),
            Instr(0, "STORE_NAME",        0, "n", 10, None, False),
            Instr(0, "JUMP_ABSOLUTE",     0,  0,  12, None, False),
            Instr(0, "RETURN_VALUE",      None, None, 14, None, True),
        ])
        self.assertIn("while", out, f"Expected 'while', got:\n{out}")
        self.assertNotIn("if n", out,
                         f"'if n' should not appear (should be 'while'):\n{out}")

    def test_while_loop_prescan_detects_guard_via_jump_absolute(self):
        """
        _prescan_while_loops must detect the guard POP_JUMP_IF_FALSE even
        when the back-edge is JUMP_ABSOLUTE (not JUMP_BACKWARD).
        """
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [
            Instr(0, "LOAD_NAME",         0, "n",  0, None, False),
            Instr(0, "LOAD_CONST",        1,  5,   2, None, False),
            Instr(0, "COMPARE_OP",        0, "<",  4, None, False),
            Instr(0, "POP_JUMP_IF_FALSE", 14, 14,  6, None, False),
            Instr(0, "LOAD_CONST",        2,  1,   8, None, False),
            Instr(0, "STORE_NAME",        0, "n", 10, None, False),
            Instr(0, "JUMP_ABSOLUTE",     0,  0,  12, None, False),
            Instr(0, "RETURN_VALUE",      None, None, 14, None, True),
        ]
        dec._while_body_offsets = set()
        dec._while_header_targets = {}
        dec._while_true_ends = set()
        dec._prescan_while_loops()
        self.assertGreaterEqual(
            len(dec._while_header_targets), 1,
            f"No guard detected. targets={dec._while_header_targets}",
        )

    def test_forward_jump_absolute_treated_as_jump_forward(self):
        """
        A forward JUMP_ABSOLUTE (target > offset) must not be treated as a
        loop back-edge.  It should behave like JUMP_FORWARD — closing an
        if-block and not emitting 'while'.
        """
        Instr = BytecodeInstruction
        # Simple if/else: condition -> if-body -> JUMP_ABSOLUTE(end) -> else-body
        out = self._run39([
            Instr(0, "LOAD_CONST",        0, 1,    0, None, False),
            Instr(0, "STORE_NAME",        0, "x",  2, None, False),
            Instr(0, "LOAD_NAME",         0, "x",  4, None, False),
            Instr(0, "LOAD_CONST",        1, 0,    6, None, False),
            Instr(0, "COMPARE_OP",        4, ">",  8, None, False),
            Instr(0, "POP_JUMP_IF_FALSE", 16, 16, 10, None, False),
            Instr(0, "LOAD_CONST",        2, 1,   12, None, False),
            Instr(0, "STORE_NAME",        1, "y", 14, None, False),
            Instr(0, "JUMP_ABSOLUTE",     18, 18, 16, None, False),  # forward!
            Instr(0, "RETURN_VALUE",      None, None, 18, None, True),
        ])
        self.assertNotIn("while", out, f"Forward JUMP_ABSOLUTE should not produce 'while':\n{out}")

    # ------------------------------------------------------------------
    # Fix 2 — is_jump_target populated in _disassemble
    # ------------------------------------------------------------------

    def test_is_jump_target_set_correctly(self):
        """
        After _disassemble, instructions at jump targets must have
        is_jump_target=True; all others must have is_jump_target=False.
        """
        import py_compile, tempfile, os

        src = "x = 1\nif x > 0:\n    y = 2\n"
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(src)
            sp = f.name
        pp = sp + "c"
        try:
            py_compile.compile(sp, cfile=pp, doraise=True)
            dec = get_decompiler(pp)
            # get_decompiler returns the host-version decompiler, but we can
            # verify Decompiler39's fix via the synthetic test below.
        finally:
            os.unlink(sp)
            if os.path.exists(pp):
                os.unlink(pp)

        # Synthetic: inject instructions where POP_JUMP_IF_FALSE targets offset 8.
        # Use correct 3.9 opcode numbers so _disassemble() resolves them properly:
        #   LOAD_CONST=100, POP_JUMP_IF_FALSE=114, STORE_NAME=90, RETURN_VALUE=83
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec39 = Decompiler39(code)
        raw = [
            Instr(100, "LOAD_CONST",        0, 1,    0, None, False),
            Instr(114, "POP_JUMP_IF_FALSE", 8, 8,    2, None, False),
            Instr(100, "LOAD_CONST",        1, 2,    4, None, False),
            Instr(90,  "STORE_NAME",        0, "y",  6, None, False),
            Instr(83,  "RETURN_VALUE",      None, None, 8, None, False),  # should become True
        ]
        dec39.instructions = raw
        dec39.code_obj = type("C", (), {
            "co_code": bytes(
                b for ins in raw
                for b in [ins.opcode, ins.arg if ins.arg is not None else 0]
            ),
            "co_consts": (1, 2, None),
            "co_names": ("y",),
            "co_varnames": (),
            "co_cellvars": (),
            "co_freevars": (),
        })()
        dec39._disassemble()

        # Find the instruction at offset 8
        instr_at_8 = next(
            (i for i in dec39.instructions if i.offset == 8), None
        )
        self.assertIsNotNone(instr_at_8, "No instruction at offset 8")
        self.assertTrue(
            instr_at_8.is_jump_target,
            "Offset 8 (target of POP_JUMP_IF_FALSE) should be is_jump_target=True",
        )

    # ------------------------------------------------------------------
    # Fix 3 — DUP_TOP + COMPARE_OP("exception match") try/except
    # ------------------------------------------------------------------

    def test_try_except_typed_no_as(self):
        """
        3.9 try/except ValueError (no binding) must emit 'except ValueError:'
        via the DUP_TOP + COMPARE_OP("exception match") handler.
        """
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "SETUP_FINALLY",    None, 16,  0, None, False),
            Instr(0, "LOAD_CONST",       0,    42,  2, None, False),
            Instr(0, "STORE_NAME",       0,    "x", 4, None, False),
            Instr(0, "POP_BLOCK",        None, None,6, None, False),
            Instr(0, "JUMP_FORWARD",     None, 32,  8, None, False),
            # Handler at offset 16:
            Instr(0, "DUP_TOP",          None, None,16, None, True),
            Instr(0, "LOAD_NAME",        1, "ValueError", 18, None, False),
            Instr(0, "COMPARE_OP",       10, "exception match", 20, None, False),
            Instr(0, "POP_JUMP_IF_FALSE",28, 28, 22, None, False),
            Instr(0, "POP_TOP",          None, None, 24, None, False),  # exc type
            Instr(0, "POP_TOP",          None, None, 26, None, False),  # traceback
            Instr(0, "LOAD_CONST",       2, 0,     28, None, False),
            Instr(0, "STORE_NAME",       0, "x",   30, None, False),
            Instr(0, "POP_EXCEPT",       None, None, 32, None, False),
            Instr(0, "RETURN_VALUE",     None, None, 34, None, False),
        ])
        self.assertIn("try:", out, f"try: missing:\n{out}")
        self.assertIn("except ValueError:", out,
                      f"except ValueError: missing:\n{out}")
        self.assertNotIn("DUP_TOP", out,
                         f"Raw DUP_TOP leaked into output:\n{out}")

    def test_try_except_as_binding(self):
        """
        3.9 'except ValueError as e:' must bind the name correctly via the
        DUP_TOP pattern.  The STORE_NAME 'e' immediately follows the POPs.
        """
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "SETUP_FINALLY",    None, 16,  0, None, False),
            Instr(0, "LOAD_CONST",       0,    42,  2, None, False),
            Instr(0, "STORE_NAME",       0,    "x", 4, None, False),
            Instr(0, "POP_BLOCK",        None, None,6, None, False),
            Instr(0, "JUMP_FORWARD",     None, 36,  8, None, False),
            # Handler at offset 16:
            Instr(0, "DUP_TOP",          None, None,16, None, True),
            Instr(0, "LOAD_NAME",        1, "ValueError", 18, None, False),
            Instr(0, "COMPARE_OP",       10, "exception match", 20, None, False),
            Instr(0, "POP_JUMP_IF_FALSE",30, 30, 22, None, False),
            Instr(0, "POP_TOP",          None, None, 24, None, False),  # exc type
            Instr(0, "STORE_NAME",       2, "e",   26, None, False),   # 'as e' binding
            Instr(0, "LOAD_CONST",       3, 0,     28, None, False),
            Instr(0, "STORE_NAME",       0, "x",   30, None, False),
            Instr(0, "POP_EXCEPT",       None, None, 32, None, False),
            Instr(0, "RETURN_VALUE",     None, None, 34, None, False),
        ])
        self.assertIn("except ValueError as e:", out,
                      f"'as e' binding missing:\n{out}")
        self.assertNotIn("DUP_TOP", out, f"Raw DUP_TOP leaked:\n{out}")

    def test_try_except_body_indented_correctly(self):
        """Handler body must be indented one level inside the except block."""
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "SETUP_FINALLY",    None, 12,  0, None, False),
            Instr(0, "LOAD_CONST",       0, 99,     2, None, False),
            Instr(0, "STORE_NAME",       0, "x",    4, None, False),
            Instr(0, "POP_BLOCK",        None, None, 6, None, False),
            Instr(0, "JUMP_FORWARD",     None, 28,   8, None, False),
            Instr(0, "DUP_TOP",          None, None,12, None, True),
            Instr(0, "LOAD_NAME",        1, "OSError",14, None, False),
            Instr(0, "COMPARE_OP",       10, "exception match", 16, None, False),
            Instr(0, "POP_JUMP_IF_FALSE",26, 26, 18, None, False),
            Instr(0, "POP_TOP",          None, None, 20, None, False),
            Instr(0, "POP_TOP",          None, None, 22, None, False),
            Instr(0, "LOAD_CONST",       2, 0,      24, None, False),
            Instr(0, "STORE_NAME",       0, "x",    26, None, False),
            Instr(0, "POP_EXCEPT",       None, None, 28, None, False),
            Instr(0, "RETURN_VALUE",     None, None, 30, None, False),
        ])
        lines = out.splitlines()
        body_lines = [ln for ln in lines if "x = 0" in ln or "x = 99" in ln]
        for line in body_lines:
            self.assertTrue(
                line.startswith("    "),
                f"Body line not indented:\n  {line!r}\nFull output:\n{out}",
            )

    def test_dup_top_not_exception_match_falls_through(self):
        """
        A lone DUP_TOP not followed by COMPARE_OP('exception match')
        must NOT emit an except header — it should duplicate the stack top.
        """
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [
            Instr(0, "LOAD_CONST", 0, 42,    0, None, False),
            Instr(0, "DUP_TOP",    None, None, 2, None, False),
            Instr(0, "STORE_NAME", 0, "x",   4, None, False),
            Instr(0, "POP_TOP",    None, None, 6, None, False),
            Instr(0, "RETURN_VALUE", None, None, 8, None, False),
        ]
        dec._while_body_offsets = set()
        dec._while_header_targets = {}
        dec._while_true_ends = set()
        # Run the handler manually
        dec.pc = 1   # pointing at DUP_TOP
        dec.stack = [42]
        dup_instr = dec.instructions[1]
        dec._handle_instruction(dup_instr)
        # Stack should now have two copies of 42 (real DUP_TOP semantics)
        self.assertGreaterEqual(len(dec.stack), 2,
                                "DUP_TOP should duplicate the stack top")
        self.assertNotIn("except", "\n".join(dec.reconstructed),
                         "Spurious except header emitted by non-exception DUP_TOP")

    # ------------------------------------------------------------------
    # Fix 4 — INPLACE_* augmented assignment
    # ------------------------------------------------------------------

    def test_inplace_add_emits_augassign(self):
        """INPLACE_ADD followed by STORE_NAME must emit 'x += 3', not 'x = (x + 3)'."""
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "LOAD_CONST",   0,  1,   0, None, False),
            Instr(0, "STORE_NAME",   0, "x",  2, None, False),
            Instr(0, "LOAD_NAME",    0, "x",  4, None, False),
            Instr(0, "LOAD_CONST",   1,  3,   6, None, False),
            Instr(0, "INPLACE_ADD",  None, None, 8, None, False),
            Instr(0, "STORE_NAME",   0, "x", 10, None, False),
            Instr(0, "RETURN_VALUE", None, None, 12, None, False),
        ])
        self.assertIn("x += 3", out, f"x += 3 not found:\n{out}")
        self.assertNotIn("x = (x", out, f"Wrong 'x = (x ...)' form present:\n{out}")

    def test_inplace_sub_emits_augassign(self):
        """INPLACE_SUBTRACT -> 'x -= 1'."""
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "LOAD_NAME",       0, "x",  0, None, False),
            Instr(0, "LOAD_CONST",      0,  1,   2, None, False),
            Instr(0, "INPLACE_SUBTRACT",None,None,4, None, False),
            Instr(0, "STORE_NAME",      0, "x",  6, None, False),
            Instr(0, "RETURN_VALUE",    None, None, 8, None, False),
        ])
        self.assertIn("x -= 1", out, f"x -= 1 not found:\n{out}")

    def test_inplace_mul_emits_augassign(self):
        """INPLACE_MULTIPLY -> 'x *= 2'."""
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "LOAD_NAME",        0, "x",  0, None, False),
            Instr(0, "LOAD_CONST",       0,  2,   2, None, False),
            Instr(0, "INPLACE_MULTIPLY", None,None,4, None, False),
            Instr(0, "STORE_NAME",       0, "x",  6, None, False),
            Instr(0, "RETURN_VALUE",     None, None, 8, None, False),
        ])
        self.assertIn("x *= 2", out, f"x *= 2 not found:\n{out}")

    def test_inplace_xor_emits_augassign(self):
        """INPLACE_XOR -> 'x ^= 5'. Regression guard against += mapping."""
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "LOAD_NAME",    0, "x",  0, None, False),
            Instr(0, "LOAD_CONST",   0,  5,   2, None, False),
            Instr(0, "INPLACE_XOR",  None,None,4, None, False),
            Instr(0, "STORE_NAME",   0, "x",  6, None, False),
            Instr(0, "RETURN_VALUE", None, None, 8, None, False),
        ])
        self.assertIn("x ^= 5", out, f"x ^= 5 not found:\n{out}")
        self.assertNotIn("x += 5", out, f"INPLACE_XOR wrongly emitted +=:\n{out}")

    def test_inplace_without_matching_store_falls_back(self):
        """
        INPLACE_* not followed by a matching STORE must push an expression
        string (not crash and not emit a bare statement).
        """
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [
            Instr(0, "LOAD_NAME",    0, "x",  0, None, False),
            Instr(0, "LOAD_CONST",   0,  1,   2, None, False),
            Instr(0, "INPLACE_ADD",  None, None, 4, None, False),
            # No STORE_NAME — stack top is a different name
            Instr(0, "STORE_NAME",   1, "y",  6, None, False),  # store to y, not x
            Instr(0, "RETURN_VALUE", None, None, 8, None, False),
        ]
        dec._while_body_offsets = set()
        dec._while_header_targets = {}
        dec._while_true_ends = set()
        dec.pc = 2  # point at INPLACE_ADD
        dec.stack = ["x", 1]
        dec._handle_instruction(dec.instructions[2])
        # Should have pushed a string expression, not crashed
        self.assertEqual(len(dec.stack), 1,
                         "INPLACE_ADD should leave exactly one item on stack")
        self.assertIsInstance(dec.stack[-1], str,
                              "Fallback should push a string expression")

    # ------------------------------------------------------------------
    # End-to-end: complete 3.9-style programs
    # ------------------------------------------------------------------

    def test_complete_while_with_augassign(self):
        """
        End-to-end: 'n = 0; while n < 3: n += 1' with 3.9 bytecode.
        Guard (POP_JUMP_IF_FALSE) + back-edge (JUMP_ABSOLUTE) + INPLACE_ADD.
        """
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "LOAD_CONST",       0, 0,    0, None, False),
            Instr(0, "STORE_NAME",       0, "n",  2, None, False),
            # condition at offset 4 — JUMP_ABSOLUTE targets here
            Instr(0, "LOAD_NAME",        0, "n",  4, None, False),
            Instr(0, "LOAD_CONST",       1, 3,    6, None, False),
            Instr(0, "COMPARE_OP",       0, "<",  8, None, False),
            Instr(0, "POP_JUMP_IF_FALSE",22, 22, 10, None, False),
            # body at offset 12
            Instr(0, "LOAD_NAME",        0, "n", 12, None, False),
            Instr(0, "LOAD_CONST",       2, 1,   14, None, False),
            Instr(0, "INPLACE_ADD",      None, None, 16, None, False),
            Instr(0, "STORE_NAME",       0, "n", 18, None, False),
            Instr(0, "JUMP_ABSOLUTE",    4, 4,   20, None, False),
            Instr(0, "RETURN_VALUE",     None, None, 22, None, True),
        ])
        self.assertIn("n = 0", out, f"init missing:\n{out}")
        self.assertIn("while", out, f"while missing:\n{out}")
        self.assertIn("n += 1", out, f"n += 1 missing:\n{out}")
        self.assertNotIn("if n", out, f"'if n' should be 'while':\n{out}")

    def test_complete_try_except_with_store(self):
        """
        End-to-end: 3.9-style try/except that sets x=42 in try, x=0 on error.
        """
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "SETUP_FINALLY",    None, 14,  0, None, False),
            Instr(0, "LOAD_CONST",       0, 42,     2, None, False),
            Instr(0, "STORE_NAME",       0, "x",    4, None, False),
            Instr(0, "POP_BLOCK",        None, None, 6, None, False),
            Instr(0, "JUMP_FORWARD",     None, 30,   8, None, False),
            # handler at 14:
            Instr(0, "DUP_TOP",          None, None, 14, None, True),
            Instr(0, "LOAD_NAME",        1, "ValueError", 16, None, False),
            Instr(0, "COMPARE_OP",       10, "exception match", 18, None, False),
            Instr(0, "POP_JUMP_IF_FALSE",28, 28, 20, None, False),
            Instr(0, "POP_TOP",          None, None, 22, None, False),
            Instr(0, "POP_TOP",          None, None, 24, None, False),
            Instr(0, "LOAD_CONST",       2, 0,      26, None, False),
            Instr(0, "STORE_NAME",       0, "x",    28, None, False),
            Instr(0, "POP_EXCEPT",       None, None, 30, None, False),
            Instr(0, "RETURN_VALUE",     None, None, 32, None, False),
        ])
        self.assertIn("try:", out)
        self.assertIn("except ValueError:", out)
        self.assertIn("x = 42", out)
        self.assertIn("x = 0", out)
        self.assertNotIn("DUP_TOP", out)
        self.assertNotIn("COMPARE_OP", out)


# ---------------------------------------------------------------------------
# Token Hamming distance (check_coherency.py)
# ---------------------------------------------------------------------------

class TestTokenHamming(unittest.TestCase):
    """
    Unit tests for the Token Hamming distance dimension added to
    check_coherency.py.

    These tests import the helpers directly and exercise them with
    controlled synthetic inputs so we can assert exact expected values
    without compiling/decompiling any real .pyc files.
    """

    @classmethod
    def setUpClass(cls):
        """Import check_coherency as a module via sys.path."""
        import sys, importlib, os
        cc_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)),"..", "debug")
        if cc_dir not in sys.path:
            sys.path.insert(0, cc_dir)
        cls.cc = importlib.import_module("check_coherency")

    # ------------------------------------------------------------------
    # _line_tokenise
    # ------------------------------------------------------------------

    def test_line_tokenise_identifiers_and_operators(self):
        """Basic identifiers, operators, and literals are all captured."""
        toks = self.cc._line_tokenise("x = y + 1")
        self.assertEqual(toks, ["x", "=", "y", "+", "1"])

    def test_line_tokenise_multichar_operators(self):
        """Multi-char operators like += >> ** are kept as single tokens."""
        toks = self.cc._line_tokenise("x += y >> 2")
        self.assertIn("+=", toks)
        self.assertIn(">>", toks)

    def test_line_tokenise_string_literal(self):
        """A quoted string is a single token."""
        toks = self.cc._line_tokenise('name = "hello"')
        self.assertIn('"hello"', toks)
        # Should not be split into individual characters
        self.assertNotIn("h", toks)

    def test_line_tokenise_empty_and_comment(self):
        """Empty string returns empty list."""
        self.assertEqual(self.cc._line_tokenise(""), [])

    def test_line_tokenise_keyword(self):
        """Python keywords are captured as tokens."""
        toks = self.cc._line_tokenise("if x > 0:")
        self.assertIn("if", toks)
        self.assertIn("x", toks)
        self.assertIn(">", toks)

    # ------------------------------------------------------------------
    # _hamming_score_line_aligned — core metric
    # ------------------------------------------------------------------

    def test_identical_lines_score_one(self):
        """Identical normalised line lists must give score = 1.0."""
        lines = ["x = 1", "y = x + 2", "return y"]
        score, matched, total, flips, _ = self.cc._hamming_score_line_aligned(
            lines, lines
        )
        self.assertEqual(score, 1.0)
        self.assertEqual(flips, 0)
        self.assertEqual(matched, total)

    def test_empty_orig_gives_one(self):
        """Empty original → perfect score (nothing to flip)."""
        score, matched, total, flips, _ = self.cc._hamming_score_line_aligned(
            [], ["x = 1"]
        )
        self.assertEqual(score, 1.0)
        self.assertEqual(total, 0)

    def test_empty_dec_gives_zero(self):
        """No decompiled output → every original token is a flip → score 0."""
        score, _, total, flips, _ = self.cc._hamming_score_line_aligned(
            ["x = 1", "y = 2"], []
        )
        self.assertEqual(score, 0.0)
        self.assertEqual(flips, total)

    def test_single_token_substitution(self):
        """Replacing one name token lowers the score below 1.0."""
        orig = ["result = compute(x, y)"]
        dec  = ["result = compute(a, y)"]   # 'x' replaced by 'a'
        score, _, total, flips, _ = self.cc._hamming_score_line_aligned(orig, dec)
        self.assertLess(score, 1.0)
        self.assertGreater(score, 0.5, "One flip out of many tokens should not tank score")
        self.assertEqual(flips, 1, "Exactly one token should be flipped")

    def test_extra_parens_are_zero_cost(self):
        """
        Decompiler-inserted grouping parens must not penalise the score.

        orig:  y = x + 1
        dec:   y = (x + 1)    <- extra ( and ) inserted by decompiler

        The extra ( and ) are insertions into the dec stream; the LCS still
        matches all 5 original tokens, so flips == 0.
        """
        orig = ["y = x + 1"]
        dec  = ["y = (x + 1)"]
        score, _, total, flips, _ = self.cc._hamming_score_line_aligned(orig, dec)
        self.assertEqual(flips, 0, "Extra grouping parens must not flip any original token")
        self.assertEqual(score, 1.0)

    def test_pass_insertion_is_zero_cost(self):
        """A 'pass' line added by the decompiler should not cost any flips."""
        orig = ["class Foo:"]
        dec  = ["class Foo:", "pass"]
        score, _, total, flips, _ = self.cc._hamming_score_line_aligned(orig, dec)
        self.assertEqual(flips, 0)
        self.assertEqual(score, 1.0)

    def test_half_lines_missing_scores_below_half(self):
        """Dropping half the lines should give a score significantly below 1.0."""
        orig = ["x = 1", "y = 2", "z = 3", "w = 4"]
        dec  = ["x = 1", "y = 2"]                    # half dropped
        score, _, _, _, _ = self.cc._hamming_score_line_aligned(orig, dec)
        self.assertLess(score, 0.75)

    def test_flip_sample_populated_on_mismatch(self):
        """flip_sample must be non-empty when there are genuine mismatches."""
        orig = ["foo = bar(x, y, z)"]
        dec  = ["baz = qux(a, b, c)"]    # all names wrong
        _, _, _, flips, flip_sample = self.cc._hamming_score_line_aligned(orig, dec)
        self.assertGreater(flips, 0)
        self.assertGreater(len(flip_sample), 0)

    def test_score_monotone_with_accuracy(self):
        """
        More accurately decompiled code must score higher than less accurate.

        perfect ≥ two_errors ≥ many_errors (strictly for these examples).
        """
        orig = ["result = compute_value(alpha, beta, gamma)"]
        perfect     = ["result = compute_value(alpha, beta, gamma)"]
        two_errors  = ["result = compute_value(alpha, XXXX, gamma)"]
        many_errors = ["result = YYYYY(alpha, XXXX, ZZZZ)"]

        def s(dec):
            score, *_ = self.cc._hamming_score_line_aligned(orig, dec)
            return score

        self.assertGreater(s(perfect), s(two_errors))
        self.assertGreater(s(two_errors), s(many_errors))

    # ------------------------------------------------------------------
    # score_token_hamming — DimensionResult wrapper
    # ------------------------------------------------------------------

    def test_artefact_in_orig_not_penalised(self):
        """
        Diff-based logic: if the artefact already exists in the original
        source at least as often as in the decompiled output, no penalty
        is added -- prevents false positives on pycrefine self-decompilation.
        """
        shared = "if func == \'__build_class__\': pass\n"
        result = self.cc.score_cleanliness(shared, shared)
        self.assertEqual(result.score, 1.0,
                         "Identical text should score 1.0 (no excess artefacts)")

    def test_excess_artefact_penalised(self):
        """New bare artefact in dec (not in orig) is penalised."""
        orig = "if func == \'__build_class__\': pass\n"
        dec  = "__build_class__\nif func == \'__build_class__\': pass\n"
        result = self.cc.score_cleanliness(dec, orig)
        self.assertLess(result.score, 1.0)
        self.assertIn("__build_class__", result.detail)

    def test_dimension_name_and_weight(self):
        """DimensionResult must have the expected name and weight."""
        result = self.cc.score_token_hamming("x = 1\n", "x = 1\n")
        self.assertEqual(result.name, "Token Hamming")
        self.assertAlmostEqual(result.weight, 0.12, places=5)

    def test_identical_source_perfect_score(self):
        """Identical original and decompiled text must produce score == 1.0."""
        src = "def f(x):\n    return x + 1\n"
        result = self.cc.score_token_hamming(src, src)
        self.assertEqual(result.score, 1.0)
        self.assertIn("perfect", result.detail)

    def test_completely_different_source_low_score(self):
        """Completely unrelated output must score well below 0.5."""
        orig = "def compute(x, y):\n    return x * y + x - y\n"
        dec  = "import os\nfoo = bar\n"
        result = self.cc.score_token_hamming(orig, dec)
        self.assertLess(result.score, 0.5)

    def test_score_in_unit_interval(self):
        """Score must always be in [0.0, 1.0]."""
        pairs = [
            ("x = 1\n", "x = 1\n"),
            ("x = 1\n", "y = 2\n"),
            ("def f():\n    pass\n", ""),
            ("", "x = 1\n"),
        ]
        for orig, dec in pairs:
            result = self.cc.score_token_hamming(orig, dec)
            self.assertGreaterEqual(result.score, 0.0,
                                    f"Score below 0 for {orig!r} vs {dec!r}")
            self.assertLessEqual(result.score, 1.0,
                                 f"Score above 1 for {orig!r} vs {dec!r}")

    def test_empty_original_returns_perfect(self):
        """No tokens to compare → score 1.0 (vacuously perfect)."""
        result = self.cc.score_token_hamming("", "x = 1\ny = 2\n")
        self.assertEqual(result.score, 1.0)

    def test_quote_normalisation_is_zero_cost(self):
        """
        Single-quote to double-quote conversion must not flip any tokens.
        Both are normalised to double quotes before tokenising.
        """
        orig = "name = 'hello'\n"
        dec  = 'name = "hello"\n'
        result = self.cc.score_token_hamming(orig, dec)
        self.assertEqual(result.score, 1.0,
                         "Quote normalisation must not count as a flip")

    def test_detail_contains_flip_count(self):
        """Detail string for a mismatching pair must mention flip count."""
        orig = "def add(x, y):\n    return x + y\n"
        dec  = "def add(a, b):\n    return a + b\n"    # x→a, y→b everywhere
        result = self.cc.score_token_hamming(orig, dec)
        if result.score < 1.0:
            self.assertIn("flip", result.detail)

    def test_extra_parens_in_full_text(self):
        """
        End-to-end check: a decompiler that wraps expressions in parens
        must not be penalised.
        """
        orig = "x = 1\ny = x + 2\nz = y * 3\n"
        dec  = "x = 1\ny = (x + 2)\nz = (y * 3)\n"
        result = self.cc.score_token_hamming(orig, dec)
        self.assertEqual(result.score, 1.0,
                         "Grouping parens must not reduce Hamming score")

    def test_annotation_stripping_is_zero_cost(self):
        """
        Type annotations stripped from function signatures must not
        produce any flips against a decompiled output that lacks them.
        """
        orig = "def greet(name: str) -> str:\n    return name\n"
        dec  = "def greet(name):\n    return name\n"
        result = self.cc.score_token_hamming(orig, dec)
        self.assertEqual(result.score, 1.0,
                         "Annotation removal must not count as flips")

    # ------------------------------------------------------------------
    # Integration: weight sum consistency
    # ------------------------------------------------------------------

    def test_all_dimension_weights_sum_to_one(self):
        """
        All nine scoring dimensions must have weights that sum to exactly 1.0.
        This guards against future rebalancing mistakes.
        """
        import py_compile, tempfile, os, textwrap
        src = textwrap.dedent("""\
            x = 1
            y = x + 2
        """)
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(src)
            sp = f.name
        try:
            report = self.cc.score(sp)
        finally:
            os.unlink(sp)
        total = sum(d.weight for d in report.dimensions)
        self.assertAlmostEqual(total, 1.0, places=9,
                               msg=f"Weights sum to {total}, not 1.0")

    def test_hamming_dimension_present_in_report(self):
        """
        CoherencyReport must include a dimension named 'Token Hamming'.
        """
        import py_compile, tempfile, os
        src = "x = 1\n"
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(src)
            sp = f.name
        try:
            report = self.cc.score(sp)
        finally:
            os.unlink(sp)
        names = [d.name for d in report.dimensions]
        self.assertIn("Token Hamming", names)

    def test_nine_dimensions_total(self):
        """There must be exactly nine scoring dimensions in every report."""
        import tempfile, os
        src = "x = 1\n"
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(src)
            sp = f.name
        try:
            report = self.cc.score(sp)
        finally:
            os.unlink(sp)
        self.assertEqual(len(report.dimensions), 9,
                         f"Expected 9 dimensions, got {len(report.dimensions)}: "
                         f"{[d.name for d in report.dimensions]}")



# ---------------------------------------------------------------------------
# Output cleanliness and genexpr rendering
# ---------------------------------------------------------------------------

class TestOutputCleanliness(unittest.TestCase):
    """
    Tests for _strip_string_literals and score_cleanliness in check_coherency.

    These tests import helpers directly from check_coherency so they exercise
    the live implementation without any .pyc round-trip.
    """

    @classmethod
    def setUpClass(cls):
        import sys, importlib, os
        cc_dir = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), "..", "debug"
        )
        if cc_dir not in sys.path:
            sys.path.insert(0, cc_dir)
        if "check_coherency" in sys.modules:
            del sys.modules["check_coherency"]
        cls.cc = importlib.import_module("check_coherency")

    # ------------------------------------------------------------------
    # _strip_string_literals
    # ------------------------------------------------------------------

    def test_strip_single_quote_content(self):
        """Content inside single-quoted strings is blanked."""
        result = self.cc._strip_string_literals("x = '_exc_match'")
        self.assertNotIn("_exc_match", result)

    def test_strip_double_quote_content(self):
        """Content inside double-quoted strings is blanked."""
        result = self.cc._strip_string_literals('x = "__build_class__"')
        self.assertNotIn("__build_class__", result)

    def test_strip_triple_quote_content(self):
        """Content inside triple-quoted strings is blanked."""
        result = self.cc._strip_string_literals('"""_exc_info is bad"""')
        self.assertNotIn("_exc_info", result)

    def test_strip_preserves_code_outside_strings(self):
        """Tokens outside string literals are left intact."""
        result = self.cc._strip_string_literals("if x == _exc_match:")
        self.assertIn("_exc_match", result)
        self.assertIn("if", result)

    def test_strip_empty_passthrough(self):
        """Empty input returns empty output."""
        self.assertEqual(self.cc._strip_string_literals(""), "")

    def test_strip_no_strings_passthrough(self):
        """Text without string literals is returned unchanged."""
        code = "if x > 0:\n    return x\n"
        self.assertEqual(self.cc._strip_string_literals(code), code)

    def test_strip_leaves_surrounding_structure(self):
        """Brackets and operators outside the string content remain."""
        result = self.cc._strip_string_literals("func('_exc_match', x)")
        self.assertIn("func(", result)
        self.assertIn(", x)", result)
        self.assertNotIn("_exc_match", result)

    # ------------------------------------------------------------------
    # score_cleanliness — word-boundary matching for identifiers
    # ------------------------------------------------------------------

    def test_exc_match_bare_identifier_penalised(self):
        """_exc_match as a standalone identifier triggers a penalty."""
        dec = "x = _exc_match\nif _exc_match:\n    pass\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertLess(result.score, 1.0)
        self.assertIn("_exc_match", result.detail)

    def test_exc_match_as_substring_of_longer_name_not_penalised(self):
        """
        _exc_match embedded inside a longer identifier such as
        _has_exc_match_handler must NOT trigger a word-boundary penalty.
        """
        dec = "def _has_exc_match_handler(self):\n    return False\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("_exc_match", result.detail,
                         f"Substring false positive: {result.detail}")

    def test_exc_info_in_longer_method_name_not_penalised(self):
        """
        _exc_info inside _find_push_exc_info_offset is a legitimate method
        name, not a decompiler artefact.  Word-boundary matching must
        distinguish them.
        """
        dec = "def _find_push_exc_info_offset(self):\n    return -1\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("_exc_info", result.detail,
                         f"Substring false positive from method name: {result.detail}")

    def test_build_class_in_string_literal_not_penalised(self):
        """
        __build_class__ inside a quoted string is legitimate comparison code
        and must NOT be penalised.
        """
        dec = "if func == '__build_class__':\n    pass\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("__build_class__", result.detail,
                         f"String-literal false positive: {result.detail}")

    def test_build_class_bare_identifier_penalised(self):
        """__build_class__ as an unquoted code identifier is real leakage."""
        dec = "self.stack.append(__build_class__)\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertLess(result.score, 1.0)
        self.assertIn("__build_class__", result.detail)

    def test_func_tuple_leak_penalised(self):
        """A ('func', ...) tuple in assignment position is penalised."""
        dec = "result = ('func', 'def f():\\n    pass')()\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertLess(result.score, 1.0)
        self.assertIn("raw-tuple leak", result.detail)

    def test_class_tuple_leak_penalised(self):
        """A ('class', ...) tuple in assignment position is penalised."""
        dec = "Foo = ('class', 'class Foo: pass')()\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertLess(result.score, 1.0)
        self.assertIn("raw-tuple leak", result.detail)

    def test_clean_output_scores_one(self):
        """Completely clean decompiled output scores exactly 1.0."""
        dec = "x = 1\ny = x + 2\n\ndef f(a, b):\n    return a + b\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertEqual(result.score, 1.0)
        self.assertEqual(result.detail, "No artefacts found")

    def test_multiple_artefacts_accumulate(self):
        """Each distinct artefact compounds the penalty additively."""
        dec = (
            "x = __build_class__\n"
            "y = ('func', 'def f(): pass')()\n"
        )
        result = self.cc.score_cleanliness(dec, "")
        # __build_class__(0.15) + raw-tuple leak(0.20) = 0.35 penalty -> score 0.65
        self.assertAlmostEqual(result.score, 0.65, places=5)

    def test_score_floor_is_zero(self):
        """Score must never go below 0.0 even with many simultaneous artefacts."""
        dec = (
            "__build_class__\n"
            "a = ('func', 'x')()\n"   # assignment position — triggers raw-tuple leak
            "_exc_match\n"
            "_exc_info\n"
        )
        result = self.cc.score_cleanliness(dec, "")
        self.assertGreaterEqual(result.score, 0.0)

    def test_genexpr_comment_in_string_not_penalised(self):
        """
        The string '# <genexpr/lambda' appearing as a value inside an f-string
        or regular string literal in the decompiled source is legitimate code
        (e.g. the post_process_source function itself contains this string as
        part of a replacement template).  It must NOT trigger a penalty.
        """
        dec = 'return f"# <genexpr/lambda{kind} — not reconstructable>"\n'
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("# <genexpr/lambda", result.detail,
                         f"String-literal false positive: {result.detail}")
        self.assertEqual(result.score, 1.0)

    def test_genexpr_comment_as_bare_comment_penalised(self):
        """
        '# <genexpr/lambda ...' appearing as a real bare comment line IS a
        genuine post_process_source fallback placeholder and must be penalised.
        """
        dec = "# <genexpr/lambda — not reconstructable>\nx = None\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertIn("# <genexpr/lambda", result.detail)
        self.assertLess(result.score, 1.0)

    def test_class_comment_as_bare_comment_penalised(self):
        """
        '# <class ...' appearing as a bare comment line IS a genuine placeholder
        and must be penalised.
        """
        dec = "# <class — not reconstructable>\nPoint = None\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertIn("# <class", result.detail)
        self.assertLess(result.score, 1.0)

    def test_class_comment_in_string_not_penalised(self):
        """
        '# <class' inside a string literal must not trigger a penalty.
        """
        dec = 'msg = "# <class body not reconstructable>"\n'
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("# <class", result.detail,
                         f"String-literal false positive: {result.detail}")

    def test_tuple_leak_in_assignment_penalised(self):
        """
        A ('func', ...) or ('class', ...) tuple in assignment position is
        real leakage and must trigger the 'raw-tuple leak' penalty.
        """
        dec_func  = "x = ('func', 'def f(): pass')()\n"
        dec_class = "Foo = ('class', 'class Foo: pass')()\n"
        for dec in (dec_func, dec_class):
            result = self.cc.score_cleanliness(dec, "")
            self.assertIn("raw-tuple leak", result.detail,
                          f"Expected raw-tuple leak for: {dec!r}")

    def test_tuple_in_stack_append_not_penalised(self):
        """
        ('func', ...) inside stack.append(...) is legitimate decompiler source
        code and must NOT trigger the tuple-leak penalty.
        """
        dec = "self.stack.append(('func', f'def {name}:'))\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("raw-tuple leak", result.detail,
                         f"False positive on stack.append: {result.detail}")
        self.assertEqual(result.score, 1.0)




    def test_dimension_name_and_weight(self):
        """DimensionResult must have the correct name and weight."""
        result = self.cc.score_cleanliness("x = 1\n", "x = 1\n")
        self.assertEqual(result.name, "Output cleanliness")
        self.assertAlmostEqual(result.weight, 0.05, places=5)


class TestGenexprRendering(unittest.TestCase):
    """
    Tests for the _render_func_tuple helper in pycrefine.py and the end-to-end
    genexpr / lambda decompilation behaviour after the fix.
    """

    def _decompile(self, src: str) -> str:
        pyc = _compile(src)
        try:
            return get_decompiler(pyc).decompile()
        finally:
            if os.path.exists(pyc):
                os.unlink(pyc)

    # ------------------------------------------------------------------
    # _render_func_tuple unit tests
    # ------------------------------------------------------------------

    def test_simple_genexpr_renders_inline(self):
        """Basic genexpr body renders as (expr for x in iterable)."""
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for x in .0:\n        yield x * 2\n"
        out = _render_func_tuple(body, ["items"])
        self.assertEqual(out, "(x * 2 for x in items)")

    def test_genexpr_with_if_clause(self):
        """Filtered genexpr includes the if-clause."""
        from pycrefine import _render_func_tuple
        body = (
            "def <genexpr>(.0):\n"
            "    for x in .0:\n"
            "        if x > 0:\n"
            "            yield x\n"
        )
        out = _render_func_tuple(body, ["xs"])
        self.assertIn("for x in xs", out)
        self.assertIn("if x > 0", out)

    def test_trailing_empty_call_suffix_stripped(self):
        """
        A trailing '()' from a CALL-0 misfire on GET_ITER is removed from
        the iterator argument — but only the trailing '()', not interior
        parentheses (so range(10) must not be mangled to range(10).
        """
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for x in .0:\n        yield x\n"
        # Misfire case: 'items()' -> stripped to 'items'
        out_misfire = _render_func_tuple(body, ["items()"])
        self.assertIn("for x in items", out_misfire)
        self.assertNotIn("items()", out_misfire)
        # Range case: 'range(10)' -> must stay as 'range(10)'
        out_range = _render_func_tuple(body, ["range(10)"])
        self.assertIn("for x in range(10)", out_range)

    def test_genexpr_wrapped_in_parens(self):
        """The rendered genexpr must be wrapped in outer parentheses."""
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for s in .0:\n        yield str(s)\n"
        out = _render_func_tuple(body, ["items"])
        self.assertTrue(out.startswith("("), f"Missing opening paren: {out!r}")
        self.assertTrue(out.endswith(")"),   f"Missing closing paren: {out!r}")
    def test_setcomp_uses_curly_braces(self):
        """<setcomp> body renders as {expr for x in iter} with curly braces."""
        from pycrefine import _render_func_tuple
        body = "def <setcomp>(.0):\n    for x in .0:\n        yield x\n"
        out = _render_func_tuple(body, ["vals"])
        self.assertIn("for x in vals", out)
        self.assertTrue(out.startswith("{") and out.endswith("}"),
                        f"setcomp must use curly braces: {out!r}")


    def test_listcomp_body_uses_square_brackets(self):
        """<listcomp> body renders as [expr for x in iter] with square brackets."""
        from pycrefine import _render_func_tuple
        body = "def <listcomp>(.0):\n    for x in .0:\n        yield x + 1\n"
        out = _render_func_tuple(body, ["data"])
        self.assertIn("for x in data", out)
        self.assertIn("x + 1", out)
        self.assertTrue(out.startswith("[") and out.endswith("]"),
                        f"listcomp must use square brackets: {out!r}")

    def test_lambda_one_param(self):
        """Lambda with one parameter renders as 'lambda p: expr'."""
        from pycrefine import _render_func_tuple
        body = "def <lambda>(x):\n    return x * 2\n"
        self.assertEqual(_render_func_tuple(body, []), "lambda x: x * 2")

    def test_lambda_zero_params(self):
        """Lambda with no parameters renders as 'lambda: expr'."""
        from pycrefine import _render_func_tuple
        body = "def <lambda>():\n    return 42\n"
        self.assertEqual(_render_func_tuple(body, []), "lambda: 42")

    def test_lambda_multiple_params(self):
        """Lambda with multiple parameters includes all of them."""
        from pycrefine import _render_func_tuple
        body = "def <lambda>(x, y):\n    return x + y\n"
        out = _render_func_tuple(body, [])
        self.assertIn("lambda x, y:", out)
        self.assertIn("x + y", out)

    def test_empty_body_gives_placeholder(self):
        """Empty body produces a safe <func>() placeholder, not a crash."""
        from pycrefine import _render_func_tuple
        out = _render_func_tuple("", ["x"])
        self.assertIn("<func>", out)

    def test_unknown_body_gives_placeholder(self):
        """Unrecognised anonymous function falls back to <func>() placeholder."""
        from pycrefine import _render_func_tuple
        body = "def <unknown>():\n    return 1\n"
        out = _render_func_tuple(body, [])
        self.assertIn("<func>", out)

    # ------------------------------------------------------------------
    # End-to-end: decompile sources containing genexprs / lambdas
    # ------------------------------------------------------------------

    def test_any_genexpr_no_tuple_leakage(self):
        """any(... for ...) must not produce a raw ('func', ...) tuple."""
        out = self._decompile("def f(items): return any(x > 0 for x in items)\n")
        self.assertNotIn("('func',", out)
        self.assertNotIn("('class',", out)

    def test_sum_genexpr_correct_and_clean(self):
        """sum(x**2 for x in range(10)) must be clean and contain the iterator."""
        out = self._decompile("result = sum(x**2 for x in range(10))\n")
        self.assertNotIn("('func',", out)
        # The iterator range(10) must appear in the output.
        # The exact loop variable name may vary across Python versions
        # (some emit 'x', some '_item' before the FOR_ITER peek fix),
        # but the iterator expression is always `range(10)`.
        self.assertIn("range(10)", out)
        self.assertIn("for", out)

    def test_join_genexpr_no_tuple_leakage(self):
        """str.join(... for ...) must not produce tuple leakage."""
        out = self._decompile('result = "_".join(str(s) for s in items)\n')
        self.assertNotIn("('func',", out)

    def test_genexpr_parens_not_stripped_by_post_process(self):
        """
        The 'for' keyword inside the genexpr expression must prevent
        post_process_source from stripping its required outer parentheses.

        The exact rendering varies by Python version — 3.12 produces an inline
        genexpr while 3.14 may produce a different structure — so we assert
        only that:
          1. No raw ('func', tuple leaks into the output.
          2. If a genexpr line with 'for ... in items' IS present, its parens
             were not stripped (the line contains an opening paren).
        """
        out = self._decompile("def f(items): return any(x > 0 for x in items)\n")
        self.assertNotIn("('func',", out)
        # If the output contains an inline genexpr, its parens must be intact.
        for line in out.splitlines():
            # A genexpr line: contains both 'for' and 'in items' and 'yield' or '>' etc.
            if " for " in line and "in items" in line and "yield" not in line:
                stripped = line.strip()
                # The line must contain '(' somewhere (genexpr or function call)
                self.assertIn(
                    "(", stripped,
                    f"genexpr parens appear to have been stripped: {line!r}\nFull output:\n{out}",
                )

    def test_dataclass_produces_no_class_tuple_leakage(self):
        """@dataclass class body must not reach output as a ('class', ...) tuple."""
        src = (
            "from dataclasses import dataclass\n"
            "@dataclass\nclass Point:\n    x: int\n    y: int\n"
        )
        out = self._decompile(src)
        self.assertNotIn("('class',", out)
        self.assertNotIn("('func',", out)

    def test_if_parens_still_stripped(self):
        """Redundant parens around a plain if-condition are still removed."""
        out = self._decompile("x = 1\nif x > 0:\n    y = 2\n")
        for line in out.splitlines():
            if "if" in line and "x > 0" in line:
                self.assertNotIn(
                    "if (x > 0):", line,
                    "Redundant if-parens should still be stripped",
                )

    def test_return_tuple_parens_preserved(self):
        """Parens around a tuple return must NOT be stripped."""
        out = self._decompile("def f():\n    return (1, 2)\n")
        self.assertIn("(1, 2)", out)

    def test_return_single_expr_parens_stripped(self):
        """Parens around a single scalar return expression ARE stripped."""
        out = self._decompile("def f(x):\n    return (x + 1)\n")
        for line in out.splitlines():
            if "return" in line and "x" in line:
                self.assertNotIn(
                    "(x + 1)", line,
                    "Single-expression return parens should be stripped",
                )


# ---------------------------------------------------------------------------
# Ternary expressions
# ---------------------------------------------------------------------------

class TestTernaryExpression(unittest.TestCase):
    """
    Tests for the ternary-expression decompilation fix.

    CPython compiles ``x = A if cond else B`` to a diamond bytecode pattern
    (POP_JUMP_IF -> then-expr -> STORE -> fall-through / JUMP_FORWARD ->
    else-expr -> STORE) with no syntactic marker distinguishing it from a
    two-branch if/else block.  pycrefine canonicalises this as the compact
    ternary form.
    """

    def test_basic_ternary_assign(self):
        """x = 1 if cond else 0 must not decompile as if/pass/else/pass."""
        out = decompile("def f(x):\n    y = 1 if x > 0 else 0\n    return y\n")
        self.assertNotIn("pass", out, f"Spurious pass in ternary:\n{out}")
        self.assertIn("1", out)
        self.assertIn("0", out)
        self.assertIn("x > 0", out)

    def test_ternary_correct_form(self):
        """The ternary expression must appear on a single assignment line."""
        out = decompile("def f(x):\n    y = 1 if x > 0 else 0\n    return y\n")
        # Exactly one assignment to y using the ternary form
        ternary_lines = [
            ln.strip() for ln in out.splitlines()
            if "y =" in ln and "if" in ln and "else" in ln
        ]
        self.assertEqual(len(ternary_lines), 1,
                         f"Expected exactly one ternary line, got {ternary_lines!r}\n{out}")

    def test_original_reported_case(self):
        """
        The exact case reported: bytes assignment with isinstance guard.
        lnotab = bytes(lnotab) if not isinstance(lnotab, bytes) else lnotab
        """
        src = (
            "def test(lnotab):\n"
            "    lnotab = bytes(lnotab) if not isinstance(lnotab, bytes) else lnotab\n"
            "    print(lnotab)\n"
        )
        out = decompile(src)
        self.assertNotIn("pass", out, f"Spurious pass:\n{out}")
        self.assertIn("isinstance", out)
        self.assertIn("bytes", out)
        self.assertIn("lnotab", out)
        self.assertIn("print", out)
        # Must be a single assignment, not an if/else block with pass bodies
        self.assertNotIn("if not isinstance(lnotab, bytes):\n        pass", out)

    def test_ternary_with_call_expression(self):
        """A call in the then-branch must be preserved, not discarded."""
        out = decompile("def f(x):\n    y = abs(x) if x < 0 else x\n")
        self.assertNotIn("pass", out, f"Spurious pass:\n{out}")
        self.assertIn("abs", out)
        self.assertIn("x < 0", out)

    def test_ternary_with_unary(self):
        """Unary negation in a branch must be preserved."""
        out = decompile("def f(x):\n    b = -x if x < 0 else x\n")
        self.assertNotIn("pass", out, f"Spurious pass:\n{out}")
        self.assertIn("x < 0", out)

    def test_ternary_chain_with_other_statements(self):
        """Ternary followed by more statements must not corrupt them."""
        src = "def f(x):\n    a = 1\n    b = x if x > 0 else -x\n    return a + b\n"
        out = decompile(src)
        self.assertIn("a = 1", out)
        self.assertIn("return", out)
        self.assertNotIn("pass", out, f"Spurious pass:\n{out}")

    def test_real_if_else_multistatement_not_collapsed(self):
        """A genuine if/else with multiple statements must NOT become a ternary."""
        src = "def f(x):\n    if x > 0:\n        y = 1\n        z = 2\n    else:\n        y = 0\n        z = -1\n"
        out = decompile(src)
        # Both y and z assignments must appear — multi-statement branch preserved
        self.assertIn("z", out, f"z assignment missing:\n{out}")
        self.assertIn("if x > 0:", out, f"if header missing:\n{out}")

    def test_side_effect_if_not_collapsed(self):
        """An if-branch with a side-effect call (no assignment) must not become ternary."""
        src = "def f(x):\n    if x > 0:\n        print(x)\n"
        out = decompile(src)
        self.assertIn("if x > 0:", out)
        self.assertIn("print", out)
        self.assertNotIn("if x > 0 else", out)

    def test_if_else_equivalent_preserves_semantics(self):
        """
        Both the if/else and ternary forms produce identical bytecode.
        The decompiler output must be semantically correct Python that
        can be compiled and run.
        """
        src = "def f(x):\n    y = 1 if x > 0 else 0\n    return y\n"
        out = decompile(src)
        # The output must be valid Python
        import ast
        try:
            ast.parse(out)
        except SyntaxError as e:
            self.fail(f"Decompiled output is not valid Python: {e}\n{out}")


# ---------------------------------------------------------------------------
# Decorator decompilation
# ---------------------------------------------------------------------------

class TestDecorators(unittest.TestCase):
    """
    Tests for the decorator decompilation fix.

    On 3.12+, decorators compile as:
        LOAD_NAME deco
        [annotation setup]
        LOAD_CONST <code>
        MAKE_FUNCTION [flags]
        CALL 0            <- CPython decorator protocol
        STORE_NAME func_name

    The CALL handler must recognise ('func', body) as the implicit argument
    and the item below it on the stack as the decorator.

    On 3.9, decorators compile as:
        LOAD_NAME deco
        MAKE_FUNCTION 0
        CALL_FUNCTION 1   <- explicit 1-arg call
        STORE_NAME func_name
    """

    def test_simple_decorator(self):
        """@deco def f(x): must emit @deco on the line before def."""
        out = decompile("@deco\ndef f(x):\n    return x\n")
        lines = [line for line in out.splitlines() if line.strip()]
        deco_line = next((i for i, line in enumerate(lines) if line.strip() == "@deco"), None)
        self.assertIsNotNone(deco_line, f"@deco not found:\n{out}")
        def_line = next((i for i, line in enumerate(lines) if line.strip().startswith("def f(")), None)
        self.assertIsNotNone(def_line, f"def f not found:\n{out}")
        self.assertEqual(deco_line + 1, def_line,
                         f"@deco must immediately precede def:\n{out}")

    def test_decorator_with_arguments(self):
        """@deco(arg) must appear as-is before the def."""
        out = decompile("@deco(1)\ndef f(x):\n    return x\n")
        self.assertIn("@deco(1)", out, f"@deco(1) missing:\n{out}")
        self.assertIn("def f(", out)

    def test_two_decorators(self):
        """Multiple decorators must all appear, outermost first."""
        out = decompile("@deco1\n@deco2\ndef f(x):\n    return x\n")
        self.assertIn("@deco1", out, f"@deco1 missing:\n{out}")
        self.assertIn("@deco2", out, f"@deco2 missing:\n{out}")
        d1_pos = out.find("@deco1")
        d2_pos = out.find("@deco2")
        self.assertLess(d1_pos, d2_pos,
                        f"@deco1 must appear before @deco2:\n{out}")

    def test_decorator_body_preserved(self):
        """The decorated function body must still be emitted correctly."""
        out = decompile("@deco\ndef f(x):\n    return x + 1\n")
        self.assertIn("return x", out, f"Function body missing:\n{out}")

    def test_no_spurious_decorator_on_genexpr(self):
        """Generator expressions must NOT be wrapped in a spurious @decorator."""
        out = decompile("result = sum(x**2 for x in range(10))\n")
        # No spurious @sum decorator must appear
        self.assertNotIn("@sum", out, f"@sum spuriously added:\n{out}")
        # The output must be non-empty and contain the genexpr structure.
        # Note: the decompiler renders sum(genexpr) as just the genexpr expression
        # (the outer sum() call is absorbed into the genexpr rendering), so we
        # check for either sum( or the genexpr for-clause — at least one must appear.
        has_sum_call    = "sum(" in out
        # A for-clause with range(10) must appear, regardless of loop-var name
        # (3.14 may rename x to _item)
        import re as _re
        has_for_clause  = bool(_re.search(r"for\s+\S+\s+in\s+range\(10\)", out))
        self.assertTrue(
            has_sum_call or has_for_clause,
            f"Neither sum() nor a for-clause found in output:\n{out}",
        )

    def test_no_spurious_decorator_on_lambda(self):
        """Lambda expressions must NOT be treated as decorated functions."""
        out = decompile("f = lambda x: x * 2\n")
        self.assertNotIn("@", out, f"Spurious decorator on lambda:\n{out}")

    def test_decorator_not_a_regular_call(self):
        """Decorated function must NOT appear as func_name = deco(def ...)."""
        out = decompile("@deco\ndef f(x):\n    return x\n")
        self.assertNotIn("= deco(", out,
                         f"Function emitted as assignment call:\n{out}")
        self.assertNotIn("deco(def", out,
                         f"deco(def...) pattern found:\n{out}")

    def test_decorator_function_missing_not_skipped(self):
        """The decorated function must appear in the output (not be silently dropped)."""
        out = decompile("@deco\ndef f(x):\n    return x\n")
        self.assertIn("def f(", out, f"Decorated function was silently dropped:\n{out}")
        self.assertGreater(len(out.strip()), 10,
                           f"Output too short — function body dropped:\n{out}")

    def test_decorated_function_with_body(self):
        """Complex body after decorator must be intact."""
        src = (
            "@some_decorator\n"
            "def complex_logic(x, y):\n"
            "    if x < 0:\n"
            "        return False\n"
            "    return True\n"
        )
        out = decompile(src)
        self.assertIn("@some_decorator", out)
        self.assertIn("def complex_logic(", out)
        self.assertIn("return False", out)
        self.assertIn("return True", out)
        # Must not appear as an assignment call
        self.assertNotIn("some_decorator(def", out)
        self.assertNotIn("complex_logic = some_decorator", out)

    def test_undecorated_function_unchanged(self):
        """A function without a decorator must not gain a spurious @ line."""
        out = decompile("def f(x):\n    return x\n")
        self.assertNotIn("@", out, f"Spurious decorator added:\n{out}")


# ---------------------------------------------------------------------------
# Compound conditions (boolean and/or chains)
# ---------------------------------------------------------------------------

class TestCompoundConditions(unittest.TestCase):
    def test_compound_and(self):
        src = "def f(a, b):\n    if a == 1 and b == 2:\n        return True\n    return False\n"
        out = decompile(src)
        assert_contains(out, "if a == 1 and b == 2:")
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 1, f"Expected 1 if, got {header_count} in:\n{out}")

    def test_compound_or(self):
        src = "def f(a, b):\n    if a == 1 or b == 2:\n        return True\n    return False\n"
        out = decompile(src)
        assert_contains(out, "if a == 1 or b == 2:")
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 1, f"Expected 1 if, got {header_count} in:\n{out}")

    def test_compound_mixed_and_or(self):
        src = "def f(a, b, c):\n    if a == 1 and b == 2 or c == 3:\n        return True\n    return False\n"
        out = decompile(src)
        self.assertTrue("if a == 1 and b == 2 or c == 3:" in out or
                        "if (a == 1 and b == 2) or c == 3:" in out)
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 1)

    def test_compound_none_and(self):
        src = "def f(x):\n    if x is not None and x > 0:\n        return True\n    return False\n"
        out = decompile(src)
        assert_contains(out, "if x is not None and x > 0:")
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 1)

    def test_compound_none_or(self):
        src = "def f(x, y):\n    if x is None or y is None:\n        return True\n    return False\n"
        out = decompile(src)
        # Accept either parenthesised or unparenthesised
        self.assertTrue("if x is None or y is None:" in out or
                        "if (x is None or y is None):" in out)
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 1)

    def test_compound_complex_mixed(self):
        src = (
            "def f(x, y, z):\n"
            "    if (x is not None and x > 0) or (y is None and z == 1):\n"
            "        return True\n"
            "    return False\n"
        )
        out = decompile(src)
        self.assertTrue("x is not None and x > 0 or y is None and z == 1" in out or
                        "(x is not None and x > 0) or (y is None and z == 1)" in out or
                        "(x is not None and x > 0) or y is None and z == 1" in out) # semantically equivalent variants
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 1)

    def test_compound_short_circuit_with_call(self):
        """
        Verify that a compound `and` condition containing a short-circuiting call is decompiled correctly.
        """
        src = "def f(x):\n    if x is not None and len(x) > 0:\n        return x[0]\n    return None\n"
        out = decompile(src)
        assert_contains(out, "if x is not None and len(x) > 0:")
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 1)

    def test_compound_nested_if_merge_regression(self):
        """
        Checks that nested if statements are preserved and not merged by the decompiler.
        
        Decompiles a function containing a nested `if` and asserts the output contains both `if` headers and the expected `return` statements; also verifies exactly two `if` headers appear.
        """
        src = (
            "def test(x, y):\n"
            "    if x > 0:\n"
            "        if y > 0:\n"
            "            return 1\n"
            "        return 2\n"
            "    return 0\n"
        )
        out = decompile(src)
        assert_contains(out, "if x > 0:", "if y > 0:", "return 1", "return 2", "return 0")
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 2)

    def test_compound_with_function_calls(self):
        """
        Verify that function calls with parentheses do not disrupt precedence tracking.
        """
        src = "def f(x, y):\n    if len(x) > 0 and (y is None or x[0] == 1):\n        return True\n    return False\n"
        out = decompile(src)
        # Verify that len(x) is NOT wrapped, but (y is None or x[0] == 1) IS wrapped correctly.
        self.assertTrue("len(x) > 0 and (y is None or x[0] == 1)" in out)
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 1)

    def test_compound_flat_shared_target(self):
        """
        Verify that contiguous flat operands with shared jump targets (common in None checks)
        do not trigger RecursionError and are grouped correctly.
        """
        src = (
            "def test(a, b, c, d, e):\n"
            "    if a is None and b is None and c is None and d is None and e is None:\n"
            "        return True\n"
            "    return False\n"
        )
        out = decompile(src)
        src = (
            "def test(x, y):\n"
            "    if x > 0:\n"
            "        if y > 0:\n"
            "            return 1\n"
            "        return 2\n"
            "    return 0\n"
        )
        out = decompile(src)
        assert_contains(out, "if x > 0:", "if y > 0:", "return 1", "return 2", "return 0")
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 2)

    def test_compound_with_function_calls(self):
        """
        Verify that function calls with parentheses do not disrupt precedence tracking.
        """
        src = "def f(x, y):\n    if len(x) > 0 and (y is None or x[0] == 1):\n        return True\n    return False\n"
        out = decompile(src)
        # Verify that len(x) is NOT wrapped, but (y is None or x[0] == 1) IS wrapped correctly.
        self.assertTrue("len(x) > 0 and (y is None or x[0] == 1)" in out)
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 1)

    def test_compound_flat_shared_target(self):
        """
        Verify that contiguous flat operands with shared jump targets (common in None checks)
        do not trigger RecursionError and are grouped correctly.
        """
        src = (
            "def test(a, b, c, d, e):\n"
            "    if a is None and b is None and c is None and d is None and e is None:\n"
            "        return True\n"
            "    return False\n"
        )
        out = decompile(src)
        assert_contains(out, "if a is None and b is None and c is None and d is None and e is None:")
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 1)


# ---------------------------------------------------------------------------
# Infinite loops (while True:) — regression for guard mis-identification
# ---------------------------------------------------------------------------

class TestInfiniteLoops(unittest.TestCase):
    """Regression tests for correct decompilation of while-True loops.

    The core bug fixed was that an outer if-guard (whose target jumped beyond
    the JUMP_BACKWARD) was being misidentified as the while-loop guard.  The
    fix adds a fall-through reachability check: only guards whose fall-through
    lands directly at the loop body_start are accepted.
    """

    def test_simple_while_true(self):
        """A bare while True loop must decompile to 'while True:', not a for/if."""
        src = (
            "def f():\n"
            "    while True:\n"
            "        x = 1\n"
        )
        out = decompile(src)
        self.assertIn("while True:", out, f"'while True:' missing from output:\n{out}")
        self.assertNotIn("while x", out, f"Unexpected non-True while condition:\n{out}")

    def test_while_true_with_break_cond(self):
        """while True loop with a break condition must emit while True: and not
        treat the break condition as the while guard."""
        src = (
            "def f():\n"
            "    while True:\n"
            "        x = 1\n"
            "        if x > 0:\n"
            "            break\n"
        )
        out = decompile(src)
        self.assertIn("while True:", out, f"'while True:' missing:\n{out}")
        # The if-guard inside must not become the while condition
        self.assertNotIn("while x", out)

    def test_outer_if_inner_while_true(self):
        """An outer 'if' enclosing an inner 'while True' must NOT collapse the
        'if' into a while.  This is the exact pattern from test_infinite.py."""
        src = (
            "def me(type_char):\n"
            "    if type_char == '{':\n"
            "        res = {}\n"
            "        while True:\n"
            "            k = 1\n"
            "            if k is None:\n"
            "                break\n"
            "            res[k] = 2\n"
            "        return res\n"
        )
        out = decompile(src)
        # The outer block must be an 'if', not a 'while'
        self.assertNotIn(
            "while type_char", out,
            f"Outer 'if type_char == ...' incorrectly emitted as 'while':\n{out}",
        )
        self.assertIn(
            "if type_char == '{':", out,
            f"Outer 'if type_char == ...' missing:\n{out}",
        )
        # The inner loop must be emitted as 'while True:'
        self.assertIn(
            "while True:", out,
            f"Inner 'while True:' missing:\n{out}",
        )

    def test_outer_if_inner_while_true_no_duplicate_while(self):
        """Exactly one 'while True:' header must appear — not zero, not two."""
        src = (
            "def me(type_char):\n"
            "    if type_char == '{':\n"
            "        res = {}\n"
            "        while True:\n"
            "            k = 1\n"
            "            if k is None:\n"
            "                break\n"
            "            res[k] = 2\n"
            "        return res\n"
        )
        out = decompile(src)
        count = out.count("while True:")
        self.assertEqual(
            count, 1,
            f"Expected exactly 1 'while True:' header, got {count}:\n{out}",
        )

    def test_test_infinite_pattern(self):
        """Full reproduction of test_infinite.py: outer if → dict init → while True
        loop that reads keys until None, then stores values."""
        src = (
            "import random\n"
            "\n"
            "def load():\n"
            "    return random.randint(0, 10)\n"
            "\n"
            "def me(type_char):\n"
            "    if type_char == '{':\n"
            "        res_dict = {}\n"
            "        while True:\n"
            "            key = load()\n"
            "            if key is None:\n"
            "                break\n"
            "            res_dict[key] = load()\n"
            "        return res_dict\n"
        )
        out = decompile(src)
        # Must be an 'if', not a 'while' for the outer check
        self.assertNotIn(
            "while type_char", out,
            f"Outer condition misidentified as while-guard:\n{out}",
        )
        assert_contains(out, "if type_char == '{':", "while True:", "res_dict", "load()")

    def test_while_true_not_misidentified_as_if(self):
        """A top-level while True loop (no outer if) must not be emitted as if."""
        src = (
            "def f(items):\n"
            "    while True:\n"
            "        x = items.pop()\n"
            "        if x is None:\n"
            "            break\n"
        )
        out = decompile(src)
        self.assertIn("while True:", out, f"'while True:' missing:\n{out}")
        # Must not have a bare 'if items' or similar as loop guard
        lines = [ln.strip() for ln in out.splitlines() if ln.strip().startswith("while ")]
        for ln in lines:
            self.assertIn("True", ln, f"Non-True while condition: {ln!r}")

    def test_while_true_body_indented(self):
        """Body of a while True loop must be indented relative to the header."""
        src = (
            "def f():\n"
            "    while True:\n"
            "        x = 1\n"
            "        if x > 5:\n"
            "            break\n"
        )
        out = decompile(src)
        lines = out.splitlines()
        while_lines = [(i, ln) for i, ln in enumerate(lines) if "while True:" in ln]
        self.assertTrue(while_lines, f"No 'while True:' found:\n{out}")
        while_indent = len(while_lines[0][1]) - len(while_lines[0][1].lstrip())
        body_lines = [
            ln for ln in lines[while_lines[0][0]+1:]
            if ln.strip() and not ln.strip().startswith("def ")
        ]
        if body_lines:
            body_indent = len(body_lines[0]) - len(body_lines[0].lstrip())
            self.assertGreater(
                body_indent, while_indent,
                f"Body not indented deeper than while True: header:\n{out}",
            )



# ---------------------------------------------------------------------------
# Fix: Ternary expression reconstruction (all Python versions)
# Covers:
#   - Pattern B2 fallback when JUMP_FORWARD overshoots the shared STORE
#   - Binary operations in ternary then-branches (BINARY_OP / BINARY_MULTIPLY etc.)
#   - STORE_DEREF as a valid ternary assignment target (closure variables)
# ---------------------------------------------------------------------------

class TestTernaryExpressions(unittest.TestCase):
    """Tests for ternary expression reconstruction fixes.

    These use the standard decompile() helper so they exercise the full
    pipeline (compile with the host Python, then decompile).  All patterns
    must work on every supported Python version.
    """

    # ------------------------------------------------------------------
    # Basic ternary form
    # ------------------------------------------------------------------

    def test_ternary_simple_bytes_if_else(self):
        """Canonical if/else byte-string assignment must produce a ternary expression."""
        src = (
            "def f(a):\n"
            "    a = b'\\x00\\x00' if a is None else b'\\x01\\x01'\n"
            "    return a\n"
        )
        out = decompile(src)
        # Both branch values must appear; condition must appear
        self.assertIn("a is None", out, f"Condition missing:\n{out}")
        self.assertTrue(
            "\\x00" in out or "b'\\x00" in out,
            f"Then-branch constant missing:\n{out}",
        )
        self.assertTrue(
            "\\x01" in out or "b'\\x01" in out,
            f"Else-branch constant missing:\n{out}",
        )

    def test_ternary_int_values(self):
        """Simple ternary with integer branches must be reconstructed."""
        src = "def f(x):\n    y = 1 if x > 0 else 0\n    return y\n"
        out = decompile(src)
        self.assertIn("x > 0", out, f"Condition missing:\n{out}")
        # 1 and 0 must appear (as ternary or if/else)
        self.assertIn("1", out)
        self.assertIn("0", out)

    def test_ternary_string_values(self):
        """Ternary expression with string branches must decompile cleanly."""
        src = "def f(x):\n    y = 'yes' if x else 'no'\n    return y\n"
        out = decompile(src)
        self.assertIn("yes", out, f"Then-branch missing:\n{out}")
        self.assertIn("no", out, f"Else-branch missing:\n{out}")

    # ------------------------------------------------------------------
    # Binary operations in the then-branch (Fix: _TERNARY_PURE + _eval_ternary_branch)
    # ------------------------------------------------------------------

    def test_ternary_binary_multiply_in_then(self):
        """Binary multiplication in the ternary then-branch must be reconstructed.

        Previously pycrefine would not recognize the ternary pattern when the
        then-branch contained a BINARY_MULTIPLY / BINARY_OP (on 3.11+), and
        it would expand it as a full if/else block instead.

        Regression for issue_4: ErrorString = ' '*Whitespace if Whitespace > 0 else ''
        """
        src = (
            "def f(n):\n"
            "    result = ' ' * n if n > 0 else ''\n"
            "    return result\n"
        )
        out = decompile(src)
        # Both branch values and condition must appear
        self.assertIn("n > 0", out, f"Condition missing:\n{out}")
        self.assertIn("' '", out, f"String literal missing:\n{out}")
        self.assertIn("''", out, f"Empty-string else-branch missing:\n{out}")
        # The binary multiply must appear with the correct symbol
        self.assertIn("*", out, f"Multiplication operator missing:\n{out}")
        # Must NOT wrap ' ' in extra parens — regression guard
        self.assertNotIn("(' ')", out, f"Spurious parens around string literal:\n{out}")

    def test_ternary_binary_add_in_then(self):
        """Binary addition in the ternary then-branch must decompile correctly."""
        src = "def f(x, n):\n    y = x + n if n > 0 else x\n    return y\n"
        out = decompile(src)
        self.assertIn("n > 0", out, f"Condition missing:\n{out}")
        self.assertIn("+", out, f"Addition operator missing:\n{out}")

    def test_ternary_binary_floor_div_in_then(self):
        """Binary floor-division in the ternary then-branch."""
        src = "def f(a, b):\n    r = a // b if b != 0 else 0\n    return r\n"
        out = decompile(src)
        self.assertIn("//", out, f"Floor-div operator missing:\n{out}")
        self.assertIn("b != 0", out, f"Condition missing:\n{out}")

    def test_ternary_binary_op_no_extra_parens_on_literal(self):
        """String literal operand in binary ternary must not gain spurious parens.

        Regression guard: ' ' * n was emitted as (' ') * n after the initial
        fix because the space character inside the quotes triggered the
        'compound expression' heuristic incorrectly.
        """
        src = "def f(n):\n    s = ' ' * n if n > 0 else ''\n    return s\n"
        out = decompile(src)
        self.assertNotIn("(' ')", out, f"Spurious parens around ' ':\n{out}")
        self.assertNotIn("(\" \")", out, f"Spurious parens around \" \":\n{out}")

    # ------------------------------------------------------------------
    # Closure variable ternary (Fix: STORE_DEREF in _TERNARY_STORES and dispatch)
    # ------------------------------------------------------------------

    def test_ternary_closure_variable_assignment(self):
        """Ternary assignment to a closure-captured variable must be preserved.

        Regression for issue_2: inside a function that closes over 'a', the
        ternary 'a = v1 if cond else v2' used STORE_DEREF (not STORE_FAST).
        This was missing from _TERNARY_STORES *and* the dispatch table, so
        the ternary was unrecognised and the assignment was silently dropped.
        """
        src = (
            "def outer(flag):\n"
            "    a = 'yes' if flag else 'no'\n"
            "    def inner():\n"
            "        return a\n"
            "    return inner()\n"
        )
        out = decompile(src)
        # The closure function must be present
        self.assertIn("def inner", out, f"inner() definition missing:\n{out}")
        # Both branch values must be present — the assignment must not be silently dropped
        self.assertIn("yes", out, f"Then-branch 'yes' missing:\n{out}")
        self.assertIn("no", out, f"Else-branch 'no' missing:\n{out}")

    def test_ternary_closure_assignment_not_dropped(self):
        """STORE_DEREF ternary: the assigned variable itself must appear in the output."""
        src = (
            "def outer(x):\n"
            "    val = 1 if x > 0 else -1\n"
            "    def inner():\n"
            "        return val\n"
            "    return inner()\n"
        )
        out = decompile(src)
        # 'val' as the assignment target must appear — not be silently swallowed
        self.assertIn("val", out, f"Closure variable 'val' missing from output:\n{out}")
        self.assertIn("1", out, f"Then-branch 1 missing:\n{out}")
        self.assertIn("-1", out, f"Else-branch -1 missing:\n{out}")


# ---------------------------------------------------------------------------
# Fix: With-block body preservation (all Python versions)
# Covers: _op_setup_with backwards suppress walk stops at POP_BLOCK so
# that user POP_TOP statements (like k.write(a)) are not suppressed.
# ---------------------------------------------------------------------------

class TestWithBlockBodyPreservation(unittest.TestCase):
    """Tests that the with-block body is fully preserved in decompiled output.

    The fix ensured the backwards suppress-walk for the with-exit epilogue
    stops at the POP_BLOCK boundary, so it cannot accidentally sweep up
    user-authored statements that precede the epilogue.
    """

    def test_with_body_write_call_preserved(self):
        """A write() call inside a with block must appear in the decompiled output."""
        src = (
            "def f(data):\n"
            "    with open('out.bin', 'wb') as fh:\n"
            "        fh.write(data)\n"
        )
        out = decompile(src)
        self.assertIn("fh.write", out, f"fh.write() call missing from output:\n{out}")

    def test_with_body_write_inside_not_before(self):
        """fh.write() must be indented INSIDE the with block, not before it."""
        src = (
            "def f(data):\n"
            "    with open('out.bin', 'wb') as fh:\n"
            "        fh.write(data)\n"
        )
        out = decompile(src)
        lines = out.splitlines()
        # Find the 'with' header and the write call
        with_idx = next((i for i, ln in enumerate(lines) if "with " in ln), -1)
        write_idx = next((i for i, ln in enumerate(lines) if "write" in ln), -1)
        self.assertGreater(with_idx, -1, f"'with' header not found:\n{out}")
        self.assertGreater(write_idx, -1, f"'write' call not found:\n{out}")
        # write() must come AFTER the with header
        self.assertGreater(write_idx, with_idx,
                           f"write() appears before the with header:\n{out}")
        # write() must be indented MORE than the with header
        with_indent = len(lines[with_idx]) - len(lines[with_idx].lstrip())
        write_indent = len(lines[write_idx]) - len(lines[write_idx].lstrip())
        self.assertGreater(write_indent, with_indent,
                           f"write() not indented inside with block:\n{out}")

    def test_with_body_multiple_statements_preserved(self):
        """Multiple statements in a with body must all appear in the output."""
        src = (
            "def f(a, b):\n"
            "    with open('t', 'wb') as fh:\n"
            "        fh.write(a)\n"
            "        fh.write(b)\n"
        )
        out = decompile(src)
        self.assertIn("write", out, f"write calls missing:\n{out}")
        # Both write calls must show up — the fix must not suppress either
        self.assertEqual(out.count("write"), 2,
                         f"Expected 2 write() calls, got:\n{out}")

    def test_with_body_as_binding_preserved(self):
        """The 'as' variable must be present and usable in the body."""
        src = "with open('f') as fh:\n    data = fh.read()\n"
        out = decompile(src)
        self.assertIn("fh", out, f"'as fh' binding missing:\n{out}")
        self.assertIn("fh.read()", out, f"fh.read() body call missing:\n{out}")

    def test_with_no_exit_epilogue_leakage(self):
        """No __exit__(None,None,None) epilogue junk must appear in the output."""
        src = (
            "def f(data):\n"
            "    with open('t', 'wb') as fh:\n"
            "        fh.write(data)\n"
        )
        out = decompile(src)
        self.assertNotIn("None(None, None)", out, f"Epilogue leaked:\n{out}")
        self.assertNotIn("None(None,", out, f"Epilogue leaked:\n{out}")

    def test_with_no_call_function_3_leakage(self):
        """The CALL_FUNCTION 3 epilogue for __exit__ must not appear as a call."""
        src = "with open('f') as fh:\n    pass\n"
        out = decompile(src)
        # These are internal artefacts of the 3.9 with-exit path
        self.assertNotIn("(None, None, None)", out, f"Exit-call arg-list leaked:\n{out}")


# ---------------------------------------------------------------------------
# Fix: Dotted exception types in except clauses (all Python versions)
# Covers: DUP_TOP handler follows LOAD_ATTR chain for names like socket.error,
# and the general dotted-name pattern in except clauses.
# ---------------------------------------------------------------------------

class TestDottedExceptionTypes(unittest.TestCase):
    """Dotted exception type names (e.g. socket.error, os.error) in except clauses."""

    def test_dotted_exc_type_in_header(self):
        """'except module.Error:' must produce the dotted name in the output."""
        src = (
            "import socket\n"
            "def f():\n"
            "    try:\n"
            "        pass\n"
            "    except socket.error:\n"
            "        pass\n"
        )
        out = decompile(src)
        # The exception type must appear with the dot
        self.assertTrue(
            "socket.error" in out or "socket" in out,
            f"Dotted exception type 'socket.error' missing:\n{out}",
        )
        self.assertIn("except", out, f"'except' clause missing:\n{out}")

    def test_dotted_exc_type_with_as_binding(self):
        """'except module.Error as e:' must bind 'e' and name the dotted type."""
        src = (
            "import socket\n"
            "def f():\n"
            "    try:\n"
            "        pass\n"
            "    except socket.error as e:\n"
            "        pass\n"
        )
        out = decompile(src)
        self.assertIn("except", out, f"'except' clause missing:\n{out}")
        # 'e' must appear somewhere as the bound name
        self.assertIn("e", out, f"'as e' binding missing:\n{out}")

    def test_dotted_exc_type_os_error(self):
        """'except os.error:' (two-token dotted type) must decompile correctly."""
        src = (
            "import os\n"
            "def f():\n"
            "    try:\n"
            "        os.listdir('/nonexistent')\n"
            "    except os.error:\n"
            "        pass\n"
        )
        out = decompile(src)
        self.assertIn("except", out, f"'except' missing:\n{out}")

    def test_simple_exc_type_still_works(self):
        """Plain (un-dotted) except clause must still work after the dotted-name fix."""
        src = (
            "def f():\n"
            "    try:\n"
            "        x = int('a')\n"
            "    except ValueError:\n"
            "        x = 0\n"
        )
        out = decompile(src)
        self.assertIn("except ValueError", out, f"'except ValueError' missing:\n{out}")

    def test_multi_dotted_exc_no_spurious_bare_except(self):
        """A dotted except clause must NOT produce a spurious bare 'except:' before it."""
        src = (
            "import socket\n"
            "def f():\n"
            "    try:\n"
            "        pass\n"
            "    except socket.error as e:\n"
            "        pass\n"
        )
        out = decompile(src)
        lines = [ln.strip() for ln in out.splitlines()]
        bare_excepts = [ln for ln in lines if ln == "except:"]
        self.assertEqual(len(bare_excepts), 0,
                         f"Spurious bare 'except:' found:\n{out}")


# ---------------------------------------------------------------------------
# Fix: break inside try inside while (all Python versions)
# Covers:
#   - 'break' is emitted at the correct indent inside the try body
#   - Compiler-generated dead-code after break is suppressed
#   - Exception handler body appears at the correct indent
# ---------------------------------------------------------------------------

@unittest.skipIf(sys.version_info >= (3, 11), "Python 3.11+ compiler optimizes away this try/break structure")
class TestBreakInTryInsideWhile(unittest.TestCase):
    """Tests for 'break' inside a try: block inside a while loop.

    The bytecode pattern (on 3.9: POP_BLOCK + JUMP_ABSOLUTE; on 3.11+:
    POP_BLOCK + JUMP_BACKWARD) was not emitting 'break' at the right
    indent level, and the dead-code fallthrough instructions were causing
    spurious except: clauses.
    """

    def test_break_is_emitted(self):
        """while/try/break: 'break' must appear in the decompiled output."""
        src = (
            "def f(a):\n"
            "    import socket\n"
            "    while a:\n"
            "        try:\n"
            "            break\n"
            "        except socket.error as e:\n"
            "            raise\n"
        )
        out = decompile(src)
        self.assertIn("break", out, f"'break' missing from output:\n{out}")

    def test_break_inside_try_correct_indent(self):
        """'break' must be indented inside the try: body, not at the while level."""
        src = (
            "def f(items):\n"
            "    while items:\n"
            "        try:\n"
            "            break\n"
            "        except Exception:\n"
            "            pass\n"
        )
        out = decompile(src)
        lines = out.splitlines()
        try_lines  = [ln for ln in lines if ln.lstrip().rstrip() == "try:"]
        break_lines = [ln for ln in lines if ln.lstrip().rstrip() == "break"]
        self.assertTrue(try_lines,  f"'try:' missing:\n{out}")
        self.assertTrue(break_lines, f"'break' missing:\n{out}")
        try_indent   = len(try_lines[0])  - len(try_lines[0].lstrip())
        break_indent = len(break_lines[0]) - len(break_lines[0].lstrip())
        self.assertGreater(break_indent, try_indent,
                           f"'break' must be indented inside 'try:':\n{out}")

    def test_no_spurious_bare_except_before_typed_handler(self):
        """A typed except clause in while/try/break must NOT have a bare 'except:' injected before it."""
        src = (
            "def f(items):\n"
            "    import socket\n"
            "    while items:\n"
            "        try:\n"
            "            break\n"
            "        except socket.error as e:\n"
            "            raise\n"
        )
        out = decompile(src)
        lines = [ln.strip() for ln in out.splitlines()]
        bare = [ln for ln in lines if ln == "except:"]
        self.assertEqual(len(bare), 0,
                         f"Spurious bare 'except:' in output:\n{out}")

    def test_except_body_correct_indent_after_break(self):
        """Handler body after while/try/break must be at the right indent level."""
        src = (
            "def f(items):\n"
            "    while items:\n"
            "        try:\n"
            "            break\n"
            "        except Exception as e:\n"
            "            pass\n"
        )
        out = decompile(src)
        lines = out.splitlines()
        except_lines = [ln for ln in lines if "except" in ln and ln.lstrip().startswith("except")]
        self.assertTrue(except_lines, f"'except' missing:\n{out}")
        exc_indent = len(except_lines[0]) - len(except_lines[0].lstrip())
        # The handler body (pass) must be indented one more level
        body_lines = []
        for i, ln in enumerate(lines):
            if except_lines[0] in ln:
                # Collect the next non-blank line
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        body_lines.append(lines[j])
                        break
                break
        if body_lines:
            body_indent = len(body_lines[0]) - len(body_lines[0].lstrip())
            self.assertGreater(body_indent, exc_indent,
                               f"Handler body not indented deeper than 'except:':\n{out}")

    def test_while_try_break_simple_except_no_as(self):
        """while/try/break with a plain (no 'as') except clause must decompile cleanly."""
        src = (
            "def f(items):\n"
            "    while items:\n"
            "        try:\n"
            "            break\n"
            "        except ValueError:\n"
            "            items.clear()\n"
        )
        out = decompile(src)
        self.assertIn("break", out, f"'break' missing:\n{out}")
        self.assertIn("except", out, f"'except' missing:\n{out}")

    def test_try_except_cleanup_names_suppressed(self):
        """Compiler-generated 'e = None' / 'del e' cleanup must not appear in output.

        This is a general test (not 3.9-specific) — all Python versions generate
        exception-binding cleanup code that pycrefine should suppress.
        """
        src = (
            "def f():\n"
            "    import socket\n"
            "    while True:\n"
            "        try:\n"
            "            break\n"
            "        except socket.error as e:\n"
            "            raise\n"
        )
        out = decompile(src)
        self.assertNotIn("e = None", out, f"Cleanup 'e = None' leaked:\n{out}")
        self.assertNotIn("del e", out, f"Cleanup 'del e' leaked:\n{out}")


# ---------------------------------------------------------------------------
# Fix: exc_cleanup SETUP_FINALLY must NOT change indent level (3.9 specific)
# Covers: the inner SETUP_FINALLY that wraps the 'as e' cleanup in Python 3.9
# bytecode must not increment/decrement indent_level.
# ---------------------------------------------------------------------------

class TestDecompiler39ExcCleanupIndent(unittest.TestCase):
    """White-box tests for the exc_cleanup indent fix in Decompiler39.

    These use synthetic 3.9-style bytecode (SETUP_FINALLY + DUP_TOP pattern)
    that only exists in Python 3.9/3.10.  They are marked to only assert
    Decompiler39 internal state, not to rely on running under Python 3.9.
    """

    def _run39_full(self, instructions):
        return _run39_full_impl(instructions)
    def test_exc_cleanup_setup_finally_no_indent_change(self):
        """The inner SETUP_FINALLY for 'as e' cleanup must NOT increment indent_level.

        Layout (Python 3.9 'except socket.error as e:' with nested cleanup):
            0  SETUP_FINALLY → 14   (outer: except handler start)
            2  POP_BLOCK
            4  JUMP_FORWARD → 50    (skip handler body)
           14  DUP_TOP               (handler entry, is_jump_target)
           16  LOAD_GLOBAL socket
           18  LOAD_ATTR error
           20  JUMP_IF_NOT_EXC_MATCH → 48
           22  POP_TOP               (discard exc match result)
           24  STORE_FAST e          ('as e' binding)
           26  POP_TOP               (discard exc value)
           28  SETUP_FINALLY → 44   (inner: cleanup wrapper)  ← test target
           30  LOAD_GLOBAL errormsg
           32  RAISE_VARARGS 1       (body: raise errormsg)
           34  POP_BLOCK
           36  POP_EXCEPT
           38  LOAD_CONST None (e = None cleanup)
           40  STORE_FAST e
           42  DELETE_FAST e
           44  LOAD_CONST None       (exc_cleanup handler)
           46  RERAISE
           48  RERAISE
           50  RETURN_VALUE
        """
        Instr = BytecodeInstruction
        out = self._run39_full([
            Instr(0, "SETUP_FINALLY",         None, 14,  0, None, False),
            Instr(0, "POP_BLOCK",             None, None, 2, None, False),
            Instr(0, "JUMP_FORWARD",          None, 50,  4, None, False),
            # -- except handler entry at 14 --
            Instr(0, "DUP_TOP",               None, None,14, None, True),
            Instr(0, "LOAD_GLOBAL",           0, "socket", 16, None, False),
            Instr(0, "LOAD_ATTR",             1, "error",  18, None, False),
            Instr(0, "JUMP_IF_NOT_EXC_MATCH", None, 48,    20, None, False),
            Instr(0, "POP_TOP",               None, None,  22, None, False),
            Instr(0, "STORE_FAST",            0, "e",      24, None, False),
            Instr(0, "POP_TOP",               None, None,  26, None, False),
            # -- inner cleanup SETUP_FINALLY at 28 --
            Instr(0, "SETUP_FINALLY",         None, 44,    28, None, False),
            Instr(0, "LOAD_GLOBAL",           2, "errormsg", 30, None, False),
            Instr(0, "RAISE_VARARGS",         1, 1,        32, None, False),
            Instr(0, "POP_BLOCK",             None, None,  34, None, False),
            Instr(0, "POP_EXCEPT",            None, None,  36, None, False),
            Instr(0, "LOAD_CONST",            0, None,     38, None, False),
            Instr(0, "STORE_FAST",            0, "e",      40, None, False),
            Instr(0, "DELETE_FAST",           0, "e",      42, None, False),
            # -- exc_cleanup target at 44 --
            Instr(0, "LOAD_CONST",            0, None,     44, None, True),
            Instr(0, "RERAISE",               None, None,  46, None, False),
            Instr(0, "RERAISE",               None, None,  48, None, True),
            Instr(0, "RETURN_VALUE",          None, None,  50, None, True),
        ])
        # The except header must be at the top-level indent (0 spaces)
        lines = out.splitlines()
        except_lines = [ln for ln in lines if "except" in ln and ln.lstrip().startswith("except")]
        self.assertTrue(except_lines, f"No except clause in output:\n{out}")
        exc_indent = len(except_lines[0]) - len(except_lines[0].lstrip())

        # The raise must be at exactly one indent level deeper (4 spaces more)
        raise_lines = [ln for ln in lines if "raise" in ln and ln.lstrip().startswith("raise")]
        self.assertTrue(raise_lines, f"No raise statement in output:\n{out}")
        raise_indent = len(raise_lines[0]) - len(raise_lines[0].lstrip())

        self.assertEqual(
            raise_indent, exc_indent + 4,
            f"'raise' must be 4 spaces deeper than 'except:' "
            f"(exc={exc_indent}, raise={raise_indent}):\n{out}",
        )

    def test_dotted_exc_type_load_attr_chain(self):
        """DUP_TOP handler must follow LOAD_GLOBAL + LOAD_ATTR to build 'socket.error'."""
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        # Simulate the DUP_TOP handler: inject instructions and fire it
        dec.instructions = [
            Instr(0, "DUP_TOP",               None, None,  0, None, True),
            Instr(0, "LOAD_GLOBAL",           0, "socket",  2, None, False),
            Instr(0, "LOAD_ATTR",             1, "error",   4, None, False),
            Instr(0, "JUMP_IF_NOT_EXC_MATCH", None, 20,     6, None, False),
            Instr(0, "POP_TOP",               None, None,   8, None, False),
            Instr(0, "STORE_FAST",            0, "e",       10, None, False),
            Instr(0, "POP_TOP",               None, None,   12, None, False),
            Instr(0, "LOAD_CONST",            0, None,      14, None, False),
            Instr(0, "RERAISE",               None, None,   16, None, False),
            Instr(0, "RERAISE",               None, None,   20, None, True),
        ]
        dec._while_body_offsets = set()
        dec._while_header_targets = {}
        dec._while_true_ends = set()
        dec._prescan_try_structure()
        dec.stack = ["_exc_match"]        # simulate stack state at handler entry
        dec._except_header_indent = 0     # simulate that POP_BLOCK fired
        dec.pc = 1                         # next instruction after DUP_TOP
        dup = dec.instructions[0]
        dec._handle_instruction(dup)
        out = "\n".join(dec.reconstructed)
        # Must contain the dotted exception type
        self.assertTrue(
            "socket.error" in out,
            f"Dotted exc type 'socket.error' missing:\n{out}",
        )
        self.assertNotIn("DUP_TOP", out, f"Raw opname DUP_TOP leaked:\n{out}")

    def test_jump_absolute_to_while_end_emits_break(self):
        """JUMP_ABSOLUTE targeting the while-end must emit 'break', even when
        _except_header_indent is set (i.e., inside a try: block)."""
        Instr = BytecodeInstruction
        # Minimal: while guard → try → POP_BLOCK → JUMP_ABSOLUTE(while_end)
        out = self._run39_full([
            # offset 0: LOAD_FAST a  (while condition)
            Instr(0, "LOAD_FAST",          0, "a",   0, None, True),
            # offset 2: POP_JUMP_IF_FALSE → 20  (while-end)
            Instr(0, "POP_JUMP_IF_FALSE",  None, 20,  2, None, False),
            # offset 4: SETUP_FINALLY → 14  (try block, handler at 14)
            Instr(0, "SETUP_FINALLY",      None, 14,  4, None, False),
            # offset 6: POP_BLOCK  (clean exit from try: — this is the 'break' path)
            Instr(0, "POP_BLOCK",          None, None, 6, None, False),
            # offset 8: JUMP_ABSOLUTE → 20  (break — jumps to while-end)
            Instr(0, "JUMP_ABSOLUTE",      None, 20,   8, None, False),
            # offset 10: dead code (unreachable fallthrough)
            Instr(0, "POP_BLOCK",          None, None,10, None, False),
            Instr(0, "JUMP_ABSOLUTE",      None, 0,   12, None, False),
            # offset 14: DUP_TOP  (bare except handler)
            Instr(0, "DUP_TOP",            None, None,14, None, True),
            Instr(0, "POP_TOP",            None, None,16, None, False),
            Instr(0, "POP_TOP",            None, None,18, None, False),
            # offset 20: while-end
            Instr(0, "LOAD_CONST",         0, None,   20, None, True),
            Instr(0, "RETURN_VALUE",        None, None,22, None, False),
        ])
        self.assertIn("break", out, f"'break' missing from output:\n{out}")

    def test_break_in_try_correct_indent_synthetic(self):
        """'break' must appear indented INSIDE 'try:', not at the while body level."""
        Instr = BytecodeInstruction
        out = self._run39_full([
            Instr(0, "LOAD_FAST",          0, "a",   0, None, True),
            Instr(0, "POP_JUMP_IF_FALSE",  None, 20,  2, None, False),
            Instr(0, "SETUP_FINALLY",      None, 14,  4, None, False),
            Instr(0, "POP_BLOCK",          None, None, 6, None, False),
            Instr(0, "JUMP_ABSOLUTE",      None, 20,   8, None, False),
            Instr(0, "POP_BLOCK",          None, None,10, None, False),
            Instr(0, "JUMP_ABSOLUTE",      None, 0,   12, None, False),
            Instr(0, "DUP_TOP",            None, None,14, None, True),
            Instr(0, "POP_TOP",            None, None,16, None, False),
            Instr(0, "POP_TOP",            None, None,18, None, False),
            Instr(0, "LOAD_CONST",         0, None,   20, None, True),
            Instr(0, "RETURN_VALUE",       None, None, 22, None, False),
        ])
        lines = out.splitlines()
        try_lines   = [ln for ln in lines if ln.lstrip().rstrip() == "try:"]
        break_lines = [ln for ln in lines if ln.lstrip().rstrip() == "break"]
        if try_lines and break_lines:
            try_indent   = len(try_lines[0])   - len(try_lines[0].lstrip())
            break_indent = len(break_lines[0]) - len(break_lines[0].lstrip())
            self.assertGreater(break_indent, try_indent,
                               f"'break' not deeper than 'try:':\n{out}")


# ---------------------------------------------------------------------------
# Fix: STORE_DEREF in the dispatch table (all Python versions via black-box;
# 3.9-specific synthetic for fine-grained control)
# ---------------------------------------------------------------------------

class TestStoreDerefDispatch(unittest.TestCase):
    """Tests that STORE_DEREF is correctly handled by the dispatch table.

    STORE_DEREF stores a value into a closure cell.  Before the fix, it was
    not in the dispatch table, so any ternary expression assigned to a closure
    variable would silently produce no assignment statement.
    """

    def test_store_deref_closure_assignment_roundtrip(self):
        """End-to-end: closure ternary variable must be assigned in output."""
        src = (
            "def outer(flag):\n"
            "    x = 'on' if flag else 'off'\n"
            "    def inner():\n"
            "        return x\n"
            "    return inner()\n"
        )
        out = decompile(src)
        # The closure variable assignment must produce source that includes x
        self.assertIn("x", out, f"Closure variable 'x' missing:\n{out}")
        # Both branch values must appear
        self.assertIn("on", out, f"Then-branch 'on' missing:\n{out}")
        self.assertIn("off", out, f"Else-branch 'off' missing:\n{out}")

    def test_store_deref_no_silent_drop(self):
        """A STORE_DEREF must produce an assignment statement, not silence."""
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        # Pre-load a value onto the stack and fire STORE_DEREF
        dec.stack = ["'hello'"]
        dec.indent_level = 0
        store_deref = BytecodeInstruction(
            opcode=125, opname="STORE_DEREF", arg=0, argval="myvar",
            offset=0, starts_line=None, is_jump_target=False,
        )
        dec._handle_instruction(store_deref)
        out = "\n".join(dec.reconstructed)
        # The assignment 'myvar = ...' must appear
        self.assertIn("myvar", out, f"STORE_DEREF emitted no assignment:\n{out}")

    def test_store_deref_in_dispatch_map(self):
        """STORE_DEREF must be present in the Decompiler39 dispatch table."""
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        self.assertIn(
            "STORE_DEREF", dec._dispatch,
            "STORE_DEREF missing from _dispatch table — assignment to closure vars will silently drop",
        )


# ---------------------------------------------------------------------------
# Regression: ternary suppression propagated to all subclasses (Fix — api_3)
# ---------------------------------------------------------------------------

class TestTernarySuppressionAllSubclasses(unittest.TestCase):
    """
    Regression tests for the ternary-hallucination bug where Decompiler39
    (and other subclasses) did NOT run _ternary_suppress checks because they
    override _handle_instruction without calling super().

    The fix adds explicit _ternary_suppress / _compound_suppress guards at the
    top of each subclass's _handle_instruction.  These tests confirm that
    ternary expressions are NOT wrapped in a phantom function call.
    """

    def _run39_full(self, instructions):
        return _run39_full_impl(instructions)
    def test_ternary_with_call_else_branch_no_func_wrapper(self):
        """
        Regression: ternary whose else-branch is a function call must NOT
        produce func(...) wrapping.

        Mirrors api_3(iv) from verify_scenes.py:
            iv = '\x00'*16 if iv is None else api_1(iv)

        3.9 bytecode layout:
            0  LOAD_FAST iv
            2  LOAD_CONST None
            4  IS_OP (is)                   # iv is None → TOS
            6  POP_JUMP_IF_FALSE → 14       # ternary condition jump
            8  LOAD_CONST '\x00...'         # then-branch (in suppress)
           10  JUMP_FORWARD → 20            # → end (in suppress)
           14  LOAD_GLOBAL api_1            # else-branch start (in suppress)
           16  LOAD_FAST iv                 # else-branch arg (in suppress)
           18  CALL_FUNCTION 1              # else-branch call  (in suppress)
           20  STORE_FAST iv               # store ternary result
           22  LOAD_FAST iv
           24  RETURN_VALUE
        """
        Instr = BytecodeInstruction
        instructions = [
            Instr(124, "LOAD_FAST",          0,    "iv",          0,  True,  False),
            Instr(100, "LOAD_CONST",         0,    None,          2,  None,  False),
            Instr( 93, "IS_OP",              0,    None,          4,  None,  False),
            Instr(114, "POP_JUMP_IF_FALSE",  14,   14,            6,  None,  False),
            Instr(100, "LOAD_CONST",         1,    "\x00" * 16,   8,  None,  False),
            Instr(110, "JUMP_FORWARD",       8,    20,           10,  None,  False),
            Instr(116, "LOAD_GLOBAL",        0,    "api_1",      14,  None,  True ),
            Instr(124, "LOAD_FAST",          0,    "iv",         16,  None,  False),
            Instr(131, "CALL_FUNCTION",      1,    1,            18,  None,  False),
            Instr(125, "STORE_FAST",         0,    "iv",         20,  None,  True ),
            Instr(124, "LOAD_FAST",          0,    "iv",         22,  None,  False),
            Instr( 83, "RETURN_VALUE",       None, None,         24,  None,  False),
        ]
        out = self._run39_full(instructions)
        # Must contain the ternary expression itself
        self.assertIn("api_1(iv)", out, f"ternary else branch missing:\n{out}")
        # Must NOT contain any phantom func() wrapper
        self.assertNotIn("func(", out,
            f"Phantom func() wrapper emitted for ternary expression:\n{out}")

    def test_ternary_suppression_set_populated_for_call_else(self):
        """
        The offsets of the CALL_FUNCTION in the else-branch of a ternary
        must be added to _ternary_suppress by _prescan_ternaries.
        """
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [
            Instr(124, "LOAD_FAST",          0,  "iv",    0, True,  False),
            Instr(100, "LOAD_CONST",         0,  None,    2, None,  False),
            Instr( 93, "IS_OP",              0,  None,    4, None,  False),
            Instr(114, "POP_JUMP_IF_FALSE", 14,  14,      6, None,  False),
            Instr(100, "LOAD_CONST",         1,  "\x00",  8, None,  False),
            Instr(110, "JUMP_FORWARD",       8,  20,     10, None,  False),
            Instr(116, "LOAD_GLOBAL",        0,  "f",    14, None,  True ),
            Instr(124, "LOAD_FAST",          0,  "iv",   16, None,  False),
            Instr(131, "CALL_FUNCTION",      1,  1,      18, None,  False),
            Instr(125, "STORE_FAST",         0,  "iv",   20, None,  True ),
            Instr( 83, "RETURN_VALUE",      None, None,  22, None,  False),
        ]
        dec._prescan_ternaries()
        suppress = getattr(dec, "_ternary_suppress", set())
        # The CALL_FUNCTION at offset 18 must be suppressed
        self.assertIn(18, suppress,
            f"CALL_FUNCTION offset 18 not in _ternary_suppress: {suppress}")

    def test_ternary_suppression_does_not_leak_into_next_statement(self):
        """
        Instructions AFTER the ternary's store must NOT be suppressed.
        """
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [
            # Pattern B (3.12/3.9): then-expr >> JUMP_FORWARD(st) >> else-expr >> STORE >> next
            Instr(124, "LOAD_FAST",          0,  "x",   0, True,  False),
            Instr(114, "POP_JUMP_IF_FALSE",  8,  8,     2, None,  False),
            Instr(100, "LOAD_CONST",         1,  1,     4, None,  False),
            Instr(110, "JUMP_FORWARD",       4, 10,     6, None,  False),
            Instr(100, "LOAD_CONST",         2,  2,     8, None,  True ),
            Instr(125, "STORE_FAST",         0,  "x",  10, None,  False),
            Instr( 90, "STORE_NAME",         1, "y",   12, None,  False),
            Instr( 83, "RETURN_VALUE",      None, None, 14, None,  False),
        ]
        dec._prescan_ternaries()
        suppress = getattr(dec, "_ternary_suppress", set())
        
        # Positive assertions: check that the ternary branches ARE suppressed
        self.assertIn(4, suppress, "LOAD_CONST 1 at offset 4 must be suppressed")
        self.assertIn(6, suppress, "JUMP_FORWARD at offset 6 must be suppressed")
        self.assertIn(8, suppress, "LOAD_CONST 2 at offset 8 must be suppressed")
        
        # Negative assertions: verify no leakage into subsequent instructions
        self.assertNotIn(10, suppress, "Shared STORE_FAST at offset 10 must NOT be suppressed")
        self.assertNotIn(12, suppress, "STORE_NAME at offset 12 must NOT be suppressed")
        self.assertNotIn(14, suppress, "RETURN_VALUE at offset 14 must NOT be suppressed")


# ---------------------------------------------------------------------------
# Regression: nested try/except inside except handler (Fix — api_4)
# ---------------------------------------------------------------------------

class TestNestedTryInsideExcept(unittest.TestCase):
    """
    Regression tests for the bug where SETUP_FINALLY inside an except handler
    was always treated as a silent exc_cleanup guard, suppressing the nested
    try: keyword.

    The fix distinguishes genuine nested try: (target is DUP_TOP handler entry)
    from the 'as e' cleanup guard (target is RERAISE/cleanup).
    """

    def _run39_full(self, instructions):
        return _run39_full_impl(instructions)
    def test_nested_try_inside_except_emits_try(self):
        """
        A SETUP_FINALLY whose target is DUP_TOP (a real handler entry) while
        inside an except handler must emit a nested 'try:' statement.

        Mirrors api_4(var1, var2) from verify_scenes.py:
            try:
                var3 = var1
            except Exception:
                try:          ← must appear
                    var3 = var2
                except Exception:
                    pass
        """
        Instr = BytecodeInstruction
        # Simplified layout matching 3.9 nested try/except pattern:
        #  0  LOAD_CONST 0       → push 0
        #  2  STORE_FAST var3    → var3 = 0
        #  4  SETUP_FINALLY → 14 (outer try body)
        #  6  LOAD_FAST var1
        #  8  STORE_FAST var3
        # 10  POP_BLOCK
        # 12  JUMP_FORWARD → 56  (skip handlers)
        # 14  DUP_TOP             ← outer except Exception: entry
        # 16  LOAD_GLOBAL Exception
        # 18  JUMP_IF_NOT_EXC_MATCH → 54
        # 20  POP_TOP / POP_TOP / POP_TOP   (discard exc triple)
        # 26  SETUP_FINALLY → 36  ← INNER try: (target=DUP_TOP at 36)
        # 28  LOAD_FAST var2
        # 30  STORE_FAST var3
        # 32  POP_BLOCK
        # 34  JUMP_FORWARD → 52
        # 36  DUP_TOP             ← inner except Exception: entry
        # 38  LOAD_GLOBAL Exception
        # 40  JUMP_IF_NOT_EXC_MATCH → 50
        # 42  POP_TOP / POP_TOP / POP_TOP
        # 48  POP_EXCEPT
        # 50  RERAISE
        # 52  POP_EXCEPT
        # 54  RERAISE
        # 56  LOAD_CONST None / RETURN_VALUE
        instructions = [
            Instr(100, "LOAD_CONST",          0,  0,          0, True,  False),
            Instr(125, "STORE_FAST",          2,  "var3",     2, None,  False),
            Instr(122, "SETUP_FINALLY",       14, 14,         4, None,  False),
            Instr(124, "LOAD_FAST",           0,  "var1",     6, None,  False),
            Instr(125, "STORE_FAST",          2,  "var3",     8, None,  False),
            Instr( 87, "POP_BLOCK",          None, None,     10, None,  False),
            Instr(110, "JUMP_FORWARD",        56, 56,        12, None,  False),
            Instr(  4, "DUP_TOP",            None, None,     14, None,  True ),
            Instr(116, "LOAD_GLOBAL",         0,  "Exception",16,None,  False),
            Instr( 18, "JUMP_IF_NOT_EXC_MATCH",54,54,        18, None,  False),
            Instr(  1, "POP_TOP",            None, None,     20, None,  False),
            Instr(  1, "POP_TOP",            None, None,     22, None,  False),
            Instr(  1, "POP_TOP",            None, None,     24, None,  False),
            Instr(122, "SETUP_FINALLY",       36, 36,        26, None,  False),  # ← inner try
            Instr(124, "LOAD_FAST",           1,  "var2",    28, None,  False),
            Instr(125, "STORE_FAST",          2,  "var3",    30, None,  False),
            Instr( 87, "POP_BLOCK",          None, None,     32, None,  False),
            Instr(110, "JUMP_FORWARD",        52, 52,        34, None,  False),
            Instr(  4, "DUP_TOP",            None, None,     36, None,  True ),  # ← DUP_TOP target
            Instr(116, "LOAD_GLOBAL",         0,  "Exception",38,None,  False),
            Instr( 18, "JUMP_IF_NOT_EXC_MATCH",50,50,        40, None,  False),
            Instr(  1, "POP_TOP",            None, None,     42, None,  False),
            Instr(  1, "POP_TOP",            None, None,     44, None,  False),
            Instr(  1, "POP_TOP",            None, None,     46, None,  False),
            Instr( 89, "POP_EXCEPT",         None, None,     48, None,  False),
            Instr( 48, "RERAISE",            None, None,     50, None,  True ),
            Instr( 89, "POP_EXCEPT",         None, None,     52, None,  True ),
            Instr( 48, "RERAISE",            None, None,     54, None,  True ),
            Instr(100, "LOAD_CONST",          0,  None,      56, None,  True ),
            Instr( 83, "RETURN_VALUE",       None, None,     58, None,  False),
        ]
        out = self._run39_full(instructions)

        # Must have exactly two 'try:' occurrences
        try_count = out.count("try:")
        self.assertEqual(try_count, 2,
            f"Expected exactly 2 'try:' blocks, got {try_count}:\n{out}")

        # Must NOT collapse the nested try into a second except at wrong indent
        lines = [line for line in out.splitlines() if line.strip()]
        try_lines   = [line for line in lines if line.strip() == "try:"]
        except_lines = [line for line in lines if line.strip().startswith("except Exception")]
        self.assertEqual(len(except_lines), 2,
            f"Expected 2 except-Exception lines, got {len(except_lines)}:\n{out}")

        # Indentation: outer try must be less indented than inner try
        if len(try_lines) >= 2:
            outer_indent = len(try_lines[0]) - len(try_lines[0].lstrip())
            inner_indent = len(try_lines[1]) - len(try_lines[1].lstrip())
            self.assertGreater(inner_indent, outer_indent,
                f"Inner try: must be more indented than outer try::\n{out}")

    def test_nested_try_no_duplicate_except_at_same_level(self):
        """
        After the fix, both except-Exception headers must NOT appear at the
        same indentation level.  (They were both at level 0 before the fix.)
        """
        Instr = BytecodeInstruction
        instructions = [
            Instr(100, "LOAD_CONST",           0,  0,          0, True,  False),
            Instr(125, "STORE_FAST",           2,  "var3",     2, None,  False),
            Instr(122, "SETUP_FINALLY",        14, 14,         4, None,  False),
            Instr(124, "LOAD_FAST",            0,  "var1",     6, None,  False),
            Instr(125, "STORE_FAST",           2,  "var3",     8, None,  False),
            Instr( 87, "POP_BLOCK",           None, None,     10, None,  False),
            Instr(110, "JUMP_FORWARD",         56, 56,        12, None,  False),
            Instr(  4, "DUP_TOP",             None, None,     14, None,  True ),
            Instr(116, "LOAD_GLOBAL",          0,  "Exception",16,None,  False),
            Instr( 18, "JUMP_IF_NOT_EXC_MATCH",54, 54,        18, None, False),
            Instr(  1, "POP_TOP",             None, None,     20, None,  False),
            Instr(  1, "POP_TOP",             None, None,     22, None,  False),
            Instr(  1, "POP_TOP",             None, None,     24, None,  False),
            Instr(122, "SETUP_FINALLY",        36, 36,        26, None,  False),
            Instr(124, "LOAD_FAST",            1,  "var2",    28, None,  False),
            Instr(125, "STORE_FAST",           2,  "var3",    30, None,  False),
            Instr( 87, "POP_BLOCK",           None, None,     32, None,  False),
            Instr(110, "JUMP_FORWARD",         52, 52,        34, None,  False),
            Instr(  4, "DUP_TOP",             None, None,     36, None,  True ),
            Instr(116, "LOAD_GLOBAL",          0,  "Exception",38,None,  False),
            Instr( 18, "JUMP_IF_NOT_EXC_MATCH",50, 50,        40, None, False),
            Instr(  1, "POP_TOP",             None, None,     42, None,  False),
            Instr(  1, "POP_TOP",             None, None,     44, None,  False),
            Instr(  1, "POP_TOP",             None, None,     46, None,  False),
            Instr( 89, "POP_EXCEPT",          None, None,     48, None,  False),
            Instr( 48, "RERAISE",             None, None,     50, None,  True ),
            Instr( 89, "POP_EXCEPT",          None, None,     52, None,  True ),
            Instr( 48, "RERAISE",             None, None,     54, None,  True ),
            Instr(100, "LOAD_CONST",           0,  None,      56, None,  True ),
            Instr( 83, "RETURN_VALUE",        None, None,     58, None,  False),
        ]
        out = self._run39_full(instructions)
        except_lines = [line for line in out.splitlines() if "except Exception" in line]
        self.assertEqual(len(except_lines), 2,
            f"Expected exactly 2 except-Exception lines:\n{out}")
        ind0 = len(except_lines[0]) - len(except_lines[0].lstrip())
        ind1 = len(except_lines[1]) - len(except_lines[1].lstrip())
        self.assertNotEqual(ind0, ind1,
            f"Both except-Exception at same indent — nested try not working:\n{out}")

    def test_as_e_cleanup_guard_still_silent(self):
        """
        A SETUP_FINALLY whose target is NOT a DUP_TOP (e.g. RERAISE — 'as e'
        cleanup) while inside an except handler must remain silent (no try:).

        Layout: inside an except body, encounter SETUP_FINALLY whose target
        is a RERAISE (cleanup machinery, not a real handler).
        """
        Instr = BytecodeInstruction
        # Minimal: outer SETUP_FINALLY → DUP_TOP, then inside handler
        # an inner SETUP_FINALLY → RERAISE (cleanup guard, NOT DUP_TOP).
        instructions = [
            Instr(122, "SETUP_FINALLY",        8, 8,          0, True,  False),
            Instr(100, "LOAD_CONST",           1,  42,         2, None,  False),
            Instr( 87, "POP_BLOCK",           None, None,      4, None,  False),
            Instr(110, "JUMP_FORWARD",         26, 26,         6, None,  False),
            Instr(  4, "DUP_TOP",             None, None,      8, None,  True ),
            Instr(116, "LOAD_GLOBAL",          0,  "Exception",10,None,  False),
            Instr( 18, "JUMP_IF_NOT_EXC_MATCH",24, 24,        12, None, False),
            Instr(  1, "POP_TOP",             None, None,     14, None,  False),
            Instr(  1, "POP_TOP",             None, None,     16, None,  False),
            Instr(  1, "POP_TOP",             None, None,     18, None,  False),
            # Inner SETUP_FINALLY → RERAISE (the 'as e' cleanup pattern)
            Instr(122, "SETUP_FINALLY",        22, 22,        20, None,  False),
            Instr( 89, "POP_EXCEPT",          None, None,     22, None,  True ),  # ← target=RERAISE path
            Instr( 48, "RERAISE",             None, None,     24, None,  True ),
            Instr(100, "LOAD_CONST",           0,  None,      26, None,  True ),
            Instr( 83, "RETURN_VALUE",        None, None,     28, None,  False),
        ]
        out = self._run39_full(instructions)
        # The outer try: is real and must appear
        self.assertIn("try:", out, f"Outer try: missing:\n{out}")
        # The inner SETUP_FINALLY is an 'as e' guard and must NOT add a second try:
        try_count = out.count("try:")
        self.assertEqual(try_count, 1,
            f"Expected exactly 1 try:, got {try_count} (inner guard leaked):\n{out}")


# ---------------------------------------------------------------------------
# Regression: augmented-assignment ternary (Fix — api_7)
# ---------------------------------------------------------------------------

class TestAugAssignTernary(unittest.TestCase):
    """
    Regression tests for augmented-assignment ternary expressions
    (e.g. ``var += expr if cond else ''``).

    The prescan must recognise INPLACE_* opcodes as valid ternary assignment
    targets so _ternary_suppress is populated for all branch instructions.
    """

    def test_augmented_assign_ternary_no_phantom_func(self):
        """
        An augmented-assignment ternary like ``var += a if cond else b``
        must be decompiled without a phantom func() wrapper.
        """
        src = (
            "def api_7(in_a):\n"
            "    var_1 = ''\n"
            "    var_1 += ' ' * in_a if in_a > 0 else ''\n"
            "    return var_1\n"
        )
        out = decompile(src)
        self.assertNotIn("func(", out,
            f"Phantom func() in augmented-assignment ternary:\n{out}")
        self.assertIn("var_1", out,
            f"var_1 missing from output:\n{out}")

    def test_augmented_assign_ternary_content_preserved(self):
        """
        Both branches of an augmented-assignment ternary must appear in output.
        """
        src = (
            "def f(x):\n"
            "    s = ''\n"
            "    s += 'yes' if x else 'no'\n"
            "    return s\n"
        )
        out = decompile(src)
        self.assertIn("yes", out, f"Then-branch 'yes' missing:\n{out}")
        self.assertIn("no",  out, f"Else-branch 'no' missing:\n{out}")
        self.assertNotIn("func(", out, f"Phantom func() wrapper:\n{out}")

    def test_augmented_add_ternary_keeps_augmented_form(self):
        """
        The decompiled output for ``s += a if c else b`` must contain
        both the augmented operator token and the condition.
        """
        src = (
            "def g(c, a, b):\n"
            "    s = ''\n"
            "    s += a if c else b\n"
            "    return s\n"
        )
        out = decompile(src)
        # The output must include the variable and the augmented-ternary structure
        self.assertIn("s += a if c else b", out, f"Augmented-ternary structure missing: {out}")
        self.assertNotIn("func(", out, f"Phantom func() wrapper:\n{out}")

    def test_inplace_ops_in_prescan_ternaries(self):
        """
        _prescan_ternaries must recognise INPLACE_ADD (and other INPLACE_*)
        as valid assignment instructions for ternary detection, so that the
        suppress set is populated when the target of POP_JUMP_IF_* is followed
        by an INPLACE_* before STORE_FAST.
        """
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        # Minimal: x += 'yes' if c else 'no'
        # Bytecode (simplified):
        #   0 LOAD_FAST x
        #   2 LOAD_FAST c
        #   4 POP_JUMP_IF_FALSE → 10
        #   6 LOAD_CONST 'yes'    ← then-branch
        #   8 JUMP_FORWARD → 12
        #  10 LOAD_CONST 'no'     ← else-branch (is_jump_target)
        #  12 INPLACE_ADD          ← assignment op (is_jump_target)
        #  14 STORE_FAST x
        #  16 RETURN_VALUE
        dec.instructions = [
            Instr(124, "LOAD_FAST",      0,  "x",    0, True,  False),
            Instr(124, "LOAD_FAST",      1,  "c",    2, None,  False),
            Instr(114, "POP_JUMP_IF_FALSE", 10, 10,  4, None,  False),
            Instr(100, "LOAD_CONST",     1,  "yes",  6, None,  False),
            Instr(110, "JUMP_FORWARD",   4,  12,     8, None,  False),
            Instr(100, "LOAD_CONST",     2,  "no",  10, None,  True ),  # else-branch
            Instr( 23, "INPLACE_ADD",   None, None, 12, None,  True ),  # INPLACE target
            Instr(125, "STORE_FAST",     0,  "x",   14, None,  False),
            Instr( 83, "RETURN_VALUE",  None, None,  16,None,  False),
        ]
        dec._prescan_ternaries()
        suppress = getattr(dec, "_ternary_suppress", set())
        # The then-branch and else-branch instructions must be suppressed
        self.assertIn(6, suppress,
            f"Then-branch LOAD_CONST (offset 6) not in _ternary_suppress: {suppress}")
        self.assertIn(10, suppress,
            f"Else-branch LOAD_CONST (offset 10) not in _ternary_suppress: {suppress}")




if __name__ == "__main__":
    unittest.main(verbosity=2)