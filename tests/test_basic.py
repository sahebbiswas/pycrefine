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


@unittest.skipIf(sys.version_info[:2] == (3, 11), "3.11 bytecode is not supported yet (#4)")
class TestGeneratorExpressionFilters(unittest.TestCase):
    """The `if` filters of a generator expression must keep their polarity (#127).

    Since 3.13 a generator expression's filter is compiled as a jump back to the
    loop head, which the decompiler writes as `if not cond: continue`. That line
    rejects an item, so the filter it stands for is `cond`.
    """

    CASES = {
        "truthiness": (
            "def a(xs):\n    return sum(1 for x in xs if x)\n"
            "print(a([0, 1, 2, 0, 3]), a([]), a([0, 0]))\n"
        ),
        "and_filter": (
            "def b(xs, ys):\n    return sum(1 for x in xs if x and ys)\n"
            "print(b([0, 1, 2], [1]), b([0, 1, 2], []), b([], [1]))\n"
        ),
        "comparison": (
            "def c(xs):\n    return list(x for x in xs if x > 1)\n"
            "print(c([0, 1, 2, 3]), c([5, 0]))\n"
        ),
        "zip_not_equal": (
            "def diff(ot, dt):\n    return sum(1 for t1, t2 in zip(ot, dt) if t1 != t2)\n"
            "print(diff('abcd', 'abxd'), diff('ab', 'ab'), diff('ab', 'cd'))\n"
        ),
        "negated": "xs = [0, 1, 2, 0]\nprint(list(x for x in xs if not x), list(x for x in xs if not x > 1))\n",
        "or_filter": (
            "def e(xs, y):\n    return list(x for x in xs if x or y)\n"
            "print(e([0, 1, 2], 0), e([0, 1, 2], 1))\n"
        ),
        "chained_filters": (
            "def f(xs):\n    return list(x for x in xs if x if x > 1 if x < 9)\n"
            "print(f([0, 1, 2, 5, 9, 12]))\n"
        ),
        "is_none": (
            "xs = [None, 1, None, 2]\nprint(list(x for x in xs if x is None), list(x for x in xs if x is not None))\n"
        ),
        "membership": (
            "d = {1, 3}\nxs = [1, 2, 3, 4]\n"
            "print(list(x for x in xs if x in d), list(x for x in xs if x not in d))\n"
        ),
        "chained_comparison": "xs = [0, 1, 5, 9, 12]\nprint(list(x for x in xs if 1 < x < 9))\n",
        "ternary_filter": (
            "xs = [0, 1, 2, 3]\nprint(list(x for x in xs if (x if x > 1 else 0)))\n"
        ),
        "nested_loops": (
            "def g(xs, ys):\n    return list((x, y) for x in xs for y in ys if x != y)\n"
            "print(g([1, 2], [2, 3]))\n"
        ),
        "filter_on_each_loop": (
            "def h(xs, ys):\n    return list((x, y) for x in xs if x for y in ys if y)\n"
            "print(h([0, 1, 2], [0, 3]))\n"
        ),
        "filter_in_call_argument": (
            "words = ['a', '', 'bc', '']\n"
            "print(' '.join(w.upper() for w in words if w), max(len(w) for w in words if w))\n"
        ),
    }

    def _run(self, code):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(code, {"__name__": "__main__"})
        return out.getvalue()

    def test_behavior_is_preserved(self):
        for name, src in self.CASES.items():
            with self.subTest(case=name):
                decompiled = decompile(src)
                try:
                    recompiled = compile(decompiled, "<decompiled>", "exec")
                except SyntaxError as exc:
                    self.fail(f"Decompiled output does not compile ({exc}):\n{decompiled}")
                self.assertEqual(
                    self._run(recompiled), self._run(compile(src, "<original>", "exec")),
                    f"Decompiled code behaves differently:\n{decompiled}",
                )

    def test_issue_127_repro_keeps_the_filter(self):
        out = decompile(
            "def a(xs): return sum(1 for x in xs if x)\n"
            "def c(xs): return list(x for x in xs if x > 1)\n"
        )
        self.assertNotIn("if not x", out)
        self.assertIn("if x)", out)
        self.assertIn("if x > 1)", out)


