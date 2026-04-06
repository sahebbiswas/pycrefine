import sys
import unittest

from .test_helpers import _run39_full_impl, assert_contains, decompile


class TestExceptions(unittest.TestCase):
    def test_try_except_typed(self):
        out = decompile("try:\n    x = int('1')\nexcept ValueError:\n    x = 0\n")
        assert_contains(out, "try:", "except ValueError:", "x = 0")

    def test_try_except_as(self):
        out = decompile("try:\n    x = int('a')\nexcept ValueError as e:\n    x = 0\n")
        self.assertIn("except ValueError", out)
        self.assertIn("except ValueError as e:", out)

    def test_try_except_as_no_cleanup_leak(self):
        out = decompile("try:\n    x = int('a')\nexcept ValueError as e:\n    x = 0\n")
        self.assertNotIn("e = None", out)
        self.assertNotIn("del e", out)

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
        src = (
            "try:\n    x = int('a')\n"
            "except ValueError:\n    x = 0\n"
            "except TypeError:\n    x = -1\n"
        )
        out = decompile(src)
        lines = out.splitlines()
        exc_lines = [ln for ln in lines if ln.lstrip().startswith("except")]
        indents = {len(ln) - len(ln.lstrip()) for ln in exc_lines}
        self.assertEqual(len(indents), 1)

    def test_try_except_no_sentinel_in_output(self):
        out = decompile("try:\n    x = int('1')\nexcept ValueError:\n    x = 0\n")
        self.assertNotIn("_exc_match", out)
        self.assertNotIn("_exc_info", out)

    def test_raise_simple(self):
        out = decompile("raise ValueError('bad')\n")
        assert_contains(out, "raise ValueError")

    def test_raise_in_function(self):
        out = decompile("def f(x):\n    if x < 0:\n        raise ValueError('bad')\n    return x\n")
        assert_contains(out, "raise ValueError")

    def test_raise_from(self):
        src = "try:\n    pass\nexcept Exception as e:\n    raise RuntimeError('wrap') from e\n"
        out = decompile(src)
        assert_contains(out, "raise RuntimeError", "from e")

    def test_try_except_finally(self):
        src = (
            "try:\n    x = int('1')\n"
            "except ValueError:\n    x = 0\n"
            "finally:\n    print('done')\n"
        )
        out = decompile(src)
        assert_contains(out, "try:", "except ValueError:", "finally:", "print('done')")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        try_pos = next(i for i, ln in enumerate(lines) if ln == "try:")
        except_pos = next(i for i, ln in enumerate(lines) if ln.startswith("except"))
        finally_pos = next(i for i, ln in enumerate(lines) if ln == "finally:")
        self.assertLess(try_pos, except_pos)
        self.assertLess(except_pos, finally_pos)

    def test_try_finally_no_except(self):
        src = "try:\n    x = 1\nfinally:\n    print('done')\n"
        out = decompile(src)
        assert_contains(out, "try:", "finally:", "print('done')")

    def test_with_statement(self):
        src = "with open('f') as fh:\n    data = fh.read()\n"
        out = decompile(src)
        self.assertIn("with ", out)
        self.assertIn("open(", out)
        self.assertNotIn("None(None, None)", out)

    def test_with_as_variable_bound(self):
        src = "with open('f') as fh:\n    x = fh.read()\n"
        out = decompile(src)
        self.assertIn("fh", out)
        self.assertIn("fh.read()", out)

    def test_with_explicit_return_none(self):
        src = "def f():\n    with open('f'):\n        return None\n"
        out = decompile(src)
        self.assertIn("return None", out)

    def test_with_try_except_finally(self):
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

    def test_sequential_try_except_finally_blocks(self):
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
        self.assertEqual(out.count("except ValueError:"), 2)

    def test_finally_body_after_except_not_before(self):
        src = "try:\n    x = 1\nexcept ValueError:\n    x = 0\nfinally:\n    print('fin')\n"
        out = decompile(src)
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        except_pos = next((i for i, ln in enumerate(lines) if ln.startswith("except")), -1)
        finally_pos = next((i for i, ln in enumerate(lines) if ln == "finally:"), -1)
        self.assertGreater(finally_pos, except_pos)

    def test_no_exit_epilogue_leakage(self):
        src = "with open('f') as fh:\n    pass\n"
        out = decompile(src)
        self.assertNotIn("None(None, None)", out)

    def test_reraise_wrapper_suppressed(self):
        src = "try:\n    x = int('1')\nexcept ValueError:\n    x = 0\nfinally:\n    print('done')\n"
        out = decompile(src)
        self.assertNotIn("RERAISE", out)
        self.assertNotIn("PUSH_EXC_INFO", out)


