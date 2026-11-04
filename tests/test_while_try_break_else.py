"""Code after a `try` in a `while` loop is not taken for an `else` clause when it closes the loop (#305).

3.10 lays the handlers out between the try body and the rest of the loop body, which ends in the test that closes
the loop. A handler that leaves the loop (`break`) and the code after the `try` were read as a try with an `else`
clause: the try body ended in a `break` the source never had, and the handler fell through.
"""
import ast
import unittest

from .test_helpers import decompile


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


_LOOP = ("def run(n):\n    seen = []\n    i = 0\n    while i < n:\n        i += 1\n        try:\n"
         "            if i % 3 == 0:\n                raise ValueError(i)\n            seen.append(i)\n{rest}"
         "    seen.append('end')\n    return seen\n")
_SIZES = (0, 1, 2, 3, 4, 5, 7, 10)


class TestWhileTryBreakElse(unittest.TestCase):

    def assert_same(self, source):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source)["run"], _namespace(out)["run"]
        for size in _SIZES:
            self.assertEqual(rebuilt(size), original(size), f"run({size})\n{out}")
        return out

    def test_break_in_the_handler(self):
        out = self.assert_same(_LOOP.format(rest="        except ValueError:\n            break\n"
                                                 "        seen.append('after')\n"))
        self.assertNotIn("else:", out)

    def test_break_in_the_handler_without_code_after_the_try(self):
        self.assert_same(_LOOP.format(rest="        except ValueError:\n            break\n"))

    def test_pass_in_the_handler(self):
        out = self.assert_same(_LOOP.format(rest="        except ValueError:\n            pass\n"
                                                 "        seen.append('after')\n"))
        self.assertNotIn("else:", out)

    def test_continue_in_the_handler(self):
        self.assert_same(_LOOP.format(rest="        except ValueError:\n            continue\n"
                                           "        seen.append('after')\n"))


if __name__ == "__main__":
    unittest.main()
