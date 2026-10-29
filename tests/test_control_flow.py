import ast
import os
import sys
import unittest

from .test_helpers import _run39_full_impl, assert_contains, assert_not_contains, decompile


class TestControlFlow(unittest.TestCase):
    def test_if_simple(self):
        out = decompile("x = 1\nif x > 0:\n    print(x)\n")
        assert_contains(out, "if x > 0:", "print(x)")

    def test_if_else(self):
        out = decompile("x = 1\nif x > 0:\n    y = 1\nelse:\n    y = 0\n")
        self.assertIn("x > 0", out)
        self.assertTrue("y = 1" in out or "1 if" in out)
        self.assertTrue("y = 0" in out or "else 0" in out)

    def test_if_elif_else_flattening(self):
        src = (
            "def test_elif(x):\n"
            "    y = 0\n"
            "    if x == 1:\n"
            "        print('a')\n"
            "        y = 1\n"
            "    else:\n"
            "        if x == 2:\n"
            "            print('b')\n"
            "            y = 2\n"
            "        else:\n"
            "            print('c')\n"
            "            y = 3\n"
            "    print('done')\n"
            "    return y\n"
        )
        out = decompile(src)
        self.assertIn("elif x == 2:", out)
        self.assertNotRegex(out, r"else:\s*\n\s*if")

    def test_for_else_no_flattening(self):
        """for...else: the beautifier must never inject an `elif` keyword, and the decompiler must emit `else:`."""
        src = (
            "def f(items):\n"
            "    for x in items:\n"
            "        if x:\n"
            "            break\n"
            "    else:\n"
            "        print('not found')\n"
            "    print('done')\n"
        )
        out = decompile(src)
        self.assertNotIn("elif", out)
        self.assertIn("for", out)
        self.assertIn("else:", out)

    @unittest.skipIf(sys.version_info >= (3, 10), "try..else only implemented for 3.9 so far")
    def test_try_else_strict(self):
        """try...else: verify `else:` is emitted and `elif` is absent."""
        src = (
            "def f():\n"
            "    try:\n"
            "        risky = int('1')\n"
            "    except ValueError:\n"
            "        risky = 0\n"
            "    else:\n"
            "        print('ok', risky)\n"
            "        print('done')\n"
        )
        out = decompile(src)
        self.assertNotIn("elif", out)
        self.assertIn("try:", out)
        self.assertIn("else:", out)

    def test_try_else_no_flattening_unsupported(self):
        """try...else: the beautifier must never inject an `elif` keyword.

        Note: the decompiler may not emit `else:` for try...else on any Python
        version (known limitation), so we only verify the negative constraint that
        `elif` is absent and `try:` is present.
        """
        src = (
            "def f():\n"
            "    try:\n"
            "        risky = int('1')\n"
            "    except ValueError:\n"
            "        risky = 0\n"
            "    else:\n"
            "        print('ok', risky)\n"
            "        print('done')\n"
        )
        out = decompile(src)
        self.assertNotIn("elif", out)
        self.assertIn("try:", out)

    def test_for_loop(self):
        out = decompile("for i in range(3):\n    print(i)\n")
        assert_contains(out, "for i in range(3):", "print(i)")

    def test_for_loop_variable_name(self):
        out = decompile("items = [1, 2]\nfor item in items:\n    pass\n")
        assert_contains(out, "for item in items:")

    def test_for_tuple_unpack(self):
        out = decompile("pairs = [(1, 2)]\nfor a, b in pairs:\n    print(a)\n")
        assert_contains(out, "for a, b in pairs:", "print(a)")

    def test_while_conditional(self):
        out = decompile("n = 0\nwhile n < 5:\n    n += 1\n")
        assert_contains(out, "while n < 5:", "n += 1")

    def test_while_conditional_no_stray_if(self):
        out = decompile("n = 0\nwhile n < 5:\n    n += 1\n")
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        cond_headers = [ln for ln in lines if ln in ("while n < 5:", "if n < 5:")]
        self.assertEqual(len(cond_headers), 1)

    def test_while_true_with_break(self):
        out = decompile("while True:\n    x = 1\n    if x > 0:\n        break\n")
        assert_contains(out, "while True:")

    def test_nested_while(self):
        src = "i = 0\nwhile i < 3:\n    j = 0\n    while j < 2:\n        j += 1\n    i += 1\n"
        out = decompile(src)
        assert_contains(out, "j += 1", "i += 1")
        self.assertIn("i < 3:", out)
        self.assertIn("j < 2:", out)

    def test_nested_while_no_duplicate_headers(self):
        src = "i = 0\nwhile i < 3:\n    j = 0\n    while j < 2:\n        j += 1\n    i += 1\n"
        out = decompile(src)
        self.assertEqual(out.count("while i < 3:"), 1)
        self.assertEqual(out.count("j < 2:"), 1)

    def test_if_comparison_equals(self):
        out = decompile("x = 1\nif x == 1:\n    pass\n")
        assert_contains(out, "x == 1")

    def test_if_comparison_not_equals(self):
        out = decompile("x = 1\nif x != 2:\n    pass\n")
        assert_contains(out, "x != 2")

    def test_if_implicit_else_avoidance(self):
        src = (
            "def test(x):\n"
            "    if x is None:\n"
            "        return True\n"
            "    if x == 1:\n"
            "        return True\n"
            "    return False\n"
        )
        out = decompile(src)
        self.assertNotIn("else:", out)
        assert_contains(out, "if x is None:", "if x == 1:", "return False")


class TestModuleLevelImplicitReturn(unittest.TestCase):
    """Module and class bodies never contain `return` (issue #90)."""

    def _check(self, src):
        out = decompile(src)
        self.assertNotIn("return", out)
        compile(out, "<decompiled>", "exec")
        return out

    def test_trailing_main_guard(self):
        out = self._check('if __name__ == "__main__":\n    print(1)\n')
        self.assertIn("print(1)", out)

    def test_trailing_try_except(self):
        out = self._check("try:\n    import foo\nexcept ImportError:\n    foo = None\n")
        self.assertIn("foo = None", out)

    def test_trailing_with(self):
        out = self._check("with open(f) as g:\n    data = g.read()\n")
        self.assertIn("data = g.read()", out)

    def test_class_body_ending_in_if(self):
        out = self._check("class A:\n    if X:\n        y = 1\n")
        self.assertIn("y = 1", out)

    def test_break_out_of_last_loop_is_kept(self):
        # 3.12+ compiles this break as `return None`.
        out = self._check("for i in range(3):\n    if i:\n        break\n    print(i)\n")
        self.assertIn("break", out)

    def test_explicit_return_none_in_function_is_kept(self):
        out = decompile("def f(a):\n    if a:\n        return None\n    print(a)\n")
        self.assertIn("return None", out)


class TestNoneGuards(unittest.TestCase):
    def test_pjif_none_emits_is_not_none(self):
        out = decompile("x = None\nif x is not None:\n    print(1)\n")
        self.assertIn("if x is not None:", out)

    def test_pjif_not_none_emits_is_none(self):
        out = decompile("x = None\nif x is None:\n    print(2)\n")
        self.assertIn("if x is None:", out)

    def test_none_guard_no_inverted_condition(self):
        out = decompile("x = None\nif x is None:\n    print('yes')\n")
        self.assertIn("print('yes')", out)

    def test_none_guard_consistency(self):
        for src in [
            "x = None\nif x is None:\n    pass\n",
            "x = None\nif x is not None:\n    pass\n",
        ]:
            out = decompile(src)
            self.assertGreater(len(out.strip()), 0)


