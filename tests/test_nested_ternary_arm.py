"""A conditional expression that is an arm of another conditional expression keeps its whole arm (#288).

The inner conditional's true arm ends in a jump to a `return` that the false arm also has its own copy of
(3.9 to 3.11), or in its own `return` (3.12 and later): the arm was written as `pass`, or lost its `a() or` operand.
"""
import ast
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


_ARGS = [(flag, g, ra, rb) for flag in (0, 1) for g in (0, 1) for ra in (0, 5) for rb in (0, 7)]


class TestNestedTernaryArm(unittest.TestCase):

    def assert_same_results(self, source):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for flag, g, ra, rb in _ARGS:
            def call(ns):
                return ns["f"](flag, g, lambda: ra, lambda: rb, "other")
            self.assertEqual(call(rebuilt), call(original), f"{(flag, g, ra, rb)}\n{out}")
        return out

    def test_binary_operator_arm(self):
        out = self.assert_same_results(
            "def f(flag, g, a, b, other):\n    return ((a() + b()) if g else a()) if flag else other\n")
        self.assertNotIn("pass", out)

    def test_or_chain_arm(self):
        out = self.assert_same_results(
            "def f(flag, g, a, b, other):\n    return ((a() or b()) if g else a()) if flag else other\n")
        self.assertEqual(out.count("a() or b()"), 1, out)

    def test_and_chain_arm(self):
        self.assert_same_results(
            "def f(flag, g, a, b, other):\n    return ((a() and b()) if g else a()) if flag else other\n")

    def test_arm_in_the_false_branch(self):
        self.assert_same_results(
            "def f(flag, g, a, b, other):\n    return other if flag else ((a() or b()) if g else b())\n")


if __name__ == "__main__":
    unittest.main()
