"""A conditional expression as an element of a display (a tuple, list, set or dict) or a slice.

The join of its two arms is the BUILD_* that makes the display (or the load of the next element, then the BUILD_*), which
was not recognised as the place that uses its value: `[a if c else b]` came out as `a if c else [b]` and
`(a if c else b, 2)` as an empty `if` followed by `(b, 2)`.
"""
import ast
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestTernaryInDisplays(unittest.TestCase):

    def assert_same(self, body):
        source = "def f(c, a, b):\n" + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for args in ((True, 1, 2), (False, 1, 2), (True, 0, 5), (False, 7, 0)):
            self.assertEqual(rebuilt["f"](*args), original["f"](*args), f"{args}\n{out}")
        return out

    def test_list_with_the_conditional_alone(self):
        self.assert_same("    return [a if c else b]\n")

    def test_list_with_the_conditional_between_elements(self):
        self.assert_same("    return [1, a if c else b, 3]\n")

    def test_tuple_with_the_conditional_first(self):
        self.assert_same("    return (a if c else b, 2)\n")

    def test_set(self):
        self.assert_same("    return {10, a if c else b}\n")

    def test_dict_value_and_key(self):
        self.assert_same("    return {'k': a if c else b, 'j': 2}\n")
        self.assert_same("    return {a if c else b: 1}\n")

    def test_arithmetic_in_the_arms(self):
        self.assert_same("    return (1, 2, a + 1 if c else b - 1)\n")

    def test_the_display_is_stored(self):
        self.assert_same("    x = [a if c else b, 5]\n    return x\n")

    def test_f_string_subscript_and_slice(self):
        self.assert_same("    return f'{a if c else b}!'\n")
        self.assert_same("    return [10, 20, 30][a if c else b]\n")
        self.assert_same("    return [1, 2, 3, 4][1:a if c else b]\n")


if __name__ == "__main__":
    unittest.main()
