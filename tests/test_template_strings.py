"""Template strings (t-strings) on 3.14 (#269).

`t'Hello {name!r:>5}!'` compiles to BUILD_INTERPOLATION and BUILD_TEMPLATE, which had no handler: the decompiler
reported an unsupported opcode and the expression was lost.
"""
import ast
import sys
import unittest

from .test_helpers import decompile

_ON_3_14 = sys.version_info >= (3, 14)


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _shape(template):
    """What a Template is made of: its strings and, for each field, its value, source text, conversion and spec."""
    if isinstance(template, (list, tuple)):
        return tuple(_shape(item) for item in template)
    if not hasattr(template, "interpolations"):
        return template
    return (template.strings,
            tuple((i.value, i.expression, i.conversion, i.format_spec) for i in template.interpolations))


@unittest.skipUnless(_ON_3_14, "t-strings are new in 3.14")
class TestTemplateStrings(unittest.TestCase):

    def assert_same_template(self, body, call):
        out = decompile(body)
        ast.parse(out)
        original, rebuilt = _namespace(body), _namespace(out)
        self.assertEqual(_shape(eval(call, rebuilt)), _shape(eval(call, original)), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_the_reproduction(self):
        out = self.assert_same_template("def f(name):\n    return t'Hello {name!r:>5}!'\n", "f('x')")
        self.assertIn("{name!r:>5}", out)

    def test_plain_text(self):
        self.assert_same_template("def f():\n    return t'plain'\n", "f()")

    def test_a_lone_field(self):
        self.assert_same_template("def f(a):\n    return t'{a}'\n", "f(3)")

    def test_two_fields_and_a_nested_spec(self):
        self.assert_same_template("def f(a, b):\n    return t'{a}+{b:>{a}}'\n", "f(4, 'x')")

    def test_each_conversion(self):
        self.assert_same_template("def f(a):\n    return t'{a!s} {a!r} {a!a}'\n", "f('é')")

    def test_an_expression_as_written(self):
        out = self.assert_same_template("def f(a, b):\n    return t'{a+b} {b  *  2}'\n", "f(1, 2)")
        self.assertIn("{a+b}", out)

    def test_a_debug_field(self):
        self.assert_same_template("def f(x):\n    return t'{x=}'\n", "f(7)")

    def test_braces_and_quotes_in_the_text(self):
        self.assert_same_template("def f(a):\n    return t'{{literal}} \"q\" it\\'s {a}'\n", "f(1)")

    def test_a_subscript_and_a_set(self):
        self.assert_same_template("def f(d):\n    return t'{d[\"k\"]}', t'{ {1, 2} }'\n", "f({'k': 1})")

    def test_as_an_argument_and_in_a_container(self):
        self.assert_same_template("def f(a):\n    return [t'x{a}', len(t'{a}{a}'.strings)]\n", "f(1)")

    def test_a_conditional_expression_in_a_field(self):
        out = self.assert_same_template("def f(a, b, flag):\n    return t'{a if flag else b}'\n", "f(1, 2, True)")
        self.assertIn("{a if flag else b}", out)
        self.assert_same_template("def f(a, b, flag):\n    return t'{a if flag else b}'\n", "f(1, 2, False)")

    def test_a_conditional_expression_with_a_conversion_and_a_spec(self):
        self.assert_same_template(
            "def f(a, b, flag):\n    return t'pre {a if flag else b!r:>5} post {a}'\n", "f('x', 'y', False)")

    def test_an_f_string_next_to_a_t_string(self):
        self.assert_same_template("def f(a):\n    return f'{a!r:>4}', t'{a!r:>4}'\n", "f(1)")


if __name__ == "__main__":
    unittest.main()
