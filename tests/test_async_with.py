"""`async with` (#85).

The entry and the exit of an `async with` await what `__aenter__` and `__aexit__` return. The awaits are no source, so they
are folded away and the rest reads as the `with` the decompiler already rebuilds, written with `async`: on 3.11 and later
`BEFORE_ASYNC_WITH` (3.14: the `LOAD_SPECIAL __aenter__` preamble) with a `GET_AWAITABLE` loop after it, an awaited exit
call, and an awaited `WITH_EXCEPT_START` in the handler.
"""
import ast
import asyncio
import sys
import unittest

from .test_helpers import decompile

_PRELUDE = (
    "import asyncio\n"
    "class CM:\n"
    "    def __init__(self, log, name, suppress=False):\n"
    "        self.log, self.name, self.suppress = log, name, suppress\n"
    "    async def __aenter__(self):\n"
    "        self.log.append(('enter', self.name))\n"
    "        await asyncio.sleep(0)\n"
    "        return self.name + '!'\n"
    "    async def __aexit__(self, kind, value, tb):\n"
    "        seen = kind.__name__ if kind else None\n"
    "        self.log.append(('exit', self.name, seen))\n"
    "        await asyncio.sleep(0)\n"
    "        return self.suppress\n"
    "async def val(x):\n    await asyncio.sleep(0)\n    return x\n")


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _outcome(function, args):
    """The value *function* gives for *args* (or the type of the error it raised) and what the managers logged."""
    log = []

    async def drive():
        return await function(log, *args)
    try:
        result = ("value", asyncio.run(drive()))
    except Exception as exc:  # pylint: disable=broad-except
        result = ("raised", type(exc).__name__)
    return result, log


@unittest.skipIf(sys.version_info < (3, 11), "async with is read from 3.11 on (3.10: #303)")
class TestAsyncWith(unittest.TestCase):

    def assert_same(self, body, *calls, name="f"):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for args in calls:
            self.assertEqual(_outcome(rebuilt[name], args), _outcome(original[name], args), f"{args}\n{out}")
        return out

    def test_async_with_and_as(self):
        out = self.assert_same(
            "async def f(log):\n    async with CM(log, 'a') as x:\n        log.append(x)\n    return log\n", ())
        self.assertIn("async with CM(log, 'a') as x:", out)

    def test_async_with_without_as(self):
        out = self.assert_same("async def f(log):\n    async with CM(log, 'a'):\n        log.append(1)\n    return 2\n", ())
        self.assertIn("async with CM(log, 'a'):", out)

    def test_code_after_the_block_stays_after_it(self):
        out = self.assert_same(
            "async def f(log):\n    async with CM(log, 'a') as x:\n        y = x\n    log.append('after')\n    return y\n", ())
        self.assertNotIn("        log.append('after')", out)

    def test_await_in_the_body(self):
        self.assert_same(
            "async def f(log):\n    async with CM(log, 'a') as x:\n        y = await val(x)\n        log.append(await val(y))\n"
            "    return y\n", ())

    def test_return_in_the_body(self):
        self.assert_same(
            "async def f(log, a):\n    async with CM(log, 'a') as x:\n        if a:\n            return x\n"
            "        log.append('body')\n    return 'end'\n", (True,), (False,))

    def test_return_of_an_await_in_the_body(self):
        self.assert_same(
            "async def f(log):\n    async with CM(log, 'a') as x:\n        return await val(x)\n", ())

    def test_two_managers_in_one_statement(self):
        self.assert_same(
            "async def f(log):\n    async with CM(log, 'a') as x, CM(log, 'b') as y:\n        return x + y\n", ())

    def test_nested_blocks(self):
        self.assert_same(
            "async def f(log):\n    async with CM(log, 'a') as x:\n        async with CM(log, 'b') as y:\n"
            "            log.append(x + y)\n        log.append('between')\n    return log\n", ())

    def test_an_error_in_the_body_reaches_the_exit(self):
        self.assert_same(
            "async def f(log):\n    async with CM(log, 'a'):\n        raise ValueError('x')\n", ())

    def test_an_exit_that_swallows_the_error(self):
        self.assert_same(
            "async def f(log):\n    async with CM(log, 'a', suppress=True):\n        raise ValueError('x')\n"
            "    log.append('continued')\n    return 'ok'\n", ())

    def test_try_in_the_body_and_around_the_block(self):
        self.assert_same(
            "async def f(log):\n    try:\n        async with CM(log, 'a') as x:\n            log.append(1 / 0)\n"
            "    except ZeroDivisionError:\n        log.append('caught')\n    return log\n", ())
        self.assert_same(
            "async def f(log):\n    async with CM(log, 'a'):\n        try:\n            log.append(1 / 0)\n"
            "        except ZeroDivisionError:\n            log.append('inner')\n    return log\n", ())

    def test_in_a_loop_with_continue_and_break(self):
        self.assert_same(
            "async def f(log, n):\n    for i in range(n):\n        async with CM(log, str(i)):\n            if i == 1:\n"
            "                continue\n            if i == 3:\n                break\n            log.append(await val(i))\n"
            "    return log\n", (5,))

    def test_in_a_method(self):
        self.assert_same(
            "class K:\n    async def run(self, log):\n        async with CM(log, 'k') as x:\n            return x\n"
            "async def f(log):\n    return await K().run(log)\n", ())

    def test_unpacking_and_captured_targets(self):
        self.assert_same(
            "class P(CM):\n    async def __aenter__(self):\n        return (1, 2)\n"
            "async def f(log):\n    async with P(log, 'p') as (x, y):\n        return x + y\n", ())
        out = self.assert_same(
            "async def f(log):\n    async with CM(log, 'a') as x:\n        get = lambda: x\n    return get()\n", ())
        self.assertIn("as x:", out)

    def test_attribute_and_subscript_targets(self):
        for target in ("o.x", "d[0]", "(x, o.a)", "(o.a, d[1])"):
            with self.subTest(target):
                self.assert_same(
                    "class P(CM):\n    async def __aenter__(self):\n        return (1, 2)\n"
                    "class Box:\n    x = a = None\n"
                    f"async def f(log):\n    o, d = Box(), {{}}\n    async with P(log, 'p') as {target}:\n"
                    "        log.append('in')\n    return log, o.__dict__, d\n", ())

    def test_async_with_in_a_handler_and_a_finally(self):
        out = self.assert_same(
            "async def f(log):\n    try:\n        raise ValueError('v')\n    except ValueError:\n"
            "        async with CM(log, 'h'):\n            log.append('in handler')\n    finally:\n"
            "        async with CM(log, 'f'):\n            log.append('in finally')\n    return log\n", ())
        self.assertEqual(out.count("async with"), 2)

    def test_a_plain_with_stays_a_plain_with(self):
        out = decompile("import contextlib\ndef f():\n    with contextlib.suppress(KeyError):\n        pass\n")
        self.assertNotIn("async", out)

    def test_nothing_is_reported_for_async_with(self):
        import os  # pylint: disable=import-outside-toplevel
        from pycrefine import get_decompiler  # pylint: disable=import-outside-toplevel
        from .test_helpers import _compile  # pylint: disable=import-outside-toplevel
        pyc = _compile(_PRELUDE + "async def f(log):\n    async with CM(log, 'a') as x:\n        return x\n")
        try:
            dec = get_decompiler(pyc)
            dec.decompile()
            self.assertEqual([d for d in dec.diagnostics if "async" in str(d)], [])
        finally:
            os.unlink(pyc)


if __name__ == "__main__":
    unittest.main()
