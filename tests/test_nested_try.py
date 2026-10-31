"""A try whose body is a try/except keeps its own finally (#253) and except clauses (#169)."""
import ast
import sys
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class _Base(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out


@unittest.skipIf(sys.version_info < (3, 11), "3.9 and 3.10 try/except is tracked separately")
class TestNestedTryFinally(_Base):

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


@unittest.skipIf(sys.version_info < (3, 11), "3.9 and 3.10 try/except is tracked separately")
class TestNestedTryExcept(_Base):
    """The outer except clauses of a try whose body is another try (#169)."""

    def test_returning_handlers(self):
        src = ("def d(f):\n    try:\n        try:\n            f()\n        except KeyError:\n"
               "            return 'inner'\n    except ValueError:\n        return 'outer'\n    return 'none'\n"
               "def k():\n    raise KeyError\n"
               "def v():\n    raise ValueError\n")
        out = self.assert_same_results(src, "d(lambda: 0)", "d(k)", "d(v)")
        self.assertLess(out.index("except KeyError"), out.index("except ValueError"))
        self.assertEqual(out.count("try:"), 2)

    def test_falling_through_handlers(self):
        src = ("def d(f, o):\n    try:\n        try:\n            f()\n        except KeyError:\n"
               "            o.append('k')\n    except ValueError:\n        o.append('v')\n    o.append('end')\n    return o\n"
               "def k():\n    raise KeyError\n"
               "def v():\n    raise ValueError\n")
        self.assert_same_results(src, "d(lambda: 0, [])", "d(k, [])", "d(v, [])")

    def test_outer_catches_error_from_inner_handler(self):
        src = ("def d(f, o):\n    try:\n        try:\n            f()\n        except KeyError:\n"
               "            raise ValueError\n    except ValueError:\n        o.append('outer')\n    return o\n"
               "def k():\n    raise KeyError\n")
        self.assert_same_results(src, "d(k, [])", "d(lambda: 0, [])")

    def test_two_outer_handlers(self):
        src = ("def d(f, o):\n    try:\n        try:\n            f()\n        except KeyError:\n"
               "            o.append('k')\n    except ValueError:\n        o.append('v')\n"
               "    except TypeError:\n        o.append('t')\n    return o\n"
               "def t():\n    raise TypeError\n"
               "def v():\n    raise ValueError\n")
        self.assert_same_results(src, "d(t, [])", "d(v, [])", "d(lambda: 0, [])")


if __name__ == "__main__":
    unittest.main()
