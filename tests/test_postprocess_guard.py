"""The post-processing layer rewrites text, so it is held to the tree of the code it was given (#121).

Every transform that claims `preserves_ast` must leave the parsed tree of parseable input unchanged,
the whole layer must be idempotent, and each transform has its own cases. The raw text used is what
the backends hand to `post_process_source` for every file of the versioned corpus.
"""
import ast
import py_compile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from pycrefine import get_decompiler
from pycrefine import postprocess as pp

_CORPUS = Path(__file__).resolve().parent.parent / "quality" / "corpus"
LEVELS = ("none", "core", "aggressive")


def _raw_outputs():
    """(level, raw text) for every unit the backends post-process, across the corpus sources."""
    seen = []
    real = pp.post_process_source

    def record(source, beautification_level="core"):
        seen.append((beautification_level, source))
        return real(source, beautification_level)

    with mock.patch("pycrefine.decompiler.generic.post_process_source", record):
        for path in sorted(_CORPUS.rglob("*.py")):
            if path.name.startswith("generate_"):
                continue
            with tempfile.TemporaryDirectory() as tmp:
                pyc = str(Path(tmp) / "m.pyc")
                try:
                    py_compile.compile(str(path), cfile=pyc, doraise=True)
                except (py_compile.PyCompileError, SyntaxError):
                    continue
                for level in LEVELS:
                    try:
                        get_decompiler(pyc, beautification_level=level).decompile()
                    except Exception:  # pylint: disable=broad-except
                        continue  # a file the running Python cannot decompile is not this test's concern
    return seen


def _without_imports(text):
    """The tree of ``text`` with import statements replaced by the names they bind, in order."""
    tree = ast.parse(text)
    names = []

    class Strip(ast.NodeTransformer):
        def visit_Import(self, node):  # pylint: disable=invalid-name
            names.extend(("import", a.name, a.asname) for a in node.names)
            return ast.Pass()

        def visit_ImportFrom(self, node):  # pylint: disable=invalid-name
            names.extend((node.module, a.name, a.asname) for a in node.names)
            return ast.Pass()

    return ast.dump(Strip().visit(tree)), names


def _tree(text):
    try:
        return ast.dump(ast.parse(text))
    except SyntaxError:
        return None


