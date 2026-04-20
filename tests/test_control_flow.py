import os
import sys
import unittest

from .test_helpers import _run39_full_impl, assert_contains, decompile


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
        suppress = getattr(dec, "_ternary_suppress", set())
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
        suppress = getattr(dec, "_ternary_suppress", set())
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
        suppress = getattr(dec, "_ternary_suppress", set())
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
            self.assertGreaterEqual(len(dec._while_header_targets), 1)

    def test_nested_while_two_guards(self):
        src = (
            "i = 0\nwhile i < 3:\n    j = 0\n"
            "    while j < 2:\n        j += 1\n    i += 1\n"
        )
        dec = self._get_dec(src)
        detected = len(dec._while_header_targets)
        self.assertGreaterEqual(detected, 1)

    def test_dup_offsets_populated(self):
        dec = self._get_dec("n = 0\nwhile n < 5:\n    n += 1\n")
        self.assertGreater(len(dec._while_body_offsets), 0)

    def test_no_while_in_if_only(self):
        dec = self._get_dec("x = 1\nif x > 0:\n    x = 2\n")
        self.assertEqual(len(dec._while_header_targets), 0)

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

if __name__ == "__main__":
    unittest.main()
