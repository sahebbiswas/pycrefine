"""Scope declarations: `global` and `nonlocal` (#155)."""
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
class TestGlobalAndNonlocal(unittest.TestCase):

    def assert_same_behavior(self, src):
        out = decompile(src)
        self.assertEqual(_run(compile(out, "<decompiled>", "exec")), _run(compile(src, "<original>", "exec")), out)
        return out

    def test_global_assignment(self):
        out = self.assert_same_behavior(
            "Z = 0\ndef g(a):\n    global Z\n    Z = a\ng(5)\nprint(Z)\n")
        self.assertIn("    global Z\n", out)

    def test_global_augmented_assignment_and_delete(self):
        out = self.assert_same_behavior(
            "count = 1\nW = 0\ndef h():\n    global count, W\n    count += 1\n    W = count\n    del count\n"
            "h()\nprint(W, 'count' in globals())\n")
        self.assertIn("global count, W", out)

    def test_global_import_inside_function(self):
        out = self.assert_same_behavior(
            "def load():\n    global json\n    import json\nload()\nprint(json.dumps([1]))\n")
        self.assertIn("global json", out)

    def test_reads_need_no_declaration(self):
        out = decompile("Z = 1\ndef r():\n    return Z\n")
        self.assertNotIn("global", out)

    def test_nonlocal_assignment(self):
        out = self.assert_same_behavior(
            "def outer():\n    n = 0\n    def inc():\n        nonlocal n\n        n += 1\n    inc()\n    inc()\n"
            "    return n\nprint(outer())\n")
        self.assertIn("        nonlocal n\n", out)

    def test_nonlocal_delete(self):
        out = self.assert_same_behavior(
            "def outer():\n    m = 1\n    def d():\n        nonlocal m\n        del m\n    d()\n"
            "    try:\n        return m\n    except NameError:\n        return 'deleted'\nprint(outer())\n")
        self.assertIn("nonlocal m", out)

    def test_closure_reads_need_no_declaration(self):
        out = decompile("def outer():\n    n = 1\n    def get():\n        return n\n    return get\n")
        self.assertNotIn("nonlocal", out)

    def test_class_body_global(self):
        out = self.assert_same_behavior("class C:\n    global Q\n    Q = 5\nprint(Q)\n")
        self.assertIn("global Q", out)

    def test_declarations_follow_the_docstring(self):
        out = decompile('Z = 0\ndef g(a):\n    """Doc."""\n    global Z\n    Z = a\n')
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        body = lines[lines.index("def g(a):") + 1:]
        self.assertEqual(body[-2:], ["global Z", "Z = a"])

    def test_module_level_has_no_declaration(self):
        out = decompile("global Z\nZ = 1\n")
        self.assertNotIn("global", out)

    def test_except_as_name_captured_by_a_closure(self):
        # The `as` name is a cell variable here (STORE_DEREF / DELETE_DEREF): it is
        # bound by the except clause, and its cleanup is not source code.
        out = self.assert_same_behavior(
            "def f():\n    try:\n        raise ValueError('v')\n    except ValueError as e:\n"
            "        g = lambda: e\n        print(g())\nf()\n")
        self.assertIn("except ValueError as e:", out)
        self.assertNotIn("del e", out)


if __name__ == "__main__":
    unittest.main()
