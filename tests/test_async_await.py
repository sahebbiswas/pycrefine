"""`async def` and `await` (#85).

An `async def` was written as a plain `def` and each `await` as a `yield None`. A function with the CO_COROUTINE or
CO_ASYNC_GENERATOR flag is written with `async def`, and the loop an `await` compiles to is read as one expression, the
same way `yield from` is (GET_AWAITABLE; LOAD_CONST None; YIELD_FROM on 3.9 and 3.10, and SEND; YIELD_VALUE; RESUME;
JUMP_BACKWARD_NO_INTERRUPT [END_SEND] on 3.11 and later).
"""
import ast
import asyncio
import unittest

from .test_helpers import decompile

_PRELUDE = (
    "import asyncio\n"
    "async def val(x):\n    await asyncio.sleep(0)\n    return x\n"
    "async def boom(x):\n    await asyncio.sleep(0)\n    raise ValueError(x)\n"
    "class Box:\n    def __init__(self, v):\n        self.v = v\n    async def get(self):\n        return self.v\n"
    "    async def pair(self, a, b):\n        return (a, b)\n")


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _outcome(function, args):
    """What a coroutine function gives for *args*: its value, or the type of the error it raised."""
    async def drive():
        return await function(*args)
    try:
        return ("value", asyncio.run(drive()))
    except Exception as exc:  # pylint: disable=broad-except
        return ("raised", type(exc).__name__)


def _collect(function, args):
    """What an async generator function yields for *args*, and the error it ended with."""
    async def drive():
        out = []
        try:
            async for item in function(*args):
                out.append(item)
        except Exception as exc:  # pylint: disable=broad-except
            out.append(("raised", type(exc).__name__))
        return out
    return asyncio.run(drive())


