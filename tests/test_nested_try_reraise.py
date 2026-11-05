"""A `try` whose body is another `try` with an `except` clause that re-raises or raises (#340).

The outer `try` has no protected range of its own when its body is only the inner `try`, so its
handler was not tied to it and the code the outer body ran after the inner `try` ended was written
as the `else` of the outer one, or was lost. Each shape is decompiled and run against the original,
on every Python the suite runs on.
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


CALLS = ((0,), (2,), (None,))


class TestNestedTryReraise(unittest.TestCase):

    def assert_same(self, body, calls=CALLS):
        source = "def f(stop):\n    out = []\n" + body + "    return out\n"
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for args in calls:
            self.assertEqual(_outcome(rebuilt["f"], args), _outcome(original["f"], args), f"{args}\n{out}")
        self.assertNotIn("else:", out)
        return out

    def test_a_bare_raise_in_the_inner_handler(self):
        self.assert_same(
            "    try:\n        try:\n            out.append(10 // stop)\n        except ZeroDivisionError:\n"
            "            raise\n    except ArithmeticError:\n        out.append(2)\n")

    def test_a_raise_of_another_exception(self):
        self.assert_same(
            "    try:\n        try:\n            out.append(10 // stop)\n        except ZeroDivisionError:\n"
            "            out.append(1)\n            raise ValueError\n    except ValueError:\n        out.append(2)\n")

    def test_a_statement_before_the_inner_try(self):
        self.assert_same(
            "    try:\n        out.append(0)\n        try:\n            out.append(10 // stop)\n"
            "        except ZeroDivisionError:\n            out.append(1)\n            raise\n"
            "    except ArithmeticError:\n        out.append(2)\n")

    def test_the_inner_handler_returns(self):
        self.assert_same(
            "    try:\n        try:\n            out.append(10 // stop)\n        except ZeroDivisionError:\n"
            "            return ['inner']\n    except TypeError:\n        out.append('outer')\n")


    def test_three_levels_of_try(self):
        # each level is only the next one: a NOP per level, and a handler for each
        self.assert_same(
            "    try:\n        try:\n            try:\n                out.append(10 // stop)\n"
            "            except ZeroDivisionError:\n                out.append('a')\n                raise ValueError\n"
            "        except ValueError:\n            out.append('b')\n            raise KeyError\n"
            "    except KeyError:\n        out.append('c')\n")

    def test_four_levels_of_try(self):
        self.assert_same(
            "    try:\n        try:\n            try:\n                try:\n"
            "                    out.append(10 // stop)\n                except ZeroDivisionError:\n"
            "                    out.append('a')\n                    raise ValueError\n"
            "            except ValueError:\n                out.append('b')\n                raise KeyError\n"
            "        except KeyError:\n            out.append('c')\n            raise TypeError\n"
            "    except TypeError:\n        out.append('d')\n")

    def test_a_try_that_starts_with_pass_is_not_a_nest(self):
        # (a NOP that only precedes a try must not be bound to a later handler)
        self.assert_same(
            "    try:\n        pass\n        out.append(10 // stop)\n    except ZeroDivisionError:\n"
            "        out.append('z')\n    try:\n        out.append(1 // stop)\n    except ZeroDivisionError:\n"
            "        out.append('z2')\n")

    def test_a_pass_before_two_sequential_tries(self):
        self.assert_same(
            "    pass\n    try:\n        out.append(10 // stop)\n    except ZeroDivisionError:\n"
            "        out.append('z')\n    try:\n        out.append(1 // stop)\n    except ZeroDivisionError:\n"
            "        out.append('z2')\n")


if __name__ == "__main__":
    unittest.main()
