"""`return` inside an except clause stays in the clause (#165)."""
import contextlib
import io
import sys
import unittest

from .test_helpers import decompile

BOOM = (
    "def boom(v):\n    if v == 'bad':\n        raise ValueError(v)\n"
    "    if v == 'key':\n        raise KeyError(v)\n    return v\n"
)


def _run(code):
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        exec(code, {"__name__": "__main__"})
    return buf.getvalue()


@unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 11)), "3.10/3.11 have known control-flow failures")
class TestReturnInExceptClause(unittest.TestCase):

    def assert_same_behavior(self, func_src, call="{f}(v)"):
        name = func_src.split("(", 1)[0][4:]
        src = (BOOM + func_src + "for v in ('ok', 'bad', 'key'):\n    try:\n        print(" + call.format(f=name)
               + ")\n    except Exception as ex:\n        print('raised', type(ex).__name__)\n")
        out = decompile(src)
        self.assertEqual(_run(compile(out, "<decompiled>", "exec")), _run(compile(src, "<original>", "exec")), out)
        return out

    def test_return_constant(self):
        out = self.assert_same_behavior(
            "def f(v):\n    try:\n        boom(v)\n    except ValueError:\n        return 'bad'\n    return 'ok'\n")
        self.assertRegex(out, r"except ValueError:\n\s+return 'bad'")

    def test_return_expression_with_as_name(self):
        out = self.assert_same_behavior(
            "def f(v):\n    try:\n        boom(v)\n    except ValueError as e:\n        return 'val:' + str(e)\n"
            "    print('after')\n    return 'ok'\n")
        self.assertRegex(out, r"except ValueError as e:\n\s+return \(?'val:' \+ str\(e\)")

    def test_bare_return_before_more_code(self):
        out = self.assert_same_behavior(
            "def f(v):\n    try:\n        boom(v)\n    except ValueError as e:\n        return 'val'\n"
            "    except KeyError:\n        return\n    print('after', v)\n    return 'ok'\n")
        self.assertRegex(out, r"except KeyError:\n\s+return")

    def test_bare_return_after_statement(self):
        self.assert_same_behavior(
            "def f(v):\n    try:\n        boom(v)\n    except (ValueError, KeyError) as e:\n"
            "        print('caught', type(e).__name__)\n        return\n    print('fine')\n")

    def test_handler_at_function_end_gets_no_return(self):
        out = decompile(BOOM + "def f(v):\n    try:\n        boom(v)\n    except ValueError:\n        print('handled')\n")
        self.assertNotIn("return None", out)

    def test_return_from_loop(self):
        self.assert_same_behavior(
            "def f(xs):\n    for x in xs:\n        try:\n            boom(x)\n        except ValueError:\n"
            "            return 'stop at ' + x\n        except KeyError as e:\n            return e.args[0]\n"
            "    return 'done'\n", call="{f}([v, 'ok'])")

    def test_conditional_return_then_more_statements(self):
        self.assert_same_behavior(
            "def f(v):\n    try:\n        boom(v)\n    except ValueError:\n        if v:\n            return 1\n"
            "        print('x')\n    return 2\n")

    def test_bare_except(self):
        self.assert_same_behavior("def f(v):\n    try:\n        boom(v)\n    except:\n        return v * 2\n")

    def test_return_through_finally_39(self):
        if sys.version_info[:2] != (3, 9):
            self.skipTest("3.12+ try/except/finally where every path returns: #171")
        self.assert_same_behavior(
            "def f(v):\n    try:\n        boom(v)\n    except ValueError:\n        return 'bad'\n"
            "    finally:\n        print('fin')\n    return 'end'\n")


if __name__ == "__main__":
    unittest.main()
