import unittest
import pycrefine

def decompile(src: str) -> str:
    import tempfile, py_compile, os
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(src)
        sp = f.name
    pp = sp + "c"
    py_compile.compile(sp, cfile=pp, doraise=True)
    decompiler = pycrefine.get_decompiler(pp)
    out = decompiler.decompile()
    os.unlink(sp)
    os.unlink(pp)
    return out

class TestLambdaDecompilation(unittest.TestCase):
    def test_lambda_in_re_sub_not_leaked(self):
        # This is the negative case where it used to produce `def <lambda>`
        src = """import re\ndef fix(text):\n    text = re.sub(\n        r"([ \\t]*)([A-Za-z_][A-Za-z0-9_.]*)\\s*=\\s*\\('(func|class)',[^\\n]*",\n        lambda m: (\n            f"{m.group(1)}# <{'genexpr/lambda' if m.group(3) == 'func' else 'class'}"\n            f" \\u2014 not reconstructable>\\n{m.group(1)}{m.group(2)} = None"\n        ),\n        text,\n    )\n"""
        out = decompile(src)
        self.assertNotIn("def <lambda>", out)
        self.assertNotIn("('func'", out)
        self.assertIn("lambda m:", out)
        
    def test_lambda_simple(self):
        src = "a = lambda x: x + 1\n"
        out = decompile(src)
        self.assertNotIn("def <lambda>", out)
        self.assertNotIn("('func'", out)
        self.assertIn("lambda x: x + 1", out)

    def test_lambda_no_args(self):
        src = "a = lambda: 42\n"
        out = decompile(src)
        self.assertNotIn("def <lambda>", out)
        self.assertNotIn("('func'", out)
        self.assertIn("lambda: 42", out)

    def test_lambda_multiline_string_return(self):
        src = "a = lambda m: (\n    f'line1'\n    f'line2'\n)\n"
        out = decompile(src)
        self.assertNotIn("def <lambda>", out)
        self.assertNotIn("('func'", out)
        self.assertIn("lambda m:", out)

    def test_lambda_immediately_called(self):
        src = "a = (lambda x: x + 1)(2)\n"
        out = decompile(src)
        self.assertNotIn("def <lambda>", out)
        self.assertNotIn("('func'", out)
        self.assertIn("(lambda x: x + 1)", out)

    def test_lambda_in_list(self):
        src = "a = [lambda x: x + 1, lambda y: y - 1]\n"
        out = decompile(src)
        self.assertNotIn("def <lambda>", out)
        self.assertNotIn("('func'", out)
        self.assertIn("lambda x: x + 1", out)
        self.assertIn("lambda y: y - 1", out)

    def test_lambda_attribute_access(self):
        src = "a = (lambda x: x + 1).__name__\n"
        out = decompile(src)
        self.assertNotIn("def <lambda>", out)
        self.assertNotIn("('func'", out)
        self.assertIn("(lambda x: x + 1)", out)
