"""Class-body annotations and assignments inside `if`/`else` or `try`/`except` keep their place and their `else` (#262).

3.12 and 3.13 copy the body's final `return None` into each branch, and a class body suppresses it, so the `else`
clause ran unconditionally. On 3.14 a bare annotation came out after the conditional block that follows it, with a
stray `pass` in that block, and an annotation in an `except` body lost its own line.
"""
import ast
import unittest

from .test_helpers import decompile


def _run(code, **names):
    namespace = dict(names)
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _shape(namespace, cls):
    body = namespace[cls]
    return list(getattr(body, "__annotations__", {})), sorted(k for k in vars(body) if not k.startswith("__"))


class TestClassAnnotationsFlow(unittest.TestCase):

    def assert_same_class(self, source, cls="C"):
        out = decompile(source)
        ast.parse(out)
        for cond in (True, False):
            self.assertEqual(_shape(_run(out, cond=cond), cls), _shape(_run(source, cond=cond), cls),
                             f"cond={cond}\n{out}")
        return out

    def test_bare_annotations_around_an_if(self):
        out = self.assert_same_class(
            "class C:\n    a: int\n    if cond:\n        b: int\n    c: int = 1\n    d: str\n")
        self.assertNotIn("pass", out)

    def test_else_branch_of_annotated_assignments(self):
        out = self.assert_same_class(
            "class C:\n    if cond:\n        x: int = 1\n    else:\n        y: int = 2\n")
        self.assertIn("else:", out)

    def test_else_branch_with_code_after_it(self):
        out = self.assert_same_class(
            "class C:\n    if cond:\n        x: int = 1\n    else:\n        y: int = 2\n    z = 3\n")
        self.assertIn("else:", out)

    def test_else_branch_of_plain_assignments(self):
        self.assert_same_class("class C:\n    if cond:\n        x = 1\n    else:\n        y = 2\n")

    def test_annotations_in_an_except_clause(self):
        out = self.assert_same_class(
            "class C:\n    try:\n        p: int = 1\n    except ValueError:\n        q: int = 2\n        p: str\n"
            "    r: int\n")
        self.assertIn("q: int = 2", out)

    def test_annotations_in_a_failing_try(self):
        self.assert_same_class(
            "class C:\n    try:\n        p: int = 1 // 0\n    except ZeroDivisionError:\n        q: int = 2\n"
            "        p: str\n    r: int\n")

    def test_repeated_bare_annotations_keep_their_order(self):
        source = "class C:\n    a: int\n    b: str = 'x'\n    a: int\n    c: float = 2\n"
        self.assert_same_class(source)
        self.assertEqual([line.strip() for line in decompile(source).splitlines()[1:]],
                         ["a: int", "b: str = 'x'", "a: int", "c: float = 2"])

    def test_class_defining_its_own_annotate_function(self):
        source = ("class C:\n    def __annotate__(format):\n        return {'z': int}\n"
                  "    if cond:\n        y: str\n    x: int = 1\n")
        self.assert_same_class(source)
        out = decompile(source)
        self.assertIn("y: str", out)
        self.assertIn("x: int = 1", out)

    def test_module_level_else(self):
        source = "if cond:\n    x = 1\nelse:\n    y = 2\n"
        out = decompile(source)
        for cond in (True, False):
            self.assertEqual(sorted(k for k in _run(out, cond=cond) if k in "xy"),
                             sorted(k for k in _run(source, cond=cond) if k in "xy"), out)


if __name__ == "__main__":
    unittest.main()