class TestTernaryExpression(unittest.TestCase):
    def test_basic_ternary_assign(self):
        out = decompile("def f(x):\n    y = 1 if x > 0 else 0\n    return y\n")
        self.assertIn("1", out)
        self.assertIn("0", out)
        self.assertIn("x > 0", out)

    def test_ternary_correct_form(self):
        out = decompile("def f(x):\n    y = 1 if x > 0 else 0\n    return y\n")
        ternary_lines = [ln.strip() for ln in out.splitlines() if "y =" in ln and "if" in ln and "else" in ln]
        self.assertEqual(len(ternary_lines), 1)

    def test_original_reported_case(self):
        src = (
            "def test(lnotab):\n"
            "    lnotab = bytes(lnotab) if not isinstance(lnotab, bytes) else lnotab\n"
            "    print(lnotab)\n"
        )
        out = decompile(src)
        self.assertIn("isinstance", out)
        self.assertIn("bytes", out)
        self.assertNotIn("if not isinstance(lnotab, bytes):\n        pass", out)

    def test_ternary_with_call_expression(self):
        out = decompile("def f(x):\n    y = abs(x) if x < 0 else x\n")
        self.assertIn("abs", out)
        self.assertIn("x < 0", out)

    def test_ternary_with_unary(self):
        out = decompile("def f(x):\n    b = -x if x < 0 else x\n")
        self.assertIn("x < 0", out)

    def test_ternary_chain_with_other_statements(self):
        src = "def f(x):\n    a = 1\n    b = x if x > 0 else -x\n    return a + b\n"
        out = decompile(src)
        self.assertIn("a = 1", out)
        self.assertIn("return", out)

    def test_real_if_else_multistatement_not_collapsed(self):
        src = "def f(x):\n    if x > 0:\n        y = 1\n        z = 2\n    else:\n        y = 0\n        z = -1\n"
        out = decompile(src)
        self.assertIn("z", out)
        self.assertIn("if x > 0:", out)

    def test_side_effect_if_not_collapsed(self):
        src = "def f(x):\n    if x > 0:\n        print(x)\n"
        out = decompile(src)
        self.assertIn("if x > 0:", out)
        self.assertNotIn("if x > 0 else", out)

    def test_if_else_equivalent_preserves_semantics(self):
        src = "def f(x):\n    y = 1 if x > 0 else 0\n    return y\n"
        out = decompile(src)
        import ast
        ast.parse(out)


class TestCompoundConditions(unittest.TestCase):
    def test_compound_and(self):
        src = "def f(a, b):\n    if a == 1 and b == 2:\n        return True\n    return False\n"
        out = decompile(src)
        assert_contains(out, "if a == 1 and b == 2:")

    def test_compound_or(self):
        src = "def f(a, b):\n    if a == 1 or b == 2:\n        return True\n    return False\n"
        out = decompile(src)
        assert_contains(out, "if a == 1 or b == 2:")

    def test_compound_mixed_and_or(self):
        src = "def f(a, b, c):\n    if a == 1 and b == 2 or c == 3:\n        return True\n    return False\n"
        out = decompile(src)
        self.assertTrue("if a == 1 and b == 2 or c == 3:" in out or "if (a == 1 and b == 2) or c == 3:" in out)

    def test_compound_none_and(self):
        src = "def f(x):\n    if x is not None and x > 0:\n        return True\n    return False\n"
        out = decompile(src)
        assert_contains(out, "if x is not None and x > 0:")

    def test_compound_none_or(self):
        src = "def f(x, y):\n    if x is None or y is None:\n        return True\n    return False\n"
        out = decompile(src)
        self.assertTrue("if x is None or y is None:" in out or "if (x is None or y is None):" in out)

    def test_compound_complex_mixed(self):
        src = (
            "def f(x, y, z):\n"
            "    if (x is not None and x > 0) or (y is None and z == 1):\n"
            "        return True\n"
            "    return False\n"
        )
        out = decompile(src)
        self.assertTrue(
            "x is not None and x > 0 or y is None and z == 1" in out or "(x is not None and x > 0) or (y is None and z == 1)" in out)

    def test_compound_short_circuit_with_call(self):
        src = "def f(x):\n    if x is not None and len(x) > 0:\n        return x[0]\n    return None\n"
        out = decompile(src)
        assert_contains(out, "if x is not None and len(x) > 0:")

    def test_compound_nested_if_merge_regression(self):
        src = (
            "def test(x, y):\n"
            "    if x > 0:\n"
            "        if y > 0:\n"
            "            return 1\n"
            "        return 2\n"
            "    return 0\n"
        )
        out = decompile(src)
        assert_contains(out, "if x > 0:", "if y > 0:", "return 1", "return 2", "return 0")
        header_count = sum(1 for line in out.splitlines() if line.lstrip().startswith("if "))
        self.assertEqual(header_count, 2)

    def test_compound_with_function_calls(self):
        src = "def f(x, y):\n    if len(x) > 0 and (y is None or x[0] == 1):\n        return True\n    return False\n"
        out = decompile(src)
        self.assertTrue("len(x) > 0 and (y is None or x[0] == 1)" in out)

    def test_compound_flat_shared_target(self):
        src = (
            "def test(a, b, c, d, e):\n"
            "    if a is None and b is None and c is None and d is None and e is None:\n"
            "        return True\n"
            "    return False\n"
        )
        out = decompile(src)
        assert_contains(out, "if a is None and b is None and c is None and d is None and e is None:")


class TestInfiniteLoops(unittest.TestCase):
    def test_simple_while_true(self):
        src = (
            "def f():\n"
            "    while True:\n"
            "        x = 1\n"
        )
        out = decompile(src)
        self.assertIn("while True:", out)

    def test_while_true_with_break_cond(self):
        src = (
            "def f():\n"
            "    while True:\n"
            "        x = 1\n"
            "        if x > 0:\n"
            "            break\n"
        )
        out = decompile(src)
        self.assertIn("while True:", out)

    def test_outer_if_inner_while_true(self):
        src = (
            "def me(type_char):\n"
            "    if type_char == '{':\n"
            "        res = {}\n"
            "        while True:\n"
            "            k = 1\n"
            "            if k is None:\n"
            "                break\n"
            "            res[k] = 2\n"
            "        return res\n"
        )
        out = decompile(src)
        self.assertNotIn("while type_char", out)
        self.assertIn("if type_char == '{':", out)
        self.assertIn("while True:", out)

    def test_outer_if_inner_while_true_no_duplicate_while(self):
        src = (
            "def me(type_char):\n"
            "    if type_char == '{':\n"
            "        res = {}\n"
            "        while True:\n"
            "            k = 1\n"
            "            if k is None:\n"
            "                break\n"
            "            res[k] = 2\n"
            "        return res\n"
        )
        out = decompile(src)
        count = out.count("while True:")
        self.assertEqual(count, 1)

    def test_test_infinite_pattern(self):
        src = (
            "import random\n"
            "\n"
            "def load():\n"
            "    return random.randint(0, 10)\n"
            "\n"
            "def me(type_char):\n"
            "    if type_char == '{':\n"
            "        res_dict = {}\n"
            "        while True:\n"
            "            key = load()\n"
            "            if key is None:\n"
            "                break\n"
            "            res_dict[key] = load()\n"
            "        return res_dict\n"
        )
        out = decompile(src)
        self.assertNotIn("while type_char", out)
        assert_contains(out, "if type_char == '{':", "while True:", "res_dict", "load()")

    def test_while_true_not_misidentified_as_if(self):
        src = (
            "def f(items):\n"
            "    while True:\n"
            "        x = items.pop()\n"
            "        if x is None:\n"
            "            break\n"
        )
        out = decompile(src)
        self.assertIn("while True:", out)

    def test_while_true_body_indented(self):
        src = (
            "def f():\n"
            "    while True:\n"
            "        x = 1\n"
            "        if x > 5:\n"
            "            break\n"
        )
        out = decompile(src)
        lines = out.splitlines()
        while_lines = [(i, ln) for i, ln in enumerate(lines) if "while True:" in ln]
        self.assertTrue(while_lines)
        while_indent = len(while_lines[0][1]) - len(while_lines[0][1].lstrip())
        body_lines = [ln for ln in lines[while_lines[0][0]+1:] if ln.strip() and not ln.strip().startswith("def ")]
        if body_lines:
            body_indent = len(body_lines[0]) - len(body_lines[0].lstrip())
            self.assertGreater(body_indent, while_indent)


