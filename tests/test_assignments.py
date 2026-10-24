"""Assignment targets: starred unpacking (#156)."""
import ast
import contextlib
import io
import sys
import unittest

from .test_helpers import decompile


def _run(code):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, {"__name__": "__main__"})
    return buf.getvalue()


@unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 11)), "3.10/3.11 have known control-flow failures")
class TestStarredAssignment(unittest.TestCase):

    def assert_same_ast(self, src):
        out = decompile(src)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(src)), out)
        return out

    def assert_same_behavior(self, src):
        out = decompile(src)
        self.assertEqual(_run(compile(out, "<decompiled>", "exec")), _run(compile(src, "<original>", "exec")), out)
        return out

    def test_star_last(self):
        self.assert_same_ast("def f(x):\n    first, *rest = x\n    return first, rest\n")

    def test_star_first(self):
        self.assert_same_ast("def f(x):\n    *init, last = x\n    return init, last\n")

    def test_star_middle(self):
        self.assert_same_ast("def f(x):\n    a, *mid, b = x\n    return a, mid, b\n")

    def test_star_only(self):
        self.assert_same_behavior("def f():\n    *only, = (1, 2)\n    return only\nprint(f())\n")

    def test_nested_star(self):
        self.assert_same_ast("def f(x):\n    (a, *b), c = x\n    return a, b, c\n")

    def test_for_targets(self):
        self.assert_same_ast("def f(xs):\n    for head, *tail in xs:\n        print(head, tail)\n")
        self.assert_same_ast("def f(xs):\n    for (a, *b), c in xs:\n        print(a, b, c)\n")

    def test_comprehension_targets(self):
        self.assert_same_ast("def f(xs):\n    return [h for h, *t in xs], {h: t for h, *t in xs}\n")

    def test_module_level(self):
        self.assert_same_behavior("first, *rest = 'abc'\nprint(first, rest)\n")

    def test_attribute_and_subscript_targets(self):
        # Targets that are not plain names fall back to indexing and slicing the value.
        self.assert_same_behavior(
            "class O:\n    x = 0\n"
            "def f():\n    o = O()\n    d = {}\n    o.a, *o.b = [5, 6, 7]\n    d['x'], *mid, d['y'] = range(5)\n"
            "    return o.a, o.b, d, mid\nprint(f())\n")

    def test_plain_unpacking_still_works(self):
        self.assert_same_ast("def f(x):\n    a, (b, c) = x\n    (d,) = c\n    return a, b, d\n")

    def test_nested_fused_target_pending(self):
        from pycrefine import BytecodeInstruction as Instr
        from pycrefine.decompiler.generic import DecompilerGeneric

        code = compile("pass", "<test>", "exec")
        dec = DecompilerGeneric(code)
        instructions = [
            Instr(100, "LOAD_FAST", 0, "t", 0, None, False),
            Instr(92, "UNPACK_SEQUENCE", 2, 2, 2, None, False),
            Instr(92, "UNPACK_SEQUENCE", 2, 2, 4, None, False),
            Instr(125, "STORE_FAST", 0, "x", 6, None, False),
            Instr(160, "STORE_FAST_STORE_FAST", 0, ("a", "b"), 8, None, False),
        ]
        dec.instructions = instructions
        pending = []
        parsed = dec._parse_store_target(instructions, 1, pending)
        self.assertIsNotNone(parsed)
        self.assertEqual(parsed[0], "(x, a), b")
        self.assertEqual(pending, [])

    def test_unpack_at_start_of_instructions(self):
        from pycrefine import BytecodeInstruction as Instr
        from pycrefine.decompiler.generic import DecompilerGeneric

        code = compile("pass", "<test>", "exec")
        dec = DecompilerGeneric(code)
        instructions = [
            Instr(93, "UNPACK_EX", 1, 1, 0, None, False),
            Instr(125, "STORE_FAST", 0, "a", 2, None, False),
            Instr(125, "STORE_FAST", 1, "b", 4, None, False),
        ]
        dec.instructions = instructions
        dec.pc = 0
        dec.stack = ["val"]
        dec._op_unpack_sequence(instructions[0])
        self.assertEqual(dec.reconstructed, ["a, *b = val"])


if __name__ == "__main__":
    unittest.main()
