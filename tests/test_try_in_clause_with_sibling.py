"""A try with several clauses inside an except clause that has a later sibling clause (#349)."""
import ast
import unittest

from .test_helpers import decompile

BARE = (
    "def f(xs):\n    out = []\n    try:\n        out.append(xs[0])\n    except IndexError:\n"
    "        try:\n            out.append(xs[5])\n        except KeyError:\n            out.append('k')\n"
    "        except:\n            out.append('b')\n        out.append('after')\n"
    "    except TypeError:\n        out.append('t')\n    return out\n")
TYPED = BARE.replace("        except:\n            out.append('b')\n",
                     "        except ValueError:\n            out.append('v')\n")
BARE_SIBLING = (
    "def f(xs):\n    out = []\n    try:\n        out.append(xs[0])\n    except IndexError:\n"
    "        try:\n            out.append(xs[5])\n        except KeyError:\n            out.append('k')\n"
    "        out.append('after')\n    except:\n        out.append('b')\n    return out\n")
CASES = ([], [0], ["a"], None, 5)


def _results(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    results = []
    for arg in CASES:
        try:
            results.append(namespace["f"](arg))
        except Exception as exc:  # pylint: disable=broad-except
            results.append(type(exc).__name__)
    return results


class TestTryInClauseWithSibling(unittest.TestCase):

    def test_bare_inner_clause(self):
        out = decompile(BARE)
        ast.parse(out)
        self.assertEqual(_results(out), _results(BARE), out)

    def test_typed_inner_clauses(self):
        out = decompile(TYPED)
        ast.parse(out)
        self.assertEqual(_results(out), _results(TYPED), out)

    def test_a_bare_sibling_clause_after_the_inner_statement(self):
        out = decompile(BARE_SIBLING)
        ast.parse(out)
        self.assertEqual(_results(out), _results(BARE_SIBLING), out)
        lines = out.splitlines()
        after = next(ln for ln in lines if "'after'" in ln)
        sibling = next(ln for ln in lines if ln.strip() == "except:")
        self.assertGreater(len(after) - len(after.lstrip()), len(sibling) - len(sibling.lstrip()), out)

    def test_the_code_after_the_inner_statement_stays_in_the_clause(self):
        out = decompile(BARE)
        lines = out.splitlines()
        after = next(ln for ln in lines if "'after'" in ln)
        sibling = next(ln for ln in lines if ln.strip() == "except TypeError:")
        self.assertGreater(len(after) - len(after.lstrip()), len(sibling) - len(sibling.lstrip()), out)


if __name__ == "__main__":
    unittest.main()