class TestWithBlockBodyPreservation(unittest.TestCase):
    def test_with_body_write_call_preserved(self):
        src = "def f(data):\n    with open('out.bin', 'wb') as fh:\n        fh.write(data)\n"
        out = decompile(src)
        self.assertIn("fh.write", out)

    def test_with_body_write_inside_not_before(self):
        src = "def f(data):\n    with open('out.bin', 'wb') as fh:\n        fh.write(data)\n"
        out = decompile(src)
        lines = out.splitlines()
        with_idx = next((i for i, ln in enumerate(lines) if "with " in ln), -1)
        write_idx = next((i for i, ln in enumerate(lines) if "write" in ln), -1)
        self.assertGreater(write_idx, with_idx)
        with_indent = len(lines[with_idx]) - len(lines[with_idx].lstrip())
        write_indent = len(lines[write_idx]) - len(lines[write_idx].lstrip())
        self.assertGreater(write_indent, with_indent)

    def test_with_body_multiple_statements_preserved(self):
        src = "def f(a, b):\n    with open('t', 'wb') as fh:\n        fh.write(a)\n        fh.write(b)\n"
        out = decompile(src)
        self.assertEqual(out.count("write"), 2)

    def test_with_body_as_binding_preserved(self):
        src = "with open('f') as fh:\n    data = fh.read()\n"
        out = decompile(src)
        self.assertIn("fh", out)
        self.assertIn("fh.read()", out)

    def test_with_no_exit_epilogue_leakage(self):
        src = "def f(data):\n    with open('t', 'wb') as fh:\n        fh.write(data)\n"
        out = decompile(src)
        self.assertNotIn("None(None, None)", out)

    def test_with_no_call_function_3_leakage(self):
        src = "with open('f') as fh:\n    pass\n"
        out = decompile(src)
        self.assertNotIn("(None, None, None)", out)


class TestDottedExceptionTypes(unittest.TestCase):
    def test_dotted_exc_type_in_header(self):
        src = "import socket\ndef f():\n    try:\n        pass\n    except socket.error:\n        pass\n"
        out = decompile(src)
        self.assertTrue("socket.error" in out or "socket" in out)
        self.assertIn("except", out)

    def test_dotted_exc_type_with_as_binding(self):
        src = "import socket\ndef f():\n    try:\n        pass\n    except socket.error as e:\n        pass\n"
        out = decompile(src)
        self.assertIn("except", out)
        self.assertIn("e", out)

    def test_dotted_exc_type_os_error(self):
        src = "import os\ndef f():\n    try:\n        os.listdir('/nonexistent')\n    except os.error:\n        pass\n"
        out = decompile(src)
        self.assertIn("except", out)

    def test_simple_exc_type_still_works(self):
        src = "def f():\n    try:\n        x = int('a')\n    except ValueError:\n        x = 0\n"
        out = decompile(src)
        self.assertIn("except ValueError", out)

    def test_multi_dotted_exc_no_spurious_bare_except(self):
        src = "import socket\ndef f():\n    try:\n        pass\n    except socket.error as e:\n        pass\n"
        out = decompile(src)
        self.assertNotIn("except:", out.splitlines())


@unittest.skipIf(sys.version_info >= (3, 11), "Python 3.11+ compiler optimizes away this try/break structure")
class TestBreakInTryInsideWhile(unittest.TestCase):
    def test_break_is_emitted(self):
        src = "def f(a):\n    import socket\n    while a:\n        try:\n            break\n        except socket.error as e:\n            raise\n"
        out = decompile(src)
        self.assertIn("break", out)

    def test_break_inside_try_correct_indent(self):
        src = "def f(items):\n    while items:\n        try:\n            break\n        except Exception:\n            pass\n"
        out = decompile(src)
        lines = out.splitlines()
        try_lines = [ln for ln in lines if ln.lstrip().rstrip() == "try:"]
        break_lines = [ln for ln in lines if ln.lstrip().rstrip() == "break"]
        self.assertTrue(try_lines and break_lines)
        try_indent = len(try_lines[0]) - len(try_lines[0].lstrip())
        break_indent = len(break_lines[0]) - len(break_lines[0].lstrip())
        self.assertGreater(break_indent, try_indent)

    def test_no_spurious_bare_except_before_typed_handler(self):
        src = "def f(items):\n    import socket\n    while items:\n        try:\n            break\n        except socket.error as e:\n            raise\n"
        out = decompile(src)
        self.assertNotIn("except:", [ln.strip() for ln in out.splitlines()])

    def test_except_body_correct_indent_after_break(self):
        src = "def f(items):\n    while items:\n        try:\n            break\n        except Exception as e:\n            pass\n"
        out = decompile(src)
        lines = out.splitlines()
        except_lines = [ln for ln in lines if "except" in ln and ln.lstrip().startswith("except")]
        self.assertTrue(except_lines)
        exc_indent = len(except_lines[0]) - len(except_lines[0].lstrip())
        for i, ln in enumerate(lines):
            if except_lines[0] in ln:
                for j in range(i + 1, len(lines)):
                    if lines[j].strip():
                        body_indent = len(lines[j]) - len(lines[j].lstrip())
                        self.assertGreater(body_indent, exc_indent)
                        break
                break

    def test_while_try_break_simple_except_no_as(self):
        src = "def f(items):\n    while items:\n        try:\n            break\n        except ValueError:\n            items.clear()\n"
        out = decompile(src)
        self.assertIn("break", out)
        self.assertIn("except", out)

    def test_try_except_cleanup_names_suppressed(self):
        src = "def f():\n    import socket\n    while True:\n        try:\n            break\n        except socket.error as e:\n            raise\n"
        out = decompile(src)
        self.assertNotIn("e = None", out)
        self.assertNotIn("del e", out)