@unittest.skipIf(sys.version_info[:2] == (3, 11), "3.11 bytecode is not supported yet (#4)")
class TestUnaryOperators(unittest.TestCase):
    """Unary `-`, `+`, `~` and `not` must survive decompilation."""

    CASES = {
        "negative": "x = 2\nprint(-x)\n",
        "positive": "x = 2\nprint(+x)\n",
        "invert": "x = 2\nprint(~x)\n",
        "not_value": "x = 2\nprint(not x)\nprint(not 0)\n",
        "negative_in_expression": "x = 3\ny = -x + 1\nprint(y, (-x) ** 2, -x ** 2, -(x + 1))\n",
        "not_comparison": "x = 3\nr = not (x > 1)\nprint(r)\n",
        "in_collection": "x = 2\nprint([-x, ~x, +x, not x])\n",
        "in_function": "def f(a):\n    return -a, ~a, +a, not a\nprint(f(5))\n",
        "in_call_and_attribute": "x = 4\nprint((-x).bit_length(), abs(-x))\n",
        "in_condition": "x = 0\nif not x:\n    print('zero')\nprint(-x == 0)\n",
        "ternary_branches": (
            "a = 1\nb = 2\nflag = True\nr = ~a if flag else ~b\nu = -a if flag else -b\n"
            "w = +a if flag else +b\nt = not a if flag else b\nprint(r, u, w, t)\n"
        ),
        "ternary_operand": (
            "a = 1\nb = 2\nfor flag in (True, False):\n    print(-(a if flag else b), "
            "~(a if flag else b), +(a if flag else b), not (a if flag else b))\n"
        ),
        "negated_sum": "a = 1\nb = 2\nprint(-(a + b), not (a < b), -(a * b) ** 2)\n",
        "lambda": "f = lambda x: -x\nprint(f(3))\n",
        "comprehension": "xs = [1, 2, 3]\nprint([-x for x in xs], [x for x in xs if not x % 2], {~x for x in xs})\n",
        "fstring": "x = 3\nprint(f'{-x} {not x} {~x}')\n",
        "subscript": "xs = [1, 2, 3]\ni = 1\nprint(xs[-1], xs[-2:], xs[:-1], xs[-i], xs[~i])\n",
        "augmented_assignment": "x = 3\nx += -x\nx *= -2\nprint(x)\n",
        "default_arguments": "def f(a, b=-1, c=not 0):\n    return a, b, c\nprint(f(1))\n",
        "stacked_operators": "x = 2\nprint(- -x, ~-x, -~x, +-x)\n",
        "power_precedence": "x = 2\nprint(-x ** 2, (-x) ** 2, 2 ** -x)\n",
        "attribute_and_call": "x = 5\nprint((-x).bit_length(), (~x).bit_length(), str(-x))\n",
        "loops_and_asserts": "n = 3\nwhile not n == 0:\n    n -= 1\nassert not n == 1\nassert -n <= 0\nprint(n)\n",
        "return_ternary": "def f(a, b, c):\n    return -a if c else ~b\nprint(f(1, 2, True), f(1, 2, False))\n",
        "float_and_complex": "x = 2.5\nz = 1j\nprint(-x, -z, +x, -(x + 1))\n",
    }

    def _run(self, code):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(code, {"__name__": "__main__"})
        return out.getvalue()

    def test_behavior_is_preserved(self):
        for name, src in self.CASES.items():
            with self.subTest(case=name):
                decompiled = decompile(src)
                try:
                    recompiled = compile(decompiled, "<decompiled>", "exec")
                except SyntaxError as exc:
                    self.fail(f"Decompiled output does not compile ({exc}):\n{decompiled}")
                self.assertEqual(
                    self._run(recompiled), self._run(compile(src, "<original>", "exec")),
                    f"Decompiled code behaves differently:\n{decompiled}",
                )

    def test_operators_appear(self):
        out = decompile("x = 2\nprint(-x)\nprint(~x)\nprint(+x)\n")
        assert_contains(out, "-x", "~x", "+x")


