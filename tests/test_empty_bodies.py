"""Bodies that compile to nothing (#167)."""
import ast
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
class TestEmptyBodies(unittest.TestCase):

    def assert_same_behavior(self, src):
        out = decompile(src)
        self.assertEqual(_run(compile(out, "<decompiled>", "exec")), _run(compile(src, "<original>", "exec")), out)
        return out

    def test_empty_class(self):
        out = self.assert_same_behavior("class MyError(Exception):\n    pass\n\nprint(MyError.__mro__[1].__name__)\n")
        self.assertIn("class MyError(Exception):\n    pass", out)

    def test_ellipsis_class_and_methods(self):
        out = self.assert_same_behavior(
            "class Proto:\n    def a(self): ...\n    def b(self):\n        pass\n\nclass E:\n    ...\n"
            "print(Proto().a(), Proto().b(), E)\n")
        compile(out, "<decompiled>", "exec")

    def test_empty_functions(self):
        self.assert_same_behavior(
            "def p():\n    pass\n\ndef r():\n    return\n\n"
            "def nested():\n    def inner():\n        pass\n    class C:\n        pass\n    return inner(), C.__name__\n"
            "print(p(), r(), nested())\n")

    def test_lambda_returning_none(self):
        out = self.assert_same_behavior("f = lambda: None\ng = lambda x: None\nprint(f(), g(1))\n")
        self.assertIn("lambda: None", out)

    def test_docstring_only_bodies_need_no_pass(self):
        out = decompile('def f():\n    """Only."""\n\nclass K:\n    """Only."""\n')
        self.assertEqual(ast.dump(ast.parse(out)),
                         ast.dump(ast.parse('def f():\n    """Only."""\n\nclass K:\n    """Only."""\n')))

    def test_empty_try_body(self):
        # 3.11+ emits the handlers of `try: pass` although nothing can reach them.
        src = ("def f(x):\n    try:\n        pass\n    except (ValueError, TypeError):\n        pass\n"
               "    try:\n        print('t', x)\n    except Exception:\n        pass\n    print('end')\n"
               "f(1)\n")
        out = self.assert_same_behavior(src)
        self.assertIn("except (ValueError, TypeError):", out)
        self.assertLess(out.index("except (ValueError, TypeError):"), out.index("print('t', x)"))

    def test_empty_try_in_a_loop(self):
        self.assert_same_behavior(
            "def f(items):\n    for x in items:\n        try:\n            pass\n        except Exception:\n"
            "            continue\n        print(x)\nf([1, 2])\n")

    def test_other_empty_blocks(self):
        self.assert_same_behavior(
            "import contextlib\n"
            "def f(x, ys):\n    if x:\n        pass\n    else:\n        print('no')\n    for y in ys:\n        pass\n"
            "    while x > 10:\n        pass\n    with contextlib.suppress(Exception):\n        pass\n"
            "    print('end')\nf(0, [1])\nf(1, [])\n")


if __name__ == "__main__":
    unittest.main()
