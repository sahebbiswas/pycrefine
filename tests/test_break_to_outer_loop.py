"""A `break` that ends a `for` loop which is the last statement of an enclosing loop body (#275)."""
import ast
import sys
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestBreakToOuterLoop(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_break_without_else(self):
        """3.12+ wrote the `break` as `continue`, so the inner loop ran on."""
        src = ("def h(n):\n    out = []\n    for i in range(n):\n        for j in range(3):\n"
               "            if i == j:\n                break\n            out.append((i, j))\n    return out\n")
        out = self.assert_same_results(src, "h(4)", "h(0)")
        self.assertIn("break", out)

    def test_for_else_in_for(self):
        src = ("def f(n):\n    out = []\n    for i in range(n):\n        for j in range(2):\n"
               "            if i == j:\n                break\n        else:\n            out.append(i)\n    return out\n")
        out = self.assert_same_results(src, "f(5)", "f(0)")
        self.assertIn("else:", out)

    @unittest.skipIf(sys.version_info < (3, 10), "a for/else in a while on 3.9 is tracked in #275")
    def test_for_else_in_while(self):
        src = ("def g(n):\n    out = []\n    i = 0\n    while i < n:\n        i += 1\n        for j in range(2):\n"
               "            if i == j:\n                break\n        else:\n            out.append(i)\n    return out\n")
        out = self.assert_same_results(src, "g(5)", "g(0)")
        self.assertIn("else:", out)

    def test_break_then_more_code_in_inner_loop(self):
        src = ("def f(rows):\n    out = []\n    for row in rows:\n        for v in row:\n            if v < 0:\n"
               "                break\n            out.append(v)\n    return out\n")
        self.assert_same_results(src, "f([[1, 2], [3, -1, 4], [5]])")

    def test_break_in_a_while_inner_loop_is_not_affected(self):
        src = ("def f(n):\n    out = []\n    for i in range(n):\n        k = 0\n        while k < 5:\n"
               "            if k == i:\n                break\n            k += 1\n        out.append(k)\n    return out\n")
        self.assert_same_results(src, "f(4)")

    @unittest.skipIf(sys.version_info < (3, 10), "long loop bodies on 3.9 are not handled")
    def test_long_outer_loop_with_extended_arg(self):
        """A jump back to a FOR_ITER with an EXTENDED_ARG prefix (an outer body of over 255 instructions)."""
        filler = "".join(f"        out.append({k})\n" for k in range(130))
        src = ("def f(n):\n    out = []\n    for i in range(n):\n        for j in range(2):\n"
               "            if i == j:\n                break\n        else:\n            out.append(-i)\n"
               + filler + "    return out\n")
        out = self.assert_same_results(src, "f(4)", "f(0)")
        self.assertIn("else:", out)
        src2 = ("def g(n):\n    out = []\n    for i in range(n):\n        for j in range(3):\n"
                "            if i == j:\n                break\n            out.append((i, j))\n"
                + filler + "    return out\n")
        out2 = self.assert_same_results(src2, "g(4)")
        self.assertIn("break", out2)


if __name__ == "__main__":
    unittest.main()
