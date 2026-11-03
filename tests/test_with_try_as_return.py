"""A `with` whose `try` has `except E as e: return ...`, in a loop (#298).

The clause's return runs the `with` exit and the `e = None; del e` clean-up; the statement after the `with` was written
inside the `with` on 3.12 to 3.14, so `__exit__` ran after it instead of before it. The exception-clause fixes of #297
and #299 cover it; this keeps the shape from coming back.
"""
import ast
import unittest

from .test_helpers import decompile

_PRELUDE = ("class Ctx:\n    def __init__(s, log):\n        s.log = log\n    def __enter__(s):\n        return s\n"
            "    def __exit__(s, *a):\n        s.log.append('exit')\n        return False\n"
            "def boom(v):\n    if v:\n        raise ValueError(v)\n")


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestWithTryAsReturn(unittest.TestCase):

    def assert_same_results(self, body, *calls):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_the_reproduction(self):
        self.assert_same_results(
            "def d(log, v):\n    for i in range(2):\n        with Ctx(log):\n            try:\n                boom(v)\n"
            "            except ValueError as e:\n                return str(e) + '!'\n        log.append(i)\n"
            "    return log\n", "d([], 0)", "d([], 'x')")

    def test_without_the_name(self):
        self.assert_same_results(
            "def d(log, v):\n    for i in range(2):\n        with Ctx(log):\n            try:\n                boom(v)\n"
            "            except ValueError:\n                return 'caught'\n        log.append(i)\n"
            "    return log\n", "d([], 0)", "d([], 'x')")

    def test_in_a_while_loop(self):
        self.assert_same_results(
            "def d(log, v):\n    i = 0\n    while i < 2:\n        i += 1\n        with Ctx(log):\n            try:\n"
            "                boom(v)\n            except ValueError as e:\n                return str(e)\n"
            "        log.append(i)\n    return log\n", "d([], 0)", "d([], 'x')")

    def test_without_a_loop(self):
        self.assert_same_results(
            "def d(log, v):\n    with Ctx(log):\n        try:\n            boom(v)\n        except ValueError as e:\n"
            "            return str(e)\n    log.append('after')\n    return log\n", "d([], 0)", "d([], 'x')")


if __name__ == "__main__":
    unittest.main()
