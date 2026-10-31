"""Conditional expressions and chained comparisons used as values (#201, #154, #178, #147, #184)."""
import ast
import sys
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestValueExpressions(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_augmented_assignment_in_one_branch(self):
        """#201: `n //= 2` in one arm was written with `?` as the operator."""
        src = "def f(n):\n    if n % 2:\n        n = 3 * n + 1\n    else:\n        n //= 2\n    return n\n"
        out = self.assert_same_results(src, "[f(i) for i in range(10)]")
        self.assertNotIn("?", out)

    def test_in_place_operator_keeps_its_statement(self):
        """An in-place arm is not rewritten as `x = x op y`: `+=` on a list mutates it for its aliases."""
        src = "def f(c, x, d):\n    y = x\n    if c:\n        x += d\n    else:\n        x = d\n    return y, x\n"
        out = self.assert_same_results(src, "[f(c, [1], [2]) for c in (0, 1)]")
        self.assertIn("x += d", out)

    def test_chained_comparison_returned(self):
        out = self.assert_same_results("def r(n):\n    return 1 < n < 5\n", "[r(i) for i in range(8)]")
        self.assertIn("return 1 < n < 5", out)
        self.assertEqual(out.count("return"), 1, out)

    def test_chained_comparison_stored(self):
        out = self.assert_same_results("def s(n):\n    ok = 1 < n < 5\n    return ok\n", "[s(i) for i in range(8)]")
        self.assertIn("ok = 1 < n < 5", out)
        self.assertNotIn("\n    n\n", out)

    def test_long_chain(self):
        out = self.assert_same_results("def s(n):\n    return 1 < n < 5 < 9\n", "[s(i) for i in range(10)]")
        self.assertIn("1 < n < 5 < 9", out)

    def test_chain_with_calls(self):
        src = "def s(a, f):\n    return 0 <= f(a) < 10 != a\n"
        self.assert_same_results(src, "[s(i, lambda x: x * 2) for i in range(8)]")

    @unittest.skipIf(sys.version_info < (3, 12), "3.9-3.11 write this one wrong, tracked separately")
    def test_branch_with_star_call_and_or(self):
        """A branch with `fn(*args) or default` keeps its call."""
        src = ("def f(flag, fn, args, default, other):\n    r = (fn(*args) or default) if flag else other\n"
               "    return r\n")
        out = self.assert_same_results(src, "[f(fl, lambda *a: a, (0,), 'd', 'o') for fl in (0, 1)]",
                                       "[f(fl, lambda *a: (), (0,), 'd', 'o') for fl in (0, 1)]")
        self.assertIn("fn(*args)", out)

    def test_conditional_callee(self):
        out = self.assert_same_results(
            "def h(cond, f, g):\n    return (f if cond else g)(1, 2)\n",
            "[h(c, lambda a, b: a, lambda a, b: b) for c in (0, 1)]")
        if sys.version_info[:2] in ((3, 9), (3, 11), (3, 14)):
            self.assertIn("(f if cond else g)(1, 2)", out)

    def test_branches_with_unpacking_and_keywords(self):
        src = ("def q(name, fn, a):\n    res = fn(*a) if name else fn(k=1)\n    return res\n"
               "def r(name, fn):\n    res = fn(1, [0, 1, 2]) if name == 'r2' else fn(2)\n    return res\n")
        self.assert_same_results(src, "[q(n, lambda *a, **k: (a, k), (1, 2)) for n in (0, 1)]",
                                 "[r(n, lambda *a: a) for n in ('r2', 'x')]")

    @unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 12), (3, 13), (3, 14)),
                     "3.10 and 3.12+ copy the code after the expression into each branch")
    def test_conditional_in_an_fstring_field(self):
        out = self.assert_same_results(
            "def f(n, name):\n    return f\"{n * 2} {name.title()} {n if n > 40 else 0} {{literal}}\"\n",
            "[f(n, 'w') for n in (42, 3)]")
        self.assertIn("n if n > 40 else 0", out)


if __name__ == "__main__":
    unittest.main()
