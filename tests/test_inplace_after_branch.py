"""An augmented assignment in each arm of an `if`, and as a conditional expression's value, stays augmented (#197).

3.10 spells in-place operators `INPLACE_*` and copies the function's tail into both arms. The branches were not
recognised as a conditional expression, so `s += a if c else b` came out as two `if`s, and an arm holding a string with a
newline came out as `msg = (msg += '''...''')`, which does not parse.
"""
import ast
import itertools
import unittest

from .test_helpers import decompile


def _function(code, name="f"):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace[name]


class TestInplaceAfterBranch(unittest.TestCase):

    def assert_same(self, source, argument_sets):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _function(source), _function(out)
        for args in argument_sets:
            self.assertEqual(rebuilt(*args), original(*args), f"{args!r}\n{out}")
        return out

    def test_augmented_conditional_expression(self):
        for op, left in (("+=", 10), ("-=", 10), ("*=", 3), ("|=", 4), ("<<=", 1), ("//=", 100)):
            out = self.assert_same(
                f"def f(c, a, b):\n    s = {left}\n    s {op} a if c else b\n    return s\n",
                list(itertools.product((0, 1), (1, 2, 3), (1, 5))))
            self.assertIn(f"s {op} a if c else b", out)

    def test_a_string_with_a_newline_in_the_else_arm(self):
        out = self.assert_same(
            "def f(v):\n    msg = ''\n    if v:\n        msg += f'\\n- from {v}.'\n    else:\n"
            "        msg += '\\n- unknown.'\n    return msg\n", [(0,), (1,), ("x",)])
        self.assertNotIn("(msg +=", out)

    def test_a_string_with_a_newline_in_a_plain_statement(self):
        out = self.assert_same("def f(v):\n    msg = 'a'\n    msg += '\\nb'\n    return msg + str(v)\n", [(1,)])
        self.assertNotIn("(msg +=", out)

    def test_in_place_operators_on_a_list_keep_their_identity(self):
        out = self.assert_same(
            "def f(c):\n    xs = []\n    ys = xs\n    if c:\n        xs += [1]\n    else:\n        xs += [2]\n"
            "    return ys\n", [(0,), (1,)])
        self.assertIn("xs +=", out)

    def test_displays_in_the_arms_of_a_conditional_expression(self):
        for body in ("[a] if c else [b]", "(a, b) if c else (b,)", "{a, b} if c else {b}", "[a, 1, 2] if c else []",
                     "{'k': a} if c else {}", "{**{'k': a}} if c else {'z': b}", "[a, b][c]"):
            self.assert_same(f"def f(c, a, b):\n    x = {body}\n    return x\n",
                             list(itertools.product((0, 1), (1, 2), (3, 4))))

    def test_a_display_added_in_place_in_each_arm(self):
        self.assert_same("def f(c, a):\n    xs = []\n    xs += [a] if c else [2]\n    return xs\n",
                         list(itertools.product((0, 1), (1, 2))))


if __name__ == "__main__":
    unittest.main()