class TestTernaryExpressions(unittest.TestCase):
    def test_ternary_simple_bytes_if_else(self):
        src = (
            "def f(a):\n"
            "    a = b'\\x00\\x00' if a is None else b'\\x01\\x01'\n"
            "    return a\n"
        )
        out = decompile(src)
        self.assertIn("a is None", out)
        self.assertTrue("\\x00" in out or "b'\\x00" in out)
        self.assertTrue("\\x01" in out or "b'\\x01" in out)

    def test_ternary_int_values(self):
        src = "def f(x):\n    y = 1 if x > 0 else 0\n    return y\n"
        out = decompile(src)
        self.assertIn("x > 0", out)
        self.assertIn("1", out)
        self.assertIn("0", out)

    def test_ternary_string_values(self):
        src = "def f(x):\n    y = 'yes' if x else 'no'\n    return y\n"
        out = decompile(src)
        self.assertIn("yes", out)
        self.assertIn("no", out)

    def test_ternary_binary_multiply_in_then(self):
        src = (
            "def f(n):\n"
            "    result = ' ' * n if n > 0 else ''\n"
            "    return result\n"
        )
        out = decompile(src)
        self.assertIn("n > 0", out)
        self.assertIn("' '", out)
        self.assertIn("''", out)
        self.assertIn("*", out)
        self.assertNotIn("(' ')", out)

    def test_ternary_binary_add_in_then(self):
        src = "def f(x, n):\n    y = x + n if n > 0 else x\n    return y\n"
        out = decompile(src)
        self.assertIn("n > 0", out)
        self.assertIn("+", out)

    def test_ternary_binary_floor_div_in_then(self):
        src = "def f(a, b):\n    r = a // b if b != 0 else 0\n    return r\n"
        out = decompile(src)
        self.assertIn("//", out)
        self.assertIn("b != 0", out)

    def test_ternary_binary_op_no_extra_parens_on_literal(self):
        src = "def f(n):\n    s = ' ' * n if n > 0 else ''\n    return s\n"
        out = decompile(src)
        self.assertNotIn("(' ')", out)

    def test_ternary_closure_variable_assignment(self):
        src = (
            "def outer(flag):\n"
            "    a = 'yes' if flag else 'no'\n"
            "    def inner():\n"
            "        return a\n"
            "    return inner()\n"
        )
        out = decompile(src)
        self.assertIn("def inner", out)
        self.assertIn("yes", out)
        self.assertIn("no", out)

    def test_ternary_closure_assignment_not_dropped(self):
        src = (
            "def outer(x):\n"
            "    val = 1 if x > 0 else -1\n"
            "    def inner():\n"
            "        return val\n"
            "    return inner()\n"
        )
        out = decompile(src)
        self.assertIn("val", out)
        self.assertIn("1", out)
        self.assertIn("-1", out)


class TestTernarySuppressionAllSubclasses(unittest.TestCase):
    def _run39_full(self, instructions):
        return _run39_full_impl(instructions)

    def test_ternary_with_call_else_branch_no_func_wrapper(self):
        from pycrefine import BytecodeInstruction as Instr
        instructions = [
            Instr(124, "LOAD_FAST",          0,    "iv",          0,  True,  False),
            Instr(100, "LOAD_CONST",         0,    None,          2,  None,  False),
            Instr(93, "IS_OP",              0,    None,          4,  None,  False),
            Instr(114, "POP_JUMP_IF_FALSE",  14,   14,            6,  None,  False),
            Instr(100, "LOAD_CONST",         1,    "\x00" * 16,   8,  None,  False),
            Instr(110, "JUMP_FORWARD",       8,    20,           10,  None,  False),
            Instr(116, "LOAD_GLOBAL",        0,    "api_1",      14,  None,  True),
            Instr(124, "LOAD_FAST",          0,    "iv",         16,  None,  False),
            Instr(131, "CALL_FUNCTION",      1,    1,            18,  None,  False),
            Instr(125, "STORE_FAST",         0,    "iv",         20,  None,  True),
            Instr(124, "LOAD_FAST",          0,    "iv",         22,  None,  False),
            Instr(83, "RETURN_VALUE",       None, None,         24,  None,  False),
        ]
        out = self._run39_full(instructions)
        self.assertIn("api_1(iv)", out)
        self.assertNotIn("func(", out)

    def test_ternary_suppression_set_populated_for_call_else(self):
        from pycrefine import BytecodeInstruction as Instr
        from pycrefine import Decompiler39
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [
            Instr(124, "LOAD_FAST",          0,  "iv",    0, True,  False),
            Instr(100, "LOAD_CONST",         0,  None,    2, None,  False),
            Instr(93, "IS_OP",              0,  None,    4, None,  False),
            Instr(114, "POP_JUMP_IF_FALSE", 14,  14,      6, None,  False),
            Instr(100, "LOAD_CONST",         1,  "\x00",  8, None,  False),
            Instr(110, "JUMP_FORWARD",       8,  20,     10, None,  False),
            Instr(116, "LOAD_GLOBAL",        0,  "f",    14, None,  True),
            Instr(124, "LOAD_FAST",          0,  "iv",   16, None,  False),
            Instr(131, "CALL_FUNCTION",      1,  1,      18, None,  False),
            Instr(125, "STORE_FAST",         0,  "iv",   20, None,  True),
            Instr(83, "RETURN_VALUE",      None, None,  22, None,  False),
        ]
        dec._prescan_ternaries()
        suppress = dec.prescan.ternary_suppress
        self.assertIn(18, suppress)

    def test_ternary_suppression_does_not_leak_into_next_statement(self):
        from pycrefine import BytecodeInstruction as Instr
        from pycrefine import Decompiler39
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [
            Instr(124, "LOAD_FAST",          0,  "x",   0, True,  False),
            Instr(114, "POP_JUMP_IF_FALSE",  8,  8,     2, None,  False),
            Instr(100, "LOAD_CONST",         1,  1,     4, None,  False),
            Instr(110, "JUMP_FORWARD",       4, 10,     6, None,  False),
            Instr(100, "LOAD_CONST",         2,  2,     8, None,  True),
            Instr(125, "STORE_FAST",         0,  "x",  10, None,  False),
            Instr(90, "STORE_NAME",         1, "y",   12, None,  False),
            Instr(83, "RETURN_VALUE",      None, None, 14, None,  False),
        ]
        dec._prescan_ternaries()
        suppress = dec.prescan.ternary_suppress
        self.assertIn(4, suppress)
        self.assertIn(6, suppress)
        self.assertIn(8, suppress)
        self.assertNotIn(10, suppress)
        self.assertNotIn(12, suppress)


