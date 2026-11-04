"""`async for` (#85).

The loop awaits each item (`GET_ANEXT` and the awaiting loop `await` compiles to) and ends when the iterator raises
StopAsyncIteration, which `END_ASYNC_FOR` handles. That is no source: the loop is read as the `for` it is written like,
with `async`. 3.10 guards the loop with a `SETUP_FINALLY` block instead of a table entry.
"""
import ast
import asyncio
import sys
import unittest

from .test_helpers import decompile

_PRELUDE = (
    "import asyncio\n"
    "class Gen:\n"
    "    def __init__(self, n):\n        self.n, self.i = n, 0\n"
    "    def __aiter__(self):\n        return self\n"
    "    async def __anext__(self):\n"
    "        await asyncio.sleep(0)\n"
    "        if self.i >= self.n:\n            raise StopAsyncIteration\n"
    "        self.i += 1\n        return self.i\n"
    "async def agen(n):\n    for i in range(n):\n        await asyncio.sleep(0)\n        yield (i, i * 2)\n"
    "async def val(x):\n    await asyncio.sleep(0)\n    return x\n")


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _outcome(function, args):
    async def drive():
        return await function(*args)
    try:
        return ("value", asyncio.run(drive()))
    except Exception as exc:  # pylint: disable=broad-except
        return ("raised", type(exc).__name__)


def _collect(function, args):
    async def drive():
        out = []
        try:
            async for item in function(*args):
                out.append(item)
        except Exception as exc:  # pylint: disable=broad-except
            out.append(("raised", type(exc).__name__))
        return out
    return asyncio.run(drive())


