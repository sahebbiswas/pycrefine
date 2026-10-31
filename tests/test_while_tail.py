"""A `while` loop that ends a function (or the code of an `if`) stays a loop (#161)."""
import ast
import sys
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestWhileLoopAtTheEnd(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_no_return_inside_the_loop(self):
        """3.12 and 3.13 wrote the implicit `return None` of the loop's exit inside its body."""
        out = self.assert_same_results(
            "def f(n, out):\n    i = 0\n    while i < n:\n        out.append(i)\n        i += 1\n",
            "(lambda o: (f(4, o), o))([])",
        )
        self.assertNotIn("return", out)

    def test_loop_with_a_continue(self):
        self.assert_same_results(
            "def w4(out):\n    i = 0\n    while i < 3:\n        i += 1\n        if i < 3:\n            continue\n"
            "        out.append(i)\n",
            "(lambda o: (w4(o), o))([])",
        )

    def test_loop_whose_body_is_a_try_finally(self):
        if sys.version_info[:2] == (3, 10):
            self.skipTest("3.10 block-based try/finally is not decompiled (#179)")
        self.assert_same_results(
            "def f(n, out):\n    i = 0\n    while i < n:\n        i += 1\n        try:\n            out.append(i)\n"
            "        finally:\n            out.append('c')\n",
            "(lambda o: (f(2, o), o))([])",
        )

    def test_conditions_with_and_or_and_not(self):
        self.assert_same_results(
            "def f(n, m, out):\n    i = 0\n    while i < n and i < m:\n        out.append(i)\n        i += 1\n",
            "(lambda o: (f(5, 3, o), o))([])",
        )
        self.assert_same_results(
            "def f(n, m, out):\n    i = 0\n    while i < n or i < m:\n        out.append(i)\n        i += 1\n",
            "(lambda o: (f(2, 4, o), o))([])",
        )
        self.assert_same_results(
            "def f(xs, out):\n    while xs:\n        out.append(xs.pop())\n",
            "(lambda o: (f([1, 2, 3], o), o))([])",
        )

    def test_nested_loops(self):
        self.assert_same_results(
            "def f(n, out):\n    i = 0\n    while i < n:\n        j = 0\n        while j < i:\n"
            "            out.append((i, j))\n            j += 1\n        i += 1\n",
            "(lambda o: (f(4, o), o))([])",
        )

    def test_loop_in_a_method_a_generator_and_after_an_early_return(self):
        self.assert_same_results(
            "class C:\n    def run(self, n, out):\n        i = 0\n        while i < n:\n            out.append(i)\n"
            "            i += 1\n",
            "(lambda o: (C().run(3, o), o))([])",
        )
        self.assert_same_results(
            "def f(n):\n    i = 0\n    while i < n:\n        yield i\n        i += 1\n", "list(f(3))")
        self.assert_same_results(
            "def f(a, n, out):\n    if a:\n        return\n    i = 0\n    while i < n:\n        out.append(i)\n"
            "        i += 1\n",
            "(lambda o: (f(False, 3, o), o))([])",
        )

    def test_loop_inside_an_if_that_ends_the_function(self):
        """The `if` has an implicit return of its own, between the loop's exits (3.10, 3.11)."""
        self.assert_same_results(
            "def f(c, n, out):\n    if c:\n        i = 0\n        while i < n:\n            out.append(i)\n"
            "            i += 1\n",
            "(lambda o: (f(True, 3, o), o))([])",
            "(lambda o: (f(False, 3, o), o))([])",
        )

    def test_a_return_after_the_loop_and_a_while_else(self):
        self.assert_same_results(
            "def f(n, out):\n    i = 0\n    while i < n:\n        out.append(i)\n        i += 1\n    return\n",
            "(lambda o: (f(3, o), o))([])",
        )
        self.assert_same_results(
            "def f(n, out):\n    i = 0\n    while i < n:\n        i += 1\n    else:\n        out.append('done')\n",
            "(lambda o: (f(3, o), o))([])",
        )

    def test_an_explicit_return_in_the_loop_is_kept(self):
        out = self.assert_same_results(
            "def f(n):\n    i = 0\n    while i < n:\n        if i == 2:\n            return 'two'\n        i += 1\n"
            "    return 'end'\n",
            "f(5)", "f(1)",
        )
        self.assertIn("return 'two'", out)


if __name__ == "__main__":
    unittest.main()
