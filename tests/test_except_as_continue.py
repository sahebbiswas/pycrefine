"""`except E as e: continue` in a `while` loop (#297).

The clean-up that `except E as e` adds (`e = None; del e`, and a copy of it for an exception raised in the
clause) hid from the 3.12 to 3.14 analysis that the clause ends in a jump back to the loop. The statements
after the `try` were written inside its body, followed by a `return`, so the loop ended after one pass.
"""
import ast
import unittest

from .test_except_continue_while import _PRELUDE, _evaluate, _namespace
from .test_helpers import decompile


class TestExceptAsContinueInWhile(unittest.TestCase):

    def assert_same_results(self, body, *calls):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(_evaluate(call, rebuilt, out), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_the_reproduction(self):
        out = self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 3:\n        i += 1\n        try:\n            boom(v and i == 2)\n"
            "        except ValueError as e:\n            log.append(str(e))\n            continue\n"
            "        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")
        self.assertRegex(out, r"except ValueError as e:\n\s+log\.append\(str\(e\)\)\n\s+continue\n\s+log\.append\(i\)")

    def test_the_name_is_unbound_after_the_clause(self):
        self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 3:\n        i += 1\n        try:\n            boom(v and i == 2)\n"
            "        except ValueError as e:\n            log.append(str(e))\n            continue\n"
            "        log.append('e' in dir())\n    return log\n", "c([], 0)", "c([], 1)")

    def test_a_second_clause_without_a_name(self):
        self.assert_same_results(
            "def raise_for(v, i):\n    if v and i == 2:\n        raise ValueError(i)\n    if v and i == 3:\n"
            "        raise TypeError(i)\n"
            "def c(log, v):\n    i = 0\n    while i < 4:\n        i += 1\n        try:\n            raise_for(v, i)\n"
            "        except ValueError as e:\n            log.append(str(e))\n            continue\n"
            "        except TypeError:\n            log.append('t')\n        log.append(i)\n    return log\n",
            "c([], 0)", "c([], 1)")

    def test_two_named_clauses(self):
        self.assert_same_results(
            "def raise_for(v, i):\n    if v and i == 2:\n        raise ValueError(i)\n    if v and i == 3:\n"
            "        raise TypeError(i)\n"
            "def c(log, v):\n    i = 0\n    while i < 4:\n        i += 1\n        try:\n            raise_for(v, i)\n"
            "        except ValueError as e:\n            log.append(str(e))\n            continue\n"
            "        except TypeError as t:\n            log.append(repr(t))\n            continue\n"
            "        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")

    def test_for_loop(self):
        self.assert_same_results(
            "def c(log, v):\n    for i in range(3):\n        try:\n            boom(v and i == 1)\n"
            "        except ValueError as e:\n            log.append(str(e))\n            continue\n"
            "        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")

    def test_return_in_the_clause(self):
        self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 3:\n        i += 1\n        try:\n            boom(v and i == 2)\n"
            "        except ValueError as e:\n            return str(e)\n        log.append(i)\n    return log\n",
            "c([], 0)", "c([], 1)")

    def test_code_after_the_loop(self):
        self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 3:\n        i += 1\n        try:\n            boom(v and i == 2)\n"
            "        except ValueError as e:\n            log.append(str(e))\n            continue\n"
            "        log.append(i)\n    log.append('end')\n    return log\n", "c([], 0)", "c([], 1)")

    def test_a_clause_that_does_not_continue(self):
        self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 3:\n        i += 1\n        try:\n            boom(v and i == 2)\n"
            "        except ValueError as e:\n            log.append(str(e))\n        log.append(i)\n    return log\n",
            "c([], 0)", "c([], 1)")


if __name__ == "__main__":
    unittest.main()
