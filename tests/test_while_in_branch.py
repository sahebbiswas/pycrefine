"""A `while` loop, or an `if`/`else`, that ends an `if`/`elif` branch inside a `for` body (#265).

The compiler threads the exit of such a loop (or the end of such an `else`) to the end of
the whole `if` chain. The `while` guard was then taken for the enclosing `if` test (3.9 and
3.14), and the `else:` branch that follows the nested statement was pulled into the nested
block (3.9 and 3.12 to 3.14).
"""
import ast
import signal
import unittest

from .test_helpers import decompile

_TIMEOUT_SECONDS = 10


def _namespace(code):
    namespace = {}
    exec(compile(code, "<t>", "exec"), namespace)  # pylint: disable=exec-used
    return namespace


def _evaluate(call, namespace, out):
    """Evaluate *call*; a loop that never ends (the symptom of this bug) fails the test instead of hanging it."""
    if not hasattr(signal, "SIGALRM"):
        return eval(call, namespace)  # pylint: disable=eval-used

    def give_up(*_):
        raise AssertionError(f"{call} did not finish\n{out}")

    previous = signal.signal(signal.SIGALRM, give_up)
    signal.alarm(_TIMEOUT_SECONDS)
    try:
        return eval(call, namespace)  # pylint: disable=eval-used
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


