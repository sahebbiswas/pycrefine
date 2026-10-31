"""Assignment expressions in conditions are rebuilt as written, not as separate statements (#205)."""
import ast
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestWalrus(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def assert_evaluates_once(self, source, call):
        """`calls` counts how often the program's `probe` ran; both versions must agree."""
        counts = []
        for code in (source, decompile(source)):
            namespace = _namespace(code)
            namespace["calls"].clear()
            result = eval(call, namespace)  # pylint: disable=eval-used
            counts.append((result, len(namespace["calls"])))
        self.assertEqual(counts[1], counts[0], decompile(source))

    PROBE = "calls = []\ndef probe(v):\n    calls.append(v)\n    return v\n"

    def test_if_condition(self):
        src = "def f(data):\n    if (n := len(data)) > 3:\n        return n\n    return 0\n"
        out = self.assert_same_results(src, "f([1, 2, 3, 4])", "f([1])")
        self.assertIn(":=", out)

    def test_if_condition_evaluates_the_value_once(self):
        src = self.PROBE + "def f(v):\n    if (n := probe(v)) > 3:\n        return n\n    return 0\n"
        self.assert_evaluates_once(src, "f(5)")
        self.assert_evaluates_once(src, "f(1)")

    def test_issue_program(self):
        src = (
            "def run():\n"
            "    out = []\n"
            "    data = [3, 8, 12, 5]\n"
            "    if (n := len(data)) > 3:\n        out.append(('long', n))\n"
            "    total = 0\n"
            "    while (total := total + 5) < 20:\n        pass\n"
            "    out.append(total)\n    return out\n"
        )
        self.assert_same_results(src, "run()")

    def test_module_level(self):
        src = (
            "data = [3, 8, 12, 5]\nresult = []\n"
            "if (n := len(data)) > 3:\n    result.append(n)\n"
            "total = 0\nwhile (total := total + 5) < 20:\n    pass\nresult.append(total)\n"
        )
        self.assert_same_results(src, "result", "n", "total")

    def test_elif_condition(self):
        src = (
            "def f(data):\n    if (n := len(data)) > 3:\n        return n\n"
            "    elif (m := n * 2) > 1:\n        return m\n    return 0\n"
        ).replace("(m := n * 2)", "(m := len(data) * 2)")
        self.assert_same_results(src, "f([1, 2, 3, 4])", "f([1])", "f([])")

    def test_while_condition_consumes_an_iterator(self):
        src = (
            "def f(it):\n    out = []\n"
            "    while (x := next(it, None)) is not None:\n        out.append(x)\n    return out\n"
        )
        self.assert_same_results(src, "f(iter([1, 2, 3]))", "f(iter([]))")

    def test_while_condition_evaluates_the_value_once_per_test(self):
        src = self.PROBE + (
            "def f(n):\n    seen = []\n    i = 0\n"
            "    while (v := probe(i)) < n:\n        seen.append(v)\n        i += 1\n    return seen\n"
        )
        self.assert_evaluates_once(src, "f(3)")

    def test_in_a_boolean_chain(self):
        src = (
            "def f(s):\n    if (m := s.find('a')) != -1 and (k := m + 1) > 2:\n        return k\n    return m\n"
        )
        self.assert_same_results(src, "f('xxa')", "f('a')", "f('b')")

    def test_as_an_argument_and_a_value(self):
        src = "def f(s):\n    t = (y := s + 1) * 2\n    return [y, t, str(z := y + 1), z]\n"
        self.assert_same_results(src, "f(1)", "f(10)")

    def test_truthy_value_in_the_condition(self):
        src = "def f(s):\n    if (m := s.strip()):\n        return m\n    return None\n"
        self.assert_same_results(src, "f('  x ')", "f('   ')")

    def test_walrus_is_not_confused_with_a_chained_assignment(self):
        out = decompile("def f(g):\n    x = y = g()\n    return x\n")
        self.assertNotIn(":=", out)
        out = decompile("def f(g):\n    z = (y := g()) + 1\n    return y, z\n")
        self.assertIn("y :=", out)

    def test_target_in_a_comprehension_is_declared_global(self):
        """The target of `:=` in a comprehension belongs to the function around it (#189)."""
        src = "def f(values):\n    global Z\n    return [Z := x for x in values]\n"
        out = decompile(src)
        self.assertIn("global Z", out)
        original, rebuilt = _namespace(src), _namespace(out)
        for ns in (original, rebuilt):
            ns["f"]([1, 2, 3])
        self.assertEqual(rebuilt["Z"], original["Z"])

    def test_target_in_a_comprehension_is_declared_nonlocal(self):
        src = (
            "def outer():\n    n = 0\n    def inner(values):\n        nonlocal n\n"
            "        return [n := x for x in values]\n    inner([4, 5])\n    return n\n"
        )
        out = decompile(src)
        self.assertIn("nonlocal n", out)
        self.assertEqual(_namespace(out)["outer"](), _namespace(src)["outer"]())

    def test_local_target_in_a_comprehension_needs_no_declaration(self):
        out = decompile("def f(values):\n    r = [y := x for x in values]\n    return r, y\n")
        self.assertNotIn("global", out)
        self.assertNotIn("nonlocal", out)

    def test_target_in_a_nested_comprehension_is_declared(self):
        """A comprehension inside a comprehension is another code object before 3.12."""
        for body in ("[[Z := x for x in r] for r in rows]", "list([Z := x for x in r] for r in rows)"):
            with self.subTest(body=body):
                src = f"def f(rows):\n    global Z\n    return {body}\n"
                out = decompile(src)
                self.assertIn("global Z", out)
                original, rebuilt = _namespace(src), _namespace(out)
                for ns in (original, rebuilt):
                    ns["f"]([[1, 2], [3]])
                self.assertEqual(rebuilt["Z"], original["Z"])

    def test_nonlocal_target_in_a_nested_comprehension(self):
        src = (
            "def outer():\n    n = 0\n    def inner(rows):\n        nonlocal n\n"
            "        return [[n := x for x in r] for r in rows]\n    inner([[4, 5], [6]])\n    return n\n"
        )
        out = decompile(src)
        self.assertIn("nonlocal n", out)
        self.assertEqual(_namespace(out)["outer"](), _namespace(src)["outer"]())


if __name__ == "__main__":
    unittest.main()
