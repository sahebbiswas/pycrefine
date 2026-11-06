"""A `finally` body stays whole when an except clause holds a `with` (#367)."""
import ast
import contextlib
import sys
import unittest

from .test_helpers import decompile

SOURCE = (
    "def f(p, log, fail):\n"
    "    try:\n"
    "        log.append(1)\n"
    "        if fail:\n"
    "            raise ValueError\n"
    "    except ValueError:\n"
    "        with p:\n"
    "            log.append(2)\n"
    "    finally:\n"
    "        log.append(3)\n"
    "        log.append(4)\n"
    "    return log\n")


def _run(code, fail):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace["f"](contextlib.nullcontext(), [], fail)


@unittest.skipIf(sys.version_info < (3, 11), "3.9/3.10 lay out try/finally differently")
class TestFinallyWithInExcept(unittest.TestCase):

    def test_the_whole_finally_body_is_inside_the_clause(self):
        out = decompile(SOURCE)
        ast.parse(out)
        self.assertEqual(out.count("log.append(3)"), 1, out)
        self.assertEqual(out.count("log.append(4)"), 1, out)
        finally_at = out.index("finally:")
        self.assertLess(finally_at, out.index("log.append(3)"), out)
        self.assertLess(out.index("log.append(3)"), out.index("log.append(4)"), out)
        self.assertLess(out.index("log.append(4)"), out.index("return log"), out)

    def test_it_behaves_like_the_original(self):
        out = decompile(SOURCE)
        for fail in (False, True):
            self.assertEqual(_run(out, fail), _run(SOURCE, fail), out)

    def test_a_single_statement_finally_is_kept(self):
        out = decompile(
            "def f(p, log):\n    try:\n        log.append(1)\n    except ValueError:\n        with p:\n"
            "            log.append(2)\n    finally:\n        log.append(3)\n    return log\n")
        self.assertIn("finally:", out)
        self.assertEqual(out.count("log.append(3)"), 1, out)

    def test_a_nested_try_finally_in_the_clause_does_not_cut_the_outer_finally_short(self):
        """The nested statement itself is still rebuilt wrongly (#370); the outer body stays whole and once."""
        out = decompile(
            "def f(log, fail):\n    try:\n        if fail:\n            raise ValueError\n"
            "    except ValueError:\n        try:\n            log.append('inner')\n"
            "        finally:\n            log.append('nested')\n"
            "    finally:\n        log.append('outer')\n        log.append('after')\n    return log\n")
        self.assertEqual(out.count("log.append('outer')"), 1, out)
        self.assertEqual(out.count("log.append('after')"), 1, out)
        self.assertLess(out.index("log.append('outer')"), out.index("log.append('after')"), out)

    def test_a_with_in_the_clause_and_a_return_in_the_finally_path(self):
        src = ("def f(p, log):\n    try:\n        log.append(1)\n    except ValueError:\n        with p:\n"
               "            log.append(2)\n    finally:\n        log.append(3)\n        log.append(4)\n")
        out = decompile(src)
        ast.parse(out)
        self.assertEqual(out.count("log.append(4)"), 1, out)


if __name__ == "__main__":
    unittest.main()
