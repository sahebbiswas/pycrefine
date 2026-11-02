"""An except clause that ends in an explicit `return None` leaves the code after its loop alone (#293).

On 3.12 and 3.13 the compiler lays the code after a loop out ahead of the handlers of a `try` in
the loop body. A clause that ends in `return None` was not recognised as one that leaves, because
`return None` is also how a function ends, so that code was written inside the `try` body.
"""
import ast
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestExplicitReturnNoneInHandler(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_code_after_a_while_loop(self):
        """The reproduction in the issue."""
        src = ("def f(n, o):\n    while n > 0:\n        n -= 1\n        try:\n"
               "            o.append(int('x') if n == 1 else n)\n        except ValueError:\n"
               "            return None\n    o.append('end')\n    return o\n")
        out = self.assert_same_results(src, "f(0, [])", "f(3, [])", "f(1, [])")
        self.assertRegex(out, r"except ValueError:\n\s+return None\n")
        self.assertIn("\n    o.append('end')\n    return o", out)   # (after the loop, not in the `try`)

    def test_return_none_behaves_like_return_zero(self):
        for value in ("None", "0"):
            src = ("def g(n, o):\n    while n > 0:\n        n -= 1\n        try:\n"
                   "            o.append(int('x') if n == 1 else n)\n        except ValueError:\n"
                   f"            return {value}\n    o.append('end')\n    return o\n")
            self.assert_same_results(src, "g(0, [])", "g(3, [])")

    def test_bare_return(self):
        src = ("def i(n, o):\n    while n > 0:\n        n -= 1\n        try:\n"
               "            o.append(int('x') if n == 1 else n)\n        except ValueError:\n"
               "            return\n    o.append('end')\n    return o\n")
        self.assert_same_results(src, "i(0, [])", "i(3, [])")

    def test_implicit_return_after_the_loop(self):
        src = ("def a(n, o):\n    while n > 0:\n        n -= 1\n        try:\n"
               "            o.append(int('x') if n == 1 else n)\n        except ValueError:\n"
               "            return None\n    return None\n")
        self.assert_same_results(src, "a(0, [])", "a(3, [])")

    def test_for_loop(self):
        src = ("def b(items):\n    out = []\n    for x in items:\n        try:\n            out.append(int(x))\n"
               "        except ValueError:\n            return None\n    out.append('end')\n    return out\n")
        self.assert_same_results(src, "b(['1', '2'])", "b(['1', 'x', '3'])", "b([])")

    def test_two_handlers_and_a_statement_before_the_return(self):
        src = ("def e(items, log):\n    out = []\n    for x in items:\n        try:\n            out.append(int(x))\n"
               "        except ValueError:\n            log.append(x)\n            return None\n"
               "        except TypeError:\n            return None\n    out.append('end')\n    return out\n")
        self.assert_same_results(src, "e(['1'], [])", "e(['1', 'x'], [])", "e([1, None], [])")

    def test_while_loop_with_an_else_clause(self):
        src = ("def g(n, o):\n    while n > 0:\n        n -= 1\n        try:\n"
               "            o.append(int('x') if n == 1 else n)\n        except ValueError:\n"
               "            return None\n    else:\n        o.append('else')\n    o.append('end')\n    return o\n")
        self.assert_same_results(src, "g(0, [])", "g(3, [])")

    def test_a_handler_that_passes_rejoins_the_loop(self):
        src = ("def h(n, o):\n    while n > 0:\n        n -= 1\n        try:\n"
               "            o.append(int('x') if n == 1 else n)\n        except ValueError:\n"
               "            pass\n    o.append('end')\n    return o\n")
        out = self.assert_same_results(src, "h(0, [])", "h(3, [])")
        self.assertNotIn("return None", out)

    def test_try_at_the_end_of_a_function(self):
        """The function's own end is copied into the clause, whether the source says `return None` or `pass`."""
        src = ("def c(item, log):\n    try:\n        log.append(int(item))\n    except ValueError:\n        return None\n"
               "def d(item, log):\n    try:\n        log.append(int(item))\n    except ValueError:\n        pass\n")
        self.assert_same_results(src, "(c('1', []), c('x', []))", "(d('1', []), d('x', []))")


if __name__ == "__main__":
    unittest.main()
