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
    - Chained augmented assignment (Issue 1)
    - Negative integer attribute access (Issue 2)
    """
    
    @classmethod
    def setUpClass(cls):
        if sys.version_info < (3, 9):
            raise unittest.SkipTest("Bytecode-dependent decompilation tests only run on Python 3.9+")


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
        # Avoid escaped quotes in f-string expressions for <3.12 compatibility
        src = "def f(expr, args):\n    return f'({expr})({\", \".join(str(a) for a in args)})'\n"
        out = decompile(src)
        # Verify parentheses and join structure
        self.assertIn('({expr})({', out)
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

    def test_augmented_assign_attr(self):
        # Issue 1: self.x += 1; self.y -= 1
        src = 'class C:\n    def f(self):\n        self.x += 1\n        self.y -= 1\n'
        out = decompile(src)
        # Verify both statements are separate and not chained
        self.assertIn("self.x += 1", out)
        self.assertIn("self.y -= 1", out)
        self.assertNotIn("(self.x += 1)", out)

    def test_negative_literal_attr(self):
        # Issue 2: x = [-1].offset
        src = 'def f(instrs):\n    return instrs[-1].offset\n'
        out = decompile(src)
        # Verify parentheses are present for literal -1 but not necessarily for variable index
        self.assertIn("[-1].offset", out)

        src2 = 'def f():\n    return (-1).offset\n'
        out2 = decompile(src2)
        self.assertIn("(-1).offset", out2)

    def test_negative_literal_method(self):
        # (-1).method()
        src = 'def f(x):\n    return (-1).bit_length()\n'
        out = decompile(src)
        self.assertIn("(-1).bit_length()", out)

    def test_inline_listcomp(self):
        # Issue 6: list comprehension in ordinary function
        src = 'def f(items):\n    res = [x.upper() for x in items]\n    return res\n'
        out = decompile(src)
        # Positive sanity check: loop structure should be present on all versions
        self.assertIn("in items", out)
        self.assertIn("for ", out)
        # In 3.12+ this is inlined. Our fix should prevent 'yield'
        if sys.version_info >= (3, 12):
            self.assertNotRegex(out, r"\byield\b")
            # It might be reconstructed as a loop or a comprehension depending on beautification
            # but it should definitely NOT be a generator.
    def test_super_method_call(self):
        # Issue 13: super().method() was corrupted
        src = 'class A:\n    def f(self):\n        return super().f()\n'
        # super() outside class works for compilation if we don't run it
        out = decompile(src)
        self.assertIn("super().f()", out)
        self.assertNotIn("self(super().f", out)


if __name__ == "__main__":
    unittest.main()
