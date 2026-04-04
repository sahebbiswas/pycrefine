import unittest
import os
import sys
import importlib
import tempfile
import textwrap

def _import_check_coherency():
    cc_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "debug")
    if cc_dir not in sys.path:
        sys.path.insert(0, cc_dir)
    try:
        return importlib.import_module("check_coherency")
    except ModuleNotFoundError as exc:
        if exc.name == "check_coherency":
            return None
        raise

class TestTokenHamming(unittest.TestCase):
    """
    Unit tests for the Token Hamming distance dimension added to
    check_coherency.py.

    These tests import the helpers directly and exercise them with
    controlled synthetic inputs so we can assert exact expected values
    without compiling/decompiling any real .pyc files.
    """

    @classmethod
    def setUpClass(cls):
        """Import check_coherency as a module via sys.path."""
        cls.cc = _import_check_coherency()

    def setUp(self):
        if self.cc is None:
            self.skipTest("check_coherency module not found in ../debug/")

    def test_line_tokenise_identifiers_and_operators(self):
        toks = self.cc._line_tokenise("x = y + 1")
        self.assertEqual(toks, ["x", "=", "y", "+", "1"])

    def test_line_tokenise_multichar_operators(self):
        toks = self.cc._line_tokenise("x += y >> 2")
        self.assertIn("+=", toks)
        self.assertIn(">>", toks)

    def test_line_tokenise_string_literal(self):
        toks = self.cc._line_tokenise('name = "hello"')
        self.assertIn('"hello"', toks)
        self.assertNotIn("h", toks)

    def test_line_tokenise_empty_and_comment(self):
        self.assertEqual(self.cc._line_tokenise(""), [])

    def test_line_tokenise_keyword(self):
        toks = self.cc._line_tokenise("if x > 0:")
        self.assertIn("if", toks)
        self.assertIn("x", toks)
        self.assertIn(">", toks)

    def test_identical_lines_score_one(self):
        lines = ["x = 1", "y = x + 2", "return y"]
        score, matched, total, flips, _ = self.cc._hamming_score_line_aligned(
            lines, lines
        )
        self.assertEqual(score, 1.0)
        self.assertEqual(flips, 0)
        self.assertEqual(matched, total)

    def test_empty_orig_gives_one(self):
        score, _matched, total, _flips, _ = self.cc._hamming_score_line_aligned(
            [], ["x = 1"]
        )
        self.assertEqual(score, 1.0)
        self.assertEqual(total, 0)

    def test_empty_dec_gives_zero(self):
        score, _, total, flips, _ = self.cc._hamming_score_line_aligned(
            ["x = 1", "y = 2"], []
        )
        self.assertEqual(score, 0.0)
        self.assertEqual(flips, total)

    def test_single_token_substitution(self):
        orig = ["result = compute(x, y)"]
        dec  = ["result = compute(a, y)"]
        score, _, total, flips, _ = self.cc._hamming_score_line_aligned(orig, dec)
        self.assertLess(score, 1.0)
        self.assertGreater(score, 0.5)
        self.assertEqual(flips, 1)

    def test_extra_parens_are_zero_cost(self):
        orig = ["y = x + 1"]
        dec  = ["y = (x + 1)"]
        score, _, _total, flips, _ = self.cc._hamming_score_line_aligned(orig, dec)
        self.assertEqual(flips, 0)
        self.assertEqual(score, 1.0)

    def test_pass_insertion_is_zero_cost(self):
        orig = ["class Foo:"]
        dec  = ["class Foo:", "pass"]
        score, _, _total, flips, _ = self.cc._hamming_score_line_aligned(orig, dec)
        self.assertEqual(flips, 0)
        self.assertEqual(score, 1.0)

    def test_half_lines_missing_scores_below_half(self):
        orig = ["x = 1", "y = 2", "z = 3", "w = 4"]
        dec  = ["x = 1", "y = 2"]
        score, _, _, _, _ = self.cc._hamming_score_line_aligned(orig, dec)
        self.assertLess(score, 0.75)

    def test_flip_sample_populated_on_mismatch(self):
        orig = ["foo = bar(x, y, z)"]
        dec  = ["baz = qux(a, b, c)"]
        _, _, _, flips, flip_sample = self.cc._hamming_score_line_aligned(orig, dec)
        self.assertGreater(flips, 0)
        self.assertGreater(len(flip_sample), 0)

    def test_score_monotone_with_accuracy(self):
        orig = ["result = compute_value(alpha, beta, gamma)"]
        perfect     = ["result = compute_value(alpha, beta, gamma)"]
        two_errors  = ["result = compute_value(alpha, XXXX, gamma)"]
        many_errors = ["result = YYYYY(alpha, XXXX, ZZZZ)"]

        def s(dec):
            score, *_ = self.cc._hamming_score_line_aligned(orig, dec)
            return score

        self.assertGreater(s(perfect), s(two_errors))
        self.assertGreater(s(two_errors), s(many_errors))

    def test_artefact_in_orig_not_penalised(self):
        shared = "if func == '__build_class__': pass\n"
        result = self.cc.score_cleanliness(shared, shared)
        self.assertEqual(result.score, 1.0)

    def test_excess_artefact_penalised(self):
        orig = "if func == '__build_class__': pass\n"
        dec  = "__build_class__\nif func == '__build_class__': pass\n"
        result = self.cc.score_cleanliness(dec, orig)
        self.assertLess(result.score, 1.0)
        self.assertIn("__build_class__", result.detail)

    def test_dimension_name_and_weight(self):
        result = self.cc.score_token_hamming("x = 1\n", "x = 1\n")
        self.assertEqual(result.name, "Token Hamming")
        self.assertAlmostEqual(result.weight, 0.12, places=5)

    def test_identical_source_perfect_score(self):
        src = "def f(x):\n    return x + 1\n"
        result = self.cc.score_token_hamming(src, src)
        self.assertEqual(result.score, 1.0)
        self.assertIn("perfect", result.detail)

    def test_completely_different_source_low_score(self):
        orig = "def compute(x, y):\n    return x * y + x - y\n"
        dec  = "import os\nfoo = bar\n"
        result = self.cc.score_token_hamming(orig, dec)
        self.assertLess(result.score, 0.5)

    def test_score_in_unit_interval(self):
        pairs = [
            ("x = 1\n", "x = 1\n"),
            ("x = 1\n", "y = 2\n"),
            ("def f():\n    pass\n", ""),
            ("", "x = 1\n"),
        ]
        for orig, dec in pairs:
            result = self.cc.score_token_hamming(orig, dec)
            self.assertGreaterEqual(result.score, 0.0)
            self.assertLessEqual(result.score, 1.0)

    def test_empty_original_returns_perfect(self):
        result = self.cc.score_token_hamming("", "x = 1\ny = 2\n")
        self.assertEqual(result.score, 1.0)

    def test_quote_normalisation_is_zero_cost(self):
        orig = "name = 'hello'\n"
        dec  = 'name = "hello"\n'
        result = self.cc.score_token_hamming(orig, dec)
        self.assertEqual(result.score, 1.0)

    def test_detail_contains_flip_count(self):
        orig = "def add(x, y):\n    return x + y\n"
        dec  = "def add(a, b):\n    return a + b\n"
        result = self.cc.score_token_hamming(orig, dec)
        if result.score < 1.0:
            self.assertIn("flip", result.detail)

    def test_extra_parens_in_full_text(self):
        orig = "x = 1\ny = x + 2\nz = y * 3\n"
        dec  = "x = 1\ny = (x + 2)\nz = (y * 3)\n"
        result = self.cc.score_token_hamming(orig, dec)
        self.assertEqual(result.score, 1.0)

    def test_annotation_stripping_is_zero_cost(self):
        orig = "def greet(name: str) -> str:\n    return name\n"
        dec  = "def greet(name):\n    return name\n"
        result = self.cc.score_token_hamming(orig, dec)
        self.assertEqual(result.score, 1.0)

    def test_all_dimension_weights_sum_to_one(self):
        src = textwrap.dedent("""\
            x = 1
            y = x + 2
        """)
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(src)
            sp = f.name
        try:
            report = self.cc.score(sp)
        finally:
            if os.path.exists(sp):
                os.unlink(sp)
        total = sum(d.weight for d in report.dimensions)
        self.assertAlmostEqual(total, 1.0, places=9)

    def test_hamming_dimension_present_in_report(self):
        src = "x = 1\n"
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(src)
            sp = f.name
        try:
            report = self.cc.score(sp)
        finally:
            if os.path.exists(sp):
                os.unlink(sp)
        names = [d.name for d in report.dimensions]
        self.assertIn("Token Hamming", names)

    def test_nine_dimensions_total(self):
        src = "x = 1\n"
        with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
            f.write(src)
            sp = f.name
        try:
            report = self.cc.score(sp)
        finally:
            if os.path.exists(sp):
                os.unlink(sp)
        self.assertEqual(len(report.dimensions), 9)

