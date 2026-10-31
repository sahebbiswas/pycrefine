"""An `except E: continue` (or `break`, `return`) in a try inside a `with` inside a loop (#176)."""
import ast
import sys
import unittest

from .test_helpers import decompile

_PRELUDE = (
    "class Ctx:\n    def __init__(self, log):\n        self.log = log\n    def __enter__(self):\n        return self\n"
    "    def __exit__(self, *exc):\n        self.log.append('exit')\n        return False\n"
    "def boom(v):\n    if v:\n        raise ValueError(v)\n"
    "def pick(v, i):\n    if v == 2 and i == 0:\n        raise KeyError(i)\n    if v:\n        raise ValueError(v)\n")


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


@unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 try/except is tracked separately")
class TestExceptContinueInWith(unittest.TestCase):

    def assert_same_results(self, body, *calls):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_continue_in_handler(self):
        out = self.assert_same_results(
            "def c(log, v):\n    for i in range(2):\n        with Ctx(log):\n            try:\n"
            "                boom(v)\n            except ValueError:\n                continue\n"
            "        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")
        self.assertEqual(out.count("log.append(i)"), 1)
        self.assertNotIn("None(None", out)

    def test_break_in_handler(self):
        self.assert_same_results(
            "def c(log, v):\n    for i in range(3):\n        with Ctx(log):\n            try:\n"
            "                boom(v)\n            except ValueError:\n                break\n"
            "        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")

    @unittest.skipIf(sys.version_info < (3, 11), "3.9: a return in a handler inside a with is tracked separately")
    def test_return_in_handler(self):
        self.assert_same_results(
            "def c(log, v):\n    for i in range(2):\n        with Ctx(log):\n            try:\n"
            "                boom(v)\n            except ValueError:\n                return 'bad'\n"
            "        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")

    def test_statement_before_continue(self):
        self.assert_same_results(
            "def c(log, v):\n    for i in range(2):\n        with Ctx(log):\n            try:\n"
            "                boom(v)\n            except ValueError:\n                log.append('bad')\n"
            "                continue\n        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")

    def test_two_handlers_one_continues(self):
        self.assert_same_results(
            "def c(log, v):\n    for i in range(2):\n        with Ctx(log):\n            try:\n"
            "                pick(v, i)\n            except KeyError:\n                log.append('key')\n"
            "            except ValueError:\n                continue\n        log.append(i)\n    return log\n",
            "c([], 0)", "c([], 1)", "c([], 2)")

    @unittest.skipIf(sys.version_info < (3, 12), "3.9 and 3.11: a continue to a while condition from a handler is tracked separately")
    def test_while_loop(self):
        self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 2:\n        i += 1\n        with Ctx(log):\n            try:\n"
            "                boom(v)\n            except ValueError:\n                continue\n"
            "        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")

    def test_try_before_the_with_exit_without_a_handler_exit(self):
        """The handler that falls through keeps the code after the try inside the with."""
        out = self.assert_same_results(
            "def c(log, v):\n    for i in range(2):\n        with Ctx(log):\n            try:\n"
            "                boom(v)\n            except ValueError:\n                log.append('bad')\n"
            "            log.append('in')\n        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")
        self.assertEqual(out.count("log.append('in')"), 1)

    def _try_bodies(self, out):
        return [len(node.body) for node in ast.walk(ast.parse(out)) if isinstance(node, ast.Try)]

    def test_code_after_a_continuing_try_stays_after_it_in_for(self):
        """The statement after the try is not part of the protected body."""
        out = self.assert_same_results(
            "def c(log, v):\n    for i in range(2):\n        try:\n            boom(v)\n"
            "        except ValueError:\n            continue\n        log.append(i)\n    return log\n",
            "c([], 0)", "c([], 1)")
        self.assertEqual(self._try_bodies(out), [1])

    @unittest.skipIf(sys.version_info < (3, 12), "3.9 and 3.11: a continue to a while condition from a handler is tracked separately")
    def test_code_after_a_continuing_try_stays_after_it_in_while(self):
        out = self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 2:\n        i += 1\n        try:\n            boom(v)\n"
            "        except ValueError:\n            continue\n        log.append(i)\n    return log\n",
            "c([], 0)", "c([], 1)")
        self.assertEqual(self._try_bodies(out), [1])
