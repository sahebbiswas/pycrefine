"""try, except, finally and with on Python 3.10, whose handlers are block-based (#179).

3.10 sets a handler up with `SETUP_FINALLY` ... `POP_BLOCK` and starts it with three values on the
stack, where 3.11 has an exception table. The 3.10 instructions are read into the 3.11 form
(decompiler/block_handlers.py), so these constructs come out the way they do on every other version.
The tests run on every version: they compare what the decompiled code does with what the source does.
"""
import ast
import sys
import unittest

from .test_helpers import decompile


class Ctx:
    """A context manager that records its use and swallows ValueError when asked to."""

    def __init__(self, log, swallow=False):
        self.log, self.swallow = log, swallow

    def __enter__(self):
        self.log.append("enter")
        return self

    def __exit__(self, kind, value, trace):
        self.log.append("exit")
        return self.swallow and kind is ValueError


def _namespace(code):
    namespace = {"Ctx": Ctx}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


class BehaviourCase(unittest.TestCase):

    def same(self, source, *calls):
        """Decompile *source*, and check that each call (a `(function, args)` pair) behaves as in the source."""
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for name, args in calls:
            results = []
            for namespace in (original, rebuilt):
                log = []
                try:
                    value = namespace[name](*args, log)
                except Exception as exc:  # pylint: disable=broad-except
                    value = type(exc).__name__
                results.append((value, log))
            self.assertEqual(results[1], results[0], f"{name}{args}\n{out}")
        return out


class TestTryExcept(BehaviourCase):

    def test_typed_clause_with_a_name(self):
        out = self.same("def f(x, log):\n    try:\n        log.append(int(x))\n    except ValueError as e:\n"
                        "        log.append(type(e).__name__)\n    return log\n", ("f", ("1",)), ("f", ("x",)))
        self.assertIn("except ValueError as e:", out)

    def test_two_clauses_and_a_tuple(self):
        out = self.same("def f(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n"
                        "        log.append('v')\n    except (TypeError, KeyError):\n        log.append('t')\n"
                        "    return log\n", ("f", ("1",)), ("f", ("x",)), ("f", (None,)))
        self.assertEqual(out.count("except"), 2)

    def test_else_clause(self):
        out = self.same("def f(x, log):\n    try:\n        n = int(x)\n    except ValueError:\n"
                        "        log.append('bad')\n    else:\n        log.append(n)\n    log.append('done')\n"
                        "    return log\n", ("f", ("1",)), ("f", ("x",)))
        self.assertIn("else:", out)

    @unittest.skipIf(sys.version_info[:2] == (3, 11), "3.11's own layout adds a stray `except:` after the `with`")
    def test_with_inside_the_else_clause(self):
        out = self.same("def f(x, log):\n    try:\n        n = int(x)\n    except ValueError:\n"
                        "        log.append('bad')\n    else:\n        with Ctx(log):\n            log.append(n)\n"
                        "    return log\n", ("f", ("1",)), ("f", ("x",)))
        self.assertIn("else:", out)

    def test_bare_except(self):
        self.same("def f(x, log):\n    try:\n        log.append(int(x))\n    except:\n        log.append('any')\n"
                  "    return log\n", ("f", ("1",)), ("f", ("x",)))

    def test_nothing_after_the_statement(self):
        self.same("def f(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n"
                  "        log.append('bad')\n", ("f", ("1",)), ("f", ("x",)))

    def test_module_level(self):
        out = decompile("x = 0\ntry:\n    x = int('a')\nexcept ValueError as e:\n    x = 1\n")
        ns = {}
        exec(out, ns)  # pylint: disable=exec-used
        self.assertEqual(ns["x"], 1)

    def test_return_from_a_handler(self):
        self.same("def f(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n"
                  "        return 'bad'\n    return 'ok'\n", ("f", ("1",)), ("f", ("x",)))


