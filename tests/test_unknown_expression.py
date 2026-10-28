"""Stack state is typed: a real expression, an empty stack, an unknown expression, or an invalid pop."""
import ast
import unittest
from pathlib import Path

import pycrefine
from pycrefine import (BytecodeInstruction, DecompilationError, Decompiler39, UnknownExpression, is_unknown)
from pycrefine import diagnostics as dg

SRC_DIR = Path(pycrefine.__file__).parent


def _dec(strict=False):
    return Decompiler39(compile("pass", "<t>", "exec"), target_version=(3, 9), strict=strict)


def _instr(name, arg=None, offset=0):
    return BytecodeInstruction(0, name, arg, arg, offset, None, False)


class TestValue(unittest.TestCase):
    def test_is_a_string_that_renders_as_a_question_mark(self):
        u = UnknownExpression("no operand", "BINARY_ADD", 4)
        self.assertIsInstance(u, str)
        self.assertEqual(f"{u}", "?")
        self.assertEqual((u.reason, u.opcode, u.offset), ("no operand", "BINARY_ADD", 4))
        self.assertIn("BINARY_ADD", repr(u))

    def test_is_unknown_tells_it_apart_from_text_that_looks_the_same(self):
        self.assertTrue(is_unknown(UnknownExpression("r")))
        self.assertFalse(is_unknown("?"))
        self.assertFalse(is_unknown(None))
        self.assertFalse(is_unknown("x"))

    def test_custom_text(self):
        self.assertEqual(UnknownExpression("r", text="a ? b"), "a ? b")


class TestStackStates(unittest.TestCase):
    def test_known_expression_pops_as_itself(self):
        dec = _dec()
        dec.stack.append("x")
        self.assertEqual(dec._pop(), "x")
        self.assertEqual(dec.diagnostics, [])

    def test_empty_stack_is_fine_until_something_pops_it(self):
        dec = _dec()
        self.assertEqual(dec.stack, [])
        self.assertEqual(dec.diagnostics, [])

    def test_invalid_pop_gives_a_tagged_unknown_and_a_diagnostic(self):
        dec = _dec()
        dec._current_instr = _instr("POP_JUMP_IF_FALSE", offset=12)
        value = dec._pop()
        self.assertTrue(is_unknown(value))
        self.assertEqual((value.opcode, value.offset), ("POP_JUMP_IF_FALSE", 12))
        self.assertEqual([d.kind for d in dec.diagnostics], [dg.STACK_UNDERFLOW])

    def test_explicit_fallback_is_still_honoured(self):
        dec = _dec()
        self.assertEqual(dec._pop("None"), "None")
        self.assertIsNone(dec._pop(None))

    def test_invalid_pop_raises_in_strict_mode(self):
        with self.assertRaises(DecompilationError):
            _dec(strict=True)._pop()


class TestMalformedBytecode(unittest.TestCase):
    def test_unknown_binary_operator_yields_unknown_not_a_guess(self):
        dec = _dec()
        dec.stack.extend(["a", "b"])
        dec._current_instr = instr = _instr("BINARY_OP", 99, 6)
        dec._op_binary(instr)
        self.assertEqual([d.kind for d in dec.diagnostics], [dg.AMBIGUOUS_EXPRESSION])
        self.assertEqual(dec.stack[-1], "a ? b")

    def test_the_expression_with_an_unknown_operator_stays_typed(self):
        dec = _dec()
        dec.stack.extend(["a", "b"])
        dec._current_instr = instr = _instr("BINARY_OP", 99, 6)
        dec._op_binary(instr)
        value = dec.stack[-1]
        self.assertTrue(is_unknown(value))
        self.assertEqual(str(value), "a ? b")
        self.assertIn("99", value.reason)
        self.assertEqual((value.opcode, value.offset), ("BINARY_OP", 6))

    def test_an_unknown_operand_keeps_the_result_typed(self):
        dec = _dec()
        dec.stack.extend(["a", "b"])
        dec._current_instr = first = _instr("BINARY_OP", 99, 6)
        dec._op_binary(first)                      # a ? b, with an unknown operator
        dec.stack.append("c")
        dec._current_instr = second = _instr("BINARY_OP", 0, 10)   # + c: a known operator
        dec._op_binary(second)
        value = dec.stack[-1]
        self.assertTrue(is_unknown(value))
        self.assertEqual(str(value), "(a ? b) + c")
        self.assertIn("99", value.reason)
        self.assertEqual((value.opcode, value.offset), ("BINARY_OP", 6))   # where it first went wrong
        self.assertEqual(len(dec.diagnostics), 1, "the second operator is fine and reports nothing")

    def test_known_operands_give_plain_text_after_a_known_operator(self):
        dec = _dec()
        dec.stack.extend(["a", "b"])
        dec._current_instr = instr = _instr("BINARY_OP", 0, 6)
        dec._op_binary(instr)
        dec.stack.append("c")
        dec._current_instr = instr = _instr("BINARY_OP", 10, 10)   # "-"
        dec._op_binary(instr)
        self.assertFalse(is_unknown(dec.stack[-1]))
        self.assertEqual(dec.stack[-1], "(a + b) - c")

    def test_a_known_operator_gives_plain_text(self):
        dec = _dec()
        dec.stack.extend(["a", "b"])
        dec._current_instr = instr = _instr("BINARY_OP", 0, 6)   # "+"
        dec._op_binary(instr)
        self.assertFalse(is_unknown(dec.stack[-1]))
        self.assertEqual(dec.stack[-1], "a + b")


