"""Default parameter values (positional and keyword-only), lambdas, and assert (#157, #203)."""
import ast
import contextlib
import io
import unittest

from pycrefine import Decompiler39

from .test_helpers import decompile


def _run(code):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, {"__name__": "__main__"})
    return buf.getvalue()


class TestParameterDefaults(unittest.TestCase):

    def assert_same_ast(self, src):
        out = decompile(src)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(src)), out)
        return out

    def assert_same_behavior(self, src):
        out = decompile(src)
        self.assertEqual(_run(compile(out, "<decompiled>", "exec")), _run(compile(src, "<original>", "exec")), out)
        return out

    def test_keyword_only_defaults(self):
        """3.14 attaches them with SET_FUNCTION_ATTRIBUTE 2, which was ignored (#157)."""
        self.assert_same_ast("def g(a, *args, b=2, **kw):\n    return a, b\n")
        self.assert_same_ast("def g(a, *, b=2):\n    return a, b\n")

    def test_keyword_only_with_and_without_defaults(self):
        self.assert_same_ast("def g(a, b=1, *, c=3, d):\n    return a, b, c, d\n")
        self.assert_same_ast("def g(*, x, y=2, z):\n    return x, y, z\n")

    def test_positional_and_keyword_only_defaults_together(self):
        self.assert_same_ast("def g(a=1, b=2, *rest, c=3, d=4, **kw):\n    return a, b, rest, c, d, kw\n")

    def test_positional_only_parameters_with_defaults(self):
        self.assert_same_ast("def g(a, b=(1, 2), /, c=(3, 4), *args, d=(5, 6), **kw):\n    return a, b, c, args, d, kw\n")

    def test_defaults_that_hold_commas(self):
        """The values are read as expressions, so a comma inside one is not a separator."""
        self.assert_same_ast("def k(*, x=(1, 2), y={'a': [1, 2]}, z='s,t'):\n    return x, y, z\n")
        self.assert_same_ast("def s(items, sep=', ', end=('a,b', [1, 2]), *, fmt={'x': (1, 2)}):\n    return items\n")
        self.assert_same_ast("def f(a=f(1, 2), b=max(3, 4)):\n    return a, b\n")

    def test_defaults_in_methods(self):
        self.assert_same_ast("class C:\n    def m(self, x=5, *, y=6):\n        return x, y\n")

    def test_lambda_defaults(self):
        self.assert_same_ast("lam = lambda x, n=3, *, m=4: x + n + m\n")
        self.assert_same_ast("lam = lambda *, q=7: q\n")
        self.assert_same_ast("lam = lambda a=(1, 2), *, b={'k': (3, 4)}: (a, b)\n")

    def test_lambda_defaults_in_a_comprehension(self):
        """3.13 and later lost them there (#203)."""
        self.assert_same_behavior("fs = [lambda x, n=n: x + n for n in range(3)]\nprint([f(10) for f in fs])\n")

    def test_nested_function_defaults_in_a_comprehension(self):
        self.assert_same_behavior(
            "def adders():\n"
            "    return [lambda x, n=n: x + n for n in range(3)]\n"
            "print([f(10) for f in adders()])\n"
        )

    def test_default_that_is_a_lambda(self):
        self.assert_same_behavior("def f(key=lambda v: v * 2, *, other=lambda v, w=1: v + w):\n    return key(3), other(4)\nprint(f())\n")


class TestAssert(unittest.TestCase):

    def raised(self, source, *args, **kwargs):
        """What calling `g` raises, or its result, for the original and the decompiled source."""
        results = []
        for code in (source, decompile(source)):
            namespace = {}
            exec(compile(code, "<t>", "exec"), namespace)
            try:
                results.append(("ok", namespace["g"](*args, **kwargs)))
            except AssertionError as exc:
                results.append(("raised", exc.args))
        return results

    def assert_same_outcome(self, source, *args, **kwargs):
        original, decompiled = self.raised(source, *args, **kwargs)
        self.assertEqual(decompiled, original, decompile(source))
        return original

    def test_assert_with_a_message_raises_assertion_error(self):
        """3.14 loads AssertionError with LOAD_COMMON_CONSTANT 0, which was printed as `0(...)` (#157)."""
        src = "def g(b):\n    assert b, 'msg'\n    return b\n"
        self.assertEqual(self.assert_same_outcome(src, 1), ("ok", 1))
        self.assertEqual(self.assert_same_outcome(src, 0), ("raised", ("msg",)))
        self.assertNotIn("0(", decompile(src))

    def test_assert_without_a_message(self):
        src = "def g(b):\n    assert b\n    return b\n"
        self.assertEqual(self.assert_same_outcome(src, 0), ("raised", ()))
        self.assertNotIn("0(", decompile(src))

    def test_assert_in_a_function_with_keyword_only_defaults(self):
        src = "def g(a, *args, b=2, **kw):\n    assert b, 'msg'\n    return a, b\n"
        self.assertEqual(self.assert_same_outcome(src, 1), ("ok", (1, 2)))
        self.assertEqual(self.assert_same_outcome(src, 1, b=0), ("raised", ("msg",)))


