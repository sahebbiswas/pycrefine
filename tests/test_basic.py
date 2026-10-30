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

    def test_filters_of_every_polarity_keep_their_condition(self):
        # 3.11 compiles each of these filters to a conditional jump back to the loop head (#192).
        for src in (
            "def f(xs):\n    return [x for x in xs if x > 1]\n",
            "def f(xs):\n    return [x for x in xs if not x]\n",
            "def f(xs):\n    return [x for x in xs if x is not None]\n",
            "def f(xs):\n    return [x for x in xs if x is None]\n",
            "def f(xs):\n    return {x for x in xs if x}\n",
            "def f(xs):\n    return {x: 1 for x in xs if x > 1}\n",
            "def f(xs):\n    return (x for x in xs if x > 1)\n",
        ):
            with self.subTest(src=src):
                self._assert_roundtrip(src)

    def test_list_comprehension_yield(self):
        self._assert_roundtrip("def f(items):\n    return [x * 2 for x in items if x > 0]\n")

    def test_dict_comprehension_yield(self):
        self._assert_roundtrip("def h(items):\n    return {k: v for k, v in items}\n")

    def test_set_comprehension_yield(self):
        self._assert_roundtrip("def g(items):\n    return {x for x in items}\n")

    def test_inlined_comprehension_shapes(self):
        cases = {
            "simple_list": "def f(xs):\n    y = [x for x in xs]\n    return y\n",
            "print_in_list": "def f(xs):\n    [print(x) for x in xs]\n",
            "nested_list": "def f(m):\n    return [[c for c in row] for row in m]\n",
            "two_loops_with_filter": "def f(a, b):\n    return [(x, y) for x in a for y in b if x != y]\n",
            "chained_filters": (
                "def f(xs):\n    return [x for x in xs if x if x > 2]\n",
                # chained filters are an `and`; the 3.9 code-object path prints that
                "def f(xs):\n    return [x for x in xs if x and x > 2]\n"),
            "is_not_none_filter": "def f(xs):\n    return [x for x in xs if x is not None]\n",
            "dict_with_filter": "def f(xs, n):\n    return {k: [v] * n for k, v in xs.items() if not k.startswith('_')}\n",
            "tuple_target": "def f(xs):\n    return [a + b for (a, b), c in xs]\n",
            "sum_and_len": "def f(xs):\n    return sum([x for x in xs]) + len({y for y in xs})\n",
            "conditional_element": "def f(xs):\n    return [x if x else 0 for x in xs]\n",
            "dict_conditional_value": "def f(xs):\n    return {k: (v if v > 0 else -v) for k, v in xs}\n",
            "get_with_conditional": "def f(xs, d):\n    return [d.get(x, 'a' if x else 'b') for x in xs]\n",
            "module_level": "total = [i * i for i in range(10)]\n",
            # genexprs are still separate code objects; 3.13+ starts their
            # loop body with a STORE_FAST_LOAD_FAST superinstruction
            "genexpr_attribute": "def f(xs):\n    return sum(d.w for d in xs)\n",
        }
        for name, case in cases.items():
            src, *equivalents = case if isinstance(case, tuple) else (case,)
            with self.subTest(case=name):
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


class TestOneItemTuple(unittest.TestCase):
    """A tuple of one item keeps its comma (#234)."""

    SRC = (
        "class B:\n    pass\n"
        "def f(B):\n    return (B,)\n"
        "def g(x, A):\n"
        "    seen = []\n"
        "    for y in (A,):\n        seen.append(y)\n"
        "    return isinstance(x, (A,)), seen\n"
        "bases = (B,)\n"
        "nested = ((B,),)\n"
        "out = (f(1), g(2, int), [c.__name__ for c in bases], [[c.__name__ for c in t] for t in nested])\n"
    )

    def test_comma_is_kept(self):
        out = decompile(self.SRC)
        assert_contains(out, "return (B,)", "for y in (A,):", "isinstance(x, (A,))", "bases = (B,)")

    def test_behavior_is_preserved(self):
        ns = {}
        exec(decompile(self.SRC), ns)
        ref = {}
        exec(self.SRC, ref)
        self.assertEqual(ns["out"], ref["out"])


