"""The analysis passes (src/pycrefine/analysis) on small snippets, with no decompiler involved (#120)."""
import argparse
import dataclasses
import dis
import json
import os
import subprocess
import sys
import textwrap
import tokenize
import types
import unittest

from pycrefine.analysis import (ExceptionTableEntry, HandlerClassification, classify_handlers,
                                parse_exception_table, split_superinstructions)
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


class TestParseExceptionTable(unittest.TestCase):

    def test_an_empty_table_has_no_entries(self):
        self.assertEqual(parse_exception_table(b""), [])

    def test_one_entry(self):
        # start 2 units, length 3, target 7, depth 1 with lasti: 0x80|2, 3, 7, (1 << 1) | 1
        self.assertEqual(parse_exception_table(bytes([0x82, 3, 7, 3])),
                         [ExceptionTableEntry(4, 10, 14, 1, True)])

    def test_a_number_of_several_bytes(self):
        # 70 = 1 * 64 + 6: the first byte has the continuation bit and the high part
        self.assertEqual(parse_exception_table(bytes([0x80 | 0x40 | 1, 6, 1, 2, 0])),
                         [ExceptionTableEntry(140, 142, 4, 0, False)])

    def test_two_entries_keep_their_order(self):
        table = bytes([0x80, 1, 5, 0, 0x81, 1, 5, 2])
        self.assertEqual([(e.start, e.end, e.target, e.depth) for e in parse_exception_table(table)],
                         [(0, 2, 10, 0), (2, 4, 10, 1)])

    def test_a_table_that_ends_inside_an_entry_is_refused(self):
        for cut in (bytes([0x80]), bytes([0x80, 1]), bytes([0x80, 1, 5]), bytes([0xC0])):
            with self.subTest(cut=cut), self.assertRaises(ValueError):
                parse_exception_table(cut)

    @unittest.skipIf(sys.version_info < (3, 11), "the exception table is 3.11+")
    def test_it_agrees_with_dis_on_real_code(self):
        checked = 0
        for module in (argparse, json, os, subprocess, textwrap, tokenize, unittest, dis):
            source = open(module.__file__, encoding="utf-8").read()
            todo = [compile(source, module.__file__, "exec")]
            while todo:
                code = todo.pop()
                todo.extend(c for c in code.co_consts if isinstance(c, types.CodeType))
                ours = [tuple(e) for e in parse_exception_table(code.co_exceptiontable)]
                theirs = [tuple(e) for e in dis.Bytecode(code).exception_entries]
                self.assertEqual(ours, theirs, code.co_qualname if hasattr(code, "co_qualname") else code.co_name)
                checked += bool(ours)
        self.assertGreater(checked, 50)


if __name__ == "__main__":
    unittest.main()
