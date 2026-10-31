"""A try/except that starts the body of another try keeps its own handlers (#253)."""
import ast
import sys
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


@unittest.skipIf(sys.version_info < (3, 11), "3.9 and 3.10 try/except is tracked separately")
class TestNestedTryFinally(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_except_inside_finally_body_end(self):
        src = ("def g(f, o):\n    try:\n        try:\n            f()\n        except ValueError:\n"
               "            o.append('h')\n    finally:\n        o.append('fin')\n    o.append('tail')\n    return o\n"
               "def bad():\n    raise ValueError\n")
        out = self.assert_same_results(src, "g(lambda: 0, [])", "g(bad, [])")
        self.assertEqual(out.count("try:"), 2)

    def test_two_handlers(self):
        src = ("def g(f, o):\n    try:\n        try:\n            f()\n        except ValueError:\n"
               "            o.append('v')\n        except KeyError:\n            o.append('k')\n"
               "    finally:\n        o.append('fin')\n    return o\n"
               "def v():\n    raise ValueError\n"
               "def k():\n    raise KeyError\n")
        self.assert_same_results(src, "g(lambda: 0, [])", "g(v, [])", "g(k, [])")

    def test_unhandled_error_runs_finally(self):
        src = ("def g(f, o):\n    try:\n        try:\n            f()\n        except ValueError:\n"
               "            o.append('v')\n    finally:\n        o.append('fin')\n    return o\n"
               "def run(f):\n    o = []\n    try:\n        g(f, o)\n    except KeyError:\n        o.append('esc')\n    return o\n"
               "def k():\n    raise KeyError\n")
        self.assert_same_results(src, "run(k)", "run(lambda: 0)")

    def test_in_a_loop(self):
        src = ("def g(items, o):\n    for x in items:\n        try:\n            try:\n                if x == 1:\n"
               "                    raise ValueError\n                o.append(x)\n            except ValueError:\n"
               "                o.append('h')\n        finally:\n            o.append('f')\n    return o\n")
        self.assert_same_results(src, "g([0, 1, 2], [])")


if __name__ == "__main__":
    unittest.main()