class TestGuardOnTheCorpus(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.raw = _raw_outputs()

    def test_the_corpus_gave_input(self):
        self.assertGreater(len(self.raw), 100)

    @staticmethod
    def _masked(raw, transform):
        """The text as ``post_process_source`` hands it to ``transform``, and the literals to put back."""
        text, literals = pp._mask_multiline_strings(  # pylint: disable=protected-access
            raw, keep_print_arguments=True)
        if transform in pp.TEXT_TRANSFORMS:
            text, literals = pp._mask_multiline_strings(text, literals=literals)  # pylint: disable=protected-access
        return text, literals

    def test_each_ast_preserving_transform_keeps_the_tree(self):
        for transform in pp.STRING_TRANSFORMS + pp.TEXT_TRANSFORMS + pp.FINAL_TRANSFORMS:
            if not transform.preserves_ast:
                continue
            checked = 0
            for level, raw in self.raw:
                before = _tree(raw)
                if level not in transform.levels or before is None:
                    continue
                text, literals = self._masked(raw, transform)
                after = pp._unmask_multiline_strings(transform.apply(text), literals)  # pylint: disable=protected-access
                checked += 1
                self.assertEqual(_tree(after), before, f"{transform.name} changed the tree of:\n{raw}")
            with self.subTest(transform=transform.name):
                self.assertGreater(checked, 0)

    def test_the_whole_layer_keeps_the_tree_unless_it_drops_or_replaces_something(self):
        checked = 0
        for level, raw in self.raw:
            before = _tree(raw)
            imports = [ln.strip() for ln in raw.split("\n") if ln.lstrip().startswith(("import ", "from "))]
            if before is None or "('func'" in raw or "('class'" in raw or len(imports) != len(set(imports)):
                continue
            # (merged imports are one statement fewer, so compare the imported names in order)
            self.assertEqual(_without_imports(pp.post_process_source(raw, level)), _without_imports(raw),
                             f"[{level}]\n{raw}")
            checked += 1
        self.assertGreater(checked, 100)

    def test_the_layer_is_idempotent(self):
        for level, raw in self.raw:
            once = pp.post_process_source(raw, level)
            self.assertEqual(pp.post_process_source(once, level), once, f"[{level}]\n{raw}")

    def test_parseable_input_stays_parseable(self):
        for level, raw in self.raw:
            if _tree(raw) is not None:
                self.assertIsNotNone(_tree(pp.post_process_source(raw, level)), f"[{level}]\n{raw}")


class TestRegistry(unittest.TestCase):

    def test_names_are_unique(self):
        names = [t.name for t in pp.STRING_TRANSFORMS + pp.TEXT_TRANSFORMS + pp.FINAL_TRANSFORMS]
        self.assertEqual(len(names), len(set(names)))

    def test_none_level_runs_only_the_always_on_transforms(self):
        run = [t.name for t in pp.STRING_TRANSFORMS + pp.TEXT_TRANSFORMS + pp.FINAL_TRANSFORMS if "none" in t.levels]
        self.assertEqual(run, ["merge_imports", "strip_redundant_parens", "collapse_blank_lines",
                               "replace_leaked_tuples"])

    def test_dict_splitting_is_aggressive_only(self):
        self.assertEqual(pp.FINAL_TRANSFORMS[0].levels, frozenset(("aggressive",)))


class TestTransforms(unittest.TestCase):

    def test_merge_imports_dedupes_and_groups_by_kind(self):
        text = "import os\nimport os, sys\nfrom a import x\nfrom a import y, x\nz = 1"
        self.assertEqual(pp.merge_imports("import os\nimport os, sys\nz = 1"), "import os\nimport sys\nz = 1")
        self.assertEqual(pp.merge_imports(text), "import os\nimport sys\nfrom a import x, y\nz = 1")

    def test_merge_imports_keeps_indents_apart(self):
        self.assertEqual(pp.merge_imports("import a\ndef f():\n    import b\n    import c"),
                         "import a\ndef f():\n    import b\n    import c")

    def test_strip_redundant_parens(self):
        self.assertEqual(pp.strip_redundant_parens("x = (a + b)\ny = (z)"), "x = a + b\ny = z")

    def test_strip_parens_keeps_required_ones(self):
        text = "x = (a, b)\ny = (i for i in z)\nreturn (yield)\nif (yield):\n    pass"
        self.assertEqual(pp.strip_redundant_parens(text), text)

    def test_strip_parens_in_conditions_and_returns(self):
        self.assertEqual(pp.strip_redundant_parens("if (a < b):\n    return (a)\nwhile (c):\n    pass"),
                         "if a < b:\n    return a\nwhile c:\n    pass")

    def test_inplace_double_assignment(self):
        self.assertEqual(pp.strip_redundant_parens("    n = (n += 1)"), "    n += 1")

    def test_tidy_print_one_line_string_and_parens(self):
        self.assertEqual(pp.tidy_print_calls("print((x))"), "print(x)")
        self.assertEqual(pp.tidy_print_calls("print((a, b))"), "print((a, b))")
        self.assertEqual(pp.tidy_print_calls('print("""a\nb""")'), "print('a\\nb')")

    def test_tidy_print_leaves_prefixed_strings(self):
        self.assertEqual(pp.tidy_print_calls('print(f"""a\nb""")'), 'print(f"""a\nb""")')

    def test_collapse_blank_lines(self):
        self.assertEqual(pp.collapse_blank_lines("a\n\n\n\nb\n\nc"), "a\n\nb\n\nc")

    def test_replace_leaked_tuples(self):
        out = pp.replace_leaked_tuples("    f = ('func', 'name', code)")
        self.assertEqual(out, "    # <genexpr/lambda \u2014 not reconstructable>\n    f = None")
        out = pp.replace_leaked_tuples("c = ('class', 'C', 1)")
        self.assertEqual(out, "# <class \u2014 not reconstructable>\nc = None")

    def test_replace_stray_lambda_def(self):
        out = pp.replace_leaked_tuples("def <lambda>(x):\n    a = 1\n\n    return x\ny = 1")
        self.assertIn("not reconstructable", out)
        self.assertTrue(out.endswith("y = 1"))
        ast.parse(out)  # the body of the stray def goes with its header


class TestReviewFindings(unittest.TestCase):
    """Cases the review of #362 found; each one changed the program or broke it before."""

    def test_interleaved_imports_keep_their_order(self):
        text = "from a import x\nfrom b import y\nfrom a import z"
        self.assertEqual(pp.merge_imports(text), text)

    def test_a_rebound_alias_keeps_its_last_binding(self):
        # the earlier copy goes, so the last statement still decides what `x` is
        self.assertEqual(pp.merge_imports("import a as x\nimport b as x\nimport a as x"),
                         "import b as x\nimport a as x")

    def test_a_plain_repeat_is_still_dropped(self):
        self.assertEqual(pp.merge_imports("import a\nimport b\nimport a"), "import a\nimport b")

    def test_parenthesised_and_continued_imports_are_left_alone(self):
        for text in ("from a import (x)\nfrom a import (y)", "from a import x, \\\n    y\nfrom a import z"):
            with self.subTest(text=text):
                self.assertEqual(pp.merge_imports(text), text)

    def test_a_walrus_keeps_its_parentheses(self):
        for text in ("y = (z := 1)", "return (z := 1)", "    n += (z := 2)"):
            with self.subTest(text=text):
                self.assertEqual(pp.strip_redundant_parens(text), text)

    def test_a_yield_argument_keeps_its_parentheses(self):
        for text in ("print((yield))", "print((yield 1))"):
            with self.subTest(text=text):
                self.assertEqual(pp.tidy_print_calls(text), text)

    def test_an_escape_in_a_triple_quoted_print_argument_keeps_its_value(self):
        for source in (r'print("""a\nb""")', r'print("""it\'s\t""", 5)'):
            with self.subTest(source=source):
                text = "def f():\n    " + source + "\n"
                self.assertEqual(ast.dump(ast.parse(pp.post_process_source(text))), ast.dump(ast.parse(text)))

    def test_a_multiline_string_in_an_expression_keeps_its_blank_lines_and_text(self):
        source = 'x = """a\n\n\n\nb\ndef <lambda>(y):\nimport os\nimport sys\n    z = (q)\n"""\nprint(x)\n'
        out = pp.post_process_source(source)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(source)))