class TestAugAssignTernary(unittest.TestCase):
    def test_augmented_assign_ternary_no_phantom_func(self):
        src = (
            "def api_7(in_a):\n"
            "    var_1 = ''\n"
            "    var_1 += ' ' * in_a if in_a > 0 else ''\n"
            "    return var_1\n"
        )
        out = decompile(src)
        self.assertNotIn("func(", out)
        self.assertIn("var_1", out)

    def test_augmented_assign_ternary_content_preserved(self):
        src = (
            "def f(x):\n"
            "    s = ''\n"
            "    s += 'yes' if x else 'no'\n"
            "    return s\n"
        )
        out = decompile(src)
        self.assertIn("yes", out)
        self.assertIn("no",  out)
        self.assertNotIn("func(", out)

    def test_augmented_add_ternary_keeps_augmented_form(self):
        src = (
            "def g(c, a, b):\n"
            "    s = ''\n"
            "    s += a if c else b\n"
            "    return s\n"
        )
        out = decompile(src)
        self.assertIn("s += a if c else b", out)
        self.assertNotIn("func(", out)

    def test_inplace_ops_in_prescan_ternaries(self):
        from pycrefine import BytecodeInstruction as Instr
        from pycrefine import Decompiler39
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [
            Instr(124, "LOAD_FAST",      0,  "x",    0, True,  False),
            Instr(124, "LOAD_FAST",      1,  "c",    2, None,  False),
            Instr(114, "POP_JUMP_IF_FALSE", 10, 10,  4, None,  False),
            Instr(100, "LOAD_CONST",     1,  "yes",  6, None,  False),
            Instr(110, "JUMP_FORWARD",   4,  12,     8, None,  False),
            Instr(100, "LOAD_CONST",     2,  "no",  10, None,  True),
            Instr(23, "INPLACE_ADD",   None, None, 12, None,  True),
            Instr(125, "STORE_FAST",     0,  "x",   14, None,  False),
            Instr(83, "RETURN_VALUE",  None, None,  16, None,  False),
        ]
        dec._prescan_ternaries()
        suppress = dec.prescan.ternary_suppress
        self.assertIn(6, suppress)
        self.assertIn(10, suppress)


class TestWhilePrescan(unittest.TestCase):
    def _get_dec(self, src: str):
        from pycrefine import get_decompiler
        from tests.test_helpers import _compile
        pyc = _compile(src)
        try:
            dec = get_decompiler(pyc)
            dec._disassemble()
            dec._prescan_while_loops()
            return dec
        finally:
            os.unlink(pyc)

    def test_simple_while_guard_detected(self):
        dec = self._get_dec("n = 0\nwhile n < 5:\n    n += 1\n")
        if dec._has_jump_backward():
            self.assertGreaterEqual(len(dec.prescan.while_header_targets), 1)

    def test_nested_while_two_guards(self):
        src = (
            "i = 0\nwhile i < 3:\n    j = 0\n"
            "    while j < 2:\n        j += 1\n    i += 1\n"
        )
        dec = self._get_dec(src)
        detected = len(dec.prescan.while_header_targets)
        self.assertGreaterEqual(detected, 1)

    def test_dup_offsets_populated(self):
        dec = self._get_dec("n = 0\nwhile n < 5:\n    n += 1\n")
        if (3, 10) <= sys.version_info[:2] < (3, 14):
            # 3.10-3.13 inclusive (3.14 differs) copy the condition to the bottom of the loop body.
            self.assertGreater(len(dec.prescan.while_body_offsets), 0)
        else:
            # No duplicated condition: nothing may be suppressed.
            self.assertEqual(len(dec.prescan.while_body_offsets), 0)

    def test_no_while_in_if_only(self):
        dec = self._get_dec("x = 1\nif x > 0:\n    x = 2\n")
        self.assertEqual(len(dec.prescan.while_header_targets), 0)

    def test_while_loop_output_quality(self):
        out = decompile("n = 0\nwhile n < 10:\n    n += 1\n")
        self.assertIn("n += 1", out)
        has_while = "while n < 10:" in out
        has_if = "if n < 10:" in out
        self.assertTrue(has_while or has_if)
        self.assertEqual(out.count("while n < 10:") + out.count("if n < 10:"), 1)


class TestChainedExpressions(unittest.TestCase):
    def test_basic_chain_2(self):
        src = "def f(a, b, c):\n    if a < b and b < c:\n        return True\n    return False\n"
        out = decompile(src)
        self.assertIn("a < b < c", out)

    def test_basic_chain_3(self):
        src = "def f(a, b, c, d):\n    if a < b and b < c and c < d:\n        return True\n    return False\n"
        out = decompile(src)
        self.assertIn("a < b < c < d", out)

    def test_chain_mixed_ops(self):
        src = "def f(a, b, c):\n    if a <= b and b != c:\n        return True\n    return False\n"
        out = decompile(src)
        self.assertTrue("a <= b != c" in out or "a <= b and b != c" in out)

    def test_chain_negative_mismatched_vars(self):
        src = "def f(a, b, c, d):\n    if a < b and c < d:\n        return True\n    return False\n"
        out = decompile(src)
        self.assertIn("a < b", out)
        self.assertIn("c < d", out)
        self.assertNotRegex(out, r'<\s*b\s*<')

    def test_chain_negative_mismatched_ops(self):
        src = "def f(a, b, c):\n    if a < b and b + 1 < c:\n        return True\n    return False\n"
        out = decompile(src)
        self.assertIn("and", out)
        self.assertNotRegex(out, r'<\s*b\s*<')


