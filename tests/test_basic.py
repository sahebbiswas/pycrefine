import ast
import sys
import unittest

from .test_helpers import assert_contains, decompile


class TestBasicStatements(unittest.TestCase):
    def test_simple_assignment(self):
        out = decompile("x = 1\ny = 2\n")
        assert_contains(out, "x = 1", "y = 2")

    def test_string_assignment(self):
        out = decompile("s = 'hello'\n")
        self.assertIn("hello", out)

    def test_multiple_assignments(self):
        out = decompile("a = 1\nb = 2\nc = 3\n")
        assert_contains(out, "a = 1", "b = 2", "c = 3")

    def test_dict_literal(self):
        out = decompile("d = {'a': 1, 'b': 2}\n")
        assert_contains(out, "'a': 1", "'b': 2")

    def test_empty_dict(self):
        out = decompile("d = {}\n")
        assert_contains(out, "{}")

    def test_list_literal(self):
        out = decompile("x = [1, 2, 3]\n")
        assert_contains(out, "1", "2", "3")

    def test_empty_list(self):
        out = decompile("x = []\n")
        assert_contains(out, "[]")

    def test_set_literal(self):
        out = decompile("s = {1, 2}\n")
        assert_contains(out, "1", "2")

    def test_tuple_literal(self):
        out = decompile("t = (1, 2, 3)\n")
        assert_contains(out, "1", "2", "3")

    def test_boolean_values(self):
        out = decompile("a = True\nb = False\n")
        assert_contains(out, "True", "False")

    def test_none_value(self):
        out = decompile("x = None\n")
        assert_contains(out, "None")

    def test_subscript_read(self):
        out = decompile("a = [1, 2]\ny = a[0]\n")
        assert_contains(out, "a[0]")

    def test_subscript_write(self):
        out = decompile("a = [1, 2]\na[0] = 9\n")
        assert_contains(out, "a[0] = 9")

    def test_del_statement(self):
        out = decompile("x = 1\ndel x\n")
        assert_contains(out, "del x")

    def test_multiline_produces_output(self):
        src = "x = 1\ny = 2\nz = x + y\n"
        out = decompile(src)
        self.assertGreater(len(out.strip()), 0)


class TestAugmentedAssignment(unittest.TestCase):
    def _check_op(self, op: str, sym: str) -> None:
        src = f"x = 4\nx {op} 2\n"
        out = decompile(src)
        self.assertIn(sym, out, f"Expected augmented operator {sym!r} in output:\n{out}")

    def test_augmented_op_add(self):        self._check_op("+=",  "+=")
    def test_augmented_op_sub(self):        self._check_op("-=",  "-=")
    def test_augmented_op_mul(self):        self._check_op("*=",  "*=")
    def test_augmented_op_div(self):        self._check_op("/=",  "/=")
    def test_augmented_op_floordiv(self):   self._check_op("//=", "//=")
    def test_augmented_op_mod(self):        self._check_op("%=",  "%=")
    def test_augmented_op_pow(self):        self._check_op("**=", "**=")
    def test_augmented_op_and(self):        self._check_op("&=",  "&=")
    def test_augmented_op_or(self):         self._check_op("|=",  "|=")
    def test_augmented_op_xor(self):        self._check_op("^=",  "^=")
    def test_augmented_op_lshift(self):     self._check_op("<<=", "<<=")
    def test_augmented_op_rshift(self):     self._check_op(">>=", ">>=")

    def test_augassign_does_not_emit_xor_for_plus(self):
        out = decompile("x = 1\nx += 3\n")
        self.assertIn("x += 3", out)
        self.assertNotIn("^", out)

    def test_augassign_sequence(self):
        out = decompile("x = 5\nx += 3\nx -= 1\nx ^= 2\n")
        assert_contains(out, "x += 3", "x -= 1", "x ^= 2")


