"""At the `aggressive` beautification level a dict display of several entries is written one entry per line (#42)."""
import ast
import unittest

from pycrefine import post_process_source
from pycrefine.postprocess import split_dict_displays

from .test_helpers import decompile


class TestSplitDictDisplays(unittest.TestCase):

    def assert_same_tree(self, before, after):
        self.assertEqual(ast.dump(ast.parse(after)), ast.dump(ast.parse(before)), after)

    def test_a_display_is_split_one_entry_per_line(self):
        out = split_dict_displays("CONFIG = {'a': 1, 'b': 2}\n")
        self.assertEqual(out, "CONFIG = {\n    'a': 1,\n    'b': 2,\n}\n")

    def test_the_entries_are_indented_below_the_statement(self):
        out = split_dict_displays("def f(p):\n    if p:\n        return {'k': p, 'k2': p + 1}\n")
        self.assertEqual(out, "def f(p):\n    if p:\n        return {\n            'k': p,\n"
                              "            'k2': p + 1,\n        }\n")

    def test_nested_displays_and_spreads(self):
        source = "C = {'a': 1, 'b': {'x': 1, 'y': [1, 2]}, **extra}\n"
        out = split_dict_displays(source)
        self.assertEqual(out, "C = {\n    'a': 1,\n    'b': {\n        'x': 1,\n        'y': [1, 2],\n    },\n"
                              "    **extra,\n}\n")
        self.assert_same_tree(source, out)

    def test_several_displays_on_a_line(self):
        source = "print({'q': 1, 'r': 2}, {'s': 3, 't': 4})\n"
        self.assert_same_tree(source, split_dict_displays(source))
        self.assertEqual(split_dict_displays(source).count("\n"), 7)

    def test_displays_that_stay_as_they_are(self):
        for source in ("x = {}\n", "y = {'only': 1}\n", "z = {'a': 1,\n     'b': 2}\n",
                       "s = f\"{ {'a': 1, 'b': 2}['a'] }\"\n", "d = {k: v for k, v in pairs}\n"):
            self.assertEqual(split_dict_displays(source), source)

    def test_non_ascii_text_before_and_after_a_display(self):
        source = "d = ('é', {'ä': 1, 'ö': 'ü'}, 'ß')\n"
        out = split_dict_displays(source)
        self.assertIn("'ä': 1,", out)
        self.assert_same_tree(source, out)

    def test_multiline_strings_are_untouched(self):
        source = "t = '''a\nb''' + str({'u': 1, 'v': 2})\n"
        out = post_process_source(source, "aggressive")
        self.assertIn("'''a\nb'''", out)
        self.assert_same_tree(source, out)

    def test_source_that_does_not_parse_is_returned_as_it_is(self):
        self.assertEqual(split_dict_displays("x = {'a': 1, 'b': 2\n"), "x = {'a': 1, 'b': 2\n")

    def test_splitting_twice_changes_nothing_more(self):
        once = split_dict_displays("x = {'a': 1, 'b': {'c': 1, 'd': 2}}\n")
        self.assertEqual(split_dict_displays(once), once)

    def test_walrus_and_yield_entries_keep_their_parentheses(self):
        source = "x = {'a': (n := 1), 'b': 2}\n"
        out = split_dict_displays(source)
        self.assertEqual(out, "x = {\n    'a': (n := 1),\n    'b': 2,\n}\n")
        self.assert_same_tree(source, out)

    def test_displays_nested_in_lists_and_calls_are_split_too(self):
        source = "x = {'outer': [{'a': 1, 'b': 2}], 'other': f(1, {'c': 3, 'd': 4})}\n"
        out = split_dict_displays(source)
        self.assertNotIn("{'a'", out)
        self.assertNotIn("{'c'", out)
        self.assert_same_tree(source, out)

    def test_a_walk_that_recurses_too_deep_returns_the_source(self):
        source = "x = " + " + ".join(["a"] * 3000) + "\ny = {'a': 1, 'b': 2}\n"
        self.assertEqual(split_dict_displays(source), source)

    def test_only_the_aggressive_level_splits(self):
        source = "x = {'a': 1, 'b': 2}\n"
        self.assertIn("{'a': 1, 'b': 2}", post_process_source(source, "core"))
        self.assertIn("{'a': 1, 'b': 2}", post_process_source(source, "none"))
        self.assertNotIn("{'a': 1, 'b': 2}", post_process_source(source, "aggressive"))

    def test_decompiled_code_behaves_the_same(self):
        source = ("def f(p):\n    table = {'one': 1, 'two': p, 'nested': {'a': p + 1, 'b': [p, p]}}\n"
                  "    return table\nRESULT = f(5)\n")
        out = decompile(source, beautification_level="aggressive")
        self.assertIn("'one': 1,\n", out)
        original, rebuilt = {}, {}
        exec(compile(source, "<t>", "exec"), original)  # pylint: disable=exec-used
        exec(compile(out, "<t>", "exec"), rebuilt)  # pylint: disable=exec-used
        self.assertEqual(rebuilt["RESULT"], original["RESULT"])


if __name__ == "__main__":
    unittest.main()