@unittest.skipIf(sys.version_info < (3, 10), "async for is read from 3.10 on")
class TestAsyncFor(unittest.TestCase):

    def assert_same(self, body, *calls, name="f", generator=False):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        check = _collect if generator else _outcome
        for args in calls:
            self.assertEqual(check(rebuilt[name], args), check(original[name], args), f"{args}\n{out}")
        return out

    def test_async_for_over_an_iterator(self):
        out = self.assert_same(
            "async def f(n):\n    total = 0\n    async for x in Gen(n):\n        total += x\n    return total\n", (4,), (0,))
        self.assertIn("async for x in Gen(n):", out)

    def test_async_for_over_an_async_generator_with_unpacking(self):
        self.assert_same(
            "async def f(n):\n    out = []\n    async for a, b in agen(n):\n        out.append(a + b)\n    return out\n", (4,))

    def test_code_after_the_loop_stays_after_it(self):
        out = self.assert_same(
            "async def f(n):\n    out = []\n    async for x in Gen(n):\n        out.append(x)\n    out.append('after')\n"
            "    return out\n", (3,))
        self.assertNotIn("        out.append('after')", out)

    def test_break_continue_and_else(self):
        self.assert_same(
            "async def f(n, stop):\n    out = []\n    async for x in Gen(n):\n        if x == 2:\n            continue\n"
            "        if x == stop:\n            break\n        out.append(x)\n    else:\n        out.append('else')\n"
            "    return out\n", (5, 4), (3, 9))

    def test_return_in_the_body(self):
        self.assert_same(
            "async def f(n):\n    async for x in Gen(n):\n        if x == 2:\n            return x\n    return -1\n",
            (5,), (1,))

    def test_await_in_the_body(self):
        self.assert_same(
            "async def f(n):\n    out = []\n    async for x in Gen(n):\n        out.append(await val(x))\n    return out\n", (3,))

    def test_nested_async_loops(self):
        self.assert_same(
            "async def f(n):\n    out = []\n    async for x in Gen(n):\n        async for y in Gen(x):\n"
            "            out.append((x, y))\n    return out\n", (3,))

    def test_async_for_in_a_plain_for_and_a_while(self):
        self.assert_same(
            "async def f(n):\n    out = []\n    for k in range(2):\n        async for x in Gen(n):\n"
            "            out.append((k, x))\n    i = 0\n    while i < 2:\n        i += 1\n        async for x in Gen(i):\n"
            "            out.append(x)\n    return out\n", (2,))

    def test_async_for_in_a_try_and_a_with(self):
        self.assert_same(
            "async def f(n):\n    out = []\n    try:\n        async for x in Gen(n):\n            out.append(1 / (3 - x))\n"
            "    except ZeroDivisionError:\n        out.append('zero')\n    finally:\n        out.append('done')\n    return out\n",
            (5,), (2,))
        self.assert_same(
            "import contextlib\nasync def f(n):\n    out = []\n    with contextlib.suppress(KeyError):\n"
            "        async for x in Gen(n):\n            out.append({}[x])\n    out.append('after')\n    return out\n", (2,))

    def test_try_in_the_body(self):
        self.assert_same(
            "async def f(n):\n    out = []\n    async for x in Gen(n):\n        try:\n            out.append(1 / (2 - x))\n"
            "        except ZeroDivisionError:\n            out.append('zero')\n    return out\n", (4,))

    def test_in_an_async_generator(self):
        self.assert_same(
            "async def f(n):\n    async for x in Gen(n):\n        yield x * 10\n    yield 'end'\n", (3,), generator=True)

    def test_in_an_async_with(self):
        self.assert_same(
            "class CM:\n    async def __aenter__(self):\n        return Gen(3)\n"
            "    async def __aexit__(self, *exc):\n        return False\n"
            "async def f():\n    out = []\n    async with CM() as g:\n        async for x in g:\n            out.append(x)\n"
            "    return out\n", ())

    def test_async_for_in_a_handler_and_a_finally(self):
        out = self.assert_same(
            "async def f(n):\n    out = []\n    try:\n        raise ValueError('v')\n    except ValueError:\n"
            "        async for x in Gen(n):\n            out.append(x)\n    finally:\n"
            "        async for y in Gen(n):\n            out.append(-y)\n    return out\n", (2,))
        self.assertEqual(out.count("async for"), 2)

    def test_a_captured_or_global_target_is_bound_directly(self):
        out = self.assert_same(
            "async def f(n):\n    out = []\n    async for x in Gen(n):\n        out.append(lambda: x)\n"
            "    return out[-1]()\n", (3,))
        self.assertNotIn("_item", out)
        out = self.assert_same(
            "async def f(n):\n    global G\n    async for G in Gen(n):\n        pass\n    return G\n", (3,))
        self.assertNotIn("_item", out)

    def test_a_plain_comprehension_is_not_reported_as_async(self):
        import os  # pylint: disable=import-outside-toplevel
        from pycrefine import get_decompiler  # pylint: disable=import-outside-toplevel
        from .test_helpers import _compile  # pylint: disable=import-outside-toplevel
        pyc = _compile("async def f(xs, o):\n    return [0 for i, o.x in xs]\n")
        try:
            dec = get_decompiler(pyc)
            dec.decompile()
            self.assertEqual([d for d in dec.diagnostics if "async comprehension" in d.reason], [])
        finally:
            os.unlink(pyc)

    def test_the_iterable_is_an_await(self):
        self.assert_same(
            "async def f(n):\n    out = []\n    async for x in await val(Gen(n)):\n        out.append(x)\n    return out\n", (3,))

    def test_an_error_from_the_iterator_propagates(self):
        self.assert_same(
            "class Bad:\n    def __aiter__(self):\n        return self\n    async def __anext__(self):\n"
            "        raise KeyError('x')\n"
            "async def f():\n    async for x in Bad():\n        pass\n    return 1\n", ())

    def test_a_plain_for_stays_a_plain_for(self):
        out = decompile("def f(xs):\n    for x in xs:\n        print(x)\n")
        self.assertNotIn("async", out)

    def test_async_comprehensions_are_reported(self):
        """They are not rebuilt yet (#85): they must not come out as plain code without a word."""
        import os  # pylint: disable=import-outside-toplevel
        from pycrefine import get_decompiler  # pylint: disable=import-outside-toplevel
        from .test_helpers import _compile  # pylint: disable=import-outside-toplevel
        for source in ("async def f(xs):\n    return [i async for i in xs]\n",
                       "async def f(xs):\n    return {i: 1 async for i in xs}\n",
                       "async def f(xs):\n    return (i async for i in xs)\n",
                       "async def f(xs, g):\n    return [await g(i) for i in xs]\n"):
            with self.subTest(source):
                pyc = _compile(source)
                try:
                    dec = get_decompiler(pyc)
                    dec.decompile()
                    self.assertTrue(any("async comprehensions" in d.reason for d in dec.diagnostics) or
                                    any("async" in d.reason for d in dec.diagnostics),
                                    [str(d) for d in dec.diagnostics])
                finally:
                    os.unlink(pyc)

    def test_nothing_is_reported_for_async_for(self):
        import os  # pylint: disable=import-outside-toplevel
        from pycrefine import get_decompiler  # pylint: disable=import-outside-toplevel
        from .test_helpers import _compile  # pylint: disable=import-outside-toplevel
        pyc = _compile(_PRELUDE + "async def f(n):\n    async for x in Gen(n):\n        print(x)\n")
        try:
            dec = get_decompiler(pyc)
            dec.decompile()
            self.assertEqual([d for d in dec.diagnostics if "async" in str(d)], [])
        finally:
            os.unlink(pyc)


if __name__ == "__main__":
    unittest.main()
