"""A try whose body is only a re-raising try, with an except clause and a finally (#347)."""
import ast
import unittest

from .test_helpers import decompile

SOURCE = (
    "def f(stop):\n"
    "    out = []\n"
    "    try:\n"
    "        try:\n"
    "            out.append(10 // stop)\n"
    "        except ZeroDivisionError:\n"
    "            raise\n"
    "    except ArithmeticError:\n"
    "        out.append(2)\n"
    "    finally:\n"
    "        out.append('done')\n"
    "    return out\n")


def _call(code, stop):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace["f"](stop)


class TestNestedTryWithFinally(unittest.TestCase):

    def test_the_finally_belongs_to_the_outer_statement(self):
        out = decompile(SOURCE)
        ast.parse(out)
        lines = out.splitlines()
        indent = {ln.strip(): len(ln) - len(ln.lstrip()) for ln in lines if ln.strip()}
        self.assertEqual(indent["finally:"], indent["except ArithmeticError:"], out)
        self.assertEqual(indent["return out"], indent["finally:"], out)

    def test_it_behaves_like_the_original(self):
        out = decompile(SOURCE)
        for stop in (0, 5):
            self.assertEqual(_call(out, stop), _call(SOURCE, stop), out)

    def test_the_same_nesting_without_a_finally_is_unchanged(self):
        out = decompile(SOURCE.replace("    finally:\n        out.append('done')\n", ""))
        self.assertNotIn("finally", out)
        ast.parse(out)


if __name__ == "__main__":
    unittest.main()
