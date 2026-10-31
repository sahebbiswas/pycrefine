"""Chained assignments evaluate the value once, whatever the targets are (#254)."""
import ast
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestChainedAssignment(unittest.TestCase):

    def assert_same_ast(self, src):
        out = decompile(src)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(src)), out)
        return out

    def assert_evaluates_once(self, src, call, expected_calls=1):
        """Both sources run `call`, and the value is computed as many times as originally."""
        results = []
        for code in (src, decompile(src)):
            namespace = _namespace(code)
            namespace["calls"] = []
            results.append((eval(call, namespace), len(namespace["calls"])))  # pylint: disable=eval-used
        self.assertEqual(results[0][1], expected_calls)
        self.assertEqual(results[1], results[0], decompile(src))

    HEADER = "calls = []\ndef make():\n    calls.append(1)\n    return [7, 8]\n"

    def test_names(self):
        self.assert_same_ast("def f():\n    x = y = make()\n    return x\n")
        self.assert_same_ast("def f():\n    x = y = z = make()\n    return x\n")
        self.assert_same_ast("x = y = make()\n")

    def test_constants_and_names_chain_too(self):
        self.assert_same_ast("def f(v):\n    a = b = 0\n    c = d = v\n    return a, b, c, d\n")

    def test_attributes(self):
        self.assert_same_ast("def f(o, p):\n    o.x = p.y = make()\n")
        self.assert_same_ast("def f(o):\n    x = o.a = make()\n    return x\n")

    def test_subscripts(self):
        self.assert_same_ast("def f(a, i):\n    a[i] = a[i + 1] = make()\n")

    def test_slices(self):
        """The case of the issue."""
        self.assert_same_ast("def f(a):\n    a[1:3] = a[3:5] = make()\n")

    def test_mixed_targets(self):
        self.assert_same_ast("def f(o, a):\n    x = o.p = a[0] = a[1:2] = make()\n    return x\n")

    def test_the_issue_program_calls_make_once(self):
        src = self.HEADER + (
            "def run():\n"
            "    a = list(range(6))\n"
            "    a[1:3] = a[3:5] = make()\n"
            "    x = y = make()\n"
            "    return a, x, y\n"
        )
        self.assert_evaluates_once(src, "run()", expected_calls=2)

    def test_the_value_is_shared_between_the_targets(self):
        src = self.HEADER + "def run():\n    x = y = make()\n    return x is y\n"
        self.assert_evaluates_once(src, "run()")

    def test_unpacking_target_with_a_chained_name(self):
        src = self.HEADER + "def run():\n    a, b = c = make()\n    return a, b, c\n"
        self.assert_evaluates_once(src, "run()")

    def test_unpacking_with_a_target_that_is_an_attribute(self):
        src = self.HEADER + (
            "class O:\n    pass\n"
            "def run():\n    o = O()\n    a, o.b = c = make()\n    return a, o.b, c\n"
        )
        self.assert_evaluates_once(src, "run()")

    def test_nested_and_starred_unpacking(self):
        src = self.HEADER + (
            "def run():\n    a, *b = c = make()\n    return a, b, c\n"
            "def run2():\n    (a, b), c = d = [(1, 2), 3]\n    calls.append(1)\n    return a, b, c, d\n"
        )
        self.assert_evaluates_once(src, "run()")
        self.assert_evaluates_once(src, "run2()")

    def test_unpacking_after_a_chained_name_evaluates_once(self):
        """`c = (o.b, b) = make()`: the value is named in the first statement, not computed again."""
        src = self.HEADER + (
            "class O:\n    pass\n"
            "def run():\n    o = O()\n    c = (o.b, b) = make()\n    return c, o.b, b\n"
        )
        self.assert_evaluates_once(src, "run()")

    def test_walrus_in_a_later_target(self):
        src = self.HEADER + (
            "def g():\n    calls.append(2)\n    return 0\n"
            "def run():\n    b = [0]\n    a = b[(k := g())] = make()\n    return a, b, k\n"
        )
        self.assert_evaluates_once(src, "run()", expected_calls=2)

    def test_target_expression_is_evaluated_in_order(self):
        src = (
            "calls = []\n"
            "def make():\n    calls.append('make')\n    return 1\n"
            "def pick(n):\n    calls.append(n)\n    return n\n"
            "def run():\n    a = [0, 0, 0]\n    a[pick(1)] = a[pick(2)] = make()\n    return a, calls\n"
        )
        results = [eval("run()", _namespace(code)) for code in (src, decompile(src))]  # pylint: disable=eval-used
        self.assertEqual(results[1], results[0], decompile(src))

    def test_separate_assignments_stay_separate(self):
        out = self.assert_same_ast("def f():\n    x = make()\n    y = make()\n    return x, y\n")
        self.assertNotIn("x = y", out)

    def test_swap_is_not_a_chain(self):
        self.assert_same_ast("def f(a, b):\n    a, b = b, a\n    return a, b\n")

    def test_walrus_is_not_a_chain(self):
        out = decompile("def f(g):\n    if (n := g()):\n        return n\n    return 0\n")
        self.assertNotIn("n = n", out)


if __name__ == "__main__":
    unittest.main()
