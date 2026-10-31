"""`with` statements in an except clause, and the code after them (#247)."""
import ast
import sys
import unittest

from .test_helpers import decompile

_CM = "import contextlib\ncm = contextlib.nullcontext\n"


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


@unittest.skipIf(sys.version_info < (3, 11), "3.9 and 3.10 try/except is tracked separately")
class TestWithInExcept(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_two_withs_then_statement(self):
        src = _CM + ("def f(o):\n    try:\n        raise KeyError\n    except KeyError:\n        with cm():\n"
                     "            pass\n        with cm():\n            o.append('two')\n        x = 5\n"
                     "    return x, o\n")
        out = self.assert_same_results(src, "f([])")
        self.assertEqual(out.count("with cm()"), 2)

    def test_with_then_statement(self):
        src = _CM + ("def f(o):\n    try:\n        raise KeyError\n    except KeyError:\n        with cm():\n"
                     "            o.append('in')\n        o.append('post')\n    return o\n")
        self.assert_same_results(src, "f([])")

    def test_with_as_name_in_loop(self):
        src = _CM + ("def f(o):\n    for i in range(3):\n        try:\n            if i == 1:\n"
                     "                raise ValueError\n            o.append(('ok', i))\n        except ValueError:\n"
                     "            with cm(i) as v:\n                o.append(('in', v))\n"
                     "            o.append(('post', i))\n        o.append(('tail', i))\n    return o\n")
        out = self.assert_same_results(src, "f([])")
        self.assertNotIn("None(None", out)

    def test_with_in_handler_in_loop_without_tail(self):
        src = _CM + ("def f(o):\n    for i in range(3):\n        try:\n            if i == 1:\n"
                     "                raise ValueError\n            o.append(('ok', i))\n        except ValueError:\n"
                     "            with cm():\n                o.append('in')\n            o.append('post')\n    return o\n")
        self.assert_same_results(src, "f([])")

    def test_with_in_handler_returns(self):
        src = _CM + ("def f(g):\n    try:\n        g()\n    except KeyError:\n        with cm():\n"
                     "            pass\n        return 'handled'\n    return 'fine'\n"
                     "def k():\n    raise KeyError\n")
        self.assert_same_results(src, "f(k)", "f(lambda: 0)")


if __name__ == "__main__":
    unittest.main()