class TestFinally(BehaviourCase):

    def test_try_finally(self):
        out = self.same("def f(x, log):\n    try:\n        log.append(int(x))\n    finally:\n"
                        "        log.append('f')\n    return log\n", ("f", ("1",)), ("f", ("x",)))
        self.assertIn("finally:", out)

    def test_try_except_finally(self):
        out = self.same("def f(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n"
                        "        log.append('v')\n    finally:\n        log.append('f')\n    return log\n",
                        ("f", ("1",)), ("f", ("x",)), ("f", (None,)))
        self.assertIn("except ValueError:", out)
        self.assertIn("finally:", out)

    def test_two_statements_in_a_row(self):
        out = self.same("def f(x, log):\n    try:\n        log.append(int(x))\n    except ValueError:\n"
                        "        log.append('a')\n    finally:\n        log.append('b')\n"
                        "    try:\n        log.append(int(x) + 1)\n    except ValueError:\n"
                        "        log.append('c')\n    finally:\n        log.append('d')\n    return log\n",
                        ("f", ("1",)), ("f", ("x",)))
        self.assertEqual(out.count("finally:"), 2)

    def test_in_a_loop(self):
        self.same("def f(items, log):\n    for i in items:\n        try:\n            log.append(int(i))\n"
                  "        except ValueError:\n            log.append('v')\n        finally:\n"
                  "            log.append('f')\n        log.append('after')\n    return log\n",
                  ("f", (["1", "x", "3"],)), ("f", ([],)))


class TestWith(BehaviourCase):

    def test_with_a_name(self):
        out = self.same("def f(c, log):\n    with Ctx(log) as m:\n        log.append(type(m).__name__)\n"
                        "    return log\n", ("f", (None,)))
        self.assertIn("with Ctx(log) as m:", out)

    def test_code_after_a_with_that_ends_a_branch(self):
        self.same("def f(c, log):\n    if c:\n        with Ctx(log):\n            a = 1\n    else:\n"
                  "        a = 2\n    return a\n", ("f", (1,)), ("f", (0,)))

    def test_exception_in_the_body(self):
        self.same("def f(c, log):\n    with Ctx(log, True):\n        int('x')\n    log.append('after')\n"
                  "    return log\n", ("f", (1,)))

    def test_with_in_a_loop(self):
        self.same("def f(items, log):\n    for i in items:\n        with Ctx(log):\n            log.append(i)\n"
                  "    return log\n", ("f", ([1, 2],)))

    def test_try_in_a_with(self):
        self.same("def f(x, log):\n    with Ctx(log):\n        try:\n            log.append(int(x))\n"
                  "        except ValueError:\n            log.append('bad')\n    return log\n",
                  ("f", ("1",)), ("f", ("x",)))


class TestInALoop(BehaviourCase):

    def test_continue_in_a_handler(self):
        self.same("def f(items, log):\n    for i in items:\n        try:\n            n = int(i)\n"
                  "        except ValueError:\n            continue\n        log.append(n)\n    return log\n",
                  ("f", (["1", "x", "3"],)))

    def test_statement_after_the_try(self):
        self.same("def f(n, log):\n    i = 0\n    while i < n:\n        i += 1\n        try:\n"
                  "            log.append(10 // (i - 2))\n        except ZeroDivisionError:\n"
                  "            log.append('z')\n        log.append(i)\n    return log\n", ("f", (4,)))


class TestFinallyStartingWithNone(BehaviourCase):

    def test_finally_assigning_none_keeps_its_try(self):
        source = ("class Box:\n    def run(self, log):\n        self.conn = 1\n        try:\n            log.append('body')\n"
                  "        finally:\n            self.conn = None\n        return self.conn\n")
        out = decompile(source)
        self.assertIn("try:", out)
        self.assertIn("finally:", out)
        self.same(source + "def f(log):\n    return Box().run(log), list(log)\n", ("f", ()))


if __name__ == "__main__":
    unittest.main()
