"""`yield from`, and the value of a `yield` expression (#152).

`yield from x` compiles to a loop that sends each value on (3.11+: SEND, YIELD_VALUE, JUMP_BACKWARD_NO_INTERRUPT, with a
CLEANUP_THROW handler after the code) or to GET_YIELD_FROM_ITER; YIELD_FROM (3.9, 3.10). The value that `yield` gives back
was dropped: `x = yield` came out as a `yield` statement and a name that was never assigned.
"""
import ast
import os
import unittest

from .test_helpers import decompile

_PRELUDE = ("def inner(n):\n    for i in range(n):\n        got = yield i\n        if got:\n            return got\n"
            "    return 'done'\n")


def run(gen, *sends):
    """Drive *gen*: what it yields for each value sent in, and its return value."""
    out = []
    try:
        out.append(next(gen))
        for value in sends:
            out.append(gen.send(value))
    except StopIteration as ex:
        out.append(("stop", ex.value))
    return out


def thrown(gen):
    out = [next(gen)]
    try:
        out.append(gen.throw(ValueError("x")))
    except StopIteration as ex:
        out.append(("stop", ex.value))
    return out


def _namespace(code):
    namespace = {"run": run, "thrown": thrown}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class TestYieldExpressions(unittest.TestCase):

    def assert_same_results(self, body, *calls):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_yield_from_then_a_value(self):
        """The reproduction in the issue."""
        out = self.assert_same_results(
            "def gen():\n    yield from range(3)\n    v = yield 1\n    return v\n",
            "run(gen(), None, None, None, 42)")
        self.assertIn("yield from range(3)", out)
        self.assertIn("v = yield 1", out)

    def test_result_of_yield_from(self):
        out = self.assert_same_results(
            "def a():\n    r = yield from inner(3)\n    yield r\n",
            "run(a(), None, None, 7, None)", "run(a(), None, None, None, None)")
        self.assertIn("r = yield from inner(3)", out)

    def test_return_yield_from(self):
        self.assert_same_results(
            "def b():\n    return (yield from inner(2))\n", "run(b(), None, 5)", "run(b(), None, None, None)")

    def test_yield_from_in_a_loop(self):
        self.assert_same_results(
            "def f(items):\n    for it in items:\n        yield from inner(it)\n    yield 'end'\n",
            "run(f([2, 1]), None, None, None, None, None)")

    def test_yield_from_an_empty_iterable_then_code(self):
        self.assert_same_results(
            "def f():\n    yield from ()\n    yield 'after'\n", "run(f(), None)")

    def test_echo_loop(self):
        out = self.assert_same_results(
            "def c():\n    total = 0\n    while True:\n        x = yield total\n        if x is None:\n            break\n"
            "        total += x\n    return total\n", "run(c(), 1, 2, 3, None)")
        self.assertIn("x = yield total", out)

    def test_value_used_in_an_expression(self):
        self.assert_same_results(
            "import io\n"
            "def d(log):\n    print((yield 1), file=log)\n    y = (yield) + 1\n    log.write(str(y))\n"
            "def go(sends):\n    log = io.StringIO()\n    return run(d(log), *sends), log.getvalue()\n", "go(['A', 4])")

    def test_two_yields_in_one_statement(self):
        self.assert_same_results(
            "def g(c=True):\n    x = yield (1 if c else 2)\n    yield [x, (yield 5)]\n", "run(g(), 10, 20, 30)",
            "run(g(False), 10, 20, 30)")

    def test_bare_yield_and_yield_none(self):
        out = self.assert_same_results(
            "def i2():\n    yield\n    z = yield None\n    yield z\n", "run(i2(), None, 'z', None)")
        self.assertRegex(out, r"(?m)^    yield$")

    def test_plain_yield_statements_are_unchanged(self):
        out = decompile("def gen():\n    yield 1\n    yield 2\n")
        self.assertIn("    yield 1\n    yield 2", out)

    def test_generator_expression_is_unchanged(self):
        self.assert_same_results("def ge(xs):\n    return sum(x * 2 for x in xs)\n", "ge([1, 2, 3])")

    def test_yield_from_a_generator_expression(self):
        self.assert_same_results("def f(xs):\n    yield from (x * 2 for x in xs)\n", "run(f([1, 2]), None, None)")