class TestAsyncAwait(unittest.TestCase):

    def assert_same(self, body, *calls, name="f", generator=False):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        check = _collect if generator else _outcome
        for args in calls:
            self.assertEqual(check(rebuilt[name], args), check(original[name], args), f"{args}\n{out}")
        return out

    def test_await_as_statement_and_value(self):
        out = self.assert_same(
            "async def f(a):\n    x = await val(a)\n    await val(1)\n    return x + 1\n", (4,))
        self.assertIn("async def f(a):", out)
        self.assertIn("x = await val(a)", out)

    def test_return_await(self):
        out = self.assert_same("async def f(a):\n    return await val(a)\n", (7,))
        self.assertIn("return await val(a)", out)

    def test_await_inside_an_expression(self):
        self.assert_same(
            "async def f(a):\n    return (await val(a)) * 2 + (await val(1))\n", (5,))

    def test_await_receiver_subscript_and_attribute(self):
        self.assert_same(
            "async def f(a):\n    y = (await val([a, 2]))[0]\n    z = (await val(Box(a))).v\n"
            "    return y, z, (await val(Box(3)).get())\n".replace("(await val(Box(3)).get())", "await Box(3).get()"),
            (8,))

    def test_await_as_an_argument_and_a_call_receiver(self):
        self.assert_same(
            "async def f(a):\n    box = Box(1)\n    return await box.pair(await val(a), await val(2))\n", (6,))

    def test_await_of_a_call_with_three_arguments(self):
        """On 3.9 and 3.10 an `await f(a, b, c)` follows CALL_FUNCTION 3, like the exit call of an `async with`."""
        out = self.assert_same(
            "async def g(a, b, c):\n    return (a, b, c)\n"
            "async def f(a):\n    x = await g(a, 2, 3)\n    return await g(x, a, 1)\n", (1,))
        self.assertNotIn("yield", out)

    def test_await_of_a_conditional_expression(self):
        for body in ("async def f(a):\n    return await (val(a) if a else val(2))\n",
                     "async def f(a):\n    x = await (val(a) if a else val(2))\n    return x\n"):
            with self.subTest(body):
                self.assert_same(body, (0,), (1,))

    def test_await_of_an_operation_keeps_its_operand_whole(self):
        self.assert_same(
            "class P:\n    def __init__(self, v):\n        self.v = v\n    def __add__(self, o):\n        return val(self.v + o.v)\n"
            "async def f(a):\n    return await (P(a) + P(2))\n", (3,))

    def test_await_in_a_condition(self):
        for body in ("async def f(a):\n    if await val(a):\n        return 'yes'\n    return 'no'\n",
                     "async def f(a):\n    n = 0\n    while await val(n < a):\n        n += 1\n    return n\n"):
            with self.subTest(body):
                self.assert_same(body, (0,), (3,))

    def test_await_in_a_loop(self):
        self.assert_same(
            "async def f(a):\n    total = 0\n    for i in range(a):\n        total += await val(i)\n    return total\n",
            (5,))

    def test_await_as_the_iterable_of_a_for(self):
        self.assert_same(
            "async def f(a):\n    out = []\n    for i in await val(range(a)):\n        out.append(i)\n    return out\n",
            (4,))

    def test_await_in_a_conditional_expression(self):
        self.assert_same(
            "async def f(a):\n    return (await val(1)) if a else (await val(2))\n", (0,), (1,))

    def test_await_in_a_try(self):
        self.assert_same(
            "async def f(a):\n    try:\n        return await boom(a)\n    except ValueError:\n        return 'caught'\n",
            (1,))

    def test_await_in_a_handler_and_a_finally(self):
        self.assert_same(
            "async def f(a):\n    log = []\n    try:\n        await boom(a)\n    except ValueError:\n"
            "        log.append(await val('handler'))\n    finally:\n        log.append(await val('finally'))\n"
            "    return log\n", (1,))

    def test_await_in_a_with(self):
        self.assert_same(
            "import contextlib\nasync def f(a):\n    with contextlib.suppress(KeyError):\n"
            "        x = await val(a)\n        {}[x]\n    return await val('after')\n", (3,))

    def test_an_error_from_an_await_propagates(self):
        self.assert_same("async def f(a):\n    return await boom(a)\n", (1,))

    def test_async_def_without_an_await(self):
        out = self.assert_same("async def f(a):\n    return a + 1\n", (1,))
        self.assertIn("async def f(a):", out)

    def test_async_methods_and_nested_functions(self):
        out = self.assert_same(
            "class K:\n    async def m(self, a):\n        async def inner(b):\n            return await val(b)\n"
            "        return await inner(a)\n"
            "async def f(a):\n    return await K().m(a)\n", (9,))
        self.assertIn("    async def m(self, a):", out)
        self.assertIn("async def inner(b):", out)

    def test_async_def_with_defaults_and_keywords(self):
        self.assert_same(
            "async def f(a, b=2, *rest, c=3, **kw):\n    return (await val(a)) + b + c + len(rest) + len(kw)\n",
            (1,), (1, 5))

    def test_async_generator(self):
        out = self.assert_same(
            "async def f(a):\n    for i in range(a):\n        yield await val(i)\n    yield 99\n", (3,), generator=True)
        self.assertIn("async def f(a):", out)
        self.assertIn("yield", out)

    def test_async_generator_sent_value_and_await(self):
        self.assert_same(
            "async def f(a):\n    x = await val(a)\n    yield x\n    yield x + 1\n", (4,), generator=True)

    def test_a_plain_def_stays_a_plain_def(self):
        out = decompile("def f(a):\n    return a\n")
        self.assertNotIn("async", out)

    def test_nothing_is_reported_for_await_and_async_def(self):
        from .test_helpers import _compile  # pylint: disable=import-outside-toplevel
        import os  # pylint: disable=import-outside-toplevel
        from pycrefine import get_decompiler  # pylint: disable=import-outside-toplevel
        pyc = _compile("async def f(a):\n    x = await a\n    return await x\n")
        try:
            dec = get_decompiler(pyc)
            dec.decompile()
            self.assertEqual(list(dec.diagnostics), [])
        finally:
            os.unlink(pyc)


if __name__ == "__main__":
    unittest.main()
