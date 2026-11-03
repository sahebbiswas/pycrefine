"""The tail that 3.12 copies into the end of an `except` clause is written once (#148).

3.12 ends a clause with a copy of a short tail (`return len(log)`) as well as laying the tail out once after the try.
The clause falls through to the tail in the source, so the copy is not source code.
"""
import ast
import unittest

from .test_helpers import decompile


class CM:
    def __init__(self, log):
        self.log = log

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.log.append("exit")
        return False


def _namespace(code):
    namespace = {"CM": CM}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestHandlerTailCopy(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_after_a_try_with_a_with_body(self):
        out = self.assert_same_results(
            "def m(raises, log):\n    try:\n        with CM(log):\n            log.append(0)\n"
            "            if raises:\n                raise ValueError\n    except ValueError:\n        log.append(2)\n"
            "    return len(log)\n", "m(False, [])", "m(True, [])")
        self.assertEqual(out.count("return len(log)"), 1)

    def test_after_a_plain_try(self):
        out = self.assert_same_results(
            "def m(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n        log.append(2)\n"
            "    return len(log)\n", "m('1', [])", "m('x', [])")
        self.assertEqual(out.count("return len(log)"), 1)

    def test_a_tail_that_differs_is_kept(self):
        out = self.assert_same_results(
            "def m(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n        log.append(2)\n"
            "        return 'bad'\n    return len(log)\n", "m('1', [])", "m('x', [])")
        self.assertIn("return 'bad'", out)

    def test_code_before_the_return(self):
        out = self.assert_same_results(
            "def m(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n        log.append(2)\n"
            "    log.append('end')\n    return len(log)\n", "m('1', [])", "m('x', [])")
        self.assertEqual(out.count("return len(log)"), 1)


if __name__ == "__main__":
    unittest.main()
