"""The exits of a `try` body (POP_BLOCK on 3.9 and 3.10): continue, break and return from a loop inside a `try`.

Each shape is decompiled and run against the original, on every Python the suite runs on.
"""
import ast
import sys
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

    def _clause(self, middle, tail="        out.append('end')\n"):
        return ("    out = []\n    for x in xs:\n        try:\n            out.append(10 // x)\n"
                "        except ZeroDivisionError:\n" + middle + tail + "    return out\n")

    def test_if_ending_in_continue_inside_an_except_clause(self):
        calls = (([1, 2, 5], 0), ([0, 1, 0, 2], 0), ([0, 0], 0), ([], 0), ([0, 5, 0], 0))
        self.assert_same(self._clause("            if len(out) > 1:\n                continue\n"
                                      "            out.append('z')\n            continue\n"), *calls)
        self.assert_same(self._clause("            if len(out) > 1:\n                continue\n"
                                      "            out.append('z')\n"), *calls)
        self.assert_same(self._clause("            if len(out) > 1:\n                continue\n"
                                      "            if len(out) > 0:\n                continue\n"
                                      "            out.append('z')\n            continue\n"), *calls)

    def test_if_ending_in_break_or_return_inside_an_except_clause(self):
        calls = (([1, 2, 5], 0), ([0, 1, 0, 2], 0), ([0, 0], 0), ([], 0), ([0, 5, 0], 0))
        for exit_ in ("break", "return out"):
            self.assert_same(self._clause("            if len(out) > 1:\n                " + exit_ + "\n"
                                          "            out.append('z')\n            continue\n"), *calls)

    def test_continue_ending_an_if_followed_by_another_handler(self):
        self.assert_same("    out = []\n    for x in xs:\n        try:\n            out.append(10 // x)\n"
                         "        except ZeroDivisionError:\n            if len(out) > 1:\n                continue\n"
                         "            out.append('z')\n            continue\n        except ValueError:\n            break\n"
                         "        out.append('end')\n    return out\n", ([1, 2, 5], 0), ([0, 1, 0, 2], 0), ([0, 0], 0))

    @unittest.skipIf(sys.version_info[:2] == (3, 14), "3.14 rebuilds the inner loop wrongly (#382)")
    def test_if_ending_in_break_out_of_an_inner_loop_inside_an_except_clause(self):
        self.assert_same("    out = []\n    n = 0\n    while n < 3:\n        n += 1\n        m = 0\n        while m < 2:\n            m += 1\n"
                         "            try:\n                out.append(10 // xs[m - 1])\n            except ZeroDivisionError:\n"
                         "                if len(out) > 1:\n                    break\n                out.append('z')\n                continue\n"
                         "            out.append('end')\n        out.append('e')\n    return out\n",
                         ([1, 2, 5], 0), ([0, 1, 0, 2], 0), ([0, 0], 0), ([0, 0, 0], 0))

    def _nested_finally(self, clause, handlers=""):
        return ("    try:\n        if stop == 1:\n            raise ValueError\n        if stop == 2:\n            raise KeyError\n"
                "    except ValueError:\n" + clause + handlers +
                "    finally:\n        xs.append('outer')\n        xs.append('after')\n    return xs\n")

    @unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 rebuilds the nested finally wrongly (#378)")
    def test_finally_inside_a_clause_of_a_try_with_a_finally(self):
        calls = (([], 0), ([], 1), ([], 2))
        inner = "        try:\n            xs.append('inner')\n        finally:\n            xs.append('nested')\n"
        self.assert_same(self._nested_finally(inner), *calls)
        self.assert_same(self._nested_finally(inner + "        xs.append('tail')\n"), *calls)
        self.assert_same(self._nested_finally(inner, "    except KeyError:\n        xs.append('key')\n"), *calls)

    @unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 rebuilds the nested finally wrongly (#378)")
    def test_finally_inside_a_clause_whose_body_always_raises(self):
        raising = "        try:\n            xs.append('inner')\n            raise KeyError\n        finally:\n            xs.append('nested')\n"
        self.assert_same(self._nested_finally(raising), ([], 0), ([], 1), ([], 2))


if __name__ == "__main__":
    unittest.main()