class TestTernaryBranchEvaluation(unittest.TestCase):
    """The branch evaluator's placeholders are reported like the main loop's are."""

    @staticmethod
    def _loads(*names):
        return [_instr("LOAD_FAST", name, 2 * k) for k, name in enumerate(names)]

    def test_an_unmapped_operator_is_reported_with_its_instruction(self):
        dec = _dec()
        text = dec._eval_ternary_branch(self._loads("a", "b") + [_instr("BINARY_OP", 99, 4)])
        self.assertEqual(text, "(a ? b)")
        self.assertEqual([d.kind for d in dec.diagnostics], [dg.AMBIGUOUS_EXPRESSION])
        self.assertEqual((dec.diagnostics[0].opcode, dec.diagnostics[0].offset), ("BINARY_OP", 4))

    def test_an_unmapped_operator_stops_a_strict_run(self):
        with self.assertRaises(DecompilationError) as caught:
            _dec(strict=True)._eval_ternary_branch(self._loads("a", "b") + [_instr("BINARY_OP", 99, 4)])
        self.assertEqual(caught.exception.diagnostic.kind, dg.AMBIGUOUS_EXPRESSION)

    def test_a_known_operator_is_not_reported(self):
        dec = _dec()
        self.assertEqual(dec._eval_ternary_branch(self._loads("a", "b") + [_instr("BINARY_OP", 0, 4)]), "(a + b)")
        self.assertEqual(dec.diagnostics, [])

    SHORT_STACK = (
        # (opcode, argument, operands the branch is given, operands the opcode needs)
        ("BINARY_OP", 0, 1, 2), ("COMPARE_OP", None, 1, 2), ("IS_OP", 0, 1, 2),
        ("CONTAINS_OP", 0, 1, 2), ("BINARY_SUBSCR", None, 1, 2), ("BINARY_SLICE", None, 2, 3),
        ("BINARY_ADD", None, 1, 2), ("LOAD_ATTR", None, 0, 1), ("UNARY_NEGATIVE", None, 0, 1),
    )

    def test_an_operator_without_enough_operands_is_reported(self):
        for name, arg, have, need in self.SHORT_STACK:
            with self.subTest(name):
                dec = _dec()
                dec._eval_ternary_branch(self._loads(*"abc"[:have]) + [_instr(name, arg, 8)])
                self.assertEqual([d.kind for d in dec.diagnostics], [dg.STACK_UNDERFLOW])
                self.assertEqual((dec.diagnostics[0].opcode, dec.diagnostics[0].offset), (name, 8))
                self.assertIn(f"needs {need}", dec.diagnostics[0].reason)

    def test_an_operator_without_enough_operands_stops_a_strict_run(self):
        for name, arg, have, _ in self.SHORT_STACK:
            with self.subTest(name):
                with self.assertRaises(DecompilationError):
                    _dec(strict=True)._eval_ternary_branch(self._loads(*"abc"[:have]) + [_instr(name, arg, 8)])

    def test_an_operator_with_enough_operands_is_not_reported(self):
        for name, arg, _, need in self.SHORT_STACK:
            with self.subTest(name):
                dec = _dec()
                dec._eval_ternary_branch(self._loads(*"abc"[:need]) + [_instr(name, arg, 8)])
                self.assertEqual(dec.diagnostics, [])

    def test_a_call_missing_its_operands_is_reported(self):
        dec = _dec()
        dec._eval_ternary_branch([_instr("CALL", 1, 0)])
        self.assertEqual([d.kind for d in dec.diagnostics], [dg.STACK_UNDERFLOW, dg.STACK_UNDERFLOW])
        self.assertEqual({d.opcode for d in dec.diagnostics}, {"CALL"})

    def test_a_call_missing_its_operands_stops_a_strict_run(self):
        with self.assertRaises(DecompilationError):
            _dec(strict=True)._eval_ternary_branch([_instr("CALL", 1, 0)])

    def test_binary_operator_on_a_short_stack_does_not_crash(self):
        dec = _dec()
        dec.stack.append("a")
        dec._current_instr = instr = _instr("BINARY_ADD")
        dec._op_binary(instr)  # must not raise


