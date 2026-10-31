"""`break` in an except clause or an elif branch of a loop keeps the loop running (#207)."""
import ast
import sys
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class LoopBreakCase(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out


class TestBreakInAnElifBranch(LoopBreakCase):

    SRC = (
        "class M:\n"
        "    def send(self, event):\n        return event != 'skip' and event != 'halt'\n"
        "    def run(self, events):\n"
        "        accepted = 0\n"
        "        for event in events:\n"
        "            if self.send(event):\n                accepted += 1\n"
        "            elif event == 'halt':\n                break\n"
        "        return accepted\n"
    )

    def test_the_loop_runs_until_the_break(self):
        self.assert_same_results(
            self.SRC, "M().run(['a', 'b', 'skip', 'c'])", "M().run(['a', 'halt', 'b'])",
            "M().run(['skip', 'halt', 'a'])", "M().run([])")

    def test_break_in_an_elif_with_an_else(self):
        src = (
            "def g(xs):\n    seen = []\n    for x in xs:\n"
            "        if x == 1:\n            seen.append('one')\n"
            "        elif x == 2:\n            break\n"
            "        else:\n            seen.append(x)\n"
            "    return seen\n"
        )
        self.assert_same_results(src, "g([1, 3, 2, 4])", "g([3, 4])", "g([])")

    def test_code_after_the_loop_runs_after_a_break(self):
        src = (
            "def h(xs):\n    n = 0\n    for x in xs:\n"
            "        if x > 5:\n            n += 1\n"
            "        elif x < 0:\n            break\n"
            "    n += 100\n    return n\n"
        )
        self.assert_same_results(src, "h([6, 1, -1, 7])", "h([6, 7])")


@unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 block-based try/except is not decompiled (#179)")
class TestBreakInAnExceptClause(LoopBreakCase):

    SRC = (
        "def f(xs):\n    out = []\n    for x in xs:\n"
        "        try:\n            if x % 3 == 0:\n                raise ValueError(x)\n"
        "            out.append(x)\n"
        "        except ValueError:\n            break\n"
        "    return out\n"
    )

    def test_the_loop_stops_at_the_exception(self):
        self.assert_same_results(self.SRC, "f([1, 2, 3, 4])", "f([1, 2])", "f([0, 1])", "f([])")

    def test_break_stays_inside_the_clause(self):
        out = decompile(self.SRC)
        handler = next(n for n in ast.walk(ast.parse(out)) if isinstance(n, ast.ExceptHandler))
        self.assertTrue(any(isinstance(n, (ast.Break, ast.Return)) for n in ast.walk(handler)), out)

    def test_break_with_an_as_name(self):
        src = (
            "def f(xs):\n    out = []\n    for x in xs:\n"
            "        try:\n            out.append(10 // x)\n"
            "        except ZeroDivisionError as exc:\n            out.append(str(exc))\n            break\n"
            "    out.append('end')\n    return out\n"
        )
        self.assert_same_results(src, "f([1, 0, 5])", "f([1, 2])")

    def test_break_in_one_of_several_clauses(self):
        src = (
            "def f(xs):\n    out = []\n    for x in xs:\n"
            "        try:\n            out.append(10 // x)\n"
            "        except ZeroDivisionError:\n            break\n"
            "        except TypeError:\n            out.append('t')\n"
            "    out.append('end')\n    return out\n"
        )
        self.assert_same_results(src, "f([1, None, 0, 5])", "f([1, 2])", "f([None])")


if __name__ == "__main__":
    unittest.main()