class TestSecondReview(unittest.TestCase):
    """Cases the review of #363 found in the same layer."""

    def test_a_yield_with_a_space_after_the_parenthesis_keeps_its_parentheses(self):
        for text in ("return ( yield 1)", "if ( yield ):"):
            with self.subTest(text=text):
                self.assertEqual(pp.strip_redundant_parens(text), text)

    def test_commented_or_joined_imports_are_not_merged(self):
        for text in ("from a import x  # keep\nfrom a import y", "import a; import b\nimport c"):
            with self.subTest(text=text):
                self.assertEqual(pp.merge_imports(text), text)

    def test_a_wildcard_import_stays_a_statement_of_its_own(self):
        text = "from a import *\nfrom a import y"
        self.assertEqual(pp.merge_imports(text), text)

    def test_brackets_inside_a_string_do_not_decide_the_print_parentheses(self):
        text = 'print(("(") + (")"))'
        out = pp.tidy_print_calls(text)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(text)))

    def test_triple_quotes_inside_a_single_quoted_string_are_left_alone(self):
        text = "print('say \"\"\"hi\"\"\" now')"
        out = pp.tidy_print_calls(text)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(text)))

    def test_a_def_lambda_inside_a_one_line_string_is_not_replaced(self):
        text = 'x = "def <lambda>(y)"\nprint(x)'
        self.assertEqual(pp.replace_leaked_tuples(text), text)

    def test_a_print_line_inside_a_multiline_string_is_not_rewritten(self):
        source = 'x = """a\nprint((value))\nb"""\nprint(x)\n'
        out = pp.post_process_source(source)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(source)))

    def test_a_multiline_f_string_keeps_its_blank_lines(self):
        source = 'name = 1\nx = f"""a\n\n\n{name}\n"""\n'
        out = pp.post_process_source(source)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(source)))

    def test_a_source_that_already_holds_the_placeholder_text_survives(self):
        source = ('a = "__pycrefine_multiline_string_0__"\nb = """x\n\n\ny"""\n'
                  'c = """__pycrefine_multiline_string_0__\nz"""\n')
        out = pp.post_process_source(source)
        self.assertEqual(ast.dump(ast.parse(out)), ast.dump(ast.parse(source)))


if __name__ == "__main__":
    unittest.main()