class TestOperators(unittest.TestCase):
    def _check_binary(self, expr: str, expected: str) -> None:
        out = decompile(f"x = 4\ny = {expr}\n")
        self.assertIn(expected, out, f"Expected operator {expected!r} for expr {expr!r}:\n{out}")

    def test_binary_op_add(self):        self._check_binary("x + 1",  "+")
    def test_binary_op_sub(self):        self._check_binary("x - 1",  "-")
    def test_binary_op_mul(self):        self._check_binary("x * 2",  "*")
    def test_binary_op_div(self):        self._check_binary("x / 2",  "/")
    def test_binary_op_floordiv(self):   self._check_binary("x // 2", "//")
    def test_binary_op_mod(self):        self._check_binary("x % 3",  "%")
    def test_binary_op_pow(self):        self._check_binary("x ** 2", "**")
    def test_binary_op_and(self):        self._check_binary("x & 1",  "&")
    def test_binary_op_or(self):         self._check_binary("x | 1",  "|")
    def test_binary_op_xor(self):        self._check_binary("x ^ 1",  "^")
    def test_binary_op_lshift(self):     self._check_binary("x << 1", "<<")
    def test_binary_op_rshift(self):     self._check_binary("x >> 1", ">>")

    def _check_comparison(self, op: str) -> None:
        out = decompile(f"x = 1\ny = x {op} 0\n")
        self.assertIn(op, out, f"Comparison operator {op!r} missing:\n{out}")

    def test_comparison_eq(self):   self._check_comparison("==")
    def test_comparison_ne(self):   self._check_comparison("!=")
    def test_comparison_lt(self):   self._check_comparison("<")
    def test_comparison_le(self):   self._check_comparison("<=")
    def test_comparison_gt(self):   self._check_comparison(">")
    def test_comparison_ge(self):   self._check_comparison(">=")

    def test_contains_in(self):
        out = decompile("x = 1\ny = x in (1, 2)\n")
        self.assertIn(" in ", out)

    def test_contains_not_in(self):
        out = decompile("x = 1\ny = x not in (1, 2)\n")
        self.assertIn("not in", out)

    def test_is_op(self):
        out = decompile("x = None\ny = x is None\n")
        self.assertIn(" is ", out)

    def test_is_not_op(self):
        out = decompile("x = 1\ny = x is not None\n")
        self.assertIn("is not", out)


class TestComprehensions(unittest.TestCase):
    """Comprehensions decompile back to comprehension syntax on every version,
    including the inlined (PEP 709) form used since Python 3.12."""

    def _assert_roundtrip(self, src, *equivalents):
        """The output must parse to the AST of *src* or of an equivalent spelling."""
        out = decompile(src)
        expected = {ast.dump(ast.parse(s)) for s in (src,) + equivalents}
        self.assertIn(ast.dump(ast.parse(out)), expected, out)
        self.assertNotRegex(out, r"\byield\b|_res\b")

    def test_list_comprehension_yield(self):
        self._assert_roundtrip("def f(items):\n    return [x * 2 for x in items if x > 0]\n")

    def test_dict_comprehension_yield(self):
        self._assert_roundtrip("def h(items):\n    return {k: v for k, v in items}\n")

    def test_set_comprehension_yield(self):
        self._assert_roundtrip("def g(items):\n    return {x for x in items}\n")

    def test_inlined_comprehension_shapes(self):
        cases = [
            "def f(xs):\n    y = [x for x in xs]\n    return y\n",
            "def f(xs):\n    [print(x) for x in xs]\n",
            "def f(m):\n    return [[c for c in row] for row in m]\n",
            "def f(a, b):\n    return [(x, y) for x in a for y in b if x != y]\n",
            ("def f(xs):\n    return [x for x in xs if x if x > 2]\n",
             # chained filters are an `and`; the 3.9 code-object path prints that
             "def f(xs):\n    return [x for x in xs if x and x > 2]\n"),
            "def f(xs):\n    return [x for x in xs if x is not None]\n",
            "def f(xs, n):\n    return {k: [v] * n for k, v in xs.items() if not k.startswith('_')}\n",
            "def f(xs):\n    return [a + b for (a, b), c in xs]\n",
            "def f(xs):\n    return sum([x for x in xs]) + len({y for y in xs})\n",
            "def f(xs):\n    return [x if x else 0 for x in xs]\n",
            "def f(xs):\n    return {k: (v if v > 0 else -v) for k, v in xs}\n",
            "def f(xs, d):\n    return [d.get(x, 'a' if x else 'b') for x in xs]\n",
            "total = [i * i for i in range(10)]\n",
            # genexprs are still separate code objects; 3.13+ starts their
            # loop body with a STORE_FAST_LOAD_FAST superinstruction
            "def f(xs):\n    return sum(d.w for d in xs)\n",
        ]
        for case in cases:
            src, *equivalents = case if isinstance(case, tuple) else (case,)
            with self.subTest(src=src):
                self._assert_roundtrip(src, *equivalents)

    def test_negative_comprehension_no_yield_in_normal_loop(self):
        # A normal loop calling list.append should NOT emit yield
        src = "def f(items):\n    res = []\n    for x in items:\n        res.append(x * 2)\n    return res\n"
        out = decompile(src)
        self.assertNotIn("yield", out)
        self.assertIn("append", out)

    def test_generator_expression_with_closure(self):
        # Regression test for LOAD_CLOSURE falling through to _op_unknown
        # and producing an 'unknown_func' in the decompiled output
        src = "class C:\n    def f(self, items):\n        return any(self.check(x) for x in items)\n"
        out = decompile(src)
        self.assertNotIn("unknown_func", out)
        # The genexpr content must be present (any() wrapper may be dropped by some Python versions)
        self.assertTrue(
            "self.check" in out,
            f"Expected genexpr content in decompiled output, got: {out!r}"
        )


if __name__ == "__main__":
    unittest.main()
