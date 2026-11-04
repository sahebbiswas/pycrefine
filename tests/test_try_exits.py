"""The exits of a `try` body (POP_BLOCK on 3.9 and 3.10): continue, break and return from a loop inside a `try`.

Each shape is decompiled and run against the original, on every Python the suite runs on.
"""
import ast
import unittest

from .test_helpers import decompile


def _outcome(function, args):
    try:
        return ("value", function(*args))
    except Exception as exc:  # pylint: disable=broad-except
        return ("raised", type(exc).__name__)


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
            self.assertEqual(_outcome(rebuilt["f"], args), _outcome(original["f"], args), f"{args}\n{out}")
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

    def test_except_clauses_of_every_shape(self):
        body = ("    out = []\n    try:\n        out.append(10 // stop)\n        out.append(xs[stop])\n"
                "    except ZeroDivisionError as err:\n        out.append(type(err).__name__)\n"
                "    except (IndexError, KeyError):\n        out.append('index')\n    return out\n")
        self.assert_same(body, ([1, 2], 0), ([1, 2], 5), ([1, 2], 1), (None, 1))

    def test_a_bare_except(self):
        self.assert_same("    out = []\n    try:\n        out.append(xs[stop])\n    except:\n        out.append('other')\n"
                         "    return out\n", ([1, 2], 1), ([1, 2], 5), (None, 1))

    def test_try_finally_in_a_loop(self):
        self.assert_same("    out = []\n    for x in xs:\n        try:\n            if x == stop:\n                continue\n"
                         "            out.append(x)\n        finally:\n            out.append('f')\n    return out\n",
                         ([1, 2, 3], 2), ([], 0))

    def test_handler_that_returns_or_raises(self):
        body = ("    try:\n        return 10 // stop\n    except ZeroDivisionError:\n        return -1\n")
        self.assert_same(body, ([], 2), ([], 0))
        self.assert_same("    try:\n        return xs[stop]\n    except IndexError as err:\n        raise ValueError('no') from err\n",
                         ([1, 2], 1), ([1, 2], 7))


if __name__ == "__main__":
    unittest.main()
