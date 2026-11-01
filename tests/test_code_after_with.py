"""Code after a `with` whose body never falls through is kept (#177)."""
import ast
import unittest

from .test_helpers import decompile

_CTX = ("class Ctx:\n    def __init__(self, log):\n        self.log = log\n"
        "    def __enter__(self):\n        return self\n"
        "    def __exit__(self, *a):\n        self.log.append('exit')\n        return True\n")


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestCodeAfterWith(unittest.TestCase):

    def assert_same_results(self, body, *calls):
        source = _CTX + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_raise_then_code(self):
        out = self.assert_same_results(
            "def f(log):\n    with Ctx(log):\n        raise ValueError(1)\n    log.append('after')\n    return log\n",
            "f([])")
        self.assertIn("log.append('after')", out)

    def test_raise_in_loop(self):
        self.assert_same_results(
            "def f(log):\n    for i in range(2):\n        with Ctx(log):\n            raise ValueError(i)\n"
            "        log.append('after')\n    return log\n", "f([])")

    def test_continue_and_raise_in_loop(self):
        self.assert_same_results(
            "def f(log, xs):\n    for x in xs:\n        with Ctx(log):\n            if x:\n                continue\n"
            "            raise ValueError(x)\n        log.append('after')\n    return log\n",
            "f([], [0, 1, 0])", "f([], [])")

    def test_code_after_is_after_the_with_not_inside(self):
        out = self.assert_same_results(
            "def f(log):\n    with Ctx(log):\n        raise ValueError(1)\n    log.append('after')\n    return log\n",
            "f([])")
        lines = out.splitlines()
        with_line = next(i for i, line in enumerate(lines) if line.strip().startswith("with Ctx") and "def f" in "\n".join(lines[:i]))
        after = next(line for line in lines[with_line:] if "'after'" in line)
        self.assertEqual(len(after) - len(after.lstrip()), len(lines[with_line]) - len(lines[with_line].lstrip()))

    def test_normal_with_unchanged(self):
        self.assert_same_results(
            "def f(log):\n    with Ctx(log):\n        log.append('body')\n    log.append('after')\n    return log\n",
            "f([])")


if __name__ == "__main__":
    unittest.main()
