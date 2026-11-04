"""A chained comparison used as an `if` condition keeps its branches (#20).

On 3.12 and later a failing link jumps to a `POP_TOP` that drops the middle operand, and the code after the `if`
follows it (or is copied there when it is a short `return`). The `POP_TOP` was written as an expression statement,
and the `if` bodies, `elif` chain and `return` copies came out nested, duplicated or inverted.
"""
import ast
import itertools
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestChainedComparisonCondition(unittest.TestCase):

    def assert_same(self, source, name, arity, values):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source)[name], _namespace(out)[name]
        for args in itertools.product(values, repeat=arity):
            self.assertEqual(rebuilt(*args), original(*args), f"{name}{args}\n{out}")
        return out

    def test_elif_chain_after_a_chained_condition(self):
        out = self.assert_same(
            "def f(v):\n    if 3410 <= v <= 3429:\n        return 'a'\n    elif v >= 3560:\n        return 'b'\n"
            "    elif v >= 3430:\n        return 'c'\n    return 'd'\n", "f", 1, (0, 3410, 3429, 3430, 3560, 4000))
        self.assertIn("3410 <= v <= 3429", out)
        self.assertEqual(out.count("return"), 4, out)

    def test_return_in_the_body(self):
        out = self.assert_same("def f(a):\n    if 1 < a < 10:\n        return 1\n    return 2\n", "f", 1, (0, 1, 2, 9, 10, 11))
        self.assertEqual(out.count("return"), 2, out)

    def test_statement_before_the_return(self):
        self.assert_same("def f(a):\n    if 1 < a < 10:\n        a += 1\n        return a\n    return 2\n", "f", 1,
                         (0, 1, 2, 9, 10, 11))

    def test_three_comparisons(self):
        self.assert_same("def f(a, b, c, d):\n    if a < b < c < d:\n        return 1\n    return 2\n", "f", 4, (0, 1, 2, 3))

    def test_raise_in_the_body(self):
        def run(function, arg):
            try:
                return function(arg)
            except ValueError as exc:
                return ("raised", exc.args)
        source = "def f(a):\n    if 0 < a < 10:\n        raise ValueError(a)\n    return 2\n"
        out = decompile(source)
        original, rebuilt = _namespace(source)["f"], _namespace(out)["f"]
        for value in (-1, 0, 1, 9, 10):
            self.assertEqual(run(rebuilt, value), run(original, value), out)

    def test_chained_condition_inside_another_if(self):
        self.assert_same("def f(a, b):\n    if 0 < a < 10:\n        if 0 < b < 10:\n            return 1\n"
                         "        a += b\n    return a\n", "f", 2, (-1, 0, 5, 10))

    def test_identity_and_membership_links(self):
        values = ((), (1,), ((1,),), ((1,), (1,)))

        def outcome(function, args):
            try:
                return function(*args)
            except TypeError:
                return "TypeError"

        for test in ("a is b is c", "a < b in c", "a not in b in c", "a is not b is not c"):
            source = f"def f(a, b, c):\n    if {test}:\n        return 1\n    return 2\n"
            out = decompile(source)
            ast.parse(out)
            original, rebuilt = _namespace(source)["f"], _namespace(out)["f"]
            for args in itertools.product(values, repeat=3):
                self.assertEqual(outcome(rebuilt, args), outcome(original, args), f"{test} {args}\n{out}")
            self.assertEqual(out.count("return"), 2, out)
            self.assertNotIn("\n        b\n", out, out)

    def test_chained_condition_with_and(self):
        self.assert_same("def f(a, b):\n    if 0 < a < 10 and b:\n        return 1\n    return 2\n", "f", 2, (0, 1, 5, 10))


if __name__ == "__main__":
    unittest.main()
