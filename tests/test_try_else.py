"""The `else` clause of a `try` statement stays out of the protected body (#158, #162)."""
import ast
import sys
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


@unittest.skipIf(sys.version_info < (3, 11), "3.9 and 3.10 try/except is tracked separately")
class TestTryElse(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_error_in_else_is_not_caught(self):
        """An exception raised in the `else` clause must not reach the handler."""
        src = ("def t(f, g):\n    try:\n        f()\n    except KeyError:\n        return 'caught'\n"
               "    else:\n        g()\n    return 'done'\n"
               "def boom():\n    raise KeyError('x')\n"
               "def ok():\n    return None\n"
               "def run(f, g):\n    try:\n        return t(f, g)\n    except KeyError:\n        return 'escaped'\n")
        out = self.assert_same_results(src, "run(ok, ok)", "run(boom, ok)", "run(ok, boom)")
        if sys.version_info >= (3, 11):
            self.assertIn("else:", out)

    def test_else_then_more_code(self):
        src = ("def t(f, g, o):\n    try:\n        f()\n    except KeyError:\n        o.append('h')\n"
               "    else:\n        g()\n        o.append('e')\n    o.append('after')\n    return o\n"
               "def boom():\n    raise KeyError\n"
               "def ok():\n    pass\n")
        self.assert_same_results(src, "t(ok, ok, [])", "t(boom, ok, [])")

    def test_else_returns(self):
        src = ("def t(f, g):\n    try:\n        f()\n    except KeyError:\n        pass\n"
               "    else:\n        return g()\n    return 5\n"
               "def boom():\n    raise KeyError\n")
        self.assert_same_results(src, "t(lambda: 0, lambda: 7)", "t(boom, lambda: 7)")

    def test_store_in_body_then_else(self):
        src = ("def t(f, g):\n    try:\n        a = f()\n    except KeyError:\n        a = 0\n"
               "    else:\n        g(a)\n    return a\n"
               "def boom():\n    raise KeyError\n")
        self.assert_same_results(src, "t(lambda: 3, lambda a: None)", "t(boom, lambda a: None)")

    def test_two_handlers_and_finally(self):
        src = ("def t(f, g, o):\n    try:\n        f()\n    except KeyError:\n        o.append('k')\n"
               "    except ValueError:\n        o.append('v')\n    else:\n        g()\n"
               "        o.append('e')\n    finally:\n        o.append('f')\n    return o\n"
               "def k():\n    raise KeyError\n"
               "def v():\n    raise ValueError\n"
               "def ok():\n    pass\n")
        self.assert_same_results(src, "t(k, ok, [])", "t(v, ok, [])", "t(ok, ok, [])")

    def test_else_in_loop(self):
        src = ("def t(xs, o):\n    for x in xs:\n        try:\n            int(x)\n        except ValueError:\n"
               "            o.append('bad')\n            continue\n        else:\n            o.append(x)\n    return o\n")
        self.assert_same_results(src, "t(['1', 'a', '2'], [])")

    def test_try_without_else_is_unchanged(self):
        src = ("def t(f):\n    try:\n        f()\n    except KeyError:\n        return 1\n    return 2\n"
               "def boom():\n    raise KeyError\n")
        self.assert_same_results(src, "t(lambda: 0)", "t(boom)")


if __name__ == "__main__":
    unittest.main()