class TestNestedTryInsideExcept(unittest.TestCase):
    def _run39_full(self, instructions):
        return _run39_full_impl(instructions)

    def test_nested_try_inside_except_emits_try(self):
        from pycrefine import BytecodeInstruction as Instr
        instructions = [
            Instr(100, "LOAD_CONST",          0,  0,          0, True,  False),
            Instr(125, "STORE_FAST",          2,  "var3",     2, None,  False),
            Instr(122, "SETUP_FINALLY",       14, 14,         4, None,  False),
            Instr(124, "LOAD_FAST",           0,  "var1",     6, None,  False),
            Instr(125, "STORE_FAST",          2,  "var3",     8, None,  False),
            Instr(87, "POP_BLOCK",          None, None,     10, None,  False),
            Instr(110, "JUMP_FORWARD",        56, 56,        12, None,  False),
            Instr(4, "DUP_TOP",            None, None,     14, None,  True),
            Instr(116, "LOAD_GLOBAL",         0,  "Exception", 16, None,  False),
            Instr(18, "JUMP_IF_NOT_EXC_MATCH", 54, 54,        18, None,  False),
            Instr(1, "POP_TOP",            None, None,     20, None,  False),
            Instr(1, "POP_TOP",            None, None,     22, None,  False),
            Instr(1, "POP_TOP",            None, None,     24, None,  False),
            Instr(122, "SETUP_FINALLY",       36, 36,        26, None,  False),
            Instr(124, "LOAD_FAST",           1,  "var2",    28, None,  False),
            Instr(125, "STORE_FAST",          2,  "var3",    30, None,  False),
            Instr(87, "POP_BLOCK",          None, None,     32, None,  False),
            Instr(110, "JUMP_FORWARD",        52, 52,        34, None,  False),
            Instr(4, "DUP_TOP",            None, None,     36, None,  True),
            Instr(116, "LOAD_GLOBAL",         0,  "Exception", 38, None,  False),
            Instr(18, "JUMP_IF_NOT_EXC_MATCH", 50, 50,        40, None,  False),
            Instr(1, "POP_TOP",            None, None,     42, None,  False),
            Instr(1, "POP_TOP",            None, None,     44, None,  False),
            Instr(1, "POP_TOP",            None, None,     46, None,  False),
            Instr(89, "POP_EXCEPT",         None, None,     48, None,  False),
            Instr(48, "RERAISE",            None, None,     50, None,  True),
            Instr(89, "POP_EXCEPT",         None, None,     52, None,  True),
            Instr(48, "RERAISE",            None, None,     54, None,  True),
            Instr(100, "LOAD_CONST",          0,  None,      56, None,  True),
            Instr(83, "RETURN_VALUE",       None, None,     58, None,  False),
        ]
        out = self._run39_full(instructions)
        self.assertEqual(out.count("try:"), 2)

    def test_nested_try_no_duplicate_except_at_same_level(self):
        from pycrefine import BytecodeInstruction as Instr
        instructions = [
            Instr(100, "LOAD_CONST",           0,  0,          0, True,  False),
            Instr(125, "STORE_FAST",           2,  "var3",     2, None,  False),
            Instr(122, "SETUP_FINALLY",        14, 14,         4, None,  False),
            Instr(124, "LOAD_FAST",            0,  "var1",     6, None,  False),
            Instr(125, "STORE_FAST",           2,  "var3",     8, None,  False),
            Instr(87, "POP_BLOCK",           None, None,     10, None,  False),
            Instr(110, "JUMP_FORWARD",         56, 56,        12, None,  False),
            Instr(4, "DUP_TOP",             None, None,     14, None,  True),
            Instr(116, "LOAD_GLOBAL",          0,  "Exception", 16, None,  False),
            Instr(18, "JUMP_IF_NOT_EXC_MATCH", 54, 54,        18, None, False),
            Instr(1, "POP_TOP",             None, None,     20, None,  False),
            Instr(1, "POP_TOP",             None, None,     22, None,  False),
            Instr(1, "POP_TOP",             None, None,     24, None,  False),
            Instr(122, "SETUP_FINALLY",        36, 36,        26, None,  False),
            Instr(124, "LOAD_FAST",            1,  "var2",    28, None,  False),
            Instr(125, "STORE_FAST",           2,  "var3",    30, None,  False),
            Instr(87, "POP_BLOCK",           None, None,     32, None,  False),
            Instr(110, "JUMP_FORWARD",         52, 52,        34, None,  False),
            Instr(4, "DUP_TOP",             None, None,     36, None,  True),
            Instr(116, "LOAD_GLOBAL",          0,  "Exception", 38, None,  False),
            Instr(18, "JUMP_IF_NOT_EXC_MATCH", 50, 50,        40, None, False),
            Instr(1, "POP_TOP",             None, None,     42, None,  False),
            Instr(1, "POP_TOP",             None, None,     44, None,  False),
            Instr(1, "POP_TOP",             None, None,     46, None,  False),
            Instr(89, "POP_EXCEPT",          None, None,     48, None,  False),
            Instr(48, "RERAISE",             None, None,     50, None,  True),
            Instr(89, "POP_EXCEPT",          None, None,     52, None,  True),
            Instr(48, "RERAISE",             None, None,     54, None,  True),
            Instr(100, "LOAD_CONST",           0,  None,      56, None,  True),
            Instr(83, "RETURN_VALUE",        None, None,     58, None,  False),
        ]
        out = self._run39_full(instructions)
        except_lines = [line for line in out.splitlines() if "except Exception" in line]
        self.assertEqual(len(except_lines), 2)
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
        from pycrefine import BytecodeInstruction as Instr

        # Minimal: outer SETUP_FINALLY → DUP_TOP, then inside handler
        # an inner SETUP_FINALLY → RERAISE (cleanup guard, NOT DUP_TOP).
        instructions = [
            Instr(122, "SETUP_FINALLY",        8, 8,          0, True,  False),
            Instr(100, "LOAD_CONST",           1,  42,         2, None,  False),
            Instr(87, "POP_BLOCK",           None, None,      4, None,  False),
            Instr(110, "JUMP_FORWARD",         26, 26,         6, None,  False),
            Instr(4, "DUP_TOP",             None, None,      8, None,  True),
            Instr(116, "LOAD_GLOBAL",          0,  "Exception", 10, None,  False),
            Instr(18, "JUMP_IF_NOT_EXC_MATCH", 24, 24,        12, None, False),
            Instr(1, "POP_TOP",             None, None,     14, None,  False),
            Instr(1, "POP_TOP",             None, None,     16, None,  False),
            Instr(1, "POP_TOP",             None, None,     18, None,  False),
            # Inner SETUP_FINALLY → RERAISE (the 'as e' cleanup pattern)
            Instr(122, "SETUP_FINALLY",        22, 22,        20, None,  False),
            Instr(89, "POP_EXCEPT",          None, None,     22, None,  True),  # ← target=RERAISE path
            Instr(48, "RERAISE",             None, None,     24, None,  True),
            Instr(100, "LOAD_CONST",           0,  None,      26, None,  True),
            Instr(83, "RETURN_VALUE",        None, None,     28, None,  False),
        ]
        out = self._run39_full(instructions)
        # The outer try: is real and must appear
        self.assertIn("try:", out, f"Outer try: missing:\n{out}")
        # The inner SETUP_FINALLY is an 'as e' guard and must NOT add a second try:
        try_count = out.count("try:")
        self.assertEqual(try_count, 1,
                         f"Expected exactly 1 try:, got {try_count} (inner guard leaked):\n{out}")


