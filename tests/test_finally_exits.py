"""`finally` blocks whose paths all leave the `try`, and `continue` in an `except` with a `finally` (#171, #141, #187)."""
import ast
import sys
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestFinallyExits(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    @unittest.skipIf(sys.version_info < (3, 11), "3.10 try/except is tracked separately")
    def test_every_path_returns(self):
        """#171: the finally copies at the returns are not source; the finally block stays."""
        src = ("def h(f, log):\n    try:\n        f()\n    except ValueError:\n        return 'bad'\n"
               "    finally:\n        log.append('fin')\n    return 'end'\n"
               "def boom():\n    raise ValueError\n"
               "def other():\n    raise KeyError\n"
               "def run(f):\n    log = []\n    try:\n        r = h(f, log)\n    except KeyError:\n        r = 'key'\n"
               "    return r, log\n")
        out = self.assert_same_results(src, "run(lambda: 0)", "run(boom)", "run(other)")
        self.assertEqual(out.count("log.append('fin')"), 1, out)

    @unittest.skipIf(sys.version_info < (3, 11), "3.10 try/except is tracked separately")
    def test_body_returns(self):
        src = ("def h(f, log):\n    try:\n        return f()\n    except ValueError:\n        return 'bad'\n"
               "    finally:\n        log.append('fin')\n"
               "def boom():\n    raise ValueError\n"
               "def run(f):\n    log = []\n    return h(f, log), log\n")
        out = self.assert_same_results(src, "run(lambda: 1)", "run(boom)")
        self.assertEqual(out.count("log.append('fin')"), 1, out)

    @unittest.skipIf(sys.version_info < (3, 9) or sys.version_info[:2] == (3, 10),
                     "3.10 try/except is tracked separately")
    def test_continue_in_except_with_finally(self):
        """#187: the `continue` stays inside the except clause, before the finally."""
        src = ("def h(items, out):\n    for item in items:\n        try:\n            out.append(int(item))\n"
               "        except ValueError:\n            out.append(None)\n            continue\n"
               "        finally:\n            out.append('|')\n    return out\n")
        self.assert_same_results(src, "h(['1', 'x', '3'], [])")

    @unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 try/except is tracked separately")
    def test_empty_finally_in_a_loop(self):
        """#141: `finally: pass` in a loop is still a finally."""
        src = ("def h(xs, log):\n    for i in xs:\n        try:\n            log.append(i)\n"
               "        finally:\n            pass\n    return 1\n")
        out = self.assert_same_results(src, "(lambda l: (h([1, 2], l), l))([])")
        self.assertIn("finally:", out)


if __name__ == "__main__":
    unittest.main()
