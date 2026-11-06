"""A long tuple of exception types is still an `except`, not a `finally` (review of #359)."""
import ast
import sys
import unittest

from .test_helpers import decompile


class TestLongExceptTypes(unittest.TestCase):

    def test_seven_types_stay_an_except_clause(self):
        out = decompile("try:\n    f()\nexcept (A, B, C, D, E, F, G):\n    pass\n")
        ast.parse(out)
        self.assertIn("except (A, B, C, D, E, F, G):", out)
        self.assertNotIn("finally", out)

    def test_dotted_types_stay_an_except_clause(self):
        out = decompile("try:\n    f()\nexcept (m.A, m.B, m.C, m.D, m.E):\n    g()\n")
        self.assertIn("except (m.A, m.B, m.C, m.D, m.E):", out)

    def test_a_possibly_unbound_local_among_many_types_stays_an_except_clause(self):
        out = decompile(
            "def f(c):\n    if c:\n        E = ValueError\n    try:\n        g()\n"
            "    except (A, B, C, D, E, F, G):\n        pass\n")
        self.assertIn("except (A, B, C, D, E, F, G):", out)
        self.assertNotIn("finally", out)

    def test_a_single_possibly_unbound_local_type_is_an_except_clause(self):
        out = decompile("def f(c):\n    if c:\n        E = ValueError\n    try:\n        g()\n"
                        "    except E:\n        pass\n")
        self.assertIn("except E:", out)
        self.assertNotIn("finally", out)

    def test_a_single_closure_variable_type_is_an_except_clause(self):
        out = decompile("def f(E):\n    def h():\n        try:\n            g()\n        except E:\n"
                        "            pass\n    return h\n")
        self.assertIn("except E:", out)

    def test_a_finally_that_starts_with_such_a_load_is_still_a_finally(self):
        out = decompile("def f(c):\n    if c:\n        E = ValueError\n    try:\n        g()\n"
                        "    finally:\n        E.cleanup()\n")
        self.assertIn("finally:", out)
        self.assertNotIn("except", out)

    def test_a_real_finally_is_still_a_finally(self):
        out = decompile("try:\n    f()\nfinally:\n    a(b, c, d, e, f2, g)\n")
        self.assertIn("finally:", out)
        self.assertNotIn("except", out)


if __name__ == "__main__":
    unittest.main()
