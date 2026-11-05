"""Two statements that are not one expression, and an `if` that is not a loop's guard (review of #360).

`if c: y = 1` followed by `y = 2` was written as `y = 1 if c else 2`, which gives 1 where the original
gives 2 whenever `c` is true. `if c:` followed by `while True:` was written as `while c:`, which stops
after the first pass instead of looping on. Each shape is decompiled and run against the original, on
every Python the suite runs on.
"""
import ast
import unittest

from .test_helpers import decompile


def _outcome(function, args):
    try:
        return ("value", function(*args))
    except Exception as exc:  # pylint: disable=broad-except
        return ("raised", type(exc).__name__)


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestUnmergedStatements(unittest.TestCase):

    def assert_same(self, source, calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for args in calls:
            self.assertEqual(_outcome(rebuilt["f"], args), _outcome(original["f"], args), f"{args}\n{out}")
        return out

    def test_an_if_before_a_store_of_the_same_name_is_not_a_conditional_expression(self):
        out = self.assert_same("def f(c):\n    if c:\n        y = 1\n    y = 2\n    return y\n", [(True,), (False,)])
        self.assertNotIn(" if c else ", out)

    def test_the_same_with_a_longer_tail(self):
        out = self.assert_same(
            "def f(c, z):\n    if c:\n        y = z + 1\n    y = z * 2\n    return y, z\n", [(True, 3), (False, 3)])
        self.assertNotIn(" if c else ", out)

    def test_a_real_conditional_expression_with_a_copied_tail_is_still_one(self):
        out = self.assert_same(
            "def f(c):\n    y = 1 if c else 2\n    return y\n", [(True,), (False,)])
        self.assertIn("1 if c else 2", out)

    def test_an_if_around_an_unconditional_loop_stays_an_if(self):
        out = self.assert_same(
            "def f(c, n):\n    if c:\n        while True:\n            c = False\n            n -= 1\n"
            "            if n < 0:\n                return 5\n    return 1\n", [(True, 3), (False, 3)])
        self.assertNotIn("while c", out)

    def test_a_loop_whose_own_condition_is_the_guard_is_still_a_while(self):
        out = self.assert_same(
            "def f(c, n):\n    while c:\n        c = False\n        n -= 1\n        if n < 0:\n            return 5\n"
            "    return 1\n", [(True, 3), (False, 3)])
        self.assertIn("while c", out)

    def test_an_if_and_a_while_with_the_same_test_after_it(self):
        self.assert_same(
            "def f(c, n):\n    out = 0\n    if c:\n        out += 1\n    while c:\n        c = False\n        out += n\n"
            "    return out\n", [(True, 3), (False, 3)])


if __name__ == "__main__":
    unittest.main()
