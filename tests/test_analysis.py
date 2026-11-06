"""The analysis passes (src/pycrefine/analysis) on small snippets, with no decompiler involved (#120)."""
import dataclasses
import dis
import sys
import unittest

from pycrefine.analysis import HandlerClassification, classify_handlers, split_superinstructions
from pycrefine.instructions import BytecodeInstruction


def _instructions(source):
    code = compile(source, "<t>", "exec")
    return code, [BytecodeInstruction(i.opcode, i.opname, i.arg, i.argval, i.offset, i.starts_line,
                                      i.is_jump_target) for i in dis.get_instructions(code)]


def _ins(opname, offset, argval=None, target=False):
    return BytecodeInstruction(1, opname, None, argval, offset, 7, target)


class TestSplitSuperinstructions(unittest.TestCase):

    def test_a_fused_pair_becomes_a_store_and_a_load(self):
        fused = _ins("STORE_FAST_LOAD_FAST", 10, ("a", "b"), target=True)
        out = split_superinstructions([_ins("NOP", 8), fused, _ins("RETURN_VALUE", 12)])
        self.assertEqual([(i.opname, i.offset, i.argval) for i in out],
                         [("NOP", 8, None), ("STORE_FAST", 10, "a"), ("LOAD_FAST", 11, "b"),
                          ("RETURN_VALUE", 12, None)])
        self.assertTrue(out[1].is_jump_target)          # a jump to the pair lands on the store
        self.assertFalse(out[2].is_jump_target)
        self.assertEqual(out[1].starts_line, 7)
        self.assertIsNone(out[2].starts_line)

    def test_other_instructions_are_returned_as_they_are(self):
        body = [_ins("LOAD_FAST", 0, "x"), _ins("STORE_FAST", 2, "y")]
        out = split_superinstructions(body)
        self.assertEqual(out, body)
        self.assertIsNot(out, body)

    def test_the_input_is_left_alone(self):
        body = [_ins("STORE_FAST_LOAD_FAST", 0, ("a", "b"))]
        split_superinstructions(body)
        self.assertEqual(body[0].opname, "STORE_FAST_LOAD_FAST")

    def test_a_pair_without_two_names_is_not_split(self):
        body = [_ins("STORE_FAST_LOAD_FAST", 0, "ab")]
        self.assertEqual(split_superinstructions(body), body)


@unittest.skipIf(sys.version_info < (3, 11), "the exception table is 3.11+")
class TestClassifyHandlers(unittest.TestCase):

    def classify(self, source):
        code, instrs = _instructions(source)
        entries = dis.Bytecode(code).exception_entries
        return instrs, entries, classify_handlers(instrs, entries)

    def pushes(self, instrs):
        return {i.offset for i in instrs if i.opname == "PUSH_EXC_INFO"}

    def test_a_typed_except_is_a_real_clause(self):
        instrs, entries, result = self.classify("try:\n    f()\nexcept ValueError:\n    pass\n")
        self.assertTrue(result.except_targets)
        self.assertTrue(result.except_targets <= self.pushes(instrs))
        self.assertFalse(result.reraise_wrapper_targets & result.except_targets)
        self.assertFalse(result.suppressed_push_exc & result.except_targets)

    def test_a_bare_except_is_a_real_clause(self):
        _, _, result = self.classify("try:\n    f()\nexcept:\n    g()\n")
        self.assertTrue(result.except_targets)
        self.assertFalse(result.suppressed_push_exc & result.except_targets)

    def test_a_with_exit_handler_is_not_a_clause(self):
        instrs, _, result = self.classify("with a as b:\n    f()\n")
        self.assertFalse(result.except_targets)
        self.assertTrue(result.suppressed_push_exc)
        self.assertTrue(result.suppressed_push_exc <= self.pushes(instrs))

    def test_a_finally_handler_is_a_wrapper(self):
        _, _, result = self.classify("try:\n    f()\nfinally:\n    g()\n")
        self.assertFalse(result.except_targets)
        self.assertTrue(result.suppressed_push_exc)

    def test_the_protected_starts_are_the_entries_starts(self):
        _, entries, result = self.classify("try:\n    f()\nexcept ValueError:\n    pass\n")
        self.assertEqual(result.try_covered_starts, frozenset(e.start for e in entries))

    def test_without_entries_the_heuristic_still_finds_a_with_exit(self):
        instrs, _, _ = self.classify("with a as b:\n    f()\n")
        result = classify_handlers(instrs, [])
        self.assertFalse(result.except_targets)
        self.assertTrue(result.suppressed_push_exc)

    def test_a_long_tuple_of_types_is_still_an_except_clause(self):
        _, _, result = self.classify("try:\n    f()\nexcept (A, B, C, D, E, F, G):\n    pass\n")
        self.assertTrue(result.except_targets)
        self.assertFalse(result.suppressed_push_exc & result.except_targets)

    def test_a_possibly_unbound_local_type_is_an_except_clause(self):
        code = compile("def f(c):\n    if c:\n        E = ValueError\n    try:\n        g()\n"
                       "    except (A, B, C, D, E, F):\n        pass\n", "<t>", "exec").co_consts[0]
        instrs = [BytecodeInstruction(i.opcode, i.opname, i.arg, i.argval, i.offset, i.starts_line,
                                      i.is_jump_target) for i in dis.get_instructions(code)]
        found = classify_handlers(instrs, dis.Bytecode(code).exception_entries)
        self.assertTrue(found.except_targets)

    def test_the_result_is_frozen(self):
        _, _, result = self.classify("try:\n    f()\nexcept ValueError:\n    pass\n")
        self.assertIsInstance(result, HandlerClassification)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            result.except_targets = frozenset()  # type: ignore[misc]

    def test_a_code_object_without_handlers_has_none(self):
        _, _, result = self.classify("x = 1\n")
        self.assertEqual(result, HandlerClassification(frozenset(), frozenset(), frozenset(), frozenset()))


if __name__ == "__main__":
    unittest.main()
