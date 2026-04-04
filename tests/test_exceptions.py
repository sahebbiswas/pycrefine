import unittest
import sys
from .test_helpers import decompile, assert_contains

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
        try_pos    = next(i for i, ln in enumerate(lines) if ln == "try:")
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
        except_pos  = next((i for i, ln in enumerate(lines) if ln.startswith("except")), -1)
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
        try_lines  = [ln for ln in lines if ln.lstrip().rstrip() == "try:"]
        break_lines = [ln for ln in lines if ln.lstrip().rstrip() == "break"]
        self.assertTrue(try_lines and break_lines)
        try_indent   = len(try_lines[0])  - len(try_lines[0].lstrip())
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

from tests.test_helpers import _run39_full_impl

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
            Instr( 87, "POP_BLOCK",          None, None,     10, None,  False),
            Instr(110, "JUMP_FORWARD",        56, 56,        12, None,  False),
            Instr(  4, "DUP_TOP",            None, None,     14, None,  True ),
            Instr(116, "LOAD_GLOBAL",         0,  "Exception",16,None,  False),
            Instr( 18, "JUMP_IF_NOT_EXC_MATCH",54,54,        18, None,  False),
            Instr(  1, "POP_TOP",            None, None,     20, None,  False),
            Instr(  1, "POP_TOP",            None, None,     22, None,  False),
            Instr(  1, "POP_TOP",            None, None,     24, None,  False),
            Instr(122, "SETUP_FINALLY",       36, 36,        26, None,  False),
            Instr(124, "LOAD_FAST",           1,  "var2",    28, None,  False),
            Instr(125, "STORE_FAST",          2,  "var3",    30, None,  False),
            Instr( 87, "POP_BLOCK",          None, None,     32, None,  False),
            Instr(110, "JUMP_FORWARD",        52, 52,        34, None,  False),
            Instr(  4, "DUP_TOP",            None, None,     36, None,  True ),
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
        self.assertEqual(out.count("try:"), 2)

    def test_nested_try_no_duplicate_except_at_same_level(self):
        from pycrefine import BytecodeInstruction as Instr
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

if __name__ == "__main__":
    unittest.main()