class TestNestedTryInLoop(unittest.TestCase):
    """Regression tests for try/except blocks nested inside for/while loops."""

    def test_break_inside_try_in_for_loop(self):
        """break from inside a try block nested in a for loop must be reconstructed.

        Note: in Python 3.14+, 'except: pass' in a loop may be rendered as
        'except: continue' since both compile to identical bytecode (the handler
        simply returns to the loop head). This is semantically equivalent.
        """
        src = (
            "def f(items):\n"
            "    result = None\n"
            "    for x in items:\n"
            "        try:\n"
            "            if isinstance(x, int):\n"
            "                result = x\n"
            "                break\n"
            "        except Exception:\n"
            "            pass\n"
            "    return result\n"
        )
        out = decompile(src)
        # Must contain exactly one try: and one except block
        self.assertEqual(out.count("try:"), 1, f"Expected 1 try: block:\n{out}")
        self.assertIn("except Exception:", out)
        # No phantom else: block after the for loop
        self.assertNotIn("else:\n        pass", out)
        # try: and except: must be at loop body level (8 spaces)
        lines = out.splitlines()
        try_lines = [ln for ln in lines if "try:" in ln]
        except_lines = [ln for ln in lines if "except Exception:" in ln]
        self.assertTrue(all(ln.startswith("        ") for ln in try_lines),
                        f"try: must be at 8-space indent:\n{out}")
        self.assertTrue(all(ln.startswith("        ") for ln in except_lines),
                        f"except: must be at 8-space indent:\n{out}")

    def test_except_continue_in_for_loop(self):
        """except Exception: continue inside a for loop must be correctly decompiled.

        In Python 3.14+, 'except: continue' in a for loop is deferred and emitted
        correctly. The continue statement must appear inside the except block.
        """
        src = (
            "def f(items):\n"
            "    result = None\n"
            "    for x in items:\n"
            "        try:\n"
            "            if isinstance(x, int):\n"
            "                result = x\n"
            "                break\n"
            "        except Exception:\n"
            "            continue\n"
            "    return result\n"
        )
        out = decompile(src)
        self.assertEqual(out.count("try:"), 1, f"Expected 1 try: block:\n{out}")
        self.assertIn("except Exception:", out)
        # The handler must appear inside the for loop (8-space indent)
        lines = out.splitlines()
        except_indices = [i for i, ln in enumerate(lines) if "except Exception:" in ln]
        self.assertTrue(len(except_indices) > 0)
        for idx in except_indices:
            self.assertTrue(lines[idx].startswith("        "), f"except must be at 8-space indent:\n{out}")
            # The next non-blank line must be 'continue' at 12-space indent
            next_idx = idx + 1
            while next_idx < len(lines) and not lines[next_idx].strip():
                next_idx += 1
            self.assertTrue(next_idx < len(lines))
            self.assertEqual(lines[next_idx].lstrip(), "continue", f"Expected 'continue' after except at line {idx+1}")
            self.assertTrue(lines[next_idx].startswith("            "), f"continue must be at 12-space indent:\n{out}")

    def test_try_in_for_loop_no_phantom_else(self):
        """For loops with try/except inside must not emit a phantom else: block."""
        src = (
            "def f(items):\n"
            "    for x in items:\n"
            "        try:\n"
            "            print(x)\n"
            "        except Exception:\n"
            "            pass\n"
            "    return True\n"
        )
        out = decompile(src)
        self.assertIn("for", out)
        self.assertIn("try:", out)
        self.assertIn("except Exception:", out)
        # No phantom else after the for loop that could be caused by handler suppression
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        self.assertNotIn("else:", lines)
        # Strictly: only 1 try and 1 except, no else
        self.assertEqual(out.count("try:"), 1)
        self.assertEqual(out.count("except"), 1)

    def test_multiple_break_paths_in_loop_try(self):
        """Multiple for loops each with try/except containing break must all decompile."""
        src = (
            "def f(a, b):\n"
            "    result = None\n"
            "    if a == b:\n"
            "        for x in (4, 8, 12):\n"
            "            try:\n"
            "                if isinstance(x, int):\n"
            "                    result = x\n"
            "                    break\n"
            "            except Exception:\n"
            "                continue\n"
            "    if result is None:\n"
            "        for x in (4, 8, 12):\n"
            "            try:\n"
            "                if isinstance(x, int):\n"
            "                    result = x\n"
            "                    break\n"
            "            except Exception:\n"
            "                continue\n"
            "    return result\n"
        )
        out = decompile(src)
        # Both for loops must have their try/except blocks
        self.assertEqual(out.count("try:"), 2, f"Expected 2 try: blocks:\n{out}")
        self.assertEqual(out.count("except Exception:"), 2, f"Expected 2 except blocks:\n{out}")
        # The except blocks must be at the for-loop body level (12-space indent for nested)
        lines = out.splitlines()
        except_lines = [ln for ln in lines if ln.strip() == "except Exception:"]
        self.assertEqual(len(except_lines), 2, f"Expected 2 except lines:\n{out}")
        # All except blocks must be inside the if/for nesting (not at function level)
        for ln in except_lines:
            self.assertFalse(ln.startswith("except"), f"except must not be at function level:\n{out}")


class TestChainedComparisonSentinel(unittest.TestCase):
    def test_chained_comparison_no_sentinel(self):
        src = (
            "def _get_python_version_from_magic(version_id):\n"
            "    if 3410 <= version_id <= 3429:\n"
            "        return '3.9'\n"
            "    return None\n"
        )
        out = decompile(src)
        self.assertNotIn("?", out, "Found leaked ? sentinel in chained comparison")
        # Ensure it's not emitting multiple duplicated conditions if it can be chained
        self.assertTrue(
            "3410 <= version_id <= 3429" in out or
            ("3410 <= version_id" in out and "version_id <= 3429" in out),
            "Failed to preserve chained comparison logic"
        )

# Upper bound on traced events of one decompiled run, so a nonterminating loop fails the test.
_MAX_STEPS = 200_000


class TestWhileTrueConditionalBreak(unittest.TestCase):
    """`while True:` loops that exit through `if ...: break` (issue #96).

    On 3.12+ a loop-exit `break` at the end of module code compiles to
    `RETURN_CONST None`, and the `if` that guards it jumps straight onto the
    loop's back-edge. That jump must not be mistaken for the loop guard, or the
    condition is dropped and the loop exits (or never exits) unconditionally.
    Each case is checked by running the original and the decompiled code with
    the same scripted `input()` and comparing what they print.
    """

    _SCRIPT = ["", "", "skip", "a", "y", "q", "z", "b", "x"] * 4

    @classmethod
    def _run(cls, code):
        import builtins
        import contextlib
        import io

        feed = iter(cls._SCRIPT)
        scripted_input = lambda *args: next(feed)  # noqa: E731
        namespace = {
            "__name__": "__main__",
            "__builtins__": dict(vars(builtins), input=scripted_input),
        }
        out = io.StringIO()
        steps = 0

        def tracer(frame, event, arg):
            nonlocal steps
            steps += 1
            if steps > _MAX_STEPS:
                raise TimeoutError("step limit exceeded")
            return tracer

        previous = sys.gettrace()
        try:
            sys.settrace(tracer)
            with contextlib.redirect_stdout(out):
                exec(code, namespace)
            outcome = "ok"
        except TimeoutError:
            outcome = "step limit exceeded (loop never exited)"
        except StopIteration:
            outcome = "input exhausted (loop never exited)"
        finally:
            sys.settrace(previous)
        return out.getvalue(), outcome

    def _assert_same_behavior(self, src):
        decompiled = decompile(src)
        try:
            recompiled = compile(decompiled, "<decompiled>", "exec")
        except SyntaxError as exc:
            self.fail(f"Decompiled output does not compile ({exc}):\n{decompiled}")
        self.assertEqual(
            self._run(recompiled), self._run(compile(src, "<original>", "exec")),
            f"Decompiled loop behaves differently from the source:\n{decompiled}",
        )
        return decompiled

    def test_condition_preserved_at_module_level(self):
        out = self._assert_same_behavior(
            "while True:\n    x = input()\n    if x:\n        break\n"
        )
        assert_contains(out, "while True:", "if x:", "break")

    def test_condition_preserved_with_code_after_loop(self):
        out = self._assert_same_behavior(
            "while True:\n    x = input()\n    if x:\n        break\nprint('end', x)\n"
        )
        assert_contains(out, "while True:", "if x:", "print('end', x)")

    def test_negated_condition(self):
        self._assert_same_behavior(
            "while True:\n    x = input()\n    if not x:\n        break\n    print('got', x)\n"
        )

    def test_break_inside_function(self):
        self._assert_same_behavior(
            "def f():\n"
            "    n = 0\n"
            "    while True:\n"
            "        n += 1\n"
            "        x = input()\n"
            "        if x:\n"
            "            break\n"
            "    print('n', n)\n"
            "f()\n"
        )

    def test_nested_while_true_loops(self):
        self._assert_same_behavior(
            "while True:\n"
            "    a = input()\n"
            "    while True:\n"
            "        b = input()\n"
            "        if b == 'y':\n"
            "            break\n"
            "    print('inner done', a, b)\n"
            "    if a == 'z':\n"
            "        break\n"
        )

    def test_two_conditional_breaks(self):
        self._assert_same_behavior(
            "while True:\n"
            "    x = input()\n"
            "    if x == 'a':\n"
            "        break\n"
            "    if x == 'b':\n"
            "        print('b')\n"
            "        break\n"
            "    print('loop', x)\n"
        )

    def test_break_in_else_branch(self):
        self._assert_same_behavior(
            "while True:\n"
            "    x = input()\n"
            "    if x == 'a':\n"
            "        print('a')\n"
            "    else:\n"
            "        break\n"
            "print('out')\n"
        )

    @unittest.skipIf(sys.version_info[:2] == (3, 11),
                     "3.11 mis-decompiles loops containing try blocks (3.11 is not yet supported, see #4)")
    def test_if_inside_for_try_is_not_turned_into_while(self):
        # A conditional inside a `for` loop body must stay an `if` even though it
        # sits after an assignment and before the loop's `continue` back-edge.
        out = self._assert_same_behavior(
            "def g(a):\n"
            "    r = None\n"
            "    for i in (3, 2, 1):\n"
            "        try:\n"
            "            o = a\n"
            "            if o == 'y':\n"
            "                r = i\n"
            "                break\n"
            "        except Exception:\n"
            "            continue\n"
            "    return r\n"
            "print(g('y'), g('n'))\n"
        )
        assert_not_contains(out, "while ")

    @unittest.skipIf(sys.version_info[:2] == (3, 11),
                     "3.11 decompiles every `while cond:` loop as an `if` (3.11 is not yet supported, see #4)")
    def test_real_while_guard_still_detected(self):
        out = self._assert_same_behavior(
            "x = 3\nwhile x:\n    x = x - 1\n    print('tick', x)\nprint('end')\n"
        )
        assert_contains(out, "while x:")


