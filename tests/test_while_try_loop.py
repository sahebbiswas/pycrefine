"""A `while` loop whose `try` body leaves the loop keeps the loop (#170)."""
import ast
import sys
import unittest

from .test_helpers import decompile

_BOOM = ("def boom(v):\n    if v != 'ok':\n        raise KeyError(v)\n    return v\n"
         "def flaky(v):\n    if v % 2:\n        raise KeyError(v)\n    return v\n")


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


@unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 try/except is tracked separately")
class TestWhileTryLoop(unittest.TestCase):

    def assert_same_results(self, body, *calls):
        source = _BOOM + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_while_true_try_return(self):
        """The loop was lost: the handler's jump back is the only way to the loop head."""
        out = self.assert_same_results(
            "def f(v):\n    while True:\n        try:\n            return boom(v)\n"
            "        except KeyError:\n            v = 'ok'\n", "f('x')", "f('ok')")
        self.assertIn("while True:", out)

    def test_while_true_statement_then_try_return(self):
        out = self.assert_same_results(
            "def f(v, o):\n    while True:\n        o.append(1)\n        try:\n            return boom(v)\n"
            "        except KeyError:\n            v = 'ok'\n",
            "(lambda o: (f('x', o), o))([])", "(lambda o: (f('ok', o), o))([])")
        self.assertIn("while True:", out)

    def test_while_true_try_break(self):
        out = self.assert_same_results(
            "def f(v, o):\n    while True:\n        try:\n            o.append(boom(v))\n            break\n"
            "        except KeyError:\n            v = 'ok'\n    return o\n", "f('x', [])", "f('ok', [])")
        self.assertIn("while True:", out)

    def test_while_cond_try_return(self):
        """The loop became an `if` and the return after it ended up in the try body."""
        out = self.assert_same_results(
            "def f(v):\n    n = 0\n    while n < 3:\n        n += 1\n        try:\n            return boom(v)\n"
            "        except KeyError:\n            v = 'ok' if n > 1 else v\n    return 'gave up'\n",
            "f('x')", "f('ok')")
        self.assertIn("while n < 3:", out)
        self.assertEqual(out.count("'gave up'"), 1)

    def test_while_cond_try_falls_through(self):
        out = self.assert_same_results(
            "def f(v, o):\n    while v < 3:\n        v += 1\n        try:\n            o.append(flaky(v))\n"
            "        except KeyError:\n            v += 1\n    return o\n",
            "f(0, [])", "f(5, [])")
        self.assertIn("while v < 3:", out)

    def test_while_cond_try_long_return_after_loop(self):
        """A `return` after the loop with many operands is still written after it, not in the loop."""
        out = self.assert_same_results(
            "def f(v, o):\n    n = 0\n    while n < 2:\n        n += 1\n        try:\n            return boom(v)\n"
            "        except KeyError:\n            v = 'ok' if n == 2 else v\n"
            "    return (o, n, v, len(o), n + 1, v, o, n, 'a', 'b', 'c', 'd')\n", "f('x', [])", "f('ok', [])")
        self.assertEqual(out.count("'a', 'b', 'c', 'd'"), 1)

    def test_while_conditional_expression_condition_try_return(self):
        """The first conditional jump of the loop head belongs to the condition, not to the exit."""
        out = self.assert_same_results(
            "def f(n, m):\n    while (n if m else 0) > 0:\n        n -= 1\n        try:\n            return boom(n)\n"
            "        except KeyError:\n            pass\n    return 'end'\n", "f(0, True)", "f(0, False)")
        lines = out.rstrip().splitlines()
        self.assertEqual(lines[-1], "    return 'end'", out)   # (after the loop, not in the try body)
        self.assertEqual(out.count("'end'"), 1)

    def test_while_cond_try_return_statement_after(self):
        """The return after the loop is not repeated inside it."""
        out = self.assert_same_results(
            "def f(v, o):\n    n = 0\n    while n < 2:\n        n += 1\n        o.append(n)\n        try:\n"
            "            return boom(v)\n        except KeyError:\n            v = 'ok' if n == 2 else v\n"
            "    o.append('end')\n    return o\n", "f('x', [])", "f('ok', [])")
        self.assertEqual(out.count("'end'"), 1)

    def test_try_outside_a_loop_is_unchanged(self):
        out = self.assert_same_results(
            "def f(v):\n    try:\n        return boom(v)\n    except KeyError:\n        return 'bad'\n",
            "f('x')", "f('ok')")
        self.assertNotIn("while", out)

    def test_for_loop_with_try_continue(self):
        self.assert_same_results(
            "def f(items, o):\n    for v in items:\n        try:\n            o.append(boom(v))\n"
            "        except KeyError:\n            continue\n    return o\n", "f(['x', 'ok'], [])")
