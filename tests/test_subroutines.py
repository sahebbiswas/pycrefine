from pycrefine import BytecodeInstruction, Decompiler39
import unittest

from .test_helpers import assert_contains, decompile


class TestFunctions(unittest.TestCase):
    def test_simple_function(self):
        out = decompile("def add(a, b):\n    return a + b\n")
        assert_contains(out, "def add(a, b):", "return")

    def test_function_no_args(self):
        out = decompile("def greet():\n    return 'hi'\n")
        assert_contains(out, "def greet():", "return")

    def test_function_with_default(self):
        out = decompile("def f(x, y=10):\n    return x + y\n")
        assert_contains(out, "return")
        self.assertIn("def f(", out)
        self.assertIn("def f(x, y=10):", out)

    def test_function_default_string(self):
        out = decompile("def greet(name, greeting='Hello'):\n    return greeting + ' ' + name\n")
        assert_contains(out, "def greet(")
        self.assertNotIn('"""\nHello\n"""', out)
        self.assertIn("greeting", out)

    def test_function_body_indented(self):
        out = decompile("def f(x):\n    y = x * 2\n    return y\n")
        lines = out.splitlines()
        body_lines = [ln for ln in lines if "y = " in ln or "return" in ln]
        for line in body_lines:
            self.assertTrue(line.startswith("    "))

    def test_nested_function(self):
        src = (
            "def outer(x):\n"
            "    def inner(y):\n"
            "        return y + 1\n"
            "    return inner(x)\n"
        )
        out = decompile(src)
        assert_contains(out, "def outer(", "def inner(")

    def test_yield_function(self):
        out = decompile("def gen():\n    yield 1\n    yield 2\n")
        assert_contains(out, "def gen():", "yield 1", "yield 2")

    def test_function_call_no_args(self):
        out = decompile("def f():\n    pass\nf()\n")
        assert_contains(out, "def f():", "f()")

    def test_function_call_with_args(self):
        out = decompile("print('hello', 'world')\n")
        assert_contains(out, "print(")


class TestClasses(unittest.TestCase):
    def test_simple_class(self):
        out = decompile("class Foo:\n    pass\n")
        assert_contains(out, "class Foo")

    def test_class_with_init(self):
        out = decompile("class Foo:\n    def __init__(self):\n        self.x = 1\n")
        assert_contains(out, "class Foo:", "def __init__(self):", "self.x = 1")

    def test_class_method(self):
        out = decompile("class Foo:\n    def bar(self, x):\n        return x * 2\n")
        assert_contains(out, "class Foo:", "def bar(self, x):")

    def test_class_with_base(self):
        out = decompile("class Child(Exception):\n    pass\n")
        assert_contains(out, "Child")

    def test_class_attribute(self):
        out = decompile("class Foo:\n    def __init__(self):\n        self.name = 'test'\n")
        assert_contains(out, "self.name = 'test'")


