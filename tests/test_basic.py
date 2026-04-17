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
    """Verifies that comprehensions decompile as expected using the yield representation
    for LIST_APPEND, SET_ADD, MAP_ADD inside inline comprehensions."""

    def test_list_comprehension_yield(self):
        src = "def f(items):\n    return [x * 2 for x in items if x > 0]\n"
        out = decompile(src)
        if sys.version_info >= (3, 12):
            self.assertIn("yield (x * 2)", out)
            self.assertNotIn("append", out)
        else:
            self.assertTrue("[x * 2 for x in items if x > 0]" in out or "[(x * 2) for x in items if x > 0]" in out)

    def test_dict_comprehension_yield(self):
        src = "def h(items):\n    return {k: v for k, v in items}\n"
        out = decompile(src)
        if sys.version_info >= (3, 12):
            self.assertTrue("yield k: v" in out or "yield (k: v)" in out)
        else:
            self.assertIn("{k: v for k, v in items}", out)

    def test_set_comprehension_yield(self):
        src = "def g(items):\n    return {x for x in items}\n"
        out = decompile(src)
        if sys.version_info >= (3, 12):
            self.assertTrue("yield x" in out)
        else:
            self.assertIn("{x for x in items}", out)

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
        self.assertIn("any", out)
        self.assertTrue("self.check(x) for x in items" in out or "self.check" in out)


if __name__ == "__main__":
    unittest.main()
