"""f-string fields: a lone field, conversions, format specs and nested specs (#200, #157)."""
import ast
import contextlib
import io
import sys
import unittest

from .test_helpers import decompile


def _run(code):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, {"__name__": "__main__"})
    return buf.getvalue()


class TestFStringFields(unittest.TestCase):

    def assert_same_ast(self, src):
        """The decompiled source parses to the same tree as the original."""
        out = decompile(src)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(src)), out)
        return out

    def assert_same_behavior(self, src):
        out = decompile(src)
        self.assertEqual(_run(compile(out, "<decompiled>", "exec")), _run(compile(src, "<original>", "exec")), out)
        return out

    def test_lone_field(self):
        """A string that is one field is that field's f-string, not a set display (#200)."""
        self.assert_same_ast("def f(x):\n    return f'{x}'\n")

    def test_lone_field_with_each_conversion(self):
        for conversion in "sra":
            with self.subTest(conversion=conversion):
                self.assert_same_ast(f"def f(x):\n    return f'{{x!{conversion}}}'\n")

    def test_lone_field_with_format_spec(self):
        self.assert_same_ast("def f(x):\n    return f'{x:>5}'\n")
        self.assert_same_ast("def f(x):\n    return f'{x:.2f}'\n")

    def test_lone_field_with_conversion_and_format_spec(self):
        self.assert_same_ast("def f(x):\n    return f'{x!r:>10}'\n")

    def test_lone_field_as_argument_and_operand(self):
        self.assert_same_ast("def f(x, g):\n    return g(f'{x!r}', 1)\n")
        self.assert_same_ast("def f(x):\n    return f'{x.y!a}' + f'{x}'\n")

    def test_lone_field_assigned(self):
        self.assert_same_ast("def f(x):\n    y = f'{x!s:>6}'\n    return y\n")

    def test_conversion_and_format_spec_between_text(self):
        """3.13 and later write these as CONVERT_VALUE and FORMAT_WITH_SPEC (#157)."""
        self.assert_same_ast("def f(n):\n    return f'{n!r:>10} {n:.2f}'\n")
        self.assert_same_ast("def f(n):\n    return f'[{n!r}]'\n")

    def test_nested_format_spec(self):
        """A format spec can hold fields of its own."""
        self.assert_same_ast("def f(x, w):\n    return f'{x:{w}}'\n")
        self.assert_same_ast("def f(x, w, p):\n    return f'{x:{w}.{p}}'\n")
        self.assert_same_ast("def f(x, w):\n    return f'{x!r:{w}}|{x:*^{w}}'\n")

    def test_self_documenting_field(self):
        """f'{x=}' is the text 'x=' and the field with !r."""
        self.assert_same_ast("def f(x):\n    return f'{x=}'\n")

    def test_expression_in_the_field(self):
        self.assert_same_ast("def f(n):\n    return f'{n + 1!r}' f'x{n * 2:04d}y'\n")

    def test_field_that_starts_with_a_brace(self):
        """`{ {n: 1}[n] }`: a field written `{{...` would be read as an escaped brace."""
        self.assert_same_behavior("n = 3\nprint(f'{ {n: 1}[n] }')\nprint(f'{ {1, 2, 3}!r}')\n")

    def test_doubled_braces_around_a_field(self):
        self.assert_same_ast("def f(n):\n    return f'{{{n}}}'\n")

    def test_field_holding_both_quotes(self):
        """A lone field whose text has both quote characters needs a triple-quoted f-string."""
        out = self.assert_same_behavior(
            "names = ['ann', 'bob']\n"
            "print(f\"{', '.join(f'<{n}>' for n in names)}\")\n"
            + ("print(f\"{'nested \\\"quotes\\\" ok'}\")\n" if sys.version_info >= (3, 12) else "")
        )
        self.assertGreaterEqual(ast.dump(ast.parse(out)).count("JoinedStr"), 2, out)

    def test_field_whose_text_has_double_quotes_only(self):
        self.assert_same_behavior("d = {'k': 3.5}\nprint(f'{d[\"k\"]!r:>8}')\n")

    def test_composite_f_string_with_a_field_holding_a_double_quote(self):
        """Before 3.12 a field cannot reuse the f-string's quote, so the text around it needs another."""
        out = self.assert_same_behavior(
            "d = {'say \"hi\"': 7}\n"
            "n = 2\n"
            "print(f'''a{d.get('say \"hi\"')}b''')\n"
            "print(f'''{n}-{d.get('say \"hi\"')!r}-{n:>3}''')\n"
            "print(f'''x{d.get('say \"hi\"')}y{n}''')\n"
        )
        self.assertGreaterEqual(ast.dump(ast.parse(out)).count("JoinedStr"), 3, out)

    def test_composite_f_string_without_a_double_quote_keeps_its_quote(self):
        out = decompile("def f(d, n):\n    return f'a{d[\"k\"]}b{n}'\n")
        self.assertIn('f"a{d[\'k\']}b{n}"', out)


class TestFStringFieldBuilder(unittest.TestCase):
    """The pieces the handlers build the fields from."""

    @classmethod
    def setUpClass(cls):
        from pycrefine import Decompiler39
        cls.dec = Decompiler39(compile("pass", "<t>", "exec"), target_version=(3, 9))

    def test_field_text_is_the_whole_f_string_and_piece_is_the_field(self):
        field = self.dec._make_fstring_field("x", "!r", "'>5'")
        self.assertEqual(str(field), 'f"{x!r:>5}"')
        self.assertEqual(field.piece, "{x!r:>5}")

    def test_spec_of_one_nested_field(self):
        nested = self.dec._make_fstring_field("w")
        self.assertEqual(self.dec._make_fstring_field("x", "", nested).piece, "{x:{w}}")

    def test_unknown_spec_is_its_own_field(self):
        self.assertEqual(self.dec._make_fstring_field("x", "", "(a + b)").piece, "{x:{(a + b)}}")

    def test_quote_follows_the_text(self):
        self.assertEqual(str(self.dec._make_fstring_field("d['k']")), 'f"{d[\'k\']}"')
        self.assertEqual(str(self.dec._make_fstring_field('d["k"]')), "f'{d[\"k\"]}'")
        both = str(self.dec._make_fstring_field("f(\"a\", 'b')"))
        self.assertTrue(both.startswith('f"""') and both.endswith('"""'), both)


if __name__ == "__main__":
    unittest.main()