class TestDecompiler39Classes(unittest.TestCase):
    def _make_dec(self):
        code = compile("pass", "<test>", "exec")
        return Decompiler39(code)

    def _call_function(self, dec, num_args: int):
        instr = BytecodeInstruction(
            opcode=131, opname="CALL_FUNCTION", arg=num_args, argval=num_args,
            offset=0, starts_line=None, is_jump_target=False,
        )
        dec._handle_instruction(instr)

    def test_simple_class_produces_tuple(self):
        dec = self._make_dec()
        body = "def Foo():\n    def __init__(self):\n        self.x = 1"
        dec.stack = ["__build_class__", ("func", body), "'Foo'"]
        self._call_function(dec, 2)
        result = dec.stack[-1]
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], "class")

    def test_simple_class_header(self):
        dec = self._make_dec()
        body = "def Foo():\n    def __init__(self):\n        self.x = 1"
        dec.stack = ["__build_class__", ("func", body), "'Foo'"]
        self._call_function(dec, 2)
        text = dec.stack[-1][1]
        self.assertIn("class Foo:", text)

    def test_class_body_included(self):
        dec = self._make_dec()
        body = "def Foo():\n    def __init__(self):\n        self.x = 1"
        dec.stack = ["__build_class__", ("func", body), "'Foo'"]
        self._call_function(dec, 2)
        text = dec.stack[-1][1]
        self.assertIn("def __init__(self):", text)
        self.assertIn("self.x = 1", text)

    def test_no_raw_build_class_in_output(self):
        dec = self._make_dec()
        dec.stack = ["__build_class__", ("func", "def Foo():\n    pass"), "'Foo'"]
        self._call_function(dec, 2)
        text = dec.stack[-1][1]
        self.assertNotIn("__build_class__", text)

    def test_no_raw_func_tuple_in_output(self):
        dec = self._make_dec()
        dec.stack = ["__build_class__", ("func", "def Foo():\n    pass"), "'Foo'"]
        self._call_function(dec, 2)
        text = dec.stack[-1][1]
        self.assertNotIn("('func'", text)

    def test_class_with_single_base(self):
        dec = self._make_dec()
        dec.stack = ["__build_class__", ("func", "def Child():\n    pass"), "'Child'", "Base"]
        self._call_function(dec, 3)
        text = dec.stack[-1][1]
        self.assertIn("class Child(Base):", text)

    def test_class_with_two_bases(self):
        dec = self._make_dec()
        dec.stack = ["__build_class__", ("func", "def Multi():\n    pass"), "'Multi'", "Base", "Mixin"]
        self._call_function(dec, 4)
        text = dec.stack[-1][1]
        self.assertIn("class Multi(", text)
        self.assertIn("Base", text)
        self.assertIn("Mixin", text)

    def test_regular_call_unaffected(self):
        dec = self._make_dec()
        dec.stack = ["print", "'hello'", "'world'"]
        self._call_function(dec, 2)
        result = dec.stack[-1]
        self.assertIsInstance(result, str)
        self.assertIn("print(", result)

    def test_regular_call_with_func_tuple_arg(self):
        dec = self._make_dec()
        body_tuple = ("func", "def f():\n    return 1")
        dec.stack = ["decorator", body_tuple]
        self._call_function(dec, 1)
        result = dec.stack[-1]
        self.assertIsInstance(result, tuple)
        self.assertEqual(result[0], "func")
        body_text = result[1]
        self.assertIn("@decorator", body_text)
        self.assertIn("def f():", body_text)

    def test_store_name_emits_class_correctly(self):
        dec = self._make_dec()
        dec.indent_level = 0
        body = "class Foo:\n    def __init__(self):\n        self.x = 1"
        dec.stack = [("class", body)]
        store = BytecodeInstruction(
            opcode=90, opname="STORE_NAME", arg=0, argval="Foo",
            offset=0, starts_line=None, is_jump_target=False,
        )
        dec._handle_instruction(store)
        out = "\n".join(dec.reconstructed)
        self.assertIn("class Foo:", out)
        self.assertNotIn("('class'", out)


