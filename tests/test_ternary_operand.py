"""A conditional expression that is an operand of a comparison, `is`, `in` or a subscript (#292).

The expression was not recognised when the operator consumed its value after other loads (`(n if m else 0) > 0`), so the
test of a `while` loop came out as a constant and the arms as empty `if`/`else` statements. Where it was recognised, it was
written without parentheses: `n if m else 0 > 0` is a different expression.
"""
import ast
import signal
import unittest

from .test_helpers import decompile

_TIMEOUT_SECONDS = 10


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _evaluate(call, namespace, out):
    """Evaluate *call*; a loop that never ends fails the test instead of hanging it."""
    if not hasattr(signal, "SIGALRM"):
        return eval(call, namespace)  # pylint: disable=eval-used

    def give_up(*_):
        raise AssertionError(f"{call} did not finish\n{out}")

    previous = signal.signal(signal.SIGALRM, give_up)
    signal.alarm(_TIMEOUT_SECONDS)
    try:
        return eval(call, namespace)  # pylint: disable=eval-used
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


class TestTernaryOperand(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(_evaluate(call, rebuilt, out), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_the_reproduction(self):
        out = self.assert_same_results(
            "def f(n, m):\n    while (n if m else 0) > 0:\n        n -= 1\n    return n\n",
            "f(5, 1)", "f(5, 0)", "f(0, 1)")
        self.assertIn("while (n if m else 0) > 0:", out)

    def test_other_operand_of_the_comparison(self):
        out = self.assert_same_results(
            "def f(items, k):\n    i = 0\n    while i < (len(items) if k else 3):\n        i += 1\n    return i\n",
            "f([1, 2], 1)", "f([1, 2], 0)", "f([], 1)")
        self.assertIn("while i < (len(items) if k else 3):", out)

    def test_the_condition_changes_in_the_loop(self):
        """The test is evaluated again on every pass."""
        self.assert_same_results(
            "def f(n, flag):\n    steps = 0\n    while (n if flag[0] else 0) > 0:\n        n -= 1\n        steps += 1\n"
            "        if steps == 2:\n            flag[0] = 0\n    return steps\n", "f(9, [1])", "f(1, [1])", "f(9, [0])")

    def test_in_an_if(self):
        out = self.assert_same_results(
            "def f(n, m):\n    if (n if m else 0) > 0:\n        return 1\n    return 2\n", "f(5, 1)", "f(5, 0)", "f(-1, 1)")
        self.assertIn("if (n if m else 0) > 0:", out)

    def test_assigned(self):
        out = self.assert_same_results(
            "def f(n, m):\n    y = (n if m else 0) > 0\n    return y\n", "f(5, 1)", "f(5, 0)", "f(-5, 1)")
        self.assertIn("(n if m else 0) > 0", out)

    def test_is_and_in(self):
        self.assert_same_results(
            "def f(a, b, c, d):\n    return (a if c else b) is None, (a if c else b) in d\n",
            "f(None, 1, 1, [1])", "f(None, 1, 0, [1])", "f(1, 2, 0, [2])")

    def test_right_hand_operand(self):
        self.assert_same_results(
            "def f(a, b, c, d):\n    return d == (a if c else b), d in (a if c else b)\n",
            "f([1], [2], 1, 1)", "f([1], [2], 0, 1)", "f([1], [5], 0, 5)")

    def test_subscript(self):
        self.assert_same_results(
            "def f(a, b, c):\n    return (a if c else b)[0]\n", "f([1], [2], 1)", "f([1], [2], 0)")

    def test_in_a_comprehension_filter(self):
        self.assert_same_results(
            "def f(m):\n    return [x for x in range(3) if (x if m else 0) > 0]\n", "f(1)", "f(0)")

    def test_a_plain_comparison_is_unchanged(self):
        out = self.assert_same_results(
            "def f(n, m):\n    while n > m:\n        n -= 1\n    return n\n", "f(5, 2)")
        self.assertIn("while n > m:", out)


if __name__ == "__main__":
    unittest.main()