class TestValueBoolOps(unittest.TestCase):
    """`and` / `or` used as values (not conditions) must survive decompilation."""

    CASES = {
        "and_assign": "a, b = 0, 5\nx = a and b\ny = b and a\nprint(x, y)\n",
        "or_assign": "a, b = 0, 5\nx = a or b\ny = b or a\nprint(x, y)\n",
        "or_chain": "a, b = 0, 5\nz = a or b or 7\nprint(z)\n",
        "and_chain": "a, b, c = 1, 2, 3\nz = a and b and c\nprint(z)\n",
        "and_or_mixed": "a, b, c = 0, 5, 0\nw = (a and b) or c\nprint(w)\n",
        "or_and_mixed": "a, b, c = 0, 5, 0\nu = (a or b) and c\nprint(u)\n",
        "and_with_or_right": "a, b, c = 1, 0, 3\nt = a and (b or c)\nprint(t)\n",
        "and_and_or": "a, b, c, d = 1, 2, 0, 7\nv = a and b and c or d\nprint(v)\n",
        "or_with_and_right": "a, b, c = 0, 5, 0\nk = a or b and c\nprint(k)\n",
        "return_value": "def f(p, q):\n    return p and q\ndef g(p, q):\n    return p or q\nprint(f(0, 1), f(2, 3), g(0, 1), g(2, 3))\n",
        "lambda": "f = lambda p, q: p or q\ng = lambda p, q: p and q\nprint(f(0, 4), g(0, 4), g(2, 4))\n",
        "call_arguments": "a, b = 0, 5\nprint(a or b, a and b, a or 'x')\n",
        "nested_in_expression": "a, b = 0, 5\nprint((a or b) + 1, [a or b, a and b])\n",
        "ternary_inside": "a, b, c = 0, 5, 2\nr = a or (b if c else 3)\nprint(r)\n",
        "condition_unaffected": "a, b = 0, 5\nif a or b:\n    print('t')\nwhile a and b:\n    break\nprint('done')\n",
        "in_function_default": "def f(x, y=0 or 3):\n    return x or y\nprint(f(0), f(1))\n",
        "attribute_and_subscript": "a, b = 0, [1, 2]\nprint((a or b)[0], (a or b).count(1))\n",
        "not_operand": "a, b = 0, 5\nprint(not (a or b), (not a) and b)\n",
    }

    def _run(self, code):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(code, {"__name__": "__main__"})
        return out.getvalue()

    def test_cases_are_defined(self):
        self.assertTrue(self.CASES)

    def test_behavior_is_preserved(self):
        for name, src in self.CASES.items():
            with self.subTest(case=name):
                decompiled = decompile(src)
                try:
                    recompiled = compile(decompiled, "<decompiled>", "exec")
                except SyntaxError as exc:
                    self.fail(f"Decompiled output does not compile ({exc}):\n{decompiled}")
                self.assertEqual(
                    self._run(recompiled), self._run(compile(src, "<original>", "exec")),
                    f"Decompiled code behaves differently:\n{decompiled}",
                )

    def test_operators_appear(self):
        out = decompile("a, b = 0, 5\nx = a or b\ny = a and b\n")
        assert_contains(out, "x = a or b", "y = a and b")


