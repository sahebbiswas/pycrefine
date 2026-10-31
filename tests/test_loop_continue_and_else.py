"""`if c: continue` before a return in a `while` loop, and `else` only for a `for` that had one (#160, #206)."""
import ast
import sys
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestLoopContinueAndElse(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_continue_then_return_in_a_while(self):
        """#160: the `if` that repeats the loop's own condition is not a copy of it."""
        src = ("def w1():\n    i = 0\n    while i < 3:\n        i += 1\n        if i < 3:\n            continue\n"
               "        return i\n")
        out = self.assert_same_results(src, "w1()")
        self.assertIn("continue", out)

    def test_continue_then_return_with_another_condition(self):
        src = ("def w(n):\n    i = 0\n    while i < n:\n        i += 1\n        if i % 2:\n            continue\n"
               "        return i\n    return -1\n")
        self.assert_same_results(src, "[w(n) for n in range(5)]")

    @unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 try/except is tracked separately")
    def test_continue_then_return_in_a_try_finally(self):
        src = ("def w2(log):\n    i = 0\n    while i < 3:\n        try:\n            i += 1\n            if i < 3:\n"
               "                continue\n            return i\n        finally:\n            log.append(i)\n")
        self.assert_same_results(src, "(lambda l: (w2(l), l))([])")

    def test_plain_while_loop_is_unchanged(self):
        src = "def f(n, out):\n    i = 0\n    while i < n:\n        out.append(i)\n        i += 1\n    return out\n"
        out = self.assert_same_results(src, "f(4, [])")
        self.assertNotIn("continue", out)

    def test_statement_after_a_for_with_break_in_a_while(self):
        """#206: the statement after the `for` is not its `else`."""
        src = ("def f(n):\n    seen = []\n    i = 0\n    while i < n:\n        i += 1\n        for j in range(2):\n"
               "            if (i + j) % 3 == 0:\n                break\n            seen.append(j)\n"
               "        seen.append('after')\n    return seen\n")
        out = self.assert_same_results(src, "[f(k) for k in range(7)]")
        self.assertNotIn("else:", out)

    @unittest.skipIf(sys.version_info >= (3, 12), "3.12+ write this one wrong, tracked separately")
    def test_for_else_is_kept(self):
        src = ("def f(n):\n    out = []\n    for i in range(n):\n        for j in range(2):\n            if i == j:\n"
               "                break\n        else:\n            out.append(i)\n    return out\n")
        out = self.assert_same_results(src, "[f(k) for k in range(5)]")
        self.assertIn("else:", out)

    @unittest.skipIf(sys.version_info >= (3, 14), "3.14 writes this one wrong, tracked separately")
    def test_break_to_the_enclosing_head(self):
        """3.9: a `break` that ends the enclosing loop's body was written as `continue`."""
        src = ("def f(n):\n    out = []\n    i = 0\n    while i < n:\n        i += 1\n        for j in range(2):\n"
               "            if i == j:\n                break\n            out.append(j)\n    return out\n")
        out = self.assert_same_results(src, "[f(k) for k in range(5)]")
        self.assertIn("break", out)

    @unittest.skipIf(sys.version_info[:2] in ((3, 9), (3, 10)) or sys.version_info >= (3, 14),
                     "3.9, 3.10 and 3.14 write this one wrong, tracked separately")
    def test_for_else_in_a_while(self):
        src = ("def f(n):\n    out = []\n    i = 0\n    while i < n:\n        i += 1\n        for j in range(2):\n"
               "            if i == j:\n                break\n        else:\n            out.append(i)\n    return out\n")
        self.assert_same_results(src, "[f(k) for k in range(5)]")


if __name__ == "__main__":
    unittest.main()
