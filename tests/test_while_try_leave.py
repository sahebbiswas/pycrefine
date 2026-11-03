"""A `while True` loop whose `try` is followed by an `if`/`break`, and whose `except` clause leaves the function (#295).

3.12 and later lay the handlers out after all other code. The clause never rejoins the loop, so the code after
the `try` looked like its `else` clause: the `if`/`break` lost its body, and the rest of the loop and the code after
it ended up inside the `try`.
"""
import ast
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


_TEMPLATE = ("def f(n, o):\n    while True:\n        n -= 1\n        try:\n"
             "            o.append(int('x') if n == 1 else n)\n        except ValueError:\n            {leave}\n"
             "        if n <= 0:\n            break\n    o.append('end')\n    return o\n")
_CALLS = ("f(3, [])", "f(1, [])", "f(0, [])", "f(-2, [])")


class TestWhileTryLeave(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_return_none(self):
        out = self.assert_same_results(_TEMPLATE.format(leave="return None"), *_CALLS)
        self.assertNotIn("else:", out)

    def test_return_a_value(self):
        self.assert_same_results(_TEMPLATE.format(leave="return 0"), *_CALLS)

    def test_bare_return(self):
        self.assert_same_results(_TEMPLATE.format(leave="return"), *_CALLS)

    def test_a_real_else_clause_is_kept(self):
        out = self.assert_same_results(
            "def f(x, o):\n    try:\n        n = int(x)\n    except ValueError:\n        return None\n    else:\n"
            "        o.append(n)\n    o.append('end')\n    return o\n", "f('1', [])", "f('x', [])")
        self.assertIn("o.append(n)", out)


if __name__ == "__main__":
    unittest.main()