class TestNestedTryInForLoop39(unittest.TestCase):
    """Regression tests for Python 3.9 (SETUP_FINALLY) try/except inside for loops.

    Covers three distinct bugs:
    1. POP_JUMP_IF_FALSE inside a try body was misread as a 'while' condition instead of 'if'.
    2. JUMP_ABSOLUTE at end of exception handler body (for 'continue') was rendered as 'pass'.
    3. 'except' header indentation was wrong due to the spurious 'while' block nesting.

    The synthetic bytecode below represents the pattern:
        for offset in (16, 12, 8, 4):
            try:
                obj = in_b
                if isinstance(obj, int):
                    code_obj = obj
                    break
            except Exception:
                continue
    """

    def _run39(self, instructions):
        return _run39_full_impl(instructions)

    def _make_instructions(self):
        """Build the canonical Python 3.9 bytecode for the for+try+break+continue pattern."""
        from pycrefine import BytecodeInstruction as Instr
        # Offsets and structure:
        #   0:  LOAD_CONST (16,12,8,4)        get iterable
        #   2:  GET_ITER
        #   4:  FOR_ITER -> 60 (end of for)    ← is_jump_target
        #   6:  STORE_FAST offset               ← is_jump_target (loop top)
        #   8:  SETUP_FINALLY -> 44             open try, handler at 44
        #  10:  LOAD_FAST in_b
        #  12:  STORE_FAST obj
        #  14:  LOAD_GLOBAL isinstance
        #  16:  LOAD_FAST obj
        #  18:  LOAD_GLOBAL int
        #  20:  CALL_FUNCTION 2
        #  22:  POP_JUMP_IF_FALSE -> 34         if false: skip to POP_BLOCK (NOT while)
        #  24:  LOAD_FAST in_b
        #  26:  STORE_FAST code_obj
        #  28:  JUMP_ABSOLUTE 60               break: jump past FOR_ITER end
        #  30:  NOP (padding)
        #  32:  NOP (padding)
        #  34:  POP_BLOCK                       ← is_jump_target (POP_JUMP_IF_FALSE False target)
        #  36:  JUMP_ABSOLUTE 4                 for-loop natural continue (NOT 'continue' keyword)
        #  38-42: NOPs
        #  44:  DUP_TOP                        ← is_jump_target (handler entry)
        #  46:  LOAD_GLOBAL Exception
        #  48:  JUMP_IF_NOT_EXC_MATCH -> 58
        #  50:  POP_TOP                        strip exc type
        #  52:  POP_TOP                        strip exc value
        #  54:  POP_TOP                        strip exc tb
        #  56:  JUMP_ABSOLUTE 4                continue: jump to FOR_ITER top
        #  58:  POP_EXCEPT                     ← is_jump_target (no-match path)
        #  60:  RERAISE 0                      ← is_jump_target (FOR_ITER end)
        #  62:  LOAD_CONST None
        #  64:  RETURN_VALUE
        return [
            Instr(100, "LOAD_CONST",          1, (16,12,8,4),  0,  True,  False),
            Instr(68,  "GET_ITER",          None, None,          2,  None,  False),
            Instr(93,  "FOR_ITER",            56, 60,            4,  None,  True),   # FOR_ITER end=60
            Instr(125, "STORE_FAST",           0, "offset",      6,  None,  True),   # loop top
            Instr(122, "SETUP_FINALLY",        36, 44,           8,  None,  False),  # handler@44
            Instr(124, "LOAD_FAST",            1, "in_b",       10,  None,  False),
            Instr(125, "STORE_FAST",           2, "obj",        12,  None,  False),
            Instr(116, "LOAD_GLOBAL",          0, "isinstance", 14,  None,  False),
            Instr(124, "LOAD_FAST",            2, "obj",        16,  None,  False),
            Instr(116, "LOAD_GLOBAL",          1, "int",        18,  None,  False),
            Instr(131, "CALL_FUNCTION",        2, 2,            20,  None,  False),
            Instr(114, "POP_JUMP_IF_FALSE",   34, 34,           22,  None,  False),  # if-false -> POP_BLOCK
            Instr(124, "LOAD_FAST",            1, "in_b",       24,  None,  False),
            Instr(125, "STORE_FAST",           3, "code_obj",   26,  None,  False),
            Instr(113, "JUMP_ABSOLUTE",       60, 60,           28,  None,  False),  # break
            Instr(9,   "NOP",               None, None,         30,  None,  False),
            Instr(9,   "NOP",               None, None,         32,  None,  False),
            Instr(87,  "POP_BLOCK",         None, None,         34,  None,  True),   # is_jump_target
            Instr(113, "JUMP_ABSOLUTE",        4, 4,            36,  None,  False),  # for-loop natural
            Instr(9,   "NOP",               None, None,         38,  None,  False),
            Instr(9,   "NOP",               None, None,         40,  None,  False),
            Instr(9,   "NOP",               None, None,         42,  None,  False),
            Instr(4,   "DUP_TOP",           None, None,         44,  None,  True),   # handler entry
            Instr(116, "LOAD_GLOBAL",          0, "Exception",  46,  None,  False),
            Instr(18,  "JUMP_IF_NOT_EXC_MATCH", 58, 58,        48,  None,  False),
            Instr(1,   "POP_TOP",           None, None,         50,  None,  False),
            Instr(1,   "POP_TOP",           None, None,         52,  None,  False),
            Instr(1,   "POP_TOP",           None, None,         54,  None,  False),
            Instr(113, "JUMP_ABSOLUTE",        4, 4,            56,  None,  False),  # continue
            Instr(89,  "POP_EXCEPT",        None, None,         58,  None,  True),
            Instr(48,  "RERAISE",              0, 0,            60,  None,  True),   # FOR_ITER end
            Instr(100, "LOAD_CONST",           0, None,         62,  None,  True),
            Instr(83,  "RETURN_VALUE",      None, None,         64,  None,  False),
        ]

    def test_39_try_in_for_no_spurious_while(self):
        """POP_JUMP_IF_FALSE inside a try body must emit 'if', not 'while'."""
        out = self._run39(self._make_instructions())
        self.assertNotIn("while isinstance", out,
                         f"Spurious 'while' detected (should be 'if'):\n{out}")
        self.assertIn("if isinstance", out,
                      f"'if isinstance' missing:\n{out}")

    def test_39_try_in_for_except_emits_continue(self):
        """JUMP_ABSOLUTE at handler exit jumping to FOR_ITER must emit 'continue'."""
        out = self._run39(self._make_instructions())
        self.assertIn("continue", out,
                      f"'continue' not emitted in except handler:\n{out}")
        # The 'continue' should NOT be a bare 'pass'
        handler_lines = []
        capture = False
        for ln in out.splitlines():
            if "except Exception" in ln:
                capture = True
            elif capture and ln.strip():
                handler_lines.append(ln.strip())
                break
        self.assertFalse(
            handler_lines == ["pass"],
            f"except handler emitted 'pass' instead of 'continue':\n{out}",
        )

    def test_39_try_in_for_except_correct_indent(self):
        """'except Exception:' must be at the same indent level as 'try:'."""
        out = self._run39(self._make_instructions())
        lines = out.splitlines()
        try_lines   = [ln for ln in lines if ln.lstrip() == "try:"]
        except_lines = [ln for ln in lines if ln.lstrip().startswith("except Exception")]
        self.assertTrue(try_lines,   f"No 'try:' found:\n{out}")
        self.assertTrue(except_lines, f"No 'except Exception' found:\n{out}")
        try_indent    = len(try_lines[0])   - len(try_lines[0].lstrip())
        except_indent = len(except_lines[0]) - len(except_lines[0].lstrip())
        self.assertEqual(
            try_indent, except_indent,
            f"'try:' at indent {try_indent} but 'except' at indent {except_indent}:\n{out}",
        )

    def test_39_try_in_for_break_emitted(self):
        """JUMP_ABSOLUTE past the FOR_ITER end inside the try body must emit 'break'."""
        out = self._run39(self._make_instructions())
        self.assertIn("break", out, f"'break' not found:\n{out}")
        # break must appear inside the try/if body, not after the for loop
        lines = out.splitlines()
        try_idx   = next((i for i, ln in enumerate(lines) if ln.lstrip() == "try:"), -1)
        break_idx = next((i for i, ln in enumerate(lines) if ln.strip() == "break"), -1)
        self.assertGreater(break_idx, try_idx, f"'break' appears before 'try:':\n{out}")


