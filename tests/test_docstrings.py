"""Docstrings of modules, classes and functions round-trip exactly (#166)."""
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


@unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 11)), "3.10/3.11 have known control-flow failures")
class TestDocstrings(unittest.TestCase):

    def assert_same_docs(self, src, names):
        out = decompile(src)
        probe = "".join(f"print(repr({n}.__doc__))\n" for n in names)
        self.assertEqual(_run(compile(out + probe, "<decompiled>", "exec")),
                         _run(compile(src + probe, "<original>", "exec")), out)
        return out

    def assert_same_ast(self, src):
        out = decompile(src)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(src)), out)
        return out

    def test_function_and_method_docstrings(self):
        self.assert_same_ast(
            'def f():\n    """Func doc."""\n    return 1\n\nclass K:\n    """Class doc."""\n\n'
            '    def m(self):\n        """Method doc."""\n        return 2\n')

    def test_module_docstring(self):
        out = self.assert_same_ast('"""Module doc."""\nimport os\n')
        self.assertTrue(out.startswith('"""Module doc."""'))

    def test_multiline_docstring(self):
        src = ('def f():\n    """Summary.\n\n    Details, indented:\n        - item\n    """\n    return 1\n\n'
               'class K:\n    """Class summary.\n\n    More.\n    """\n    x = 1\n')
        self.assert_same_docs(src, ["f", "K"])
        # 3.13+ stores the text dedented; the indentation is restored from the body.
        self.assert_same_ast(src)

    def test_docstring_only_bodies(self):
        self.assert_same_ast('def f():\n    """Only."""\n\nclass K:\n    """Only."""\n')

    def test_quotes_backslashes_and_tabs(self):
        self.assert_same_docs(
            "def a():\n    '''Has \"\"\" inside.'''\n"
            "def b():\n    \"Tab\\there, backslash \\\\ and quote\\\" end\"\n"
            "def c():\n    \"\"\"Ends in a quote: 'x'\"\"\"\n"
            "def d():\n    \"\"\n"
            "def e():\n    \"\"\"Ünïcode — ok\"\"\"\n",
            ["a", "b", "c", "d", "e"])

    def test_code_like_text_is_not_rewritten(self):
        self.assert_same_docs(
            '"""Usage:\n\nimport zlib\nimport abc\nfrom x import (a,\n    b)\n\n\nend   \n"""\n'
            'def f():\n    """\n    def g():  pass\n    x  =  1 ;\n    """\n',
            ["f"])
        out = decompile('"""A\n\n\nB"""\n')
        self.assertIn("A\n\n\nB", out)

    def test_no_docstring_is_invented(self):
        out = decompile("def f():\n    x = 'not a doc'\n    return x\n\ndef g():\n    return 'a'\n")
        self.assertNotIn('"""', out)
        self.assertIn("x = 'not a doc'", out)
        self.assertIn("return 'a'", out)

    def test_constant_equal_to_the_docstring_is_still_loaded(self):
        self.assert_same_ast('def f():\n    """X"""\n    return "X"\n\nprint(f())\n')

    def test_later_doc_assignment_in_a_class_is_kept(self):
        out = self.assert_same_docs('class K:\n    """First."""\n    __doc__ = "Second."\n', ["K"])
        self.assertIn("__doc__ = 'Second.'", out)

    def test_declarations_follow_the_docstring(self):
        out = self.assert_same_ast('def f(x):\n    """Doc."""\n    global Z\n    Z = x\n')
        self.assertLess(out.index('"""Doc."""'), out.index("global Z"))


if __name__ == "__main__":
    unittest.main()
