"""A `while` loop that ends the body of a `for` loop stays a loop (#233)."""
import ast
import sys
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestWhileInFor(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_while_last_in_for_body(self):
        """3.12 and 3.13 turned it into an `if` (the loop ran at most once per item)."""
        src = ("def f(items, q):\n    for x in items:\n        while q[0] > 0:\n            q[0] -= 1\n"
               "    return q[0]\n")
        out = self.assert_same_results(src, "f([1, 2], [5])", "f([], [3])")
        self.assertIn("while", out)
        self.assertNotIn("continue", out)

    def test_while_last_in_for_body_with_statements_before(self):
        src = ("def f(items, o):\n    for x in items:\n        o.append(x)\n        n = x\n"
               "        while n > 0:\n            n -= 1\n            o.append(n)\n    return o\n")
        self.assert_same_results(src, "f([2, 0, 3], [])")

    def test_while_followed_by_a_statement(self):
        src = ("def f(items, o):\n    for x in items:\n        n = x\n        while n > 0:\n            n -= 1\n"
               "            o.append(n)\n        o.append('end')\n    return o\n")
        self.assert_same_results(src, "f([2, 0], [])")

    @unittest.skipIf(sys.version_info < (3, 11), "3.9 and 3.10 try/except is tracked separately")
    def test_while_in_an_except_clause(self):
        src = ("def f(items, q):\n    for x in items:\n        try:\n            q.get(x)\n"
               "        except ValueError:\n            while q.n > 0:\n                q.n -= 1\n    return q.n\n"
               "class Q:\n    n = 3\n    def get(self, x):\n        if x:\n            raise ValueError\n")
        out = self.assert_same_results(src, "f([0, 1], Q())", "f([0], Q())")
        self.assertNotIn("if not (q.n > 0)", out)


if __name__ == "__main__":
    unittest.main()
