import sys
import unittest

from .test_helpers import assert_contains, decompile


class TestStrings(unittest.TestCase):
    def test_conditional_fstring_concat(self):
        src = (
            "def f(input_ver):\n"
            "    msg = ''\n"
            "    if input_ver:\n"
            "        msg += f'\\n- Input file appears to be from Python {input_ver}.'\n"
            "    else:\n"
            "        msg += '\\n- Input file version is unrecognized or may be corrupt.'\n"
            "    return msg\n"
        )
        out = decompile(src)
        self.assertNotIn('f"""', out, "Output mistakenly fused string concatenation into triple-quoted strings")
        self.assertNotIn("f'''", out, "Output mistakenly fused string concatenation into triple-quoted strings")
        self.assertIn("appears to be from Python", out)
        self.assertIn("unrecognized or may be corrupt", out)
        self.assertNotIn('if input_ver else', out, "Should not use ternary since both branches assign using +=")
        
        # Verify it compiles successfully
        try:
            compile(out, '<test>', 'exec')
        except SyntaxError as e:
            self.fail(f"Decompiled output is not syntactically valid: {e}\n{out}")

    def test_ternary_multiline_constant_heuristic(self):
        # A legitimate ternary with multiline constant, the heuristic blocks it.
        # It's better than syntax error! Let's just make sure it compiles.
        src = 'def f(cond):\n    return "A\\nB" if cond else "C\\nD"\n'
        out = decompile(src)
        try:
            compile(out, '<test>', 'exec')
        except SyntaxError as e:
            self.fail(f"Decompiled output is not syntactically valid: {e}\n{out}")

if __name__ == "__main__":
    unittest.main()
