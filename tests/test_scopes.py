"""Scope declarations: `global` and `nonlocal` (#155)."""
import contextlib
import io
import sys
import unittest

from .test_helpers import _run39_full_impl, decompile


def _run(code):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, {"__name__": "__main__"})
    return buf.getvalue()


@unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 11)), "3.10/3.11 have known control-flow failures")
class TestGlobalAndNonlocal(unittest.TestCase):

    def assert_same_behavior(self, src):
        out = decompile(src)
        self.assertEqual(_run(compile(out, "<decompiled>", "exec")), _run(compile(src, "<original>", "exec")), out)
        return out

    def test_global_assignment(self):
        out = self.assert_same_behavior(
            "Z = 0\ndef g(a):\n    global Z\n    Z = a\ng(5)\nprint(Z)\n")
        self.assertIn("    global Z\n", out)

    def test_global_augmented_assignment_and_delete(self):
        out = self.assert_same_behavior(
            "count = 1\nW = 0\ndef h():\n    global count, W\n    count += 1\n    W = count\n    del count\n"
            "h()\nprint(W, 'count' in globals())\n")
        self.assertIn("global count, W", out)

    def test_global_import_inside_function(self):
        out = self.assert_same_behavior(
            "def load():\n    global json\n    import json\nload()\nprint(json.dumps([1]))\n")
        self.assertIn("global json", out)

    def test_reads_need_no_declaration(self):
        out = decompile("Z = 1\ndef r():\n    return Z\n")
        self.assertNotIn("global", out)

    def test_readonly_global_shadowed_by_enclosing_function(self):
        out = self.assert_same_behavior(
            "x = 1\ndef outer():\n    x = 2\n    def inner():\n        global x\n        return x\n"
            "    return inner()\nprint(outer())\n")
        self.assertIn("        global x\n", out)

    def test_nonlocal_assignment(self):
        out = self.assert_same_behavior(
            "def outer():\n    n = 0\n    def inc():\n        nonlocal n\n        n += 1\n    inc()\n    inc()\n"
            "    return n\nprint(outer())\n")
        self.assertIn("        nonlocal n\n", out)

    def test_nonlocal_delete(self):
        out = self.assert_same_behavior(
            "def outer():\n    m = 1\n    def d():\n        nonlocal m\n        del m\n    d()\n"
            "    try:\n        return m\n    except NameError:\n        return 'deleted'\nprint(outer())\n")
        self.assertIn("nonlocal m", out)

    def test_closure_reads_need_no_declaration(self):
        out = decompile("def outer():\n    n = 1\n    def get():\n        return n\n    return get\n")
        self.assertNotIn("nonlocal", out)

    def test_class_body_global(self):
        out = self.assert_same_behavior("class C:\n    global Q\n    Q = 5\nprint(Q)\n")
        self.assertIn("global Q", out)

    def test_declarations_follow_the_docstring(self):
        out = decompile('Z = 0\ndef g(a):\n    """Doc."""\n    global Z\n    Z = a\n')
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        body = lines[lines.index("def g(a):") + 1:]
        self.assertEqual(body[-2:], ["global Z", "Z = a"])

    def test_module_level_has_no_declaration(self):
        out = decompile("global Z\nZ = 1\n")
        self.assertNotIn("global", out)

    def test_except_as_name_captured_by_a_closure(self):
        # The `as` name is a cell variable here (STORE_DEREF / DELETE_DEREF): it is
        # bound by the except clause, and its cleanup is not source code.
        out = self.assert_same_behavior(
            "def f():\n    try:\n        raise ValueError('v')\n    except ValueError as e:\n"
            "        g = lambda: e\n        print(g())\nf()\n")
        self.assertIn("except ValueError as e:", out)
        self.assertNotIn("del e", out)

    def test_except_as_preserves_later_user_store_and_delete(self):
        out = self.assert_same_behavior(
            "Z = 1\ndef f():\n    global Z\n    try:\n        raise ValueError('v')\n"
            "    except ValueError as Z:\n        print(type(Z).__name__)\n"
            "    Z = None\n    print(Z)\n    Z = 2\n    del Z\n    print('Z' in globals())\nf()\n")
        self.assertIn("Z = None", out)
        self.assertIn("del Z", out)

    def test_except_as_39_preserves_later_user_store_and_delete(self):
        from pycrefine import BytecodeInstruction as Instr

        out = _run39_full_impl([
            Instr(122, "SETUP_FINALLY",         None, 8,    0, True,  False),
            Instr(87,  "POP_BLOCK",             None, None, 2, None,  False),
            Instr(110, "JUMP_FORWARD",          None, 40,   4, None,  False),
            Instr(4,   "DUP_TOP",               None, None, 8, None,  True),
            Instr(116, "LOAD_GLOBAL",           0, "ValueError", 10, None, False),
            Instr(121, "JUMP_IF_NOT_EXC_MATCH", None, 38,   12, None, False),
            Instr(1,   "POP_TOP",               None, None, 14, None, False),
            Instr(97,  "STORE_GLOBAL",          1, "Z",     16, None, False),
            Instr(1,   "POP_TOP",               None, None, 18, None, False),
            Instr(122, "SETUP_FINALLY",         None, 30,   20, None, False),
            Instr(87,  "POP_BLOCK",             None, None, 22, None, False),
            Instr(89,  "POP_EXCEPT",            None, None, 24, None, False),
            Instr(100, "LOAD_CONST",            0, None,    26, None, False),
            Instr(97,  "STORE_GLOBAL",          1, "Z",     28, None, False),
            Instr(98,  "DELETE_GLOBAL",         1, "Z",     29, None, False),
            Instr(110, "JUMP_FORWARD",          None, 40,   30, None, False),
            Instr(100, "LOAD_CONST",            0, None,    32, None, True),
            Instr(97,  "STORE_GLOBAL",          1, "Z",     34, None, False),
            Instr(98,  "DELETE_GLOBAL",         1, "Z",     35, None, False),
            Instr(48,  "RERAISE",               None, None, 36, None, False),
            Instr(48,  "RERAISE",               None, None, 38, None, True),
            Instr(100, "LOAD_CONST",            0, None,    40, None, True),
            Instr(97,  "STORE_GLOBAL",          1, "Z",     42, None, False),
            Instr(98,  "DELETE_GLOBAL",         1, "Z",     44, None, False),
            Instr(100, "LOAD_CONST",            0, None,    46, None, False),
            Instr(83,  "RETURN_VALUE",          None, None, 48, None, False),
        ])
        self.assertEqual(out.count("Z = None"), 1, out)
        self.assertEqual(out.count("del Z"), 1, out)

    def test_global_in_39_comprehension_code_object(self):
        from pycrefine import BytecodeInstruction as Instr
        from pycrefine.codeobj import ForeignCode
        from pycrefine.decompiler.py39 import Decompiler39

        comp_code = ForeignCode(
            co_name="<listcomp>",
            instructions=[
                Instr(100, "LOAD_FAST", 0, "x", 0, None, False),
                Instr(97, "STORE_GLOBAL", 0, "Z", 2, None, False),
            ],
            co_consts=(),
        )
        comp_code.co_code = b"dummy"
        inner_func = ForeignCode(
            co_name="inner",
            instructions=[
                Instr(97, "STORE_GLOBAL", 0, "OTHER", 0, None, False),
            ],
            co_consts=(),
        )
        inner_func.co_code = b"dummy"
        parent_code = ForeignCode(
            co_name="f",
            co_flags=0x01,
            co_varnames=("x",),
            co_cellvars=(),
            co_freevars=(),
            co_consts=(comp_code, inner_func),
            instructions=[
                Instr(100, "LOAD_CONST", 0, comp_code, 0, None, False),
                Instr(100, "LOAD_CONST", 1, inner_func, 2, None, False),
                Instr(83, "RETURN_VALUE", None, None, 4, None, False),
            ],
        )
        parent_code.co_code = b"dummy"
        dec = Decompiler39(parent_code, indent_level=1)
        # Should include global Z from <listcomp>, but exclude OTHER from ordinary function inner
        self.assertEqual(dec._scope_declarations(), ["global Z"])

    def test_nonlocal_in_39_comprehension_code_object(self):
        from pycrefine import BytecodeInstruction as Instr
        from pycrefine.codeobj import ForeignCode
        from pycrefine.decompiler.py39 import Decompiler39

        comp_code = ForeignCode(
            co_name="<listcomp>",
            instructions=[
                Instr(100, "LOAD_FAST", 0, "x", 0, None, False),
                Instr(136, "STORE_DEREF", 0, "n", 2, None, False),
            ],
            co_consts=(),
        )
        comp_code.co_code = b"dummy"
        parent_code = ForeignCode(
            co_name="inner",
            co_flags=0x01,
            co_varnames=("x",),
            co_cellvars=(),
            co_freevars=("n",),
            co_consts=(comp_code,),
            instructions=[
                Instr(100, "LOAD_CONST", 0, comp_code, 0, None, False),
                Instr(83, "RETURN_VALUE", None, None, 2, None, False),
            ],
        )
        parent_code.co_code = b"dummy"
        dec = Decompiler39(parent_code, indent_level=1)
        self.assertEqual(dec._scope_declarations(), ["nonlocal n"])

    def test_nested_39_comprehension_code_object(self):
        from pycrefine import BytecodeInstruction as Instr
        from pycrefine.codeobj import ForeignCode
        from pycrefine.decompiler.py39 import Decompiler39

        inner_comp = ForeignCode(
            co_name="<setcomp>",
            instructions=[
                Instr(100, "LOAD_FAST", 0, "x", 0, None, False),
                Instr(97, "STORE_GLOBAL", 0, "Z", 2, None, False),
            ],
            co_consts=(),
        )
        inner_comp.co_code = b"dummy"
        outer_comp = ForeignCode(
            co_name="<listcomp>",
            instructions=[
                Instr(100, "LOAD_CONST", 0, inner_comp, 0, None, False),
            ],
            co_consts=(inner_comp,),
        )
        outer_comp.co_code = b"dummy"
        parent_code = ForeignCode(
            co_name="f",
            co_flags=0x01,
            co_varnames=(),
            co_cellvars=(),
            co_freevars=(),
            co_consts=(outer_comp,),
            instructions=[
                Instr(100, "LOAD_CONST", 0, outer_comp, 0, None, False),
                Instr(83, "RETURN_VALUE", None, None, 2, None, False),
            ],
        )
        parent_code.co_code = b"dummy"
        dec = Decompiler39(parent_code, indent_level=1)
        self.assertEqual(dec._scope_declarations(), ["global Z"])

    def test_shadowed_global_read_in_39_comprehension_code_object(self):
        from pycrefine import BytecodeInstruction as Instr
        from pycrefine.codeobj import ForeignCode
        from pycrefine.decompiler.py39 import Decompiler39

        inner_comp = ForeignCode(
            co_name="<listcomp>",
            instructions=[
                Instr(116, "LOAD_GLOBAL", 0, "x", 0, None, False),
            ],
            co_consts=(),
        )
        inner_comp.co_code = b"dummy"
        parent_code = ForeignCode(
            co_name="inner",
            co_flags=0x01,
            co_varnames=(),
            co_cellvars=(),
            co_freevars=(),
            co_consts=(inner_comp,),
            instructions=[
                Instr(100, "LOAD_CONST", 0, inner_comp, 0, None, False),
                Instr(83, "RETURN_VALUE", None, None, 2, None, False),
            ],
        )
        parent_code.co_code = b"dummy"
        dec = Decompiler39(parent_code, indent_level=1)
        dec._enclosing_locals = {"x"}
        self.assertEqual(dec._scope_declarations(), ["global x"])


if __name__ == "__main__":
    unittest.main()
