"""A `try`/`except` inside a `with`, whose body raises, followed by code after the `with` (#146).

3.12 and later lay the handlers of a body that never falls through right after it, so the first handler is not the end
of the ordinary code. The `with`'s exit was missed: the code after it was written inside the `with` (3.12), or the
unwinding clean-up of the clause closed the `with` one statement too early, so a `return` landed at the wrong indent.
"""
import ast
import unittest

from .test_helpers import decompile


class CM:
    def __init__(self, log, name):
        self.log, self.name = log, name

    def __enter__(self):
        self.log.append("in " + self.name)
        return self

    def __exit__(self, *args):
        self.log.append("out " + self.name)
        return False


def _namespace(code):
    namespace = {"CM": CM}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _call(namespace, name):
    log = []
    return namespace[name](log), log


class TestRaisingWithInWith(unittest.TestCase):

    def assert_same_results(self, source, name):
        out = decompile(source)
        ast.parse(out)
        self.assertEqual(_call(_namespace(out), name), _call(_namespace(source), name), out)
        return out

    def test_the_reproduction(self):
        out = self.assert_same_results(
            "def r5(log):\n    with CM(log, 'outer'):\n        try:\n            with CM(log, 'inner'):\n"
            "                raise ValueError('x')\n        except ValueError:\n            log.append('caught')\n"
            "    return len(log)\n", "r5")
        self.assertIn("    return len(log)", out)
        self.assertNotIn("        return len(log)", out)

    def test_a_body_that_raises(self):
        self.assert_same_results(
            "def c(log):\n    with CM(log, 'outer'):\n        try:\n            raise ValueError('x')\n"
            "        except ValueError:\n            log.append('caught')\n    return len(log)\n", "c")

    def test_a_statement_before_the_return(self):
        self.assert_same_results(
            "def r6(log):\n    with CM(log, 'outer'):\n        try:\n            with CM(log, 'inner'):\n"
            "                raise ValueError('x')\n        except ValueError:\n            log.append('caught')\n"
            "    log.append('after')\n    return len(log)\n", "r6")

    def test_a_body_that_does_not_raise(self):
        self.assert_same_results(
            "def r7(log):\n    with CM(log, 'outer'):\n        try:\n            with CM(log, 'inner'):\n"
            "                log.append('body')\n        except ValueError:\n            log.append('caught')\n"
            "    return len(log)\n", "r7")


if __name__ == "__main__":
    unittest.main()
