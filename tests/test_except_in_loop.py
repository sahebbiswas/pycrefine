"""A try with several except clauses at the end of a loop body (#208)."""
import ast
import contextlib
import io
import re
import sys
import unittest

from .test_helpers import decompile

HANDLERS = (
    "    try:\n        print(int(source))\n"
    "    except ValueError as exc:\n        print('bad', exc)\n"
    "    except IndexError:\n        print('incomplete')\n"
)


def _indent(text, by):
    return "".join(f"{' ' * by}{line}\n" for line in text.splitlines())


@unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 block-based try/except is not decompiled (#179)")
class TestSeveralExceptClausesEndingALoopBody(unittest.TestCase):

    def assert_valid_and_same_behavior(self, source, call=None):
        """The output parses, and prints and returns what the original does (`call` runs a function)."""
        out = decompile(source)
        ast.parse(out)   # the stray `continue` between the clauses was a syntax error
        results = []
        for code in (source, out):
            namespace = {}
            printed = io.StringIO()
            with contextlib.redirect_stdout(printed):
                exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
                value = eval(call, namespace) if call else None  # pylint: disable=eval-used
            results.append((printed.getvalue(), value))
        self.assertEqual(results[0], results[1], out)
        self.assertTrue(results[0][0] or results[0][1] is not None, "nothing is printed or returned to compare")
        return out

    def test_module_level_loop(self):
        src = "for source in ('1', 'x'):\n" + _indent(HANDLERS, 4)
        out = self.assert_valid_and_same_behavior(src)
        # a `continue` level with the next `except` would end the try early (the bug); one inside a clause is fine
        self.assertIsNone(re.search(r"\n( *)continue\n\1except", out), out)

    def test_loop_in_a_function(self):
        src = "def f(items):\n    for source in items:\n" + _indent(HANDLERS, 4)
        self.assert_valid_and_same_behavior(src, "f(['1', 'x'])")

    def test_the_clauses_stay_in_one_try(self):
        out = decompile("def f(items):\n    for source in items:\n" + _indent(HANDLERS, 4))
        tree = ast.parse(out)
        tries = [n for n in ast.walk(tree) if isinstance(n, ast.Try)]
        self.assertEqual(len(tries), 1)
        self.assertEqual(len(tries[0].handlers), 2)
        self.assertEqual(tries[0].handlers[0].name, "exc")

    def test_every_clause_binds_a_name(self):
        src = (
            "class Boom:\n    def __int__(self):\n        raise IndexError('boom')\n"
            "def f(items):\n    for s in items:\n        try:\n            print(int(s))\n"
            "        except ValueError as e:\n            print('bad', e)\n"
            "        except IndexError as g:\n            print('i', g)\n"
        )
        # '1' succeeds, 'x' runs the first clause and Boom() runs the second
        self.assert_valid_and_same_behavior(src, "f(['1', 'x', Boom()])")

    def test_three_clauses_and_code_after_the_loop(self):
        src = (
            "def f(items):\n    for s in items:\n        try:\n            print(int(s))\n"
            "        except ValueError as e:\n            print('bad', e)\n"
            "        except IndexError:\n            print('i')\n"
            "        except KeyError:\n            print('k')\n"
            "    return 1\n"
        )
        self.assert_valid_and_same_behavior(src, "f(['1', 'x'])")

    def test_behaviour_of_the_loop_is_unchanged(self):
        src = (
            "def run(items):\n    seen = []\n    for s in items:\n        try:\n            seen.append(int(s))\n"
            "        except ValueError as e:\n            seen.append('bad')\n"
            "        except TypeError:\n            seen.append('type')\n"
            "    return seen\n"
        )
        self.assert_valid_and_same_behavior(src, "run(['1', 'x', None, '2'])")


if __name__ == "__main__":
    unittest.main()
