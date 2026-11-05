"""The exits of a `try` body (POP_BLOCK on 3.9 and 3.10): continue, break and return from a loop inside a `try`.

Each shape is decompiled and run against the original, on every Python the suite runs on.
"""
import ast
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestTryExits(unittest.TestCase):

    def assert_same(self, body, *calls):
        source = "def f(xs, stop):\n" + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for args in calls:
            self.assertEqual(rebuilt["f"](*args), original["f"](*args), f"{args}\n{out}")
        return out

    def test_continue_in_a_try(self):
        self.assert_same("    out = []\n    for x in xs:\n        try:\n            if x == stop:\n                continue\n"
                         "            out.append(10 // x)\n        except ZeroDivisionError:\n            out.append(-1)\n    return out\n",
                         ([1, 2, 0, 5], 2), ([0], 9))

    def test_break_in_a_try(self):
        self.assert_same("    out = []\n    for x in xs:\n        try:\n            if x == stop:\n                break\n"
                         "            out.append(10 // x)\n        except ZeroDivisionError:\n            out.append(-1)\n    return out\n",
                         ([1, 2, 0, 5], 2), ([1, 0, 3], 9))

    def test_try_else_and_finally(self):
        self.assert_same("    out = []\n    try:\n        out.append(10 // stop)\n    except ZeroDivisionError:\n"
                         "        out.append('zero')\n    else:\n        out.append('else')\n    finally:\n"
                         "        out.append('finally')\n    return out\n", ([], 2), ([], 0))


if __name__ == "__main__":
    unittest.main()