class TestNoMagicStrings(unittest.TestCase):
    def test_nothing_compares_against_the_placeholder_text(self):
        offenders = []
        for path in sorted(SRC_DIR.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Compare):
                    for part in [node.left, *node.comparators]:
                        if isinstance(part, ast.Constant) and part.value == "?":
                            offenders.append(f"{path.relative_to(SRC_DIR)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_no_pop_falls_back_to_the_placeholder_text(self):
        offenders = []
        for path in sorted(SRC_DIR.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_pop"
                        and any(isinstance(a, ast.Constant) and a.value == "?" for a in node.args)):
                    offenders.append(f"{path.relative_to(SRC_DIR)}:{node.lineno}")
        self.assertEqual(offenders, [])

    def test_only_the_documented_pops_name_a_fallback(self):
        """An underflow must not invent source text (#217): a fallback is None, a flag the caller tests."""
        offenders = []
        for path in sorted(SRC_DIR.rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_pop"
                        and any(isinstance(a, ast.Constant) and isinstance(a.value, str) for a in node.args)):
                    offenders.append(f"{path.relative_to(SRC_DIR)}:{node.lineno}")
        self.assertEqual(offenders, [])


class TestUnderflowDoesNotInventSource(unittest.TestCase):
    """Sites that used to fall back to func, ctx, Exception, {expr}, () and {} now yield the unknown marker."""

    def run_op(self, handler, name, arg=0, stack=()):
        dec = _dec()
        dec.stack.extend(stack)
        getattr(dec, handler)(_instr(name, arg))
        return dec

    def test_call_target(self):
        dec = self.run_op("_op_call", "CALL_FUNCTION", 0)
        self.assertEqual(dec.stack, ["?()"])
        self.assertNotIn("func", dec.stack[0])
        self.assertIn(dg.STACK_UNDERFLOW, [d.kind for d in dec.diagnostics])

    def test_except_type(self):
        dec = self.run_op("_op_check_exc_match", "JUMP_IF_NOT_EXC_MATCH")
        self.assertNotIn("Exception", "".join(map(str, dec.stack)))
        self.assertIn(dg.STACK_UNDERFLOW, [d.kind for d in dec.diagnostics])

    def test_with_item(self):
        dec = self.run_op("_op_setup_with", "SETUP_WITH", 0)
        self.assertNotIn("ctx", "".join(map(str, dec.stack)))
        self.assertIn(dg.STACK_UNDERFLOW, [d.kind for d in dec.diagnostics])

    def test_fstring_field(self):
        dec = self.run_op("_op_fstring", "FORMAT_VALUE", 0)
        self.assertEqual(dec.stack, ["{?}"])
        self.assertIn(dg.STACK_UNDERFLOW, [d.kind for d in dec.diagnostics])

    def test_const_key_map_keys(self):
        dec = self.run_op("_op_build_const_key_map", "BUILD_CONST_KEY_MAP", 0)
        self.assertEqual(dec.stack, ["{?}"])
        self.assertTrue(is_unknown(dec.stack[0]))
        self.assertNotIn("'?'", dec.stack[0])
        self.assertIn(dg.STACK_UNDERFLOW, [d.kind for d in dec.diagnostics])

    def test_const_key_map_with_unknown_keys_keeps_the_stack_aligned(self):
        """Values below an unknown key tuple are consumed, so the next operand is not shifted."""
        keys = UnknownExpression("the key tuple was never built")
        dec = self.run_op("_op_build_const_key_map", "BUILD_CONST_KEY_MAP", 2, stack=("kept", "1", "2", keys))
        self.assertEqual(dec.stack, ["kept", "{?}"])
        self.assertTrue(is_unknown(dec.stack[-1]))

    def test_const_key_map_with_known_keys_is_unchanged(self):
        dec = self.run_op("_op_build_const_key_map", "BUILD_CONST_KEY_MAP", 2, stack=("1", "2", "('a', 'b')"))
        self.assertEqual(dec.stack, ["{'a': 1, 'b': 2}"])


if __name__ == "__main__":
    unittest.main()
