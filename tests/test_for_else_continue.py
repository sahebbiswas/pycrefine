"""A `for` loop whose `else` clause ends in `continue` keeps it, with code after the loop in the body (#324).

On 3.9 and 3.11 the jump back to the enclosing loop's head was the only one, so nothing marked it as a `continue`
and the clause came out as `pass`: the code after the inner loop ran when the loop had not hit a `break`.
"""
import ast
import unittest

from .test_helpers import decompile


def _function(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace["f"]


class TestForElseContinue(unittest.TestCase):

    def assert_same(self, source, cases):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _function(source), _function(out)
        for case in cases:
            self.assertEqual(rebuilt(case), original(case), f"{case!r}\n{out}")
        return out

    def test_the_whole_else_is_continue(self):
        self.assert_same(
            "def f(a):\n    for x in a:\n        for y in x:\n            if y == 1:\n                break\n"
            "        else:\n            continue\n        return y\n    else:\n        return None\n",
            ([[0], [1]], [[0]], [], [[0, 1]], [[], [1]]))

    def test_the_else_does_something_first(self):
        out = self.assert_same(
            "def f(a):\n    seen = 0\n    for x in a:\n        for y in x:\n            if y == 1:\n                break\n"
            "        else:\n            seen += 1\n            continue\n        return seen, y\n    return seen, None\n",
            ([[0], [1]], [[0], [0]], [[0, 1]], []))
        self.assertIn("continue", out)

    def test_inside_a_while_loop(self):
        self.assert_same(
            "def f(n):\n    i = 0\n    while i < n:\n        i += 1\n        for y in range(i):\n            if y == 3:\n"
            "                break\n        else:\n            continue\n        return i\n    return -1\n",
            (0, 2, 4, 6))

    def test_the_enclosing_loop_head_has_an_extended_arg_prefix(self):
        padding = "".join(f"        r += {n}\n" for n in range(1, 160))
        source = ("def f(a):\n    r = 0\n    for x in a:\n        for y in x:\n            if y == 1:\n                break\n"
                  "        else:\n            continue\n" + padding + "        return r, y\n    return r, None\n")
        self.assert_same(source, ([[0], [1]], [[0]], [], [[0, 1]], [[], [1]]))

    def test_else_without_continue_is_unchanged(self):
        self.assert_same(
            "def f(a):\n    r = 0\n    for x in a:\n        for y in x:\n            if y == 1:\n                break\n"
            "        else:\n            r += 10\n        r += 1\n    return r\n",
            ([[0], [1]], [[0, 1], [2]], []))


if __name__ == "__main__":
    unittest.main()
