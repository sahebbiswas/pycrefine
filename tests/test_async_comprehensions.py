"""Async comprehensions (#85): `[i async for i in xs]`, `{k: v async for ...}`, `(i async for ...)`, `[await f(i) for i in xs]`.

Before 3.12 the comprehension is a coroutine (or async generator) code object that the enclosing code calls with the
async iterator and awaits; from 3.12 it is inlined, with the awaiting `async for` loop inside it. Each is read as the
comprehension it is written like, and nothing in the source awaits it.
"""
import ast
import sys
import unittest

from .test_async_for import _PRELUDE, _collect, _namespace, _outcome
from .test_helpers import decompile


class _Base(unittest.TestCase):

    def assert_same(self, body, *calls, name="f", generator=False):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        check = _collect if generator else _outcome
        for args in calls:
            self.assertEqual(check(rebuilt[name], args), check(original[name], args), f"{args}\n{out}")
        return out


class TestAwaitInAComprehension(_Base):

    def test_await_in_the_element(self):
        out = self.assert_same("async def f(n):\n    return [await val(i) for i in range(n)]\n", (4,), (0,))
        self.assertIn("[await val(i) for i in range(n)]", out)

    def test_await_in_a_set_and_a_dict(self):
        self.assert_same("async def f(n):\n    return {await val(i) for i in range(n)}\n", (4,))
        self.assert_same("async def f(n):\n    return {i: await val(i) for i in range(n)}\n", (4,))

    def test_await_in_the_condition_and_a_second_clause(self):
        self.assert_same("async def f(n):\n    return [(i, j) for i in range(n) if await val(i % 2) for j in range(i)]\n", (5,))

    def test_two_comprehensions_in_a_row(self):
        self.assert_same("async def f(n):\n    x = [await val(i) for i in range(n)]\n"
                         "    y = [await val(j + 1) for j in x]\n    return x, y\n", (3,))


@unittest.skipIf(sys.version_info < (3, 10), "async for is read from 3.10 on")
class TestAsyncForInAComprehension(_Base):

    def test_list(self):
        out = self.assert_same("async def f(n):\n    return [i async for i in Gen(n)]\n", (4,), (0,))
        self.assertIn("[i async for i in Gen(n)]", out)
        self.assertNotIn("await [", out)

    def test_set_and_dict(self):
        self.assert_same("async def f(n):\n    return {i % 2 async for i in Gen(n)}\n", (5,))
        self.assert_same("async def f(n):\n    return {i: i * i async for i in Gen(n)}\n", (4,))

    def test_condition_and_unpacking(self):
        self.assert_same("async def f(n):\n    return [a + b async for a, b in agen(n) if a % 2]\n", (6,))

    def test_async_and_plain_clauses(self):
        self.assert_same("async def f(n):\n    return [(i, j) async for i in Gen(n) for j in range(i)]\n", (4,))
        self.assert_same("async def f(n):\n    return [(i, j) for i in range(n) async for j in Gen(i)]\n", (4,))

    def test_with_an_await_in_the_element(self):
        self.assert_same("async def f(n):\n    return [await val(i) async for i in Gen(n)]\n", (3,))

    def test_assigned_and_followed_by_code(self):
        out = self.assert_same("async def f(n):\n    x = [i async for i in Gen(n)]\n    y = len(x)\n    return x, y\n", (3,))
        self.assertIn("x = [i async for i in Gen(n)]", out)

    def test_as_an_argument(self):
        self.assert_same("async def f(n):\n    return sum([i async for i in Gen(n)])\n", (4,))

    def test_async_generator_expression(self):
        out = self.assert_same("async def f(n):\n    return [x async for x in (i * 2 async for i in Gen(n))]\n", (3,))
        self.assertIn("async for i in Gen(n))", out)

    def test_an_explicit_await_of_a_comprehension_stays(self):
        """`await [..]` is a TypeError at run time, and so it is after decompiling: only the compiler's await goes."""
        out = decompile("async def f(xs):\n    return await [i async for i in xs]\n")
        ast.parse(out)
        self.assertIn("await [i async for i in xs]", out)

    def test_the_loop_variable_is_not_leaked(self):
        self.assert_same("async def f(n):\n    i = 'kept'\n    x = [i async for i in Gen(n)]\n    return i, x\n", (3,))


@unittest.skipIf(sys.version_info < (3, 12), "comprehensions are inlined from 3.12")
class TestUnrecognisedShapesAreReported(unittest.TestCase):

    def test_an_await_in_a_comprehension_that_is_not_rebuilt(self):
        """A target that is not a plain name leaves the loop written out: that must not pass without a word."""
        import os  # pylint: disable=import-outside-toplevel
        from pycrefine import get_decompiler  # pylint: disable=import-outside-toplevel
        from .test_helpers import _compile  # pylint: disable=import-outside-toplevel
        pyc = _compile("async def f(xs, g, o):\n    return [await g(i) for i, o.x in xs]\n")
        try:
            dec = get_decompiler(pyc)
            dec.decompile()
            self.assertTrue(any("async comprehension" in d.reason for d in dec.diagnostics),
                            [str(d) for d in dec.diagnostics])
        finally:
            os.unlink(pyc)


if __name__ == "__main__":
    unittest.main()