if __name__ == "__main__":
    unittest.main()


class TestCodeAfterWith(unittest.TestCase):
    """Code after a `with` statement is kept (#209).

    3.11 lays the exit handler of a `with` out inline, between the statement and the code
    that follows it; the decompiler used to treat everything up to the next handler (or the
    end of the code) as that handler, so the rest of the function disappeared. Each case
    runs the original and the decompiled code and compares what they print.
    """

    _SCRIPT = TestWhileTrueConditionalBreak._SCRIPT
    _run = TestWhileTrueConditionalBreak.__dict__["_run"]
    _assert_same_behavior = TestWhileTrueConditionalBreak._assert_same_behavior

    GUARD = "class Guard:\n    def __init__(self, tag):\n        self.tag = tag\n    def __enter__(self):\n        print('enter', self.tag)\n        return self.tag\n    def __exit__(self, *exc):\n        print('exit', self.tag)\n        return False\n"

    CASES = {
        "module_level": "with Guard('a'):\n    print('body')\nprint('after')\n",
        "as_target": "with Guard('a') as tag:\n    print('body', tag)\nprint('after', tag)\n",
        "function_return": "def f():\n    with Guard('a'):\n        x = 1\n    y = 2\n    return x + y\nprint(f())\n",
        "two_in_a_row": "def f():\n    with Guard('a'):\n        x = 1\n    with Guard('b'):\n        y = 2\n    return x + y\nprint(f())\n",
        "statement_between": "def f():\n    with Guard('a'):\n        x = 1\n    print('between')\n    with Guard('b'):\n        y = 2\n    print('end')\n    return x + y\nprint(f())\n",
        "nested": "def f():\n    with Guard('a'):\n        with Guard('b'):\n            x = 1\n        print('inner done')\n    return x\nprint(f())\n",
        "in_if_else": "def f(c):\n    if c:\n        with Guard('a'):\n            print('then')\n    else:\n        with Guard('b'):\n            print('else')\n    return c\nprint(f(0), f(1))\n",
        "break_in_for": "def f(xs):\n    out = []\n    for x in xs:\n        with Guard(x):\n            if x == 2:\n                break\n            out.append(x)\n    out.append('end')\n    return out\nprint(f([1, 2, 3]), f([1]))\n",
        "continue_in_for": "def f(xs):\n    out = []\n    for x in xs:\n        with Guard(x):\n            if x == 2:\n                continue\n            out.append(x)\n        out.append('after')\n    return out\nprint(f([1, 2, 3]))\n",
        "return_in_for": "def f(xs):\n    for x in xs:\n        with Guard(x):\n            if x == 2:\n                return 'found'\n    return 'none'\nprint(f([1, 2, 3]), f([1]))\n",
        "break_in_while": "def f(n):\n    out = []\n    i = 0\n    while i < n:\n        i += 1\n        with Guard(i):\n            if i == 3:\n                break\n            out.append(i)\n    out.append('end')\n    return out\nprint(f(5), f(2))\n",
        "statement_after_with_in_loop": "def f(xs):\n    out = []\n    for x in xs:\n        with Guard(x):\n            out.append(x)\n        out.append('after')\n    return out\nprint(f([1, 2]))\n",
    }

    def test_behavior_is_preserved(self):
        for name, src in self.CASES.items():
            with self.subTest(case=name):
                self._assert_same_behavior(self.GUARD + src)

    def test_the_code_after_the_with_is_in_the_output(self):
        out = decompile(self.GUARD + self.CASES["two_in_a_row"])
        assert_contains(out, "with Guard('b'):", "return x + y")