class TestAddParameterDefaults(unittest.TestCase):
    """`_add_parameter_defaults` writes the values into the signature text and leaves the rest alone."""

    @classmethod
    def setUpClass(cls):
        cls.dec = Decompiler39(compile("pass", "<t>", "exec"), target_version=(3, 9))

    def add(self, func_val, defaults, keyword_only=False):
        return self.dec._add_parameter_defaults(func_val, defaults, keyword_only)

    def test_positional_defaults_go_to_the_last_parameters(self):
        result = self.add(("func", "def f(a, b, c):\n    return 1\n"), "(10, 'x')")
        self.assertEqual(result, ("func", "def f(a, b=10, c='x'):\n    return 1\n"))

    def test_keyword_only_defaults_are_matched_by_name(self):
        result = self.add(("func", "def f(a, *, b, c):\n    pass\n"), "{'c': (1, 2)}", keyword_only=True)
        self.assertEqual(result, ("func", "def f(a, *, b, c=(1, 2)):\n    pass\n"))

    def test_the_body_is_not_touched(self):
        body = "    x = 'def g(a):\\n'\n    return x\n"
        result = self.add(("func", "def f(a):\n" + body), "(1,)")
        self.assertEqual(result, ("func", "def f(a=1):\n" + body))

    def test_a_lambda_is_a_text(self):
        self.assertEqual(self.add("(lambda x, n: x + n)", "(3,)"), "(lambda x, n=3: x + n)")
        self.assertEqual(self.add("lambda *, q: q", "{'q': 7}", keyword_only=True), "lambda *, q=7: q")

    def test_a_lambda_def_with_the_compiler_name(self):
        result = self.add(("func", "def <lambda>(x, n):\n    return x + n\n"), "(3,)")
        self.assertEqual(result, ("func", "def <lambda>(x, n=3):\n    return x + n\n"))

    def test_a_default_that_runs_over_several_lines(self):
        """The signature is the shortest run of lines that is a complete `def` line."""
        result = self.add(("func", "def f(a, b, c):\n    return 1\n"), "((1,\n 2),)")
        self.assertEqual(result, ("func", "def f(a, b, c=(1,\n 2)):\n    return 1\n"))
        spread = self.add(("func", "def f(a,\n      b, c, *, d):\n    pass\n"), "{'d': (3,\n 4)}", keyword_only=True)
        self.assertEqual(spread, ("func", "def f(a,\n      b, c, *, d=(3,\n 4)):\n    pass\n"))
        self.assertEqual(self.add("(lambda x,\n  y: x)", "(\"a\\nb\",)"), "(lambda x,\n  y=\"a\\nb\": x)")

    def test_compiler_parameter_names_are_accepted(self):
        result = self.add(("func", "def <genexpr>(.0, k):\n    pass\n"), "(1,)")
        self.assertEqual(result, ("func", "def <genexpr>(.0, k=1):\n    pass\n"))

    def test_none_when_the_defaults_are_not_the_display_expected(self):
        self.assertIsNone(self.add(("func", "def f(a):\n    pass\n"), "?"))
        self.assertIsNone(self.add(("func", "def f(a):\n    pass\n"), "{'a': 1}"))
        self.assertIsNone(self.add(("func", "def f(a):\n    pass\n"), "(1,)", keyword_only=True))

    def test_none_when_there_are_more_defaults_than_parameters(self):
        self.assertIsNone(self.add(("func", "def f(a):\n    pass\n"), "(1, 2)"))

    def test_none_for_something_that_is_not_a_function(self):
        self.assertIsNone(self.add("x + 1", "(1,)"))
        self.assertIsNone(self.add(("func", "class C:\n    pass\n"), "(1,)"))


if __name__ == "__main__":
    unittest.main()
