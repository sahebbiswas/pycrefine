"""`except E: continue` in a `while` loop, and `return` in a handler inside a `with` in a loop (#286).

3.9 wrote the `continue` after the whole `try` statement, and 3.11 took the statements after the `try`
into the handler (the loop head, where 3.9-3.11 test the condition, was taken for the `try`'s merge point).
On 3.9 a `return` in an except clause that sits inside a `with` also lost its place: the exit
function the compiler drops before the returned value is computed was not hidden.
"""
import ast
import signal
import sys
import time
import unittest

from .test_helpers import decompile

_TIMEOUT_SECONDS = 10

_PRELUDE = ("def boom(v):\n    if v:\n        raise ValueError(v)\n"
            "class Ctx:\n    def __init__(s, log):\n        s.log = log\n    def __enter__(s):\n        return s\n"
            "    def __exit__(s, *a):\n        s.log.append('exit')\n        return False\n")


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _evaluate(call, namespace, out):
    """Evaluate *call*; a loop that never ends (the symptom of this bug) fails the test instead of hanging it."""
    if not hasattr(signal, "SIGALRM"):
        deadline = time.monotonic() + _TIMEOUT_SECONDS

        def check_deadline(*_):
            if time.monotonic() > deadline:
                raise AssertionError(f"{call} did not finish\n{out}")
            return check_deadline

        previous_trace = sys.gettrace()
        sys.settrace(check_deadline)
        try:
            return eval(call, namespace)  # pylint: disable=eval-used
        finally:
            sys.settrace(previous_trace)

    def give_up(*_):
        raise AssertionError(f"{call} did not finish\n{out}")

    previous = signal.signal(signal.SIGALRM, give_up)
    signal.alarm(_TIMEOUT_SECONDS)
    try:
        return eval(call, namespace)  # pylint: disable=eval-used
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


class TestExceptContinueInWhile(unittest.TestCase):

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
            "def c(log, v):\n    i = 0\n    while i < 2:\n        i += 1\n        try:\n            boom(v)\n"
            "        except ValueError:\n            continue\n        log.append(i)\n    return log\n",
            "c([], 0)", "c([], 1)")
        self.assertRegex(out, r"except ValueError:\n\s+continue\n\s+log\.append\(i\)")

    def test_handler_does_work_before_continue(self):
        self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 3:\n        i += 1\n        try:\n            boom(v and i == 2)\n"
            "        except ValueError:\n            log.append('bad')\n            continue\n        log.append(i)\n"
            "    return log\n", "c([], 0)", "c([], 1)")

    def test_two_handlers(self):
        self.assert_same_results(
            "def raise_for(v, i):\n    if v and i == 2:\n        raise ValueError(i)\n    if v and i == 3:\n"
            "        raise TypeError(i)\n"
            "def c(log, v):\n    i = 0\n    while i < 4:\n        i += 1\n        try:\n            raise_for(v, i)\n"
            "        except ValueError:\n            continue\n        except TypeError:\n            log.append('t')\n"
            "        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")

    def test_with_a_condition_that_changes_in_the_loop(self):
        self.assert_same_results(
            "def c(items, log):\n    while items:\n        x = items.pop()\n        try:\n            log.append(int(x))\n"
            "        except ValueError:\n            continue\n        log.append('ok')\n    return log\n",
            "c(['1', 'x', '3'], [])", "c([], [])")

    def test_code_after_the_loop(self):
        self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 2:\n        i += 1\n        try:\n            boom(v)\n"
            "        except ValueError:\n            continue\n        log.append(i)\n    log.append('end')\n"
            "    return log\n", "c([], 0)", "c([], 1)")

    def test_while_else_with_handler_continue_and_break(self):
        self.assert_same_results(
            "def c(log, v, stop):\n    i = 0\n    while i < 3:\n        i += 1\n        try:\n            boom(v and i == 2)\n"
            "        except ValueError:\n            continue\n        if i == stop:\n            break\n        log.append(i)\n"
            "    else:\n        log.append('done')\n    return log\n",
            "c([], 0, 0)", "c([], 1, 3)", "c([], 1, 9)")

    def test_continue_inside_a_with(self):
        self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 2:\n        i += 1\n        with Ctx(log):\n            try:\n"
            "                boom(v)\n            except ValueError:\n                continue\n"
            "        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")

    def test_for_loop_is_unchanged(self):
        self.assert_same_results(
            "def c(log, v):\n    for i in range(2):\n        try:\n            boom(v)\n        except ValueError:\n"
            "            continue\n        log.append(i)\n    return log\n", "c([], 0)", "c([], 1)")

    def test_try_without_a_continue_in_a_while(self):
        self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 2:\n        i += 1\n        try:\n            boom(v)\n"
            "        except ValueError:\n            log.append('bad')\n        log.append(i)\n    return log\n",
            "c([], 0)", "c([], 1)")

    @unittest.skipUnless(sys.version_info[:2] == (3, 9), "other versions lose a break in an except clause (#207)")
    def test_break_to_the_head_of_an_enclosing_while_is_not_a_continue(self):
        """An inner `while` that ends its outer loop's body: the handler's `break` jumps to the outer head."""
        self.assert_same_results(
            "def c(log, v):\n    i = 0\n    while i < 2:\n        i += 1\n        j = 0\n        while j < 3:\n"
            "            j += 1\n            try:\n                boom(v + j - 2)\n            except ValueError:\n"
            "                break\n            log.append(j)\n    return log\n", "c([], 0)", "c([], 1)")


class TestReturnInHandlerInsideWith(unittest.TestCase):

    def assert_same_results(self, body, *calls):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(_evaluate(call, rebuilt, out), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def assert_same_log(self, body, out, values=(0, 1)):
        """The context must also be exited on the way out: compare the log each version leaves behind."""
        for v in values:
            log_original, log_rebuilt = [], []
            _namespace(_PRELUDE + body)["d"](log_original, v)
            _namespace(out)["d"](log_rebuilt, v)
            self.assertEqual(log_rebuilt, log_original, out)

    def test_the_reproduction(self):
        src = ("def d(log, v):\n    for i in range(2):\n        with Ctx(log):\n            try:\n                boom(v)\n"
               "            except ValueError:\n                return 'bad'\n        log.append(i)\n    return log\n")
        out = self.assert_same_results(src, "d([], 0)", "d([], 1)")
        self.assertRegex(out, r"except ValueError:\n\s+return 'bad'\n")
        self.assert_same_log(src, out)

    def test_computed_value(self):
        src = ("def d(log, v):\n    for i in range(2):\n        with Ctx(log):\n            try:\n                boom(v)\n"
               "            except ValueError:\n                return 'bad ' + str(v)\n        log.append(i)\n"
               "    return log\n")
        out = self.assert_same_results(src, "d([], 0)", "d([], 'x')")
        self.assert_same_log(src, out, (0, 'x'))

    def test_without_a_loop(self):
        src = ("def d(log, v):\n    with Ctx(log):\n        try:\n            boom(v)\n        except ValueError:\n"
               "            return 'bad'\n    log.append('after')\n    return log\n")
        out = self.assert_same_results(src, "d([], 0)", "d([], 1)")
        self.assert_same_log(src, out)


if __name__ == "__main__":
    unittest.main()