class TestLoopContinueAndTailIf(unittest.TestCase):
    """`continue` and trailing `if`/`else` chains inside loops (issue #103).

    The compiler threads every `continue` and every branch that ends a loop body
    straight to the loop head, so those back-jumps used to be dropped along with
    the `if` guarding them. Each case runs the original and the decompiled code
    and compares what they print.
    """

    _SCRIPT = TestWhileTrueConditionalBreak._SCRIPT
    _run = TestWhileTrueConditionalBreak.__dict__["_run"]
    _assert_same_behavior = TestWhileTrueConditionalBreak._assert_same_behavior

    CASES = {
        'for_continue': "for i in range(5):\n    if i == 1:\n        continue\n    print(i)\nprint('end')\n",
        'for_continue_func': 'def f(n):\n    out = []\n    for i in range(n):\n        if i % 2:\n            continue\n        out.append(i)\n    return out\nprint(f(7))\n',
        'for_continue_tail': "for i in range(4):\n    print('a', i)\n    if i == 2:\n        continue\n",
        'for_continue_else': "for i in range(4):\n    if i == 1:\n        continue\n    else:\n        print('x', i)\n    print('y', i)\n",
        'for_two_continues': 'for i in range(8):\n    if i == 1:\n        continue\n    if i == 3:\n        continue\n    print(i)\n',
        'for_continue_break': "for i in range(8):\n    if i == 1:\n        continue\n    if i == 4:\n        break\n    print(i)\nprint('end')\n",
        'for_nested_continue': 'for i in range(3):\n    for j in range(3):\n        if j == 1:\n            continue\n        print(i, j)\n',
        'for_continue_outer': "for i in range(3):\n    for j in range(3):\n        if j == 1:\n            break\n        print(i, j)\n    if i == 1:\n        continue\n    print('outer', i)\n",
        'for_continue_ifelse': "for i in range(5):\n    if i == 1:\n        continue\n    elif i == 3:\n        print('three')\n    else:\n        print(i)\n",
        'for_continue_compound': 'for i in range(6):\n    if i > 1 and i < 4:\n        continue\n    print(i)\n',
        'while_continue': "i = 0\nwhile i < 5:\n    i += 1\n    if i == 2:\n        continue\n    print(i)\nprint('end')\n",
        'while_continue_func': 'def f():\n    i = 0\n    r = []\n    while i < 6:\n        i += 1\n        if i % 2:\n            continue\n        r.append(i)\n    return r\nprint(f())\n',
        'wt_continue': "while True:\n    x = input()\n    if x == 'skip':\n        continue\n    print('got', x)\n    if x == 'q':\n        break\n",
        'wt_continue_else': "while True:\n    x = input()\n    if x == 'skip':\n        continue\n    else:\n        print('e', x)\n    if x == 'q':\n        break\n",
        'wt_continue_tail': "n = 0\nwhile True:\n    n += 1\n    x = input()\n    if n > 6:\n        break\n    if x == 'skip':\n        continue\n",
        'while_continue_break': "i = 0\nwhile i < 9:\n    i += 1\n    if i == 2:\n        continue\n    if i == 5:\n        break\n    print(i)\nprint('end')\n",
        'while_continue_two': 'i = 0\nwhile i < 9:\n    i += 1\n    if i == 2:\n        continue\n    if i == 4:\n        continue\n    print(i)\n',
        'R_for_tail_ifelse': "for i in range(5):\n    print('a', i)\n    if i % 2:\n        print('odd')\n    else:\n        print('even')\n",
        'R_for_if_then_rest': "for i in range(5):\n    if i % 2:\n        print('odd')\n    print('rest', i)\n",
        'R_for_tail_if': "for i in range(5):\n    print('a', i)\n    if i == 3:\n        print('three')\n",
        'R_for_nested_ifs': "for i in range(6):\n    if i > 1:\n        if i < 4:\n            print('mid', i)\n        else:\n            print('hi', i)\n    print('end', i)\n",
        'R_while_tail_ifelse': "i = 0\nwhile i < 5:\n    i += 1\n    if i % 2:\n        print('odd')\n    else:\n        print('even')\n",
        'R_for_if_elif': "for i in range(5):\n    if i == 1:\n        print('one')\n    elif i == 2:\n        print('two')\n    else:\n        print('other')\n",
        'R_for_ifelse_rest': "for i in range(5):\n    if i % 2:\n        print('odd')\n    else:\n        print('even')\n    print('rest', i)\n",
        'R_for_try_if': "for i in range(4):\n    try:\n        if i == 2:\n            print('two')\n        print(i)\n    except Exception:\n        pass\n",
        'while_nested_continue': 'i = 0\nwhile i < 3:\n    i += 1\n    j = 0\n    while j < 3:\n        j += 1\n        if j == 2:\n            continue\n        print(i, j)\n',
    }

    def test_behavior_is_preserved(self):
        for name, src in self.CASES.items():
            with self.subTest(case=name):
                out = self._assert_same_behavior(src)
                # A `continue` that ends the body is a no-op and is not compiled.
                if "continue" in src and not name.endswith("_tail"):
                    assert_contains(out, "continue")


class TestWhileConditionLoops(unittest.TestCase):
    """`while cond:` loops (issue #105).

    3.10 and 3.11 test the condition once before the loop and again at the bottom of the body,
    with a conditional jump back to the body, so there is no unconditional back-edge to find.
    Each case runs the original and the decompiled code and compares what they print.
    """

    _SCRIPT = TestWhileTrueConditionalBreak._SCRIPT
    _run = TestWhileTrueConditionalBreak.__dict__["_run"]
    _assert_same_behavior = TestWhileTrueConditionalBreak._assert_same_behavior

    CASES = {
        "truthiness": "x = 3\nwhile x:\n    x = x - 1\n    print('tick', x)\nprint('end')\n",
        "comparison": "n = 0\nwhile n < 5:\n    n += 1\n    print(n)\nprint('end')\n",
        "not_condition": "done = False\nn = 0\nwhile not done:\n    n += 1\n    done = n > 3\n    print(n)\n",
        "and_condition": "i = 0\nj = 0\nwhile i < 5 and j < 3:\n    i += 1\n    j += 2\n    print(i, j)\n",
        "or_condition": "i = 0\nj = 0\nwhile i < 2 or j < 3:\n    i += 1\n    j += 1\n    print(i, j)\n",
        "is_not_none": "items = [1, 2, 3]\nnode = items.pop()\nwhile node is not None:\n    print(node)\n    node = items.pop() if items else None\n",
        "never_entered": "n = 9\nwhile n < 5:\n    n += 1\n    print(n)\nprint('end', n)\n",
        "else_clause": "n = 0\nwhile n < 3:\n    n += 1\n    print(n)\nelse:\n    print('done')\nprint('end')\n",
        "break_skips_else": "n = 0\nwhile n < 9:\n    n += 1\n    if n == 3:\n        break\n    print(n)\nelse:\n    print('done')\nprint('end')\n",
        "in_function": "def count(limit):\n    n = 0\n    out = []\n    while n < limit:\n        n += 1\n        out.append(n)\n    return out\nprint(count(4), count(0))\n",
        "return_inside": "def first_square_over(limit):\n    n = 0\n    while n < 100:\n        n += 1\n        if n * n > limit:\n            return n\n    return -1\nprint(first_square_over(30), first_square_over(10000))\n",
        "nested": "i = 0\nwhile i < 3:\n    i += 1\n    j = 0\n    while j < i:\n        j += 1\n        print(i, j)\n",
        "if_in_body": "n = 0\nwhile n < 6:\n    n += 1\n    if n % 2:\n        print('odd', n)\n    else:\n        print('even', n)\n",
        "call_in_condition": "xs = [3, 2, 1]\nwhile len(xs) > 1:\n    print(xs.pop())\nprint(xs)\n",
        "followed_by_loop": "n = 0\nwhile n < 2:\n    n += 1\n    print('a', n)\nm = 0\nwhile m < 2:\n    m += 1\n    print('b', m)\n",
    }

    def test_equal_returns_of_different_types_stay_distinct(self):
        # `return True` and `return 1` compare equal but are different exits of the condition.
        src = "def f(a, b):\n    if a and b:\n        return True\n    return 1\n"
        ns = {}
        exec(compile(decompile(src), "<decompiled>", "exec"), ns)  # pylint: disable=exec-used
        for a, b in ((0, 0), (0, 1), (1, 0), (1, 1)):
            with self.subTest(a=a, b=b):
                got = ns["f"](a, b)
                self.assertEqual((got, type(got)), (True, bool) if a and b else (1, int))

    def test_behavior_is_preserved(self):
        for name, src in self.CASES.items():
            with self.subTest(case=name):
                out = self._assert_same_behavior(src)
                self.assertIn("while ", out)
                self.assertNotIn("pass", out)