class TestDeleteSubscriptAndAttribute(unittest.TestCase):
    """`del x[i]`, `del x[a:b]` and `del o.attr` are kept (#180)."""

    SRC = (
        "class O:\n    pass\n"
        "o = O()\no.p = O()\no.p.q = 1\no.p.r = 2\n"
        "del o.p.q\n"
        "xs = [1, 2, 3, 4, 5, 6]\n"
        "d = {'a': 1, 'b': 2, (1, 2): 3}\n"
        "del xs[0]\ndel d['b']\ndel d[1, 2]\n"
        "del xs[0:1]\ndel xs[:1]\ndel xs[1:]\ndel xs[::2]\n"
        "grid = [[1, 2, 3], [4, 5, 6]]\n"
        "del grid[0][1]\n"
        "def f(seq, key, i, j):\n"
        "    del seq[key]\n"
        "    del seq[i:j]\n"
        "    del seq[i], seq[0]\n"
        "    return seq\n"
        "out = (vars(o.p), xs, d, grid, f([0, 1, 2, 3, 4, 5, 6, 7], 1, 1, 2))\n"
    )

    @unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 try/except is not decompiled (#179)")
    def test_delete_in_exception_handler(self):
        src = (
            "def f(seq):\n"
            "    try:\n"
            "        del seq[10]\n"
            "    except IndexError:\n"
            "        del seq[-1:]\n"
            "    return seq\n"
            "out = f([1, 2, 3])\n"
        )
        ns = {}
        exec(decompile(src), ns)
        self.assertEqual(ns["out"], [1, 2])

    def test_loosely_binding_target_is_parenthesized(self):
        src = (
            "def f(a, b, k):\n"
            "    del (a < b).x\n"
            "    del (a < b)[k]\n"
            "    del (-1).x\n"
            "    del (-1)[k]\n"
            "    return (a < b).real\n"
        )
        out = decompile(src)
        assert_contains(out, "del (a < b).x", "del (a < b)[k]", "(a < b).real", "del (-1).x", "del (-1)[k]")
        compile(out, "<decompiled>", "exec")

    def test_statements_are_kept(self):
        out = decompile(self.SRC)
        assert_contains(out, "del o.p.q", "del xs[0]", "del d['b']", "del seq[key]", "del grid[0][1]")
        self.assertIn("del xs[0:1]", out)
        self.assertIn("del xs[:1]", out)

    def test_behavior_is_preserved(self):
        ns = {}
        exec(decompile(self.SRC), ns)
        ref = {}
        exec(self.SRC, ref)
        self.assertEqual(ns["out"], ref["out"])


class TestSliceAssignment(unittest.TestCase):
    """`x[a:b] = v`, chained and augmented forms keep the slice (#181)."""

    SRC = (
        "ys = list(range(8))\n"
        "ys[1:3] = []\n"
        "ys[:2] = [9, 9, 9]\n"
        "ys[4:] = [7]\n"
        "ys[:] = ys + [1]\n"
        "zs = list(range(10))\n"
        "zs[::2] = [0] * 5\n"
        "a = list(range(6))\n"
        "a[1:3] = a[3:5] = [7, 8]\n"
        "b = list(range(5))\n"
        "b[1:3] += [9]\n"
        "b[:2] *= 2\n"
        "def f(seq, i, j, new):\n"
        "    seq[i:j] = new\n"
        "    seq[i:] = new\n"
        "    seq[:j] = new\n"
        "    return seq\n"
        "class C:\n"
        "    def __init__(self):\n        self.d = list(range(5))\n"
        "c = C()\n"
        "c.d[1:-1] = ['q']\n"
        "out = (ys, zs, a, b, f(list(range(6)), 1, 3, ['x']), c.d)\n"
    )

    def test_statements_are_kept(self):
        out = decompile(self.SRC)
        assert_contains(out, "ys[1:3] = []", "ys[:2] = [9, 9, 9]", "ys[4:] = [7]", "zs[::2] = ", "b[1:3] += [9]",
                        "seq[i:j] = new", "c.d[1:-1] = ['q']")

    def test_behavior_is_preserved(self):
        ns = {}
        exec(decompile(self.SRC), ns)
        ref = {}
        exec(self.SRC, ref)
        self.assertEqual(ns["out"], ref["out"])

    def test_slice_targets_of_a_tuple_assignment_stay_simultaneous(self):
        src = (
            "xs = [1, 2, 3, 4]\n"
            "xs[0:1], xs[2:3] = xs[2:3], xs[0:1]\n"
            "def f(a, b):\n"
            "    a[0:2], b = b, a[0:2]\n"
            "    return a, b\n"
            "out = (xs, f([1, 2, 3, 4], [9, 8]))\n"
        )
        ns = {}
        exec(decompile(src), ns)
        self.assertEqual(ns["out"], ([3, 2, 1, 4], ([9, 8, 3, 4], [1, 2])))

    def test_loosely_binding_container_is_parenthesized(self):
        src = (
            "def f(a, b):\n"
            "    (a < b)[1:2] = 1\n"
            "    return (a < b)[1:2]\n"
        )
        out = decompile(src)
        assert_contains(out, "(a < b)[1:2] = 1")
        compile(out, "<decompiled>", "exec")


class TestIntegerLiteralReceiver(unittest.TestCase):
    """An integer literal followed by `.name` keeps its parentheses (#182)."""

    SRC = (
        "def f(n):\n    return n.bit_length() + (255).bit_length()\n"
        "out = ((1024).to_bytes(2, 'little'), (3).bit_length(), 1.5.hex(), (-5).bit_length(),\n"
        "       (16).real, (10).__add__(2), (1e3).is_integer(), (2j).real, f(3), (7).conjugate())\n"
    )

    def test_parentheses_are_kept(self):
        out = decompile(self.SRC)
        assert_contains(out, "(1024).to_bytes(", "(3).bit_length()", "(255).bit_length()", "(-5).bit_length()")
        compile(out, "<decompiled>", "exec")

    def test_behavior_is_preserved(self):
        ns = {}
        exec(decompile(self.SRC), ns)
        ref = {}
        exec(self.SRC, ref)
        self.assertEqual(ns["out"], ref["out"])


if __name__ == "__main__":
    unittest.main()


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
        "mixed_chain_long": "a, b, c, d, e = 0, 1, 0, 2, 3\nr = a and b or c and d or e\nprint(r)\n",
        "mixed_chain_long_first_arm": "a, b, c, d, e = 1, 2, 0, 0, 3\nr = a and b or c and d or e\nprint(r)\n",
        "mixed_chain_long_second_arm": "a, b, c, d, e = 0, 1, 1, 4, 3\nr = a and b or c and d or e\nprint(r)\n",
        "mixed_chain_returned": "def f(a, b, c):\n    return a and b or c\nprint(f(0, 1, 2), f(1, 0, 2), f(1, 3, 2))\n",
        "mixed_chain_argument": "a, b, c = 0, 5, 7\nprint((a and b) or c, a or (b and c))\n",
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


@unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 11)), "3.10/3.11 try/finally decompilation has known failures")
class TestTryFinallyWithoutExcept(unittest.TestCase):
    """A try/finally with no except clause keeps its finally body clean (#129)."""

    def assert_clean(self, out):
        for junk in ("code object", "<code", "# PUSH_NULL", "# LOAD", "# CALL"):
            self.assertNotIn(junk, out)

    def test_issue_repro_genexpr_after_finally(self):
        out = decompile(
            "def t(xs):\n    try:\n        a = xs.go()\n    finally:\n        xs.close()\n"
            "    return sum(xs)\n")
        self.assert_clean(out)
        assert_contains(out, "try:", "a = xs.go()", "finally:", "xs.close()", "return sum(xs)")

    def test_finally_with_genexpr_lambda_and_def(self):
        src = (
            "def t(xs):\n    try:\n        a = 1\n    finally:\n"
            "        total = sum(x for x in xs)\n        f = lambda y: y + 1\n"
            "        def g(z):\n            return z * 2\n    return a\n")
        out = decompile(src)
        self.assert_clean(out)
        compile(out, "<decompiled>", "exec")
        assert_contains(out, "finally:", "total = ")

    def test_module_level_try_finally(self):
        out = decompile("try:\n    x = 1\nfinally:\n    print('done')\nprint('after')\n")
        self.assert_clean(out)
        assert_contains(out, "try:", "x = 1", "finally:", "print('done')", "print('after')")
        self.assertLess(out.index("finally:"), out.index("print('after')"))

    def test_sequential_try_finally_blocks_keep_their_own_bodies(self):
        # The first finally body is empty / a prefix of the second; it must not cut
        # the second one short.
        for first in ("pass", "a()"):
            src = (
                "def f(x):\n    try:\n        a = 1\n    finally:\n        " + first + "\n"
                "    try:\n        b = 2\n    finally:\n        a()\n        b()\n    print(x)\n")
            out = decompile(src)
            self.assert_clean(out)
            compile(out, "<decompiled>", "exec")
            tail = out[out.rindex("finally:"):]
            self.assertIn("a()", tail.split("print(x)")[0])
            self.assertIn("b()", tail.split("print(x)")[0])
            lines = [ln for ln in out.splitlines() if ln.strip() in ("b()", "print(x)")]
            self.assertGreater(len(lines[0]) - len(lines[0].lstrip()), len(lines[1]) - len(lines[1].lstrip()))

    def test_empty_finally_body_is_pass(self):
        out = decompile("def t():\n    try:\n        x = 1\n    finally:\n        pass\n    return 2\n")
        self.assert_clean(out)
        self.assertIn("finally:\n        pass", out)
        compile(out, "<decompiled>", "exec")

    def test_behavior_is_preserved(self):
        import contextlib
        import io

        src = (
            "log = []\n"
            "def t(xs):\n    try:\n        a = list(xs)\n    finally:\n        log.append('closed')\n"
            "    return sum(x for x in a)\n"
            "def u():\n    try:\n        raise ValueError('x')\n    finally:\n        log.append('u')\n"
            "print(t([1, 2, 3]))\n"
            "try:\n    u()\nexcept ValueError:\n    pass\n"
            "print(log)\n")

        def run(code):
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                exec(code, {"__name__": "__main__"})
            return out.getvalue()

        decompiled = decompile(src)
        self.assertEqual(run(compile(decompiled, "<decompiled>", "exec")), run(compile(src, "<original>", "exec")))


@unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 11)), "3.10/3.11 try/finally decompilation has known failures")
class TestEarlyExitsInTryFinally(unittest.TestCase):
    """continue/break/return inside try/finally in a loop (#109).

    The compiler inlines a copy of the finally body before every early exit. Those
    copies are not source code, so they must not show up as statements.
    """

    @staticmethod
    def _run(code):
        import contextlib
        import io

        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            exec(code, {"__name__": "__main__"})
        return out.getvalue()

    def assert_same_behavior(self, src):
        decompiled = decompile(src)
        compile(decompiled, "<decompiled>", "exec")
        self.assertEqual(self._run(compile(decompiled, "<decompiled>", "exec")), self._run(compile(src, "<original>", "exec")))
        return decompiled

    def test_issue_repro_continue(self):
        out = self.assert_same_behavior(
            "for i in range(4):\n    try:\n        if i == 1:\n            continue\n        print(i)\n"
            "    finally:\n        print('f', i)\n")
        assert_contains(out, "continue", "finally:")
        self.assertEqual(out.count("print('f', i)"), 1)

    def test_try_body_statement_identical_to_finally_body(self):
        # A statement in the try body that compiles exactly like the finally body is not
        # an inline copy and must stay, with and without an early exit nearby.
        out = self.assert_same_behavior(
            "def f(xs):\n    for x in xs:\n        try:\n            print('c')\n            if x:\n"
            "                continue\n            print('w', x)\n        finally:\n            print('c')\n"
            "def g():\n    try:\n        print('c')\n        print('w')\n    finally:\n        print('c')\n"
            "def h(xs):\n    for x in xs:\n        try:\n            print('c')\n            print('c')\n"
            "        finally:\n            print('c')\n"
            "f([0, 1, 2]); g(); h([1, 2])\n")
        self.assertEqual(out.count("print('c')"), 7)

    def test_always_raising_body_with_statement_identical_to_finally_body(self):
        # No normal-path copy exists, so a matching try-body statement must not be
        # taken for one; the finally body then comes from the exception handler.
        out = self.assert_same_behavior(
            "def f():\n    try:\n        print('c')\n        raise ValueError\n    finally:\n        print('c')\n"
            "try:\n    f()\nexcept ValueError:\n    print('raised')\n")
        self.assertEqual(out.count("print('c')"), 2)

    def test_always_raising_body_in_loops(self):
        out = self.assert_same_behavior(
            "def g(xs):\n    for x in xs:\n        try:\n            if x:\n                continue\n"
            "            raise ValueError\n        finally:\n            print('c', x)\n"
            "def h(xs):\n    for x in xs:\n        for y in x:\n            try:\n                if y:\n"
            "                    continue\n                raise ValueError\n            finally:\n"
            "                print('c', y)\n        print('after')\n"
            "for fn, arg in ((g, [1, 2, 0]), (h, [[1], [2, 0]])):\n    try:\n        fn(arg)\n"
            "    except ValueError:\n        print('raised')\n")
        self.assertEqual(out.count("print('c'"), 2)

    @unittest.skipIf(sys.version_info >= (3, 11), "chained comparison as a value: #154")
    def test_return_chained_comparison_from_loops(self):
        # The chain's own cleanup (ROT_TWO; POP_TOP) precedes the iterator drops.
        self.assert_same_behavior(
            "def f(items, a, b, c):\n    for item in items:\n        return a < b < c\n"
            "def g(items, a, b, c):\n    for item in items:\n        for j in item:\n            return a < b < c\n"
            "print(f([1], 1, 2, 3), f([1], 2, 1, 3), f([], 1, 2, 3), g([[1]], 1, 2, 3), g([[1]], 3, 2, 1))\n")

    def test_continue_break_and_return(self):
        out = self.assert_same_behavior(
            "def f(xs):\n    for i in xs:\n        try:\n            if i == 1:\n                continue\n"
            "            if i == 3:\n                break\n            if i == 5:\n                return i\n"
            "            print(i)\n        finally:\n            print('f', i)\n    return -1\n"
            "print(f(range(10)), f([0, 1, 2]), f([5]))\n")
        self.assertEqual(out.count("print('f', i)"), 1)

    def test_while_loop(self):
        out = self.assert_same_behavior(
            "i = 0\nwhile i < 6:\n    i += 1\n    try:\n        if i == 2:\n            continue\n"
            "        if i == 4:\n            break\n        print(i)\n    finally:\n        print('f', i)\n")
        self.assertEqual(out.count("print('f', i)"), 1)

    def test_with_except_clause(self):
        out = self.assert_same_behavior(
            "out = []\nfor i in range(5):\n    try:\n        if i == 1:\n            continue\n"
            "        if i == 2:\n            raise ValueError(i)\n        out.append(i)\n"
            "    except ValueError:\n        out.append(-1)\n    finally:\n        out.append(100 + i)\n"
            "print(out)\n")
        self.assertEqual(out.count("100 + i"), 1)

    def test_loop_body_without_early_exit_has_no_stray_continue(self):
        out = self.assert_same_behavior(
            "def f(xs):\n    for i in xs:\n        try:\n            print(i)\n        finally:\n"
            "            print('f', i)\n    return 0\nf([1, 2])\n")
        self.assertNotIn("continue", out)

    def test_finally_is_not_swallowing_exceptions(self):
        # a `continue` inside the finally block would silently swallow the exception
        src = (
            "def f(xs):\n    for i in xs:\n        try:\n            if i == 1:\n                raise KeyError(i)\n"
            "        finally:\n            print('f', i)\n    return 0\n"
            "try:\n    f([0, 1, 2])\nexcept KeyError as e:\n    print('raised', e)\n")
        self.assert_same_behavior(src)

    def test_return_value_from_loop(self):
        out = self.assert_same_behavior(
            "def g(xs):\n    for i in xs:\n        if i > 2:\n            return i * 2\n        print(i)\n    return 0\n"
            "print(g([1, 2, 3, 4]), g([1]))\n")
        self.assertIn("return (i * 2)" if "(i * 2)" in out else "return i * 2", out)

    def test_return_value_from_nested_loops(self):
        self.assert_same_behavior(
            "def g(rows):\n    for r in rows:\n        for c in r:\n            if c > 2:\n                return c\n"
            "    return 0\nprint(g([[1, 2], [3, 4]]), g([[1]]))\n")


@unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 11)), "3.10/3.11 try/finally decompilation has known failures")
class TestFinallyAfterLoopBody(unittest.TestCase):
    """try/except/finally whose body ends in a loop keeps its finally block (#132).

    On 3.12 the compiler leaves a NOP between the loop and the inline finally body,
    which hid the finally and produced unparsable output.
    """

    SRC = (
        "def run(data, log):\n    try:\n        for d in data:\n            log.append(d)\n"
        "    except KeyError as e:\n        log.append(e)\n    except ValueError as e:\n        log.append((d, e))\n"
        "    except Exception:\n        pass\n    finally:\n        log.append('finally')\n    log.append('after')\n"
        "def items(exc):\n    yield 1\n    if exc is not None:\n        raise exc\n"
        "for exc in (None, KeyError('k'), ValueError('v'), RuntimeError('r')):\n"
        "    out = []\n    run(items(exc), out)\n    print(out)\n"
    )

    def test_finally_block_is_kept(self):
        out = decompile(self.SRC)
        compile(out, "<decompiled>", "exec")
        assert_contains(out, "try:", "except KeyError as e:", "except ValueError as e:", "except Exception:", "finally:")
        self.assertLess(out.index("finally:"), out.index("'after'"))

    def test_behavior_is_preserved(self):
        # Normal completion, and an iterator that raises into each except clause
        # (KeyError, ValueError, and one only `except Exception` catches).
        import contextlib
        import io

        def run(code):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exec(code, {"__name__": "__main__"})
            return buf.getvalue()

        out = decompile(self.SRC)
        expected = run(compile(self.SRC, "<original>", "exec"))
        self.assertEqual(expected.count("'finally'"), 4)
        self.assertEqual(run(compile(out, "<decompiled>", "exec")), expected)


@unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 11)), "3.10/3.11 try/finally decompilation has known failures")
class TestImplicitReturnAfterBlocks(unittest.TestCase):
    """No `return None` is invented after a try or with block that ends a function (#140)."""

    def assert_no_return_none(self, src):
        out = decompile(src)
        compile(out, "<decompiled>", "exec")
        self.assertNotIn("return None", out)
        return out

    def test_try_finally(self):
        self.assert_no_return_none("def f(xs):\n    try:\n        print(xs)\n    finally:\n        print('done')\n")

    def test_try_except(self):
        self.assert_no_return_none("def f(x):\n    try:\n        x.go()\n    except ValueError:\n        print('bad')\n")

    def test_try_except_finally(self):
        self.assert_no_return_none(
            "def f(x):\n    try:\n        x.go()\n    except ValueError as e:\n        print(e)\n"
            "    finally:\n        print('done')\n")

    def test_with_block(self):
        self.assert_no_return_none("def f(p):\n    with open(p) as fh:\n        fh.read()\n")

    def test_try_finally_in_loop(self):
        self.assert_no_return_none(
            "def f(xs):\n    for i in xs:\n        try:\n            print(i)\n        finally:\n            print('f')\n")

    def test_explicit_return_in_else_before_more_code(self):
        # `else: return None` is followed directly by the handlers, but the handlers
        # continue to code after the try, so that return is explicit and must stay.
        import contextlib
        import io

        src = (
            "class X:\n    def __init__(self, e):\n        self.e = e\n"
            "    def go(self):\n        if self.e:\n            raise self.e\n"
            "def f(x):\n    try:\n        x.go()\n    except ValueError:\n        print('bad')\n"
            "    else:\n        return None\n    print('after')\n"
            "f(X(None))\nf(X(ValueError()))\n")
        out = decompile(src)
        self.assertIn("return None", out)

        def run(code):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exec(code, {"__name__": "__main__"})
            return buf.getvalue()

        self.assertEqual(run(compile(out, "<decompiled>", "exec")), run(compile(src, "<original>", "exec")))

    def test_explicit_return_value_stays_in_the_try(self):
        import contextlib
        import io

        src = (
            "log = []\n"
            "class X:\n    def go(self):\n        log.append('go')\n        return 7\n"
            "def e(x):\n    try:\n        return x.go()\n    finally:\n        log.append('done')\n"
            "print(e(X()), log)\n")
        out = decompile(src)
        self.assertLess(out.index("return x.go()"), out.index("finally:"))

        def run(code):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exec(code, {"__name__": "__main__"})
            return buf.getvalue()

        self.assertEqual(run(compile(out, "<decompiled>", "exec")), run(compile(src, "<original>", "exec")))

    def _assert_same_ast(self, src):
        import ast

        out = decompile(src)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(src)), out)

    def test_return_constant_stays_in_the_try(self):
        # 3.12 returns the constant with RETURN_CONST right after the copy, 3.14 loads
        # it there; only the line numbers tell it from a return after the try.
        self._assert_same_ast("def f():\n    try:\n        print('c')\n        return 1\n    finally:\n        print('c')\n")

    def test_return_after_the_try_stays_after_it(self):
        self._assert_same_ast("def f():\n    try:\n        print('c')\n    finally:\n        print('c')\n    return 1\n")
        self._assert_same_ast("def f(x):\n    try:\n        print('c')\n    finally:\n        print('c')\n    return x\n")

    def test_return_from_a_loop_stays_in_the_try(self):
        # The iterator is dropped between the copy and the return, and the loop's own
        # exit code follows; the try body still ends at the return.
        self._assert_same_ast(
            "def f(xs):\n    for x in xs:\n        try:\n            print('c')\n            return x\n"
            "        finally:\n            print('c')\n")
        self._assert_same_ast(
            "def f(xs):\n    for x in xs:\n        for y in x:\n            try:\n                return y\n"
            "            finally:\n                print('c')\n    print('none')\n")

@unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 11)), "3.10/3.11 have known control-flow failures")
class TestTernaryWithBooleanBranches(unittest.TestCase):
    """Conditional expressions whose branches use `and`/`or` (#115)."""

    SRC = (
        "def g1(a, b, f):\n    print((a or b) if f else (a and b))\n"
        "def g2(a, b, f, xs):\n    out = []\n    for x in xs:\n        r = (a or b) if f else (a and b)\n"
        "        out.append((r, x))\n        a = x\n    return out\n"
        "def g3(a, b, c, f):\n    return (a and b or c) if f else not a\n"
        "def g4(a, b, f):\n    r = a if f else (a or b)\n    return r\n"
        "def h1(a, b, c, f):\n    return (a or b) if f else c\n"
        "def h3(a, b, c, f):\n    x = (a and b or c) if f else a\n    return x\n"
        "def k(a, b, f):\n    x = foo(1, a if f else b)\n    return x\n"
        "def foo(*args):\n    return args\n"
        "for a in (0, 1, ''):\n    for b in (0, 2):\n        for f in (0, 1):\n"
        "            g1(a, b, f)\n            print(g2(a, b, f, [3, 0]), g3(a, b, 5, f), g4(a, b, f),\n"
        "                  h1(a, b, 7, f), h3(a, b, 9, f), k(a, b, f))\n"
    )

    def test_output_compiles(self):
        compile(decompile(self.SRC), "<decompiled>", "exec")

    def test_behavior_is_preserved(self):
        import contextlib
        import io

        def run(code):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exec(code, {"__name__": "__main__"})
            return buf.getvalue()

        out = decompile(self.SRC)
        self.assertEqual(run(compile(out, "<decompiled>", "exec")), run(compile(self.SRC, "<original>", "exec")))

    def test_merged_ternary_is_rendered_as_one_expression(self):
        out = decompile("def g2(a, b, f):\n    r = (a or b) if f else (a and b)\n    print(r)\n")
        self.assertRegex(out, r"r = \(a or b\) if f else \(a and b\)")

    def test_ternary_argument_keeps_earlier_arguments(self):
        out = decompile("def k(a, b, f):\n    x = foo(1, a if f else b)\n    return x\n")
        self.assertNotIn("func(", out)
        self.assertEqual(out.count("foo(1, "), 1 if "if f else" in out else 2)


