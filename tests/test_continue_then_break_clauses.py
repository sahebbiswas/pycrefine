"""A `continue` clause followed by clauses that end otherwise leaves no stray `continue` between them (#345)."""
import ast
import unittest

from .test_helpers import decompile

SOURCE = (
    "def f(xs):\n    out = []\n    for x in xs:\n        try:\n            out.append(10 // x)\n"
    "        except ZeroDivisionError:\n            continue\n        except TypeError:\n"
    "            out.append('type')\n        except ValueError:\n            break\n    return out\n")


class _Bad:
    def __rfloordiv__(self, other):
        raise ValueError


CASES = ([], [1, 5], [0, 5], ["a", 5, 2], [5, _Bad(), 2], [0, "a", _Bad(), 3])


def _results(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return [namespace["f"](list(case)) for case in CASES]


class TestContinueThenBreakClauses(unittest.TestCase):

    def test_the_output_parses(self):
        ast.parse(decompile(SOURCE))

    def test_it_behaves_like_the_original(self):
        out = decompile(SOURCE)
        self.assertEqual(_results(out), _results(SOURCE), out)

    def test_a_single_continue_clause_is_unchanged(self):
        src = ("def f(xs):\n    out = []\n    for x in xs:\n        try:\n            out.append(10 // x)\n"
               "        except ZeroDivisionError:\n            continue\n    return out\n")
        out = decompile(src)
        ast.parse(out)
        namespace, original = {}, {}
        exec(compile(out, "<t>", "exec"), namespace)  # pylint: disable=exec-used
        exec(compile(src, "<t>", "exec"), original)  # pylint: disable=exec-used
        self.assertEqual(namespace["f"]([1, 0, 5]), original["f"]([1, 0, 5]))


if __name__ == "__main__":
    unittest.main()
