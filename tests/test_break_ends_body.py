"""A `break` out of a `for` loop that ends the body of an enclosing loop (#275).

The compiler threads that `break` straight back to the enclosing loop's head, and the `else` clause (when there is
one) ends in the same jump. On 3.9 a `for`/`else` inside a `while` was written with the `else` body inside the `for`.
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


class TestBreakEndsBody(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(_evaluate(call, rebuilt, out), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_for_else_in_a_for(self):
        self.assert_same_results(
            "def f(n):\n    out = []\n    for i in range(n):\n        for j in range(2):\n            if i == j:\n"
            "                break\n        else:\n            out.append(i)\n    return out\n", "f(0)", "f(1)", "f(4)")

    def test_for_else_in_a_while(self):
        out = self.assert_same_results(
            "def g(n):\n    out = []\n    i = 0\n    while i < n:\n        i += 1\n        for j in range(2):\n"
            "            if i == j:\n                break\n        else:\n            out.append(i)\n    return out\n",
            "g(0)", "g(1)", "g(4)")
        self.assertIn("else:", out)

    def test_for_in_a_while_without_else(self):
        self.assert_same_results(
            "def k(n):\n    out = []\n    i = 0\n    while i < n:\n        i += 1\n        for j in range(3):\n"
            "            if i == j:\n                break\n            out.append(j)\n    return out\n", "k(0)", "k(2)", "k(5)")

    def test_for_in_a_for_without_else(self):
        self.assert_same_results(
            "def h(n):\n    out = []\n    for i in range(n):\n        for j in range(3):\n            if i == j:\n"
            "                break\n            out.append(j)\n    return out\n", "h(0)", "h(2)", "h(5)")

    def test_a_while_in_a_while_with_a_for_else(self):
        self.assert_same_results(
            "def f(n):\n    out = []\n    i = 0\n    while i < n:\n        i += 1\n        k = 0\n        while k < 2:\n"
            "            k += 1\n            for j in range(2):\n                if i == j:\n                    break\n"
            "            else:\n                out.append((i, k))\n    return out\n", "f(0)", "f(3)")

    def test_statements_after_the_inner_loop(self):
        self.assert_same_results(
            "def f(n):\n    out = []\n    i = 0\n    while i < n:\n        i += 1\n        for j in range(2):\n"
            "            if i == j:\n                break\n        else:\n            out.append(i)\n"
            "        out.append('end')\n    return out\n", "f(0)", "f(3)")

    def test_a_continue_next_to_the_break(self):
        self.assert_same_results(
            "def f(n):\n    out = []\n    i = 0\n    while i < n:\n        i += 1\n        for j in range(3):\n"
            "            if j == 0:\n                continue\n            if i == j:\n                break\n"
            "            out.append((i, j))\n        else:\n            out.append(i)\n    return out\n", "f(0)", "f(4)")


if __name__ == "__main__":
    unittest.main()
