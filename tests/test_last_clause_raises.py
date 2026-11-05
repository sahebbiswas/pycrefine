"""An except clause that ends in `raise` (#344).

A clause that ends in `raise` has no POP_EXCEPT and no jump to the code after the statement, so on 3.10 and
3.11 nothing closed it and that code was written inside the clause, after the `raise`, where it never runs.
Each shape is decompiled and run against the original, on every Python the suite runs on.
"""
import ast
import unittest

from .test_helpers import decompile


def _outcome(function, args):
    try:
        return ("value", function(*args))
    except Exception as exc:  # pylint: disable=broad-except
        return ("raised", type(exc).__name__, exc.args)


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


# [1, 2] is valid at 0 and out of range at 5; the dict has the key "a"; None fails inside the body
CALLS = (([1, 2], 0), ([1, 2], 5), ({"a": 1}, "a"), ({"a": 1}, "b"), (None, 1))


class TestLastClauseRaises(unittest.TestCase):

    def assert_same(self, body):
        source = "def f(xs, stop):\n    out = []\n" + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for args in CALLS:
            self.assertEqual(_outcome(rebuilt["f"], args), _outcome(original["f"], args), f"{args}\n{out}")
        return out

    def test_the_reproduction(self):
        self.assert_same(
            "    try:\n        out.append(xs[stop])\n    except KeyError:\n        out.append('key')\n"
            "    except IndexError:\n        out.append('index')\n        raise\n    return out\n")

    def test_a_single_clause(self):
        self.assert_same(
            "    try:\n        out.append(xs[stop])\n    except IndexError:\n        out.append('index')\n        raise\n"
            "    return out\n")

    def test_a_raise_of_another_exception_from_the_bound_name(self):
        self.assert_same(
            "    try:\n        out.append(xs[stop])\n    except KeyError:\n        out.append('key')\n"
            "    except IndexError as err:\n        out.append('index')\n        raise ValueError('x') from err\n"
            "    out.append('after')\n    return out\n")

    def test_a_bare_last_clause(self):
        self.assert_same(
            "    try:\n        out.append(xs[stop])\n    except KeyError:\n        out.append('key')\n"
            "    except:\n        out.append('other')\n        raise\n    return out\n")

    def test_in_a_loop(self):
        self.assert_same(
            "    for i in range(2):\n        try:\n            out.append(xs[stop])\n        except KeyError:\n"
            "            out.append('key')\n        except IndexError:\n            out.append('index')\n"
            "            raise\n        out.append(i)\n    return out\n")

    def test_with_a_finally(self):
        self.assert_same(
            "    try:\n        out.append(xs[stop])\n    except KeyError:\n        out.append('key')\n"
            "    except IndexError:\n        out.append('index')\n        raise\n    finally:\n"
            "        out.append('fin')\n    return out\n")

    def test_with_an_else_clause(self):
        self.assert_same(
            "    try:\n        out.append(xs[stop])\n    except KeyError:\n        out.append('key')\n"
            "    except IndexError:\n        raise\n    else:\n        out.append('else')\n    return out\n")

    def test_a_raise_that_does_not_end_the_clause(self):
        self.assert_same(
            "    try:\n        out.append(xs[stop])\n    except IndexError:\n        if stop > 3:\n            raise\n"
            "        out.append('small')\n    return out\n")

    def test_a_clause_that_raises_before_another_clause(self):
        self.assert_same(
            "    try:\n        out.append(xs[stop])\n    except IndexError:\n        raise\n    except KeyError:\n"
            "        out.append('key')\n    return out\n")

    def test_a_bare_clause_that_ends_in_continue(self):
        # the scan for the end of a bare clause must stop at the backward jump, not at a later return
        head = ("    for i in range(3):\n        try:\n            out.append(xs[stop])\n")
        tail = "        out.append(i)\n    out.append('end')\n    return out\n"
        for clauses in (
                "        except KeyError:\n            return out\n        except IndexError:\n            return out\n",
                "        except KeyError:\n            out.append('key')\n            return out\n",
                "        except IndexError:\n            raise\n"):
            self.assert_same(head + clauses + "        except:\n            out.append('other')\n            continue\n" + tail)


if __name__ == "__main__":
    unittest.main()