class TestBranchEndingInNestedStatement(unittest.TestCase):

    def assert_same_results(self, source, *calls):
        out = decompile(source)
        ast.parse(out)
        original, rebuilt = _namespace(source), _namespace(out)
        for call in calls:
            self.assertEqual(_evaluate(call, rebuilt, out), eval(call, original), f"{call}\n{out}")  # pylint: disable=eval-used
        return out

    def test_while_in_elif_with_else_after(self):
        """The shape of the tokenizer in the corpus: the loop ends the `elif` branch."""
        src = ("def f(tokens):\n    out, stack = [], []\n    for t in tokens:\n        if t == 'n':\n"
               "            out.append(t)\n        elif t == ')':\n            while stack:\n"
               "                out.append(stack.pop())\n        else:\n            stack.append(t)\n"
               "    return out, stack\n")
        out = self.assert_same_results(src, "f(['n', '(', '+', ')', 'n'])", "f(['a', 'b', ')', 'c'])", "f([])")
        self.assertIn("while stack:", out)

    def test_while_in_if_else(self):
        src = ("def g(tokens):\n    out, stack = [], []\n    for t in tokens:\n        if t == ')':\n"
               "            while stack:\n                out.append(stack.pop())\n        else:\n"
               "            stack.append(t)\n    return out, stack\n")
        out = self.assert_same_results(src, "g(['a', 'b', ')', 'c'])", "g([')'])")
        self.assertIn("while stack:", out)

    def test_statement_after_the_chain(self):
        src = ("def h(tokens):\n    out, stack = [], []\n    for t in tokens:\n        if t == 'n':\n"
               "            out.append(t)\n        elif t == ')':\n            while stack:\n"
               "                out.append(stack.pop())\n        else:\n            stack.append(t)\n"
               "        out.append('.')\n    return out, stack\n")
        self.assert_same_results(src, "h(['n', 'a', 'b', ')', 'c'])")

    def test_loop_with_a_compound_condition_and_code_after_it(self):
        """The shunting-yard step: a loop, then an `if`/`else` that ends the branch, then an outer `else`."""
        src = ("def a(tokens):\n    out, stack = [], []\n    for t in tokens:\n        if t == 'n':\n"
               "            out.append(t)\n        elif t == ')':\n            while stack and stack[-1] != '(':\n"
               "                out.append(stack.pop())\n            if not stack:\n                out.append('!')\n"
               "            else:\n                stack.pop()\n        else:\n"
               "            while stack and stack[-1] == '*':\n                out.append(stack.pop())\n"
               "            stack.append(t)\n    while stack:\n        out.append(stack.pop())\n    return out\n")
        self.assert_same_results(src, "a(['n', '(', '*', '+', 'n', ')', '*', 'n', '-', 'x'])", "a([')', 'n'])", "a([])")

    def test_while_with_an_else_clause_in_a_branch(self):
        src = ("def b(items):\n    out = []\n    for x in items:\n        if x > 5:\n            i = 0\n"
               "            while i < x:\n                i += 3\n            else:\n                out.append('done')\n"
               "        else:\n            out.append(x)\n    return out\n")
        self.assert_same_results(src, "b([1, 7, 9])")

    def test_while_true_with_break_in_a_branch(self):
        src = ("def c(items):\n    out = []\n    for x in items:\n        if x > 5:\n            i = 0\n"
               "            while True:\n                i += 1\n                if i > x:\n                    break\n"
               "            out.append(i)\n        elif x > 2:\n            out.append('mid')\n        else:\n"
               "            out.append(x)\n    return out\n")
        self.assert_same_results(src, "c([1, 3, 8, 7])")

    def test_while_with_continue_in_a_middle_branch(self):
        src = ("def d(items):\n    out = []\n    for x in items:\n        if x == 1:\n            out.append('one')\n"
               "        elif x > 3:\n            n = x\n            while n > 0:\n                n -= 2\n"
               "                if n == 5:\n                    continue\n                out.append(n)\n"
               "        elif x == 2:\n            out.append('two')\n        else:\n            out.append('other')\n"
               "    return out\n")
        self.assert_same_results(src, "d([1, 2, 3, 4, 9, 0])")

    def test_while_in_the_else_of_a_branch(self):
        src = ("def g(items):\n    out = []\n    for x in items:\n        if x:\n            out.append(1)\n"
               "        else:\n            k = x\n            while k < 3:\n                k += 1\n"
               "                out.append(k)\n    return out\n")
        self.assert_same_results(src, "g([0, 1, -2])")

    def test_while_in_an_if_outside_a_loop(self):
        src = ("def e(x, stack):\n    if x:\n        while stack:\n            stack.pop()\n    else:\n"
               "        stack.append(x)\n    return stack\n"
               "def f(x, stack):\n    if x:\n        while stack:\n            stack.pop()\n    return stack\n")
        self.assert_same_results(src, "e(1, [1, 2])", "e(0, [1, 2])", "f(1, [1, 2])", "f(0, [1, 2])")

    def test_if_else_that_ends_an_elif_branch_keeps_the_outer_else(self):
        """No `while` needed: the outer `else:` ran after `stack.pop()` as well."""
        src = ("def a(tokens):\n    out, stack = [], []\n    for t in tokens:\n        if t == 'n':\n"
               "            out.append(t)\n        elif t == ')':\n            out.append('c')\n"
               "            if not stack:\n                out.append('!')\n            else:\n"
               "                stack.pop()\n        else:\n            stack.append(t)\n    return out\n")
        out = self.assert_same_results(src, "a(['n', '(', '+', ')', 'n'])", "a([')', 'n'])", "a([])")
        self.assertEqual(out.count("stack.append(t)"), 1)

    def test_if_else_that_ends_an_elif_branch_with_code_after_the_chain(self):
        src = ("def b(tokens):\n    out, stack = [], []\n    for t in tokens:\n        if t == 'n':\n"
               "            out.append(t)\n        elif t == ')':\n            if not stack:\n"
               "                out.append('!')\n            else:\n                stack.pop()\n        else:\n"
               "            stack.append(t)\n        out.append('.')\n    return out\n")
        self.assert_same_results(src, "b(['n', '(', '+', ')', 'n'])", "b([')', 'n'])")

    def test_while_else_with_break_in_a_branch(self):
        """`break` skips the loop's `else:`, so flattening that body after the loop would change the result."""
        src = ("def k(items):\n    out = []\n    for x in items:\n        if x > 5:\n            i = 0\n"
               "            while i < x:\n                i += 1\n                if i == 2:\n                    break\n"
               "            else:\n                out.append('done')\n"
               "        else:\n            out.append(x)\n    return out\n")
        self.assert_same_results(src, "k([1, 7, 9])")

    def test_break_that_ends_the_branch_leaves_only_the_while(self):
        """With nothing after the loop, `break` is threaded to the head of the `for`: it must not become a `continue`."""
        src = ("def e(items):\n    out = []\n    for x in items:\n        if x > 5:\n            i = 0\n"
               "            while i < x:\n                i += 1\n                if i == 2:\n                    break\n"
               "                out.append(i)\n        else:\n            out.append(x)\n    return out\n")
        self.assert_same_results(src, "e([1, 7, 9])")

    def test_inner_else_that_continues_before_the_outer_else(self):
        """An `else: continue` ends the branch, and the outer `else:` follows it."""
        src = ("def f(items, a, b, out):\n    for x in items:\n        if a:\n            if b:\n"
               "                out.append('fn')\n            else:\n                continue\n"
               "        else:\n            out.append('g')\n        out.append('h')\n    return out\n")
        self.assert_same_results(src, "f([1, 2], True, True, [])", "f([1, 2], True, False, [])",
                                 "f([1, 2], False, False, [])")

    def test_deeper_nesting_closes_every_branch(self):
        src = ("def f(items):\n    out = []\n    for x in items:\n        if x == 0:\n            out.append('z')\n"
               "        elif x < 10:\n            if x % 2:\n                if x > 4:\n                    out.append('big odd')\n"
               "                else:\n                    out.append('small odd')\n            else:\n"
               "                out.append('even')\n        else:\n            out.append('huge')\n    return out\n")
        self.assert_same_results(src, "f([0, 3, 7, 4, 12])")


if __name__ == "__main__":
    unittest.main()
