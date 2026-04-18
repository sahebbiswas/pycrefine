import unittest
import sys
import re
from .test_helpers import decompile, assert_contains

class TestRegressionsApril(unittest.TestCase):
    """
    Regression tests for issues reported in April 2026:
    - Ternary f-strings prefixes
    - Compound condition f-string variables
    - Complex f-string garbling
    - Dict integer keys stringification
    - Loop unpacking with 3.14 combined opcodes
    """
    
    @classmethod
    def setUpClass(cls):
        if sys.version_info < (3, 11):
            raise unittest.SkipTest("Bytecode-dependent decompilation tests only run on Python 3.11+")


    def test_ternary_fstring(self):
        # Issue 1: f"lambda {params}: {ret_expr}" if params else f"lambda: {ret_expr}"
        src = 'def f(params, ret_expr):\n    return f"lambda {params}: {ret_expr}" if params else f"lambda: {ret_expr}"\n'
        out = decompile(src)
        # Check that f-string prefixes are preserved, whether as ternary or if-else
        self.assertIn('f"lambda ', out)
        self.assertIn('f"lambda: ', out)
        self.assertIn('{params}: {ret_expr}"', out)

    def test_compound_cond_fstring(self):
        # Issue 2: cond_str = f"{raw_expr} is None" if is_or_jump else f"{raw_expr} is not None"
        src = 'def f(raw_expr, is_or_jump):\n    cond_str = f"{raw_expr} is None" if is_or_jump else f"{raw_expr} is not None"\n    return cond_str\n'
        out = decompile(src)
        # Verify raw_expr interpolation is present
        self.assertIn('{raw_expr} is None"', out)
        self.assertIn('{raw_expr} is not None"', out)

    def test_complex_fstring(self):
        # Issue 3: f"({expr})({', '.join(str(a) for a in args)})"
        # Using a raw string or triple quotes to avoid quote confusion in the test source
        src = 'def f(expr, args):\n    return f"({expr})({\', \'.join(str(a) for a in args)})"\n'
        out = decompile(src)
        # Verify parentheses and join structure
        self.assertIn('f"({expr})({', out)
        self.assertIn('join(', out)
        self.assertNotIn(")(, '.join", out)

    def test_dict_int_keys(self):
        # Issue 4: {13: '+=', 14: '&='}
        src = 'def f():\n    d = {13: "+=", 14: "&=", 15: "//="}\n    return d\n'
        out = decompile(src)
        # Check for non-quoted integer keys (13:)
        self.assertTrue(re.search(r'\b13: ', out), f"Integer key 13 not found in:\n{out}")
        self.assertTrue(re.search(r'\b14: ', out), f"Integer key 14 not found in:\n{out}")

    def test_loop_unpacking_combined_ops(self):
        # Issue 5: for target_off, count in instrs:
        src = 'def f(instrs):\n    res = []\n    for a, b in instrs:\n        res.append((a, b))\n    return res\n'
        out = decompile(src)
        # Verify it doesn't use the fallback _item if possible
        if "for a, b in instrs:" not in out:
            self.assertIn("for (a, b) in instrs:", out, f"Neither 'for a, b in instrs:' nor 'for (a, b) in instrs:' found in out:\n{out}")
        self.assertFalse(bool(re.search(r'\b_item\b', out)), f"Unexpected literal '_item' in output for tests/test_instruction_regressions.py:\n{out}")

if __name__ == "__main__":
    unittest.main()
