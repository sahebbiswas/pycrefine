"""Binary, in-place and comparison operators, and the break guard of `while True:` (the 3.9 backend's operator handlers).

Every operator of the tables is decompiled and run against the original, on every Python the suite runs on.
"""
import ast
import unittest

from .test_helpers import decompile

_BINARY = ("+", "-", "*", "/", "//", "%", "**", "<<", ">>", "&", "|", "^")
_COMPARE = ("<", "<=", "==", "!=", ">", ">=")


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestOperatorHandlers(unittest.TestCase):

    def assert_same(self, source, calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for args in calls:
            self.assertEqual(rebuilt["f"](*args), original["f"](*args), f"{args}\n{out}")
        return out

    def test_every_binary_operator(self):
        for op in _BINARY:
            with self.subTest(op=op):
                self.assert_same(f"def f(a, b):\n    return a {op} b\n", [(7, 2), (9, 3)])

    def test_every_in_place_operator(self):
        for op in _BINARY:
            with self.subTest(op=op):
                out = self.assert_same(f"def f(a, b):\n    a {op}= b\n    return a\n", [(7, 2), (9, 3)])
                self.assertIn(f"a {op}= b", out)

    def test_every_comparison(self):
        for op in _COMPARE:
            with self.subTest(op=op):
                out = self.assert_same(f"def f(a, b):\n    return a {op} b\n", [(1, 2), (2, 2), (3, 2)])
                self.assertIn(f"a {op} b", out)

    def test_in_place_on_an_attribute_and_a_subscript(self):
        self.assertIn("o.n += 1", decompile("def f(o):\n    o.n += 1\n    return o.n\n"))
        self.assertIn("xs[0] *= 2", decompile("def f(xs):\n    xs[0] *= 2\n    return xs\n"))

    def test_a_conditional_operand_is_parenthesised(self):
        self.assert_same("def f(c, a, b):\n    return (a if c else b) % 3\n", [(True, 7, 8), (False, 7, 8)])

    def test_while_true_with_a_break_guard(self):
        source = "def f(n):\n    i = 0\n    while True:\n        if i >= n:\n            break\n        i += 1\n    return i\n"
        out = self.assert_same(source, [(0,), (3,)])
        self.assertIn("while True:", out)


if __name__ == "__main__":
    unittest.main()
