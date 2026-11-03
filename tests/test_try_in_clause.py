"""A `try` statement written inside an `except` clause (#304).

3.12 and later lay the inner try's handler out right after the inner body, in the middle of the outer handler's code, and
end each path in its own copy of the code after the statement. The inner handler was taken for another clause of the
outer try, written after the outer `return`, which is not valid Python.
"""
import ast
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _call(namespace, name, value):
    log = []
    try:
        return namespace[name](value, log)
    except Exception as ex:  # pylint: disable=broad-except
        return type(ex).__name__, log


class TestTryInClause(unittest.TestCase):

    def assert_same_results(self, source, name, values=("1", "2.5", "x")):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for value in values:
            self.assertEqual(_call(rebuilt, name, value), _call(original, name, value), f"{value!r}\n{out}")
        return out

    def test_the_reproduction(self):
        out = self.assert_same_results(
            "def f(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n        try:\n"
            "            log.append(float(x))\n        except ValueError:\n            log.append('no')\n"
            "    return log\n", "f")
        self.assertEqual(out.count("except ValueError:"), 2)

    def test_a_statement_before_the_inner_try(self):
        self.assert_same_results(
            "def g(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n        log.append('a')\n"
            "        try:\n            log.append(float(x))\n        except ValueError:\n            log.append('no')\n"
            "    return log\n", "g")

    def test_code_after_the_statement(self):
        self.assert_same_results(
            "def h(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n        try:\n"
            "            log.append(float(x))\n        except ValueError:\n            log.append('no')\n"
            "    log.append('end')\n    log.append('end2')\n    return log\n", "h")

    def test_the_inner_clause_does_not_match(self):
        self.assert_same_results(
            "def k(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n        try:\n"
            "            log.append(float(x) / 0)\n        except TypeError:\n            log.append('no')\n"
            "    return log\n", "k", ("1", "2.5", "x"))

    def test_a_try_that_wraps_another_is_unchanged(self):
        self.assert_same_results(
            "def d(x, log):\n    try:\n        try:\n            log.append(int(x))\n        except KeyError:\n"
            "            log.append('k')\n    except ValueError:\n        log.append('v')\n    return log\n", "d")


if __name__ == "__main__":
    unittest.main()