class TestGenexprRendering(unittest.TestCase):
    def test_simple_genexpr_renders_inline(self):
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for x in .0:\n        yield x * 2\n"
        out = _render_func_tuple(body, ["items"])
        self.assertEqual(out, "(x * 2 for x in items)")

    def test_genexpr_with_if_clause(self):
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for x in .0:\n        if x > 0:\n            yield x\n"
        out = _render_func_tuple(body, ["xs"])
        self.assertIn("for x in xs", out)
        self.assertIn("if x > 0", out)

    def test_continue_filter_is_negated(self):
        # 3.13+ writes `if cond: continue`; that rejects the item, so the filter is `not cond`.
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for x in .0:\n        if not x > 1:\n            continue\n        yield x\n"
        self.assertEqual(_render_func_tuple(body, ["xs"]), "(x for x in xs if x > 1)")
        body = "def <genexpr>(.0):\n    for x in .0:\n        if x:\n            continue\n        yield 1\n"
        self.assertEqual(_render_func_tuple(body, ["xs"]), "(1 for x in xs if not x)")

    def test_consecutive_continue_filters_are_each_negated(self):
        from pycrefine import _render_func_tuple
        body = ("def <genexpr>(.0):\n    for x in .0:\n        if not x:\n            continue\n"
                "        if not ys:\n            continue\n        yield 1\n")
        self.assertEqual(_render_func_tuple(body, ["xs"]), "(1 for x in xs if x if ys)")

    def test_continue_filter_negation_handles_boolean_operators_and_complements(self):
        from pycrefine import _render_func_tuple
        template = "def <genexpr>(.0):\n    for x in .0:\n        if {}:\n            continue\n        yield x\n"
        cases = {
            "not x and not y": "x or y",
            "not x or not y": "x and y",
            "x is not None": "x is None",
            "x is None": "x is not None",
            "not x in d": "x in d",
            "not x not in d": "x not in d",
            "x in d": "x not in d",
            "x == 1": "not x == 1",
            "f(x)": "not f(x)",
            "a < x < b": "not a < x < b",
        }
        for cond, expected in cases.items():
            with self.subTest(cond=cond):
                self.assertEqual(
                    _render_func_tuple(template.format(cond), ["xs"]), f"(x for x in xs if {expected})")

    def test_else_continue_keeps_the_polarity_of_the_condition(self):
        # `if a: [if not b: continue] else: continue` is the 3.13+ spelling of `if a if b`.
        from pycrefine import _render_func_tuple
        body = ("def <genexpr>(.0):\n    for x in .0:\n        if 1 < x:\n            if not x < 9:\n"
                "                continue\n        else:\n            continue\n        yield x\n")
        self.assertEqual(_render_func_tuple(body, ["xs"]), "(x for x in xs if 1 < x if x < 9)")

    def test_filter_on_each_nested_loop(self):
        from pycrefine import _render_func_tuple
        body = ("def <genexpr>(.0):\n    for x in .0:\n        if not x:\n            continue\n"
                "        for y in ys:\n            if not y:\n                continue\n            yield (x, y)\n")
        self.assertEqual(
            _render_func_tuple(body, ["xs"]), "((x, y) for x in xs if x for y in ys if y)")

    def test_while_inside_a_comprehension_is_a_filter(self):
        # A comprehension cannot contain a real `while`; 3.9 writes a filter that way.
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for x in .0:\n        while x:\n            yield x\n"
        self.assertEqual(_render_func_tuple(body, ["xs"]), "(x for x in xs if x)")

    def test_continue_filter_in_list_and_dict_comprehensions(self):
        from pycrefine import _render_func_tuple
        body = "def <listcomp>(.0):\n    for x in .0:\n        if not x:\n            continue\n        yield x\n"
        self.assertEqual(_render_func_tuple(body, ["xs"]), "[x for x in xs if x]")
        body = "def <dictcomp>(.0):\n    for k, v in .0:\n        if v is None:\n            continue\n        yield k: v\n"
        self.assertEqual(_render_func_tuple(body, ["d"]), "{k: v for k, v in d if v is not None}")

    def test_unparsable_condition_is_negated_verbatim(self):
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for x in .0:\n        if x ? y:\n            continue\n        yield x\n"
        self.assertEqual(_render_func_tuple(body, ["xs"]), "(x for x in xs if not (x ? y))")

    def test_body_without_a_yield_still_falls_back(self):
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for x in .0:\n        if not x:\n            continue\n"
        self.assertIn("<func>", _render_func_tuple(body, ["xs"]))

    def test_trailing_empty_call_suffix_stripped(self):
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for x in .0:\n        yield x\n"
        self.assertIn("for x in items", _render_func_tuple(body, ["items()"]))
        self.assertIn("for x in range(10)", _render_func_tuple(body, ["range(10)"]))

    def test_genexpr_wrapped_in_parens(self):
        from pycrefine import _render_func_tuple
        body = "def <genexpr>(.0):\n    for s in .0:\n        yield str(s)\n"
        out = _render_func_tuple(body, ["items"])
        self.assertTrue(out.startswith("(") and out.endswith(")"))

    def test_setcomp_uses_curly_braces(self):
        from pycrefine import _render_func_tuple
        body = "def <setcomp>(.0):\n    for x in .0:\n        yield x\n"
        out = _render_func_tuple(body, ["vals"])
        self.assertTrue(out.startswith("{") and out.endswith("}"))

    def test_listcomp_body_uses_square_brackets(self):
        from pycrefine import _render_func_tuple
        body = "def <listcomp>(.0):\n    for x in .0:\n        yield x + 1\n"
        out = _render_func_tuple(body, ["data"])
        self.assertTrue(out.startswith("[") and out.endswith("]"))

    def test_lambda_one_param(self):
        from pycrefine import _render_func_tuple
        body = "def <lambda>(x):\n    return x * 2\n"
        self.assertEqual(_render_func_tuple(body, []), "lambda x: x * 2")

    def test_lambda_zero_params(self):
        from pycrefine import _render_func_tuple
        body = "def <lambda>():\n    return 42\n"
        self.assertEqual(_render_func_tuple(body, []), "lambda: 42")

    def test_lambda_multiple_params(self):
        from pycrefine import _render_func_tuple
        body = "def <lambda>(x, y):\n    return x + y\n"
        out = _render_func_tuple(body, [])
        self.assertIn("lambda x, y:", out)
        self.assertIn("x + y", out)

    def test_empty_body_gives_placeholder(self):
        from pycrefine import _render_func_tuple
        self.assertIn("<func>", _render_func_tuple("", ["x"]))

    def test_unknown_body_gives_placeholder(self):
        from pycrefine import _render_func_tuple
        self.assertIn("<func>", _render_func_tuple("def <unknown>():\n    return 1\n", []))

    def test_any_genexpr_no_tuple_leakage(self):
        out = decompile("def f(items): return any(x > 0 for x in items)\n")
        self.assertNotIn("('func',", out)

    def test_sum_genexpr_correct_and_clean(self):
        out = decompile("result = sum(x**2 for x in range(10))\n")
        self.assertNotIn("('func',", out)
        self.assertIn("range(10)", out)

    def test_join_genexpr_no_tuple_leakage(self):
        out = decompile('result = "_".join(str(s) for s in items)\n')
        self.assertNotIn("('func',", out)

    def test_genexpr_parens_not_stripped_by_post_process(self):
        out = decompile("def f(items): return any(x > 0 for x in items)\n")
        self.assertNotIn("('func',", out)
        for line in out.splitlines():
            if " for " in line and "in items" in line and "yield" not in line:
                self.assertIn("(", line)

    def test_dataclass_produces_no_class_tuple_leakage(self):
        src = "from dataclasses import dataclass\n@dataclass\nclass Point:\n    x: int\n    y: int\n"
        out = decompile(src)
        self.assertNotIn("('class',", out)

    def test_if_parens_still_stripped(self):
        out = decompile("x = 1\nif x > 0:\n    y = 2\n")
        for line in out.splitlines():
            if "if" in line and "x > 0" in line:
                self.assertNotIn("if (x > 0):", line)

    def test_return_tuple_parens_preserved(self):
        out = decompile("def f():\n    return (1, 2)\n")
        self.assertIn("(1, 2)", out)

    def test_return_single_expr_parens_stripped(self):
        out = decompile("def f(x):\n    return (x + 1)\n")
        for line in out.splitlines():
            if "return" in line and "x" in line:
                self.assertNotIn("(x + 1)", line)


