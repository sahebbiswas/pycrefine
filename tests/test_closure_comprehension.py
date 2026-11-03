"""An inlined comprehension (3.12+) whose loop variable a closure captures is rebuilt as a comprehension (#99).

The variable is a cell, so the save and restore of the name is `LOAD_FAST_AND_CLEAR x; MAKE_CELL x` and the
loop stores it with `STORE_DEREF`. The comprehension was left as an explicit loop that never set up its result.
"""
import ast
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestClosureComprehension(unittest.TestCase):

    def assert_same(self, source, call, show):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        self.assertEqual(show(eval(call, rebuilt)), show(eval(call, original)), out)  # pylint: disable=eval-used
        return out

    def test_list_of_lambdas(self):
        out = self.assert_same("def z(xs):\n    return [lambda: x for x in xs]\n", "z([1, 2, 3])",
                               lambda fns: [f() for f in fns])
        self.assertIn("for x in xs", out)
        self.assertNotIn("_res", out)

    def test_two_captured_variables(self):
        out = self.assert_same("def z(xs, ys):\n    return [lambda: x + y for x in xs for y in ys]\n",
                               "z([1, 2], [10, 20])", lambda fns: [f() for f in fns])
        self.assertNotIn("_res", out)

    def test_dict_with_a_filter(self):
        self.assert_same("def z(xs):\n    return {x: (lambda: x * 2) for x in xs if x}\n", "z([0, 1, 2])",
                         lambda d: {k: f() for k, f in d.items()})

    def test_the_name_is_restored(self):
        self.assert_same("def z(xs):\n    x = 'kept'\n    out = [lambda: x for x in xs]\n    return x, len(out)\n",
                         "z([1, 2])", lambda value: value)


if __name__ == "__main__":
    unittest.main()
