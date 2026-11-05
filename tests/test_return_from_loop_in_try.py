"""A `return` from inside `for` loops that sit in a `try` (#339).

Leaving a loop with `return` drops its iterator, and the `try` that holds the loop is closed on the way
(3.9). The code after the `try` was written as an `else:` clause at the wrong indent, or inside the `if`
that holds the `return`, and the handlers of a `try` around nested loops were written inside them.
Each shape is decompiled and run against the original, on every Python the suite runs on.
"""
import ast
import unittest

from .test_helpers import decompile


def _outcome(function, args):
    try:
        return ("value", function(*args))
    except Exception as exc:  # pylint: disable=broad-except
        return ("raised", type(exc).__name__, exc.args)


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


FLAT = (([1, 2, 3], 2), ([1, 2, 3], 9), (None, 1), ([], 1), (["a"], "a"))
NESTED = (([[1, 2], [3, 4]], 3), ([[1, 2], [3, 4]], 9), (None, 1), ([5], 1), ([], 1))


class TestReturnFromLoopInTry(unittest.TestCase):

    def assert_same(self, body, calls=FLAT):
        source = "def f(xs, stop):\n" + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for args in calls:
            self.assertEqual(_outcome(rebuilt["f"], args), _outcome(original["f"], args), f"{args}\n{out}")
        return out

    def test_the_reproduction(self):
        self.assert_same(
            "    try:\n        for x in xs:\n            if x == stop:\n                return x\n"
            "    except TypeError:\n        return -1\n    return None\n")

    def test_a_value_after_the_try(self):
        self.assert_same(
            "    try:\n        for x in xs:\n            if x == stop:\n                return x\n"
            "    except TypeError:\n        return -1\n    return 0\n")

    def test_a_computed_value(self):
        self.assert_same(
            "    try:\n        for x in xs:\n            if x == stop:\n                return x * 2\n"
            "    except TypeError:\n        return -1\n    return None\n")

    def test_code_in_the_loop_and_after_it(self):
        self.assert_same(
            "    out = []\n    try:\n        for x in xs:\n            if x == stop:\n                return x\n"
            "            out.append(x)\n    except TypeError:\n        return -1\n    out.append('end')\n    return out\n")

    def test_with_a_finally(self):
        self.assert_same(
            "    try:\n        for x in xs:\n            if x == stop:\n                return x\n"
            "    except TypeError:\n        return -1\n    finally:\n        pass\n    return None\n")

    def test_an_else_clause(self):
        self.assert_same(
            "    try:\n        for x in xs:\n            if x == stop:\n                return x\n"
            "    except TypeError:\n        return -1\n    else:\n        return None\n")

    def test_nested_loops(self):
        out = self.assert_same(
            "    try:\n        for row in xs:\n            for x in row:\n                if x == stop:\n"
            "                    return x\n    except TypeError:\n        return -1\n    return None\n", NESTED)
        self.assertEqual(out.count("except TypeError"), 1)

    def test_in_a_loop(self):
        self.assert_same(
            "    for _ in range(1):\n        try:\n            for x in xs:\n                if x == stop:\n"
            "                    return x\n        except TypeError:\n            return -1\n    return None\n")


if __name__ == "__main__":
    unittest.main()