class TestDecorators(unittest.TestCase):
    def test_simple_decorator(self):
        out = decompile("@deco\ndef f(x):\n    return x\n")
        assert_contains(out, "@deco", "def f(x):")

    def test_decorator_with_arguments(self):
        out = decompile("@deco(1)\ndef f(x):\n    return x\n")
        self.assertIn("@deco(1)", out)

    def test_two_decorators(self):
        out = decompile("@deco1\n@deco2\ndef f(x):\n    return x\n")
        self.assertIn("@deco1", out)
        self.assertIn("@deco2", out)
        self.assertLess(out.find("@deco1"), out.find("@deco2"))

    def test_decorator_body_preserved(self):
        out = decompile("@deco\ndef f(x):\n    return x + 1\n")
        self.assertRegex(out, r"return\s+\(?x\s*\+\s*1\)?")

    def test_no_spurious_decorator_on_genexpr(self):
        out = decompile("result = sum(x**2 for x in range(10))\n")
        self.assertNotIn("@sum", out)

    def test_no_spurious_decorator_on_lambda(self):
        out = decompile("f = lambda x: x * 2\n")
        self.assertNotIn("@", out)

    def test_decorator_not_a_regular_call(self):
        out = decompile("@deco\ndef f(x):\n    return x\n")
        self.assertNotIn("= deco(", out)

    def test_decorator_function_missing_not_skipped(self):
        out = decompile("@deco\ndef f(x):\n    return x\n")
        self.assertIn("def f(", out)

    def test_decorated_function_with_body(self):
        src = "@some_decorator\ndef complex_logic(x, y):\n    if x < 0:\n        return False\n    return True\n"
        out = decompile(src)
        self.assertIn("@some_decorator", out)
        self.assertIn("def complex_logic(", out)

    def test_undecorated_function_unchanged(self):
        out = decompile("def f(x):\n    return x\n")
        self.assertNotIn("@", out)


