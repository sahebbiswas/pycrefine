"""`break` in an except clause of a `while` loop on 3.11 (#193).

3.11 compiles the `break` as POP_EXCEPT and a forward jump past the loop. The handler is rendered on its
own, without the loop around it, so the jump was taken for the end of the clause and the `break` was lost:
the statements after the `try` ran after the handler as well, and a `while ... else` ran its `else`.
"""
import ast
import unittest

from .test_except_continue_while import _PRELUDE, _evaluate, _namespace
from .test_helpers import decompile


class TestBreakInExceptInWhile(unittest.TestCase):

    def assert_same_results(self, body, *calls):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(_evaluate(call, rebuilt, out), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_the_handler_does_work_before_the_break(self):
        out = self.assert_same_results(
            "def c(n, log, v):\n    while n:\n        n -= 1\n        try:\n            boom(v and n == 1)\n"
            "        except ValueError:\n            log.append('bad')\n            break\n        log.append(n)\n    return log\n",
            "c(3, [], 0)", "c(3, [], 1)")
        # (when nothing follows the loop, 3.12 and later copy its `return` into the clause: the same thing)
        self.assertRegex(out, r"except ValueError:\n\s+log\.append\('bad'\)\n\s+(break|return log)\n\s+log\.append\(n\)")

    def test_a_bare_break(self):
        self.assert_same_results(
            "def c(n, log, v):\n    while n:\n        n -= 1\n        try:\n            boom(v and n == 1)\n"
            "        except ValueError:\n            break\n        log.append(n)\n    return log\n",
            "c(3, [], 0)", "c(3, [], 1)")

    def test_with_a_name(self):
        self.assert_same_results(
            "def c(n, log, v):\n    while n:\n        n -= 1\n        try:\n            boom(v and n == 1)\n"
            "        except ValueError as e:\n            log.append(str(e))\n            break\n        log.append(n)\n"
            "    return log\n", "c(3, [], 0)", "c(3, [], 1)")

    def test_code_after_the_loop(self):
        self.assert_same_results(
            "def c(n, log, v):\n    while n:\n        n -= 1\n        try:\n            boom(v and n == 1)\n"
            "        except ValueError:\n            break\n        log.append(n)\n    log.append('end')\n    return log\n",
            "c(3, [], 0)", "c(3, [], 1)")

    def test_a_loop_with_an_else_clause(self):
        """`break` skips the `else:`."""
        self.assert_same_results(
            "def c(n, log, v):\n    while n:\n        n -= 1\n        try:\n            boom(v and n == 1)\n"
            "        except ValueError:\n            break\n        log.append(n)\n    else:\n        log.append('else')\n"
            "    return log\n", "c(3, [], 0)", "c(3, [], 1)")

    def test_second_clause_breaks(self):
        self.assert_same_results(
            "def raise_for(v, n):\n    if v and n == 2:\n        raise ValueError(n)\n    if v and n == 1:\n"
            "        raise TypeError(n)\n"
            "def c(n, log, v):\n    while n:\n        n -= 1\n        try:\n            raise_for(v, n)\n"
            "        except ValueError:\n            log.append('v')\n        except TypeError:\n            break\n"
            "        log.append(n)\n    return log\n", "c(3, [], 0)", "c(3, [], 1)")

    def test_a_for_loop_inside_the_while_keeps_its_own_break(self):
        self.assert_same_results(
            "def c(n, log, v):\n    while n:\n        n -= 1\n        for i in range(3):\n            try:\n"
            "                boom(v and i == 1)\n            except ValueError:\n                break\n"
            "            log.append(i)\n        log.append('w')\n    return log\n", "c(2, [], 0)", "c(2, [], 1)")

    def test_a_break_inside_an_if_of_the_clause(self):
        """The clause goes on after the `if`: what follows it stays in the clause."""
        out = self.assert_same_results(
            "def c(n, log, v):\n    while n:\n        n -= 1\n        try:\n            boom(v and n == 1)\n"
            "        except ValueError:\n            if n > 1:\n                break\n            log.append('first')\n"
            "        log.append(n)\n    return log\n", "c(3, [], 0)", "c(3, [], 1)", "c(2, [], 1)")
        self.assertRegex(out, r"(?s)except ValueError:.*log\.append\('first'\)")

    def test_a_break_ending_an_if_that_ends_the_clause(self):
        self.assert_same_results(
            "def c(n, log, v):\n    while n:\n        n -= 1\n        try:\n            boom(v and n == 1)\n"
            "        except ValueError:\n            if n > 1:\n                break\n"
            "        log.append(n)\n    return log\n", "c(3, [], 0)", "c(3, [], 1)", "c(2, [], 1)")

    def test_a_break_that_also_leaves_a_with(self):
        self.assert_same_results(
            "class Ctx:\n    def __init__(s, log):\n        s.log = log\n    def __enter__(s):\n        s.log.append('in')\n"
            "        return s\n    def __exit__(s, *a):\n        s.log.append('out')\n        return False\n"
            "def c(n, log, v):\n    while n:\n        n -= 1\n        with Ctx(log):\n            try:\n"
            "                boom(v and n == 1)\n            except ValueError:\n                break\n"
            "        log.append(n)\n    return log\n", "c(3, [], 0)", "c(3, [], 1)")

    def test_a_break_after_work_that_also_leaves_a_with(self):
        self.assert_same_results(
            "class Ctx:\n    def __init__(s, log):\n        s.log = log\n    def __enter__(s):\n        s.log.append('in')\n"
            "        return s\n    def __exit__(s, *a):\n        s.log.append('out')\n        return False\n"
            "def c(n, log, v):\n    while n:\n        n -= 1\n        with Ctx(log):\n            try:\n"
            "                boom(v and n == 1)\n            except ValueError:\n                log.append('bad')\n"
            "                break\n            log.append('ok')\n        log.append(n)\n    return log\n",
            "c(3, [], 0)", "c(3, [], 1)")

    def test_a_break_that_leaves_two_nested_withs(self):
        self.assert_same_results(
            "def c(n, log, v):\n    while n:\n        n -= 1\n        with Ctx(log):\n            with Ctx(log):\n"
            "                try:\n                    boom(v and n == 1)\n                except ValueError:\n"
            "                    break\n        log.append(n)\n    return log\n", "c(3, [], 0)", "c(3, [], 1)")

    def test_a_clause_that_does_not_break(self):
        self.assert_same_results(
            "def c(n, log, v):\n    while n:\n        n -= 1\n        try:\n            boom(v and n == 1)\n"
            "        except ValueError:\n            log.append('bad')\n        log.append(n)\n    return log\n",
            "c(3, [], 0)", "c(3, [], 1)")


if __name__ == "__main__":
    unittest.main()