class TestInlinedBuiltinCalls(unittest.TestCase):
    """`any`/`all`/`tuple` over a generator, which 3.14 inlines behind a guard (#139)."""

    SRC = (
        "def f(xs):\n    return any(x > 1 for x in xs)\n"
        "def g(xs):\n    y = all(x for x in xs if x)\n    t = tuple(x * 2 for x in xs if x != 3)\n"
        "    if any(x for x in xs if not x):\n        print('has falsy', y)\n    return y, t\n"
        "z = all(v for v in range(3))\n"
        "print(f([0, 1]), f([2]), g([1, 2, 3]), g([0, 3]), z)\n"
    )

    @unittest.skipUnless(sys.version_info[:2] == (3, 14), "LOAD_COMMON_CONSTANT table of 3.14")
    def test_common_constant_names_match_cpython(self):
        import opcode

        from pycrefine.decompiler.generic import _COMMON_CONSTANT_NAMES

        table = [getattr(c, "__name__", None) for c in getattr(opcode, "_common_constants", ())]
        self.assertEqual(tuple(table[:len(_COMMON_CONSTANT_NAMES)]), _COMMON_CONSTANT_NAMES)

    def test_calls_are_kept(self):
        out = decompile(self.SRC)
        compile(out, "<decompiled>", "exec")
        for call in ("any((x > 1 for x in xs))", "all((x for x in xs if x))", "all((v for v in range(3)))"):
            self.assertIn(call, out)
        for leak in ("_item", " is 4", " is 3", "True((", "False(("):
            self.assertNotIn(leak, out)

    def test_behavior_is_preserved(self):
        import contextlib
        import io

        def run(code):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exec(code, {"__name__": "__main__"})
            return buf.getvalue()

        out = decompile(self.SRC)
        self.assertEqual(run(compile(out, "<decompiled>", "exec")), run(compile(self.SRC, "<original>", "exec")))

    def test_shadowed_name_still_decompiles(self):
        src = "def h(xs, any):\n    return any(x for x in xs)\n"
        out = decompile(src)
        self.assertIn("return any((x for x in xs))", out)
        original, decompiled = {}, {}
        exec(compile(src, "<original>", "exec"), original)
        exec(compile(out, "<decompiled>", "exec"), decompiled)

        def shadowed(values):
            return ("shadowed", list(values))

        # The call must go to the parameter, not to the builtin.
        self.assertEqual(decompiled["h"]([0, 1], shadowed), original["h"]([0, 1], shadowed))
        self.assertEqual(decompiled["h"]([0, 1], shadowed), ("shadowed", [0, 1]))


def _run_code(code):
    import contextlib
    import io

    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, {"__name__": "__main__"})
    return buf.getvalue()


@unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 has known control-flow failures")
class TestWithBlockExits(unittest.TestCase):
    """return/break/continue inside a with block, and nested with blocks (#142).

    Every exit inlines the context manager's __exit__ call; only the exit itself is
    source code.
    """

    def test_try_statement_that_ends_the_with_body(self):
        # The try's merge label is the with block's inlined exit call.
        src = (
            "class Ctx:\n    def __init__(self, log):\n        self.log = log\n"
            "    def __enter__(self):\n        self.log.append('enter')\n"
            "    def __exit__(self, *a):\n        self.log.append('exit')\n"
            "def boom(v):\n    if v:\n        raise ValueError(v)\n"
            "def a(log, v):\n    with Ctx(log):\n        try:\n            boom(v)\n        except ValueError:\n"
            "            log.append('caught')\n    log.append('after')\n"
            "def d(log, v):\n    with Ctx(log):\n        try:\n            boom(v)\n        except ValueError:\n"
            "            log.append('caught')\n        finally:\n            log.append('fin')\n"
            "def f(log, v):\n    with Ctx(log):\n        with Ctx(log):\n            try:\n                boom(v)\n"
            "            except ValueError:\n                log.append('caught')\n    log.append('after')\n"
            "def g(log, v):\n    with Ctx(log):\n        try:\n            boom(v)\n        except ValueError:\n"
            "            log.append('caught')\n        else:\n            log.append('else')\n"
            "for fn in (a, d, f, g):\n    for v in (0, 1):\n        log = []\n        fn(log, v)\n"
            "        print(fn.__name__, v, log)\n")
        out = decompile(src)
        self.assertEqual(_run_code(compile(out, "<decompiled>", "exec")), _run_code(compile(src, "<original>", "exec")))

    def test_early_continue_before_a_raise_stays_in_the_body(self):
        # The `continue` exit comes before more protected body (`raise`); the body ends
        # with its last protected code, not at that exit.
        src = ("def r(log, xs):\n    for x in xs:\n        with ctx(log):\n            if x:\n"
               "                continue\n            raise ValueError(x)\n    return log\n")
        out = decompile(src)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(src)), out)

    def test_consecutive_with_blocks_with_early_exits(self):
        # Each block's normal exit is its own: a later block's exit, or an early
        # `continue` exit, does not end the earlier block.
        src = (
            "class Ctx:\n    def __init__(self, log, n):\n        self.log, self.n = log, n\n"
            "    def __enter__(self):\n        self.log.append('enter' + self.n)\n"
            "    def __exit__(self, *a):\n        self.log.append('exit' + self.n)\n"
            "def first(log, xs):\n    for x in xs:\n        with Ctx(log, 'a'):\n            if x:\n"
            "                continue\n            log.append('a-body')\n        with Ctx(log, 'b'):\n"
            "            log.append('b-body')\n    return log\n"
            "def second(log, xs):\n    for x in xs:\n        with Ctx(log, 'a'):\n            log.append('a')\n"
            "        with Ctx(log, 'b'):\n            if x:\n                continue\n            log.append('b')\n"
            "    return log\n"
            "print(first([], [0, 1]), second([], [0, 1]))\n")
        out = decompile(src)
        self.assertEqual(_run_code(compile(out, "<decompiled>", "exec")), _run_code(compile(src, "<original>", "exec")))

    def test_branch_selected_callee_is_not_an_exit(self):
        # `(f if c else g)(None, None, None)`: the arguments are a jump target, but a
        # value (the callee) falls into them, which never happens for an exit call.
        out = decompile("def h(c):\n    with ctx():\n        (f if c else g)(None, None, None)\n")
        # The call is kept (the conditional callee itself is #178).
        self.assertIn("None, None, None)", out)

    def test_user_call_with_none_arguments_is_not_an_exit(self):
        # Same tail as an inlined __exit__(None, None, None): LOAD_CONST None x3; CALL; POP_TOP.
        src = (
            "log = []\n"
            "class Ctx:\n    def __enter__(self):\n        log.append('enter')\n"
            "    def __exit__(self, *a):\n        log.append('exit')\n"
            "def f(a, b, c):\n    log.append((a, b, c))\n"
            "def g():\n    with Ctx():\n        f(None, None, None)\n        log.append('body')\n    log.append('after')\n"
            "g()\nprint(log)\n")
        out = decompile(src)
        self.assertIn("f(None, None, None)", out)
        self.assertEqual(_run_code(compile(out, "<decompiled>", "exec")), _run_code(compile(src, "<original>", "exec")))

    SRC = 'import contextlib, io\n\nclass CM:\n    def __init__(self, log, name):\n        self.log, self.name = log, name\n    def __enter__(self):\n        self.log.append("enter " + self.name)\n        return self\n    def __exit__(self, *exc):\n        self.log.append("exit " + self.name)\n        return False\n\ndef r1(log):\n    with CM(log, "a") as a:\n        return a.name\n\ndef r2(log, xs):\n    out = []\n    for x in xs:\n        with CM(log, str(x)):\n            if x == 1:\n                continue\n            if x == 3:\n                break\n            out.append(x)\n    return out\n\ndef r3(log):\n    with CM(log, "a"), CM(log, "b") as b:\n        return b.name * 2\n\ndef r4(log):\n    i = 0\n    while i < 5:\n        i += 1\n        with CM(log, "w"):\n            if i == 2:\n                continue\n            if i == 4:\n                return i\n    return -1\n\ndef r6(log):\n    with CM(log, "x"):\n        pass\n\n' + (
        "for name in ('r1', 'r2', 'r3', 'r4', 'r6'):\n"
        "    log = []\n"
        "    fn = globals()[name]\n"
        "    if name == 'r2':\n"
        "        res = fn(log, [0, 1, 2, 3, 4])\n"
        "    else:\n"
        "        res = fn(log)\n"
        "    print(name, res, log)\n"
    )

    def test_output_has_no_exit_calls(self):
        out = decompile(self.SRC)
        compile(out, "<decompiled>", "exec")
        for leak in ("None(None", "except:", "_exc_match", "_with_info", "(None, None, None)"):
            self.assertNotIn(leak, out)
        assert_contains(out, "return a.name", "continue", "return b.name * 2", "return i")

    def test_behavior_is_preserved(self):
        import contextlib
        import io

        def run(code):
            buf = io.StringIO()
            with contextlib.redirect_stdout(buf):
                exec(code, {"__name__": "__main__"})
            return buf.getvalue()

        out = decompile(self.SRC)
        self.assertEqual(run(compile(out, "<decompiled>", "exec")), run(compile(self.SRC, "<original>", "exec")))

    def test_return_value_stays_inside_the_with(self):
        out = decompile("def f(p):\n    with open(p) as fh:\n        return fh.read()\n")
        lines = out.splitlines()
        with_line = next(i for i, ln in enumerate(lines) if ln.strip().startswith("with open(p)"))
        ret_line = next(i for i, ln in enumerate(lines) if "return fh.read()" in ln)
        indent = lambda ln: len(ln) - len(ln.lstrip())
        self.assertGreater(ret_line, with_line)
        self.assertGreater(indent(lines[ret_line]), indent(lines[with_line]))