class TestYieldInsideTryAndWith(unittest.TestCase):

    def assert_same_results(self, body, *calls):
        source = _PRELUDE + body
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(eval(call, rebuilt), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_value_sent_into_a_try(self):
        self.assert_same_results(
            "def e():\n    try:\n        v = yield 1\n    except ValueError:\n        v = 'caught'\n    yield v\n",
            "run(e(), 'v', None)", "thrown(e())")

    def test_value_sent_into_a_with(self):
        self.assert_same_results(
            "def h(log):\n    with open(" + repr(os.devnull) + ") as fh:\n        v = yield fh.name\n        log.append(v)\n"
            "def go():\n    log = []\n    return run(h(log), 'sent'), log\n", "go()")

    def test_yield_from_in_a_try(self):
        self.assert_same_results(
            "def t():\n    try:\n        r = yield from inner(2)\n    except ValueError:\n        r = 'bad'\n    yield r\n",
            "run(t(), None, None, None)", "thrown(t())")

    def test_chained_assignment_of_a_yielded_value(self):
        self.assert_same_results(
            "def g():\n    a = b = yield 1\n    return a, b\n", "run(g(), 'v')")

    def test_yield_from_as_a_conditional_arm(self):
        self.assert_same_results(
            "def consume(x):\n    return x\n"
            "def h(c):\n    return consume((yield from inner(2)) if c else 0)\n",
            "run(h(True), None, None, None)", "run(h(False), None)")

    def test_yield_as_a_conditional_arm(self):
        self.assert_same_results(
            "def k(c):\n    x = (yield 1) if c else 0\n    return x\n", "run(k(True), 'a')", "run(k(False), 'a')")

    def test_yield_from_in_a_handler_and_a_finally(self):
        """3.12+ keeps the CLEANUP_THROW of a `yield from` in a handler in line, and its table entries split the ranges."""
        out = self.assert_same_results(
            "def boom():\n    raise ValueError('x')\n    yield 0\n"
            "def t():\n    log = []\n    try:\n        yield from boom()\n    except ValueError:\n"
            "        log.append((yield from inner(1)))\n    finally:\n        log.append((yield from inner(1)))\n"
            "    return log\n",
            "run(t(), None, None, None, None)")
        self.assertIn("return log", out)

    def test_yield_from_in_the_body_of_a_with(self):
        out = self.assert_same_results(
            "import contextlib\n"
            "def w(log):\n    with contextlib.suppress(KeyError):\n        x = yield from inner(2)\n        {}[x]\n"
            "    log.append('after')\n    return (yield from inner(1))\n"
            "def go():\n    log = []\n    return run(w(log), None, None, None, None), log\n",
            "go()")
        self.assertNotIn("        return (yield from", out)

    def test_return_of_a_yield_keeps_its_parentheses(self):
        out = self.assert_same_results("def e():\n    return (yield 1)\n", "run(e(), 'sent')")
        self.assertIn("return (yield 1)", out)

    def test_yield_as_a_condition_keeps_its_parentheses(self):
        self.assert_same_results(
            "def c():\n    if (yield 1):\n        return 'yes'\n    return 'no'\n", "run(c(), True)", "run(c(), False)")
        self.assert_same_results(
            "def c():\n    n = 0\n    while (yield n):\n        n += 1\n    return n\n", "run(c(), True, True, False)")

    def test_a_generator_does_not_end_with_a_return_none(self):
        """3.12+ lays the StopIteration handler of a generator after its last return."""
        out = self.assert_same_results("def g(a):\n    yield 1\n    yield a\n", "run(g(5), None, None)")
        self.assertNotIn("return None", out)


if __name__ == "__main__":
    unittest.main()