class TestOutputCleanliness(unittest.TestCase):
    """
    Tests for _strip_string_literals and score_cleanliness in check_coherency.
    """

    @classmethod
    def setUpClass(cls):
        cls.cc = _import_check_coherency()

    def setUp(self):
        if self.cc is None:
            self.skipTest("check_coherency module not found")

    def test_strip_single_quote_content(self):
        result = self.cc._strip_string_literals("x = '_exc_match'")
        self.assertNotIn("_exc_match", result)

    def test_strip_double_quote_content(self):
        result = self.cc._strip_string_literals('x = "__build_class__"')
        self.assertNotIn("__build_class__", result)

    def test_strip_triple_quote_content(self):
        result = self.cc._strip_string_literals('"""_exc_info is bad"""')
        self.assertNotIn("_exc_info", result)

    def test_strip_preserves_code_outside_strings(self):
        result = self.cc._strip_string_literals("if x == _exc_match:")
        self.assertIn("_exc_match", result)

    def test_strip_empty_passthrough(self):
        self.assertEqual(self.cc._strip_string_literals(""), "")

    def test_strip_no_strings_passthrough(self):
        code = "if x > 0:\n    return x\n"
        self.assertEqual(self.cc._strip_string_literals(code), code)

    def test_strip_leaves_surrounding_structure(self):
        result = self.cc._strip_string_literals("func('_exc_match', x)")
        self.assertIn("func(", result)
        self.assertNotIn("_exc_match", result)

    def test_exc_match_bare_identifier_penalised(self):
        dec = "x = _exc_match\nif _exc_match:\n    pass\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertLess(result.score, 1.0)

    def test_exc_match_as_substring_of_longer_name_not_penalised(self):
        dec = "def _has_exc_match_handler(self):\n    return False\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("_exc_match", result.detail)

    def test_exc_info_in_longer_method_name_not_penalised(self):
        dec = "def _find_push_exc_info_offset(self):\n    return -1\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("_exc_info", result.detail)

    def test_build_class_in_string_literal_not_penalised(self):
        dec = "if func == '__build_class__':\n    pass\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("__build_class__", result.detail)

    def test_build_class_bare_identifier_penalised(self):
        dec = "self.stack.append(__build_class__)\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertLess(result.score, 1.0)

    def test_func_tuple_leak_penalised(self):
        dec = "result = ('func', 'def f():\\n    pass')()\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertLess(result.score, 1.0)

    def test_class_tuple_leak_penalised(self):
        dec = "Foo = ('class', 'class Foo: pass')()\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertLess(result.score, 1.0)

    def test_clean_output_scores_one(self):
        dec = "x = 1\ndef f(a, b):\n    return a + b\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertEqual(result.score, 1.0)

    def test_multiple_artefacts_accumulate(self):
        dec = "x = __build_class__\ny = ('func', 'def f(): pass')()\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertAlmostEqual(result.score, 0.65, places=5)

    def test_score_floor_is_zero(self):
        dec = "__build_class__\na = ('func', 'x')()\n_exc_match\n_exc_info\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertGreaterEqual(result.score, 0.0)

    def test_genexpr_comment_in_string_not_penalised(self):
        dec = 'return "# <genexpr/lambda>"\n'
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("# <genexpr/lambda", result.detail)

    def test_genexpr_comment_as_bare_comment_penalised(self):
        dec = "# <genexpr/lambda — not reconstructable>\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertIn("# <genexpr/lambda", result.detail)

    def test_class_comment_as_bare_comment_penalised(self):
        dec = "# <class — not reconstructable>\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertIn("# <class", result.detail)

    def test_class_comment_in_string_not_penalised(self):
        dec = 'msg = "# <class body not reconstructable>"\n'
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("# <class", result.detail)

    def test_tuple_leak_in_assignment_penalised(self):
        dec_func  = "x = ('func', 'def f(): pass')()\n"
        result = self.cc.score_cleanliness(dec_func, "")
        self.assertIn("raw-tuple leak", result.detail)

    def test_tuple_in_stack_append_not_penalised(self):
        dec = "self.stack.append(('func', 'def f:'))\n"
        result = self.cc.score_cleanliness(dec, "")
        self.assertNotIn("raw-tuple leak", result.detail)

    def test_dimension_name_and_weight(self):
        result = self.cc.score_cleanliness("x = 1\n", "x = 1\n")
        self.assertEqual(result.name, "Output cleanliness")
        self.assertAlmostEqual(result.weight, 0.05, places=5)

if __name__ == "__main__":
    unittest.main()