class TestApi26Pattern39(unittest.TestCase):
    """Regression tests for the api_26 for+try+break+continue pattern on Python 3.9.

    This class exercises the EXACT bytecode that CPython 3.9 generates for:

        for offset in (16, 12, 8, 4):
            try:
                obj = in_b
                if isinstance(obj, int):
                    code_obj = obj
                    break
            except Exception:
                continue

    Key structural differences from the existing synthetic TestNestedTryInForLoop39:
      1. break path:  POP_BLOCK -> POP_TOP -> JUMP_ABSOLUTE(past FOR_ITER end)
         (the extra POP_TOP pops the for-iterator off the operand stack)
      2. Natural loop continue (if-false path):
         POP_BLOCK -> JUMP_ABSOLUTE(FOR_ITER offset)
         This is NOT a 'continue' statement -- it is the normal end-of-body step.
      3. Handler continue:  POP_EXCEPT -> JUMP_ABSOLUTE(FOR_ITER offset)
         This IS the 'continue' keyword and must NOT be emitted as 'pass'.

    Bugs detected by these tests:
      - [BUG-1] JUMP_ABSOLUTE(FOR_ITER) after POP_BLOCK (the if-false natural
        continue path) was being retroactively rewritten from 'if isinstance(...):'
        into 'while isinstance(...):'.
      - [BUG-2] JUMP_ABSOLUTE after POP_EXCEPT was emitted as 'pass' instead of
        'continue' because the prescan loop broke at POP_EXCEPT and never marked
        the subsequent JUMP_ABSOLUTE in _exc_handler_jump_offsets.
    """

    def _run39(self, instructions):
        return _run39_full_impl(instructions)

    def _make_api26_instructions(self):
        """Build the exact CPython 3.9 bytecode for the api_26 first for-loop block.

        Offset map (matches real api_26 bytecode from Python 3.9.13):

          0:  LOAD_CONST (16,12,8,4)         -- load the tuple
          2:  GET_ITER
          4:  FOR_ITER -> 74                 <- is_jump_target (loop head)
          6:  STORE_FAST offset              <- is_jump_target
          8:  SETUP_FINALLY -> 50            (handler at 50)
         10:  LOAD_FAST in_b
         12:  STORE_FAST obj
         14:  LOAD_GLOBAL isinstance
         16:  LOAD_FAST obj
         18:  LOAD_GLOBAL int
         20:  CALL_FUNCTION 2
         22:  POP_JUMP_IF_FALSE -> 46        (if-false -> POP_BLOCK natural continue)
         24:  LOAD_FAST obj
         26:  STORE_FAST code_obj
         28:  POP_BLOCK                      (break: clean exit from try)
         30:  POP_TOP                        (break: pop the for-iterator off stack)
         32:  JUMP_ABSOLUTE 74              (break: jump past FOR_ITER end)
         34-44: NOPs
         46:  POP_BLOCK                     <- is_jump_target (POP_JUMP_IF_FALSE target)
         48:  JUMP_ABSOLUTE 4               (natural for-loop continue, NOT 'continue')
        --- handler ---
         50:  DUP_TOP                       <- is_jump_target (handler entry)
         52:  LOAD_GLOBAL Exception
         54:  JUMP_IF_NOT_EXC_MATCH -> 70
         56:  POP_TOP
         58:  POP_TOP
         60:  POP_TOP
         62:  POP_EXCEPT                    (handler exit)
         64:  JUMP_ABSOLUTE 4               (continue: back to FOR_ITER)
         66:  POP_EXCEPT
         68:  JUMP_ABSOLUTE 4
         70:  RERAISE                       <- is_jump_target
         72:  JUMP_ABSOLUTE 4
         74:  LOAD_CONST None               <- is_jump_target (FOR_ITER exhausted)
         76:  RETURN_VALUE
        """
        from pycrefine import BytecodeInstruction as Instr
        return [
            Instr(100, "LOAD_CONST",              1, (16,12,8,4),  0,  True,  False),
            Instr(68,  "GET_ITER",              None, None,          2,  None,  False),
            Instr(93,  "FOR_ITER",                68, 74,            4,  None,  True),  # FOR_ITER end=74
            Instr(125, "STORE_FAST",               0, "offset",      6,  None,  True),
            Instr(122, "SETUP_FINALLY",            40, 50,           8,  None,  False), # handler@50
            Instr(124, "LOAD_FAST",                1, "in_b",       10,  None,  False),
            Instr(125, "STORE_FAST",               2, "obj",        12,  None,  False),
            Instr(116, "LOAD_GLOBAL",              0, "isinstance", 14,  None,  False),
            Instr(124, "LOAD_FAST",                2, "obj",        16,  None,  False),
            Instr(116, "LOAD_GLOBAL",              1, "int",        18,  None,  False),
            Instr(131, "CALL_FUNCTION",            2, 2,            20,  None,  False),
            Instr(114, "POP_JUMP_IF_FALSE",       46, 46,           22,  None,  False), # if false -> POP_BLOCK
            Instr(124, "LOAD_FAST",                2, "obj",        24,  None,  False),
            Instr(125, "STORE_FAST",               3, "code_obj",   26,  None,  False),
            # break path: POP_BLOCK -> POP_TOP -> JUMP_ABSOLUTE(74)
            Instr(87,  "POP_BLOCK",             None, None,         28,  None,  False),
            Instr(1,   "POP_TOP",               None, None,         30,  None,  False), # pop iterator
            Instr(113, "JUMP_ABSOLUTE",            74, 74,          32,  None,  False), # break
            Instr(9,   "NOP",                   None, None,         34,  None,  False),
            Instr(9,   "NOP",                   None, None,         36,  None,  False),
            Instr(9,   "NOP",                   None, None,         38,  None,  False),
            Instr(9,   "NOP",                   None, None,         40,  None,  False),
            Instr(9,   "NOP",                   None, None,         42,  None,  False),
            Instr(9,   "NOP",                   None, None,         44,  None,  False),
            # natural loop continue: POP_BLOCK -> JUMP_ABSOLUTE(4) -- NOT 'continue'
            Instr(87,  "POP_BLOCK",             None, None,         46,  None,  True),  # POP_JUMP_IF_FALSE target
            Instr(113, "JUMP_ABSOLUTE",             4, 4,           48,  None,  False), # natural continue
            # exception handler entry
            Instr(4,   "DUP_TOP",               None, None,         50,  None,  True),
            Instr(116, "LOAD_GLOBAL",              2, "Exception",  52,  None,  False),
            Instr(121, "JUMP_IF_NOT_EXC_MATCH",   70, 70,          54,  None,  False),
            Instr(1,   "POP_TOP",               None, None,         56,  None,  False),
            Instr(1,   "POP_TOP",               None, None,         58,  None,  False),
            Instr(1,   "POP_TOP",               None, None,         60,  None,  False),
            # continue: POP_EXCEPT -> JUMP_ABSOLUTE(4)
            Instr(89,  "POP_EXCEPT",            None, None,         62,  None,  False),
            Instr(113, "JUMP_ABSOLUTE",             4, 4,           64,  None,  False), # continue
            Instr(89,  "POP_EXCEPT",            None, None,         66,  None,  False),
            Instr(113, "JUMP_ABSOLUTE",             4, 4,           68,  None,  False),
            Instr(48,  "RERAISE",                   0, 0,           70,  None,  True),
            Instr(113, "JUMP_ABSOLUTE",             4, 4,           72,  None,  False),
            # post-loop
            Instr(100, "LOAD_CONST",               0, None,         74,  None,  True),
            Instr(83,  "RETURN_VALUE",          None, None,         76,  None,  False),
        ]

    def test_api26_except_handler_emits_continue_not_pass(self):
        """BUG-2: POP_EXCEPT followed by JUMP_ABSOLUTE(FOR_ITER) must emit 'continue'.

        The except handler body is empty except for 'continue'.  Before the fix
        the decompiler emitted 'pass' because the prescan loop broke at POP_EXCEPT
        and never marked the subsequent JUMP_ABSOLUTE in _exc_handler_jump_offsets,
        and the Decompiler39 POP_EXCEPT handler did not check the next instruction.
        """
        out = self._run39(self._make_api26_instructions())
        self.assertIn("continue", out,
                      f"'continue' not emitted in except handler:\n{out}")
        handler_body = []
        capture = False
        for ln in out.splitlines():
            if "except Exception" in ln:
                capture = True
            elif capture and ln.strip():
                handler_body.append(ln.strip())
                break
        self.assertNotEqual(
            handler_body, ["pass"],
            f"except handler body is 'pass' (expected 'continue'):\n{out}",
        )

    def test_api26_isinstance_emits_if_not_while(self):
        """BUG-1: POP_JUMP_IF_FALSE whose false-target is POP_BLOCK (natural
        for-loop continue) must emit 'if isinstance(...):', NOT 'while isinstance(...):'

        The JUMP_ABSOLUTE after POP_BLOCK (offset 48 -> 4) is the natural for-loop
        step, not a while back-edge.  Before the fix the decompiler retroactively
        rewrote 'if isinstance' -> 'while isinstance'.
        """
        out = self._run39(self._make_api26_instructions())
        self.assertIn("if isinstance", out,
                      f"'if isinstance' not found in output:\n{out}")
        self.assertNotIn("while isinstance", out,
                         f"Spurious 'while isinstance' detected:\n{out}")

    def test_api26_break_emitted_inside_if_body(self):
        """The break path (POP_BLOCK -> POP_TOP -> JUMP_ABSOLUTE past FOR_ITER end)
        must emit 'break' inside the if body, not at the for-loop level.
        """
        out = self._run39(self._make_api26_instructions())
        self.assertIn("break", out, f"'break' not found:\n{out}")
        lines = out.splitlines()
        if_idx    = next((i for i, ln in enumerate(lines)
                          if "if isinstance" in ln), -1)
        break_idx = next((i for i, ln in enumerate(lines)
                          if ln.strip() == "break"), -1)
        self.assertGreater(break_idx, if_idx,
                           f"'break' appears before 'if isinstance':\n{out}")

    def test_api26_except_correct_indent(self):
        """BUG-1 side-effect: 'except Exception:' must align with 'try:',
        not be indented deeper due to the spurious 'while' block nesting.
        """
        out = self._run39(self._make_api26_instructions())
        lines = out.splitlines()
        try_lines    = [ln for ln in lines if ln.lstrip() == "try:"]
        except_lines = [ln for ln in lines
                        if ln.lstrip().startswith("except Exception")]
        self.assertTrue(try_lines,    f"No 'try:' found:\n{out}")
        self.assertTrue(except_lines, f"No 'except Exception' found:\n{out}")
        try_indent    = len(try_lines[0])    - len(try_lines[0].lstrip())
        except_indent = len(except_lines[0]) - len(except_lines[0].lstrip())
        self.assertEqual(
            try_indent, except_indent,
            f"'try:' at indent {try_indent} but 'except' at indent {except_indent}:\n{out}",
        )


if __name__ == "__main__":
    unittest.main()