@unittest.skipIf(sys.version_info[:2] == (3, 10), "3.10 has known control-flow failures")
class TestSequentialWithBlocksInLoop(unittest.TestCase):
    """Two `with` blocks and an `if` in a loop body keep their nesting (#97)."""

    SRC = (
        "import tempfile\n"
        "def run(cases, func):\n"
        "    for name, src in cases.items():\n"
        "        with tempfile.NamedTemporaryFile(suffix='.py', mode='w', delete=False) as f:\n"
        "            f.write(src)\n"
        "            sp = f.name\n"
        "        pp = sp + 'c'\n"
        "        with open(pp, 'rb') as f:\n"
        "            data = f.read()\n"
        "        if func:\n"
        "            print(name, data)\n"
        "        print()\n"
    )

    def test_same_ast_as_the_source(self):
        out = decompile(self.SRC)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(self.SRC)))

    def test_with_blocks_in_sequence_and_exits(self):
        src = (
            "def g(paths, log):\n"
            "    for p in paths:\n"
            "        with open(p) as a:\n"
            "            log.append(a.read())\n"
            "        with open(p) as b:\n"
            "            if b:\n"
            "                continue\n"
            "            log.append(1)\n"
            "        log.append(2)\n"
            "    return log\n"
        )
        out = decompile(src)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(src)))


class TestCollectionDisplays(unittest.TestCase):
    """List, set and dict displays built from constants and starred parts."""

    SRC = (
        "def f(x, ys, d):\n"
        "    a = [1, 2, 3]\n"
        "    b = [x, 1, 2, 3]\n"
        "    c = [1, 2, 3, x]\n"
        "    e = [*ys, 1, 2, 3]\n"
        "    g = [1, 2, 3, *ys]\n"
        "    m = [*ys, 1, *ys]\n"
        "    h = {1, 2, 3}\n"
        "    i = {x, 1, 2, 3}\n"
        "    j = {'a': 1, 'b': 2, **d}\n"
        "    k = {**d, 'a': 1}\n"
        "    return a, b, c, e, g, h, i, j, k, m\n"
    )

    def test_displays_are_not_expanded(self):
        out = decompile(self.SRC)
        assert_contains(
            out,
            "a = [1, 2, 3]", "b = [x, 1, 2, 3]", "c = [1, 2, 3, x]",
            "e = [*ys, 1, 2, 3]", "g = [1, 2, 3, *ys]", "h = {1, 2, 3}",
            "i = {x, 1, 2, 3}", "j = {'a': 1, 'b': 2, **d}", "k = {**d, 'a': 1}",
        )
        self.assertNotIn("**{", out)
        self.assertNotIn("*{", out)
        self.assertNotIn("*[", out)

    def test_behavior_is_preserved(self):
        ns = {}
        exec(decompile(self.SRC), ns)
        ref = {}
        exec(self.SRC, ref)
        args = (7, [8, 9], {"z": 1})
        self.assertEqual(ns["f"](*args), ref["f"](*args))

    def test_parenthesized_items_keep_their_parentheses(self):
        # The source of a parenthesized item leaves its parentheses out; folding an
        # item into a display must not change what the item means.
        src = (
            "def f(a, b, d):\n"
            "    p = {**(d or {'q': 1}), 'k': 1}\n"
            "    q = {'k': (y := 1), 'j': 2}\n"
            "    r = [(a or b), 1, 2, *(a, b)]\n"
            "    s = [*(d and 'xy'), 1]\n"
            "    t = [1, 2, *(d or 'z')]\n"
            "    return p, q, r, s, t, y\n"
        )
        ns = {}
        exec(decompile(src), ns)
        ref = {}
        exec(src, ref)
        for args in ((0, 3, {}), (1, 2, {"z": 1})):
            with self.subTest(args=args):
                self.assertEqual(ns["f"](*args), ref["f"](*args))


class TestTupleAssignment(unittest.TestCase):
    """A swap or rotation by tuple assignment keeps its simultaneity (#168)."""

    SRC = (
        "class P:\n"
        "    def __init__(self):\n"
        "        self.x, self.y = 1, 2\n"
        "def sw(a, b):\n    a, b = b, a\n    return a, b\n"
        "def rot3(a, b, c):\n    a, b, c = c, a, b\n    return a, b, c\n"
        "def rot3b(a, b, c):\n    a, b, c = b, c, a\n    return a, b, c\n"
        "def idx(x, i, j):\n    x[i], x[j] = x[j], x[i]\n    return x\n"
        "def attrs(o):\n    o.x, o.y = o.y, o.x\n    return o.x, o.y\n"
        "def mixed(a, x):\n    a, x[0] = x[0], a\n    return a, x\n"
        "def fib(n):\n    a, b = 0, 1\n    for _ in range(n):\n        a, b = b, a + b\n    return a\n"
        "def gcd(a, b):\n    while b:\n        a, b = b, a % b\n    return a\n"
        "def aug(x, i):\n    x[i] += 1\n    return x\n"
        "def augattr(o):\n    o.x += 5\n    o.y *= 2\n    return o.x, o.y\n"
        "def calls(f, g):\n    p, q = f(), g()\n    return p, q\n"
        "def results():\n"
        "    o = P()\n"
        "    return [sw(1, 2), rot3(1, 2, 3), rot3b(1, 2, 3), idx([1, 2, 3], 0, 2), attrs(o),\n"
        "            mixed(1, [5, 6]), fib(10), gcd(12, 18), aug([1, 2], 1), augattr(o), calls(lambda: 1, lambda: 2)]\n"
    )

    def test_behavior_is_preserved(self):
        out = decompile(self.SRC)
        ns, ref = {}, {}
        exec(out, ns)
        exec(self.SRC, ref)
        self.assertEqual(ns["results"](), ref["results"](), out)

    def test_swaps_are_one_statement(self):
        out = decompile(self.SRC)
        assert_contains(out, "a, b = b, a", "x[i], x[j] = x[j], x[i]", "a, b = b, a + b", "a, b = b, a % b")
        assert_contains(out, "x[i] += 1", "o.x += 5", "o.y *= 2")
