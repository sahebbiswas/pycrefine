"""A conditional expression whose arm is an `and`/`or` chain keeps every operand (#276)."""
import ast
import itertools
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestTernaryBoolArm(unittest.TestCase):

    def assert_same_results(self, source, calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    @staticmethod
    def _grid(name, count):
        values = (0, 1, "x", None)
        return [f"{name}({flag}, " + ", ".join(f"(lambda: {v!r})" for v in combo) + ", 'o')"
                for flag in (0, 1) for combo in itertools.product(values, repeat=count)]

    def test_or_arm_with_star_args(self):
        """The reported form: the arm is a call with `*args` or-ed with a default."""
        src = ("def f(flag, fn, args, default, other):\n    r = (fn(*args) or default) if flag else other\n"
               "    return r\n")
        out = self.assert_same_results(src, [
            "f(1, lambda *a: (), (0,), 'd', 'o')", "f(1, lambda *a: 'v', (0,), 'd', 'o')", "f(0, None, (), 'd', 'o')"])
        self.assertIn("or default", out)

    def test_or_arm_of_calls(self):
        src = "def f(flag, a, b, other):\n    r = (a() or b()) if flag else other\n    return r\n"
        self.assert_same_results(src, self._grid("f", 2))

    def test_and_arm_of_calls(self):
        src = "def f(flag, a, b, other):\n    r = (a() and b()) if flag else other\n    return r\n"
        self.assert_same_results(src, self._grid("f", 2))

    def test_three_operand_chain(self):
        src = "def f(flag, a, b, c, other):\n    r = (a() or b() or c()) if flag else other\n    return r\n"
        self.assert_same_results(src, self._grid("f", 3))

    def test_chain_in_the_false_arm(self):
        src = "def f(flag, a, b, other):\n    r = other if flag else (a() or b())\n    return r\n"
        self.assert_same_results(src, self._grid("f", 2))

    def test_chain_in_both_arms(self):
        src = "def f(flag, a, b, c, other):\n    r = (a() or b()) if flag else (c() and a())\n    return r\n"
        self.assert_same_results(src, self._grid("f", 3))

    def test_chain_in_a_returned_conditional(self):
        src = "def f(flag, a, b, other):\n    return (a() or b()) if flag else other\n"
        self.assert_same_results(src, self._grid("f", 2))

    def test_chain_as_a_call_argument(self):
        src = ("def f(flag, a, b, other):\n    return str((a() or b()) if flag else other)\n")
        self.assert_same_results(src, self._grid("f", 2))

    def test_plain_ternary_is_unchanged(self):
        src = "def f(flag, a, b):\n    r = a if flag else b\n    return r\n"
        out = self.assert_same_results(src, ["f(1, 2, 3)", "f(0, 2, 3)"])
        self.assertIn("a if flag else b", out)

    def test_chain_before_a_conditional(self):
        """`(a and b) or c` ahead of the test still joins as one chain."""
        src = "def f(flag, a, b, c):\n    r = (a and b) or (c if flag else 5)\n    return r\n"
        self.assert_same_results(src, [f"f({fl}, {a}, {b}, {c})" for fl in (0, 1)
                                       for a in (0, 1) for b in (0, 2) for c in (0, 3)])


if __name__ == "__main__":
    unittest.main()