class TestLoopContinueInTry(unittest.TestCase):
    """`continue` and `break` inside `try` bodies and handlers in loops (issue #106)."""

    _SCRIPT = TestWhileTrueConditionalBreak._SCRIPT
    _run = TestWhileTrueConditionalBreak.__dict__["_run"]
    _assert_same_behavior = TestWhileTrueConditionalBreak._assert_same_behavior

    CASES = {
        "continue_in_try_body": (
            "for i in range(4):\n    try:\n        if i == 1:\n            continue\n"
            "        print(i)\n    except Exception:\n        pass\n"
        ),
        "continue_in_except": (
            "for i in range(4):\n    try:\n        if i == 1:\n            raise ValueError\n"
            "        print(i)\n    except ValueError:\n        continue\n    print('after', i)\n"
        ),
        "break_in_try_body": (
            "for i in range(4):\n    try:\n        if i == 2:\n            break\n"
            "        print(i)\n    except Exception:\n        pass\n"
        ),
    }

    def test_behavior_is_preserved(self):
        for name, src in self.CASES.items():
            with self.subTest(case=name):
                self._assert_same_behavior(src)

    def test_continue_in_try_body_is_emitted(self):
        out = self._assert_same_behavior(self.CASES["continue_in_try_body"])
        assert_contains(out, "continue")


class TestCodeAfterTryExceptInLoop(unittest.TestCase):
    """Statements after a `try`/`except` inside a loop body (issue #108).

    On 3.12+ the handlers sit at the end of the code and jump back to whatever
    follows the `try`. That code used to be mistaken for an inlined `finally`
    body, and a `continue` in the try body split the try range so the handlers
    were placed twice. Each case runs the original and the decompiled code and
    compares what they print.
    """

    _SCRIPT = TestWhileTrueConditionalBreak._SCRIPT
    _run = TestWhileTrueConditionalBreak.__dict__["_run"]
    _assert_same_behavior = TestWhileTrueConditionalBreak._assert_same_behavior

    CASES = {
        'while_try_continue': 'i = 0\nwhile i < 5:\n    i += 1\n    try:\n        if i == 2:\n            continue\n        print(i)\n    except Exception:\n        pass\n',
        'for_try_continue_after': "for i in range(4):\n    try:\n        if i == 1:\n            continue\n        print(i)\n    except Exception:\n        pass\n    print('after', i)\n",
        'for_try_after': "for i in range(4):\n    try:\n        print(i)\n    except Exception:\n        pass\n    print('after', i)\n",
        'for_try_raise_after': "for i in range(4):\n    try:\n        if i == 1:\n            raise ValueError\n        print(i)\n    except ValueError:\n        print('err', i)\n    print('after', i)\n",
        'while_try_after': "i = 0\nwhile i < 4:\n    i += 1\n    try:\n        print(i)\n    except Exception:\n        pass\n    print('after', i)\n",
        'for_try_else_after': "for i in range(4):\n    try:\n        print(i)\n    except Exception:\n        pass\n    else:\n        print('else', i)\n    print('after', i)\n",
        'for_try_finally_after': "for i in range(3):\n    try:\n        print(i)\n    except Exception:\n        pass\n    finally:\n        print('f', i)\n    print('after', i)\n",
        'func_try_after': 'def f(n):\n    r = []\n    for i in range(n):\n        try:\n            r.append(10 // (i - 1))\n        except ZeroDivisionError:\n            r.append(-1)\n        r.append(i)\n    return r\nprint(f(4))\n',
        'for_two_try': "for i in range(3):\n    try:\n        print(i)\n    except Exception:\n        pass\n    try:\n        print(i + 10)\n    except Exception:\n        pass\n    print('end', i)\n",
        'for_try_two_excepts': "for i in range(3):\n    try:\n        print(i)\n    except ValueError:\n        pass\n    except TypeError:\n        pass\n    print('after', i)\n",
    }

    def test_cases_are_defined(self):
        self.assertGreaterEqual(len(self.CASES), 9)

    def test_behavior_is_preserved(self):
        for name, src in self.CASES.items():
            with self.subTest(case=name):
                self._assert_same_behavior(src)

    def test_no_bogus_finally(self):
        for name, src in self.CASES.items():
            if "finally" in src:
                continue
            with self.subTest(case=name):
                assert_not_contains(self._assert_same_behavior(src), "finally", "# ")

    def test_empty_except_clause_gets_pass(self):
        out = self._assert_same_behavior(
            "for i in range(3):\n    try:\n        print(i)\n    except ValueError:\n        pass\n"
            "    except TypeError:\n        pass\n    print('after', i)\n"
        )
        assert_contains(out, "except ValueError:", "except TypeError:")

    def _prescan(self, src):
        from .test_helpers import _compile
        from pycrefine import get_decompiler

        pyc = _compile(src)
        try:
            dec = get_decompiler(pyc)
            dec.decompile()
            return dec
        finally:
            os.unlink(pyc)

    @unittest.skipIf(sys.version_info[:2] < (3, 12), "needs exception-table based try blocks")
    def test_identical_cleanup_elsewhere_is_not_a_finally_copy(self):
        """Code that matches a later try's cleanup must not become the earlier try's finally."""
        dec = self._prescan(
            "for item in [0, 2]:\n    try:\n        if item == 1:\n"
            "            raise ValueError\n    except KeyError:\n        pass\n"
            "    print('c', item)\n    try:\n        print('b', item)\n"
            "    finally:\n        print('c', item)\n"
        )
        self.assertEqual(len(dec.emit.deferred_finally_lines), 1)

    @unittest.skipIf(sys.version_info[:2] < (3, 12), "needs exception-table based try blocks")
    def test_finally_with_branch_is_still_detected(self):
        """A finally body with a conditional is a real finally even though its jump targets move."""
        dec = self._prescan(
            "flag = True\nfor item in [0, 1]:\n    try:\n        print('b', item)\n"
            "    except KeyError:\n        pass\n    finally:\n        if flag:\n"
            "            print('yes', item)\n        else:\n            print('no', item)\n"
        )
        self.assertEqual(len(dec.emit.deferred_finally_lines), 1)



class TestWithEndingThenBranch(unittest.TestCase):
    """An else after an if-branch that ends in a with statement stays in the else."""

    SRC = (
        "def f(c, m):\n"
        "    if c:\n"
        "        with m:\n"
        "            a = 1\n"
        "    else:\n"
        "        a = 2\n"
        "    return a\n"
    )

    @unittest.skipIf(sys.version_info[:2] == (3, 10) or sys.version_info >= (3, 12),
                     "these versions repeat the code after the with in the branch (same behavior)")
    def test_else_stays_in_else(self):
        out = decompile(self.SRC)
        tree = ast.parse(out)
        node = next(n for n in ast.walk(tree) if isinstance(n, ast.If))
        self.assertTrue(node.orelse, out)
        self.assertTrue(any(isinstance(n, ast.With) for n in node.body), out)

    def test_behavior_is_preserved(self):
        class M:
            def __enter__(self): return self
            def __exit__(self, *a): return False
        ns = {}
        exec(decompile(self.SRC), ns)
        self.assertEqual([ns["f"](c, M()) for c in (0, 1)], [2, 1])
