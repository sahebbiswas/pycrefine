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
        """Assert decompiled output produces identical AST as original source."""
        out = decompile(src)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(src)), out)
        return out

    def assert_same_behavior(self, src):
        """Assert decompiled code executes with the same stdout as original source."""
        out = decompile(src)
        self.assertEqual(_run(compile(out, "<decompiled>", "exec")), _run(compile(src, "<original>", "exec")), out)
        return out

    def test_star_last(self):
        """Test starred target as last item: first, *rest = x."""
        self.assert_same_ast("def f(x):\n    first, *rest = x\n    return first, rest\n")

    def test_star_first(self):
        """Test starred target as first item: *init, last = x."""
        self.assert_same_ast("def f(x):\n    *init, last = x\n    return init, last\n")

    def test_star_middle(self):
        """Test starred target in middle: a, *mid, b = x."""
        self.assert_same_ast("def f(x):\n    a, *mid, b = x\n    return a, mid, b\n")

    def test_star_only(self):
        """Test single starred target with trailing comma: *only, = (1, 2)."""
        self.assert_same_behavior("def f():\n    *only, = (1, 2)\n    return only\nprint(f())\n")

    def test_nested_star(self):
        """Test starred target inside nested tuple: (a, *b), c = x."""
        self.assert_same_ast("def f(x):\n    (a, *b), c = x\n    return a, b, c\n")

    def test_for_targets(self):
        """Test starred targets in for loops."""
        self.assert_same_ast("def f(xs):\n    for head, *tail in xs:\n        print(head, tail)\n")
        self.assert_same_ast("def f(xs):\n    for (a, *b), c in xs:\n        print(a, b, c)\n")

    def test_comprehension_targets(self):
        """Test starred targets in comprehensions."""
        self.assert_same_ast("def f(xs):\n    return [h for h, *t in xs], {h: t for h, *t in xs}\n")

    def test_module_level(self):
        """Test starred unpacking at module level."""
        self.assert_same_behavior("first, *rest = 'abc'\nprint(first, rest)\n")

    def test_attribute_and_subscript_targets(self):
        """Test fallback to indexing and slicing for attribute and subscript targets."""
        self.assert_same_behavior(
            "class O:\n    x = 0\n"
            "def f():\n    o = O()\n    d = {}\n    o.a, *o.b = [5, 6, 7]\n    d['x'], *mid, d['y'] = range(5)\n"
            "    return o.a, o.b, d, mid\nprint(f())\n")

    def test_attribute_and_subscript_targets_with_iterator(self):
        """Test UNPACK_EX fallback materializes iterator RHS as list so indexing/slicing works."""
        self.assert_same_behavior(
            "class O:\n    x = 0\n"
            "def f(values):\n    o = O()\n    a, *o.b = iter(values)\n    return a, o.b\n"
            "print(f([10, 20, 30]))\n")

    def test_attribute_and_subscript_targets_arity_error(self):
        """Test UNPACK_EX fallback raises ValueError when RHS has insufficient elements."""
        self.assert_same_behavior(
            "class O:\n    x = 0\n"
            "def f():\n    o = O()\n    d = {}\n    try:\n"
            "        d['x'], *mid, d['y'] = [1]\n    except ValueError:\n"
            "        return 'caught'\n    return 'not caught'\n"
            "print(f())\n")

    def test_attribute_and_subscript_targets_shadowed_builtins(self):
        """Test UNPACK_EX fallback arity check when len and ValueError are shadowed by parameters."""
        self.assert_same_behavior(
            "class O:\n    b = []\n"
            "def f(len, ValueError, o):\n    a, *o.b = [1, 2]\n    return a, o.b\n"
            "print(f(10, 'custom', O()))\n"
            "def g(len, ValueError, o):\n"
            "    import builtins\n"
            "    try:\n"
            "        a, *o.b = []\n"
            "    except builtins.ValueError:\n"
            "        return 'caught built-in'\n"
            "    return 'not caught'\n"
            "print(g(10, 'custom', O()))\n")

    def test_nested_fallback_unpackings_do_not_share_temporaries(self):
        """Test (a, *o.b), *tail = ...: the inner unpacking must not overwrite the outer sequence."""
        self.assert_same_behavior(
            "class O:\n    b = []\n"
            "def f():\n    o = O()\n    (a, *o.b), *tail = [[1, 2], [3, 4]]\n"
            "    return a, o.b, tail\n"
            "print(f())\n")

    def test_fallback_does_not_look_up_shadowed_list(self):
        """Test the UNPACK_EX fallback works when the function binds the name `list`."""
        self.assert_same_behavior(
            "class O:\n    b = []\n"
            "def f(list):\n    o = O()\n    a, *o.b = iter((1, 2))\n    return a, o.b\n"
            "print(f(None))\n")

    def test_fallback_arity_error_with_shadowed_getattr(self):
        """Test too few values still raises ValueError when `getattr` is a non-callable parameter."""
        self.assert_same_behavior(
            "class O:\n    b = []\n"
            "def f(getattr):\n    o = O()\n    try:\n        a, *o.b = []\n"
            "    except ValueError:\n        return 'caught'\n    return 'not caught'\n"
            "print(f(None))\n")

    def test_fallback_lone_star_target(self):
        """Test a lone starred attribute target, which needs a trailing comma: *o.b, = x."""
        self.assert_same_behavior(
            "class O:\n    b = []\n"
            "def f():\n    o = O()\n    *o.b, = iter((1, 2, 3))\n    return o.b\n"
            "print(f())\n")

    def test_fresh_accum_name_is_never_repeated(self):
        """Test _fresh_accum_name returns a new name each call and skips names the code uses."""
        from pycrefine.decompiler.generic import DecompilerGeneric

        def outer(_res):
            _res0 = 0

            def inner():
                return _res, _res0
            return inner

        dec = DecompilerGeneric(outer.__code__)
        names = [dec._fresh_accum_name() for _ in range(4)]
        self.assertEqual(len(set(names)), 4, names)
        self.assertFalse({"_res", "_res0"} & set(names), names)
        self.assertNotIn(dec._fresh_accum_name("_unpack"), names)

    def test_plain_unpacking_still_works(self):
        """Test that plain (non-starred) unpacking assignments remain functional."""
        self.assert_same_ast("def f(x):\n    a, (b, c) = x\n    (d,) = c\n    return a, b, d\n")

    def test_nested_fused_target_pending(self):
        """Test nested unpacking with fused STORE_FAST_STORE_FAST instruction targets."""
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
        """Test _op_unpack_sequence handling when UNPACK_EX is the first instruction."""
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