class TestImports(unittest.TestCase):
    def test_plain_import(self):
        out = decompile("import os\n")
        assert_contains(out, "import os")

    def test_from_import_single(self):
        out = decompile("from sys import argv\n")
        assert_contains(out, "from sys import argv")

    def test_from_import_multi(self):
        out = decompile("from os.path import join, exists\n")
        assert_contains(out, "from os.path import join, exists")

    def test_import_does_not_emit_raw_name(self):
        out = decompile("from sys import argv\n")
        self.assertNotIn("argv = argv", out)

    def test_multiple_plain_imports(self):
        out = decompile("import os\nimport sys\n")
        assert_contains(out, "import os", "import sys")

    def test_import_and_use(self):
        out = decompile("import os\nx = os.getcwd()\n")
        assert_contains(out, "import os")


class TestCallFunctionKw(unittest.TestCase):
    def test_call_function_kw_mixing_positional_and_kwargs(self):
        src = "def f(a, b, c):\n    pass\ndef caller():\n    f(30, c=1, b=2)\n"
        out = decompile(src)
        self.assertIn("f(30, c=1, b=2)", out)

    def test_keyword_names_are_read_from_the_constants(self):
        # 3.11's dis leaves the KW_NAMES operand unresolved; the names come from co_consts.
        out = decompile("def f(d):\n    return dict(a=1, b=d)\n")
        self.assertIn("dict(a=1, b=d)", out)

    def test_keyword_only_and_attribute_calls(self):
        out = decompile("def f(o, d):\n    return o.run(d, retries=3)\n")
        self.assertIn("o.run(d, retries=3)", out)

    def test_class_header_keeps_its_keywords(self):
        """`metaclass=Meta` and other class keywords are not turned into base classes."""
        import ast

        def header(source, name):
            node = next(n for n in ast.walk(ast.parse(source)) if isinstance(n, ast.ClassDef) and n.name == name)
            return ([ast.dump(b) for b in node.bases], [ast.dump(k) for k in node.keywords])

        shapes = [
            "class Meta(type): pass\nclass C(metaclass=Meta):\n    x = 1\n",
            "class B: pass\nclass C(B, metaclass=type):\n    x = 1\n",
            "class B: pass\nclass A: pass\nclass C(B, A, flag=1, other='x'):\n    x = 1\n",
            "class B: pass\nclass C(B):\n    x = 1\n",
            "class B: pass\ndef f(M):\n    class C(B, metaclass=M):\n        y = 2\n    return C\n",
        ]
        for source in shapes:
            with self.subTest(source=source):
                out = decompile(source)
                self.assertEqual(header(out, "C"), header(source, "C"), out)


if __name__ == "__main__":
    unittest.main()