@unittest.skipIf(sys.version_info[:2] == (3, 11), "3.11 bytecode is not supported yet (#4)")
class TestDeterministicConstants(unittest.TestCase):
    """Set and frozenset constants render in a fixed order (#128).

    A constant set iterates in hash order, which changes with PYTHONHASHSEED, so
    the same .pyc used to decompile differently from one run to the next.
    """

    def test_set_elements_are_sorted(self):
        from pycrefine import _const_repr
        cases = [
            (frozenset({"gamma", "alpha", "delta", "beta"}), "frozenset({'alpha', 'beta', 'delta', 'gamma'})"),
            (frozenset({10, 2, 1, 100, -5}), "frozenset({-5, 1, 2, 10, 100})"),
            (frozenset({b"b", b"a"}), "frozenset({b'a', b'b'})"),
            (frozenset({2.5, 1, 0.5}), "frozenset({0.5, 1, 2.5})"),
            (frozenset({"é", "ß", "a", "Z", ""}), "frozenset({'', 'Z', 'a', 'ß', 'é'})"),
            (frozenset(), "frozenset()"),
            ({"b", "a"}, "{'a', 'b'}"),
            (set(), "set()"),
        ]
        for value, expected in cases:
            with self.subTest(value=expected):
                self.assertEqual(_const_repr(value), expected)

    def test_mixed_types_never_raise_and_have_one_order(self):
        from pycrefine import _const_repr
        members = [None, True, 3, 2.5, "a", b"x", (1, 2), frozenset({"q", "p"}), 7j, Ellipsis]
        expected = _const_repr(frozenset(members))
        for shift in range(len(members)):
            rotated = members[shift:] + members[:shift]
            self.assertEqual(_const_repr(frozenset(rotated)), expected)
        self.assertTrue(expected.startswith("frozenset({None, True, 2.5, 3, 'a', b'x', 7j, (1, 2), "))

    def test_nan_and_infinity_do_not_break_sorting(self):
        from pycrefine import _const_repr
        out = _const_repr(frozenset({float("nan"), float("inf"), float("-inf"), 0.0, 1}))
        self.assertEqual(out, "frozenset({-inf, 0.0, 1, inf, nan})")
        # an int too large for a float must not trip the NaN check
        self.assertEqual(
            _const_repr(frozenset({float("inf"), 10 ** 400, 1})), f"frozenset({{1, {10 ** 400}, inf}})")

    def test_equal_numbers_that_render_differently_have_one_order(self):
        # (1, nan) and (1.0, nan) can both be members of one set, compare equal by
        # number, and render differently: the order must not depend on how they arrive.
        from pycrefine.instructions import _const_repr, _const_sort_key
        nan_a, nan_b = float("nan"), float("nan")
        pair = [(1, nan_a), (1.0, nan_b)]
        self.assertNotEqual(_const_sort_key(pair[0]), _const_sort_key(pair[1]))
        self.assertEqual(sorted(pair, key=_const_sort_key), sorted(reversed(pair), key=_const_sort_key))
        self.assertEqual(_const_repr(frozenset(pair)), "frozenset({(1, nan), (1.0, nan)})")

    def test_sets_nested_in_tuples_and_sets(self):
        from pycrefine import _const_repr
        value = (frozenset({"b", "a"}), (frozenset({3, 1, 2}),), 1)
        self.assertEqual(_const_repr(value), "(frozenset({'a', 'b'}), (frozenset({1, 2, 3}),), 1)")
        outer = frozenset({frozenset({"d", "c"}), frozenset({"b", "a"}), frozenset({"a"})})
        self.assertEqual(
            _const_repr(outer), "frozenset({frozenset({'a'}), frozenset({'a', 'b'}), frozenset({'c', 'd'})})")

    def test_everything_but_sets_is_spelled_exactly_like_repr(self):
        from pycrefine import _const_repr
        values = [
            None, True, False, 0, -7, 2 ** 70, 1.5, float("inf"), 3j, "a'b\"c", "line\nbreak", b"\x00\xff", "",
            (), (1,), (1, 2), ((), (1,)), ("a", ("b", (None, True))), Ellipsis, slice(1, 2),
        ]
        for value in values:
            with self.subTest(value=repr(value)):
                self.assertEqual(_const_repr(value), repr(value))

    def test_decompiled_constants_use_the_sorted_form(self):
        out = decompile("def f(n):\n    return n in {'delta', 'alpha', 'gamma', 'beta'}\n")
        self.assertIn("{'alpha', 'beta', 'delta', 'gamma'}", out)

    def test_behavior_is_preserved(self):
        import contextlib
        import io

        src = (
            "def a(n):\n    return n in {'x', 'y', 'z', 'w'}\n"
            "def d(n):\n    return n not in {1, 10, 2, 3, 100, -5}\n"
            "def c(n):\n    return n in {(1, 2), (3, 4), (0, 9)}\n"
            "def e():\n    return sorted(x for x in {'p', 'q', 'r'})\n"
            "print(a('x'), a('q'), d(10), d(11), c((3, 4)), c((4, 3)), e())\n"
        )

        def run(code):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exec(code, {"__name__": "__main__"})
            return out.getvalue()

        decompiled = decompile(src)
        self.assertEqual(run(compile(decompiled, "<decompiled>", "exec")), run(compile(src, "<original>", "exec")))

    def test_output_does_not_depend_on_the_hash_seed(self):
        import os
        import subprocess
        import pycrefine

        from .test_helpers import _compile

        src = (
            "def a(n):\n    return n in {'alpha', 'beta', 'gamma', 'delta', 'epsilon', 'zeta'}\n"
            "def b(n):\n    return n in {None, 'alpha', 'beta', 'gamma', 'delta'}\n"
            "def c(n):\n    return n in {(1, 'a'), (2, 'b'), (3, 'c'), (4, 'd'), (5, 'e')}\n"
            "def d(n):\n    return n in {1.5, 2, 'x', b'y', 3, 4, 5}\n"
        )
        pyc = _compile(src)
        try:
            pythonpath = os.path.dirname(os.path.dirname(pycrefine.__file__))
            outputs = set()
            for seed in ("0", "1", "2", "3", "4", "5"):
                env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONPATH=pythonpath)
                done = subprocess.run(
                    [sys.executable, "-m", "pycrefine", pyc], env=env, capture_output=True, text=True, check=True)
                outputs.add(done.stdout)
            self.assertEqual(len(outputs), 1, f"output varies with PYTHONHASHSEED: {outputs}")
        finally:
            os.unlink(pyc)
