"""A bare `except:` clause that follows typed `except` clauses (#342).

The clause before it jumps to the bare clause when its type does not match. A bare clause tests nothing, so
nothing named it: its code was written after the `try` (3.9, 3.10, 3.11) or as straight-line code after it
(3.12 and later), where it ran on every path.

Each shape is decompiled and run against the original, on every Python the suite runs on.
"""
import ast
import os
import unittest

from pycrefine import get_decompiler

from .test_helpers import _compile, decompile


def _outcome(function, args):
    try:
        return ("value", function(*args))
    except Exception as exc:  # pylint: disable=broad-except
        return ("raised", type(exc).__name__, exc.args)


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


# stop = 0 divides by zero, 1 and 2 index a list of two (1 is valid), None fails inside the body with a TypeError
CALLS = (([1, 2], 0), ([1, 2], 1), ([1, 2], 5), ({"a": 1}, 7), (None, 1), ([1, 2], "x"))


class TestBareExceptAfterTyped(unittest.TestCase):

    def assert_same(self, body, calls=CALLS, signature="xs, stop"):
        source = f"def f({signature}):\n" + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for args in calls:
            self.assertEqual(_outcome(rebuilt["f"], args), _outcome(original["f"], args), f"{args}\n{out}")
        return out

    def test_the_reproduction(self):
        out = self.assert_same(
            "    out = []\n    try:\n        out.append(10 // stop)\n        out.append(xs[stop])\n"
            "    except ZeroDivisionError as err:\n        out.append(type(err).__name__)\n"
            "    except (IndexError, KeyError):\n        out.append('index')\n    except:\n        out.append('other')\n"
            "    return out\n")
        self.assertIn("except:", out)

    def test_one_typed_clause_before_it(self):
        out = self.assert_same(
            "    out = []\n    try:\n        out.append(xs[stop])\n    except IndexError:\n        out.append('index')\n"
            "    except:\n        out.append('other')\n    return out\n")
        self.assertIn("except:", out)

    def test_three_typed_clauses_before_it(self):
        self.assert_same(
            "    out = []\n    try:\n        out.append(10 // stop)\n        out.append(xs[stop])\n"
            "    except ZeroDivisionError:\n        out.append('zero')\n    except IndexError:\n        out.append('index')\n"
            "    except KeyError:\n        out.append('key')\n    except:\n        out.append('other')\n    return out\n")

    def test_every_clause_returns(self):
        self.assert_same(
            "    try:\n        return xs[stop]\n    except IndexError:\n        return 'index'\n"
            "    except KeyError:\n        return 'key'\n    except:\n        return 'other'\n")

    def test_an_empty_bare_clause(self):
        self.assert_same(
            "    out = []\n    try:\n        out.append(xs[stop])\n    except IndexError:\n        out.append('index')\n"
            "    except:\n        pass\n    return out\n")

    def test_code_after_the_statement(self):
        self.assert_same(
            "    out = []\n    try:\n        out.append(xs[stop])\n    except IndexError:\n        out.append('index')\n"
            "    except:\n        out.append('other')\n    out.append('after')\n    return out\n")

    def test_with_finally(self):
        self.assert_same(
            "    out = []\n    try:\n        out.append(xs[stop])\n    except IndexError:\n        out.append('index')\n"
            "    except:\n        out.append('other')\n    finally:\n        out.append('finally')\n    return out\n")

    def test_in_a_while_loop(self):
        self.assert_same(
            "    out = []\n    n = 0\n    while n < 3:\n        n += 1\n        try:\n            out.append(xs[n])\n"
            "        except IndexError:\n            out.append('index')\n        except:\n            out.append('other')\n"
            "    return out\n")

    def test_typed_clauses_that_do_not_bind_a_name_are_unchanged(self):
        out = self.assert_same(
            "    out = []\n    try:\n        out.append(xs[stop])\n    except IndexError:\n        out.append('index')\n"
            "    except KeyError:\n        out.append('key')\n    return out\n")
        self.assertNotIn("except:", out)

    def test_a_clause_that_re_raises_ends_the_function(self):
        self.assert_same(
            "    try:\n        return xs[stop]\n    except IndexError:\n        return 'index'\n    except:\n        raise\n")

    def test_in_a_for_loop(self):
        self.assert_same(
            "    out = []\n    for x in xs:\n        try:\n            out.append(10 // x)\n"
            "        except ZeroDivisionError:\n            out.append('zero')\n        except:\n            out.append('other')\n"
            "    return out\n",
            calls=(([1, 2, 5], 0), ([1, 0, 5], 0), ([1, "a", 5], 0), ([None, 0], 0), ([], 0)))

    def test_after_an_empty_try(self):
        # `try: pass` cannot raise, so its clauses never run, but the bare clause's `return 2` must stay inside it
        self.assert_same(
            "    try:\n        pass\n    except IndexError:\n        return 1\n    except:\n        return 2\n    return 3\n")

    def test_only_a_clause_reached_by_a_failed_match_is_recorded(self):
        # `stop and out.append(1)` ends in a POP_TOP that the short-circuit jumps to; it opens no clause
        pyc = _compile("out = []\nstop = 0\nstop and out.append(1)\n")
        try:
            decompiler = get_decompiler(pyc)
            decompiler.decompile()
        finally:
            os.unlink(pyc)
        self.assertEqual(decompiler.prescan.bare_clause_entries, set())


if __name__ == "__main__":
    unittest.main()
