"""The order in which the generic dispatcher hides instructions (#223).

A finally-merge label is the first instruction of a suppressed finally body, so the step that
emits the deferred handlers must see it before ``finally_body_suppress`` hides it. The order is
a named list on the class (``SUPPRESSION_ORDER``) and the predicates apply it; these tests pin both.
"""
import dataclasses
import os
import unittest

from pycrefine import get_decompiler
from pycrefine.decompiler.generic import DecompilerGeneric
from pycrefine.decompiler.state import PrescanState

from .test_helpers import _compile, decompile

_SOURCE = "def f(x):\n    try:\n        return 10 // x\n    finally:\n        print('done')\n"


def _decompiler():
    pyc = _compile(_SOURCE)
    try:
        decompiler = get_decompiler(pyc)
        decompiler.decompile()
    finally:
        os.unlink(pyc)
    return decompiler


class _Instr:
    def __init__(self, offset):
        self.offset = offset


class TestSuppressionOrder(unittest.TestCase):
    OFFSET = 9999

    def setUp(self):
        self.decompiler = _decompiler()
        self.emitted = []
        self.decompiler._emit_deferred_finally = lambda offset, indent: self.emitted.append(offset)
        self.decompiler._emit_relocated_exit = lambda offset: self.emitted.append(("relocated", offset))
        prescan = self.decompiler.prescan
        for name in ("finally_merge_offsets", "except_only_merge_offsets", "finally_body_suppress",
                     "relocated_suppress", "with_exit_suppress_offsets", "handler_section_suppress",
                     "wrapper_body_suppress"):
            setattr(prescan, name, set())

    def test_the_order_is_the_documented_one(self):
        self.assertEqual(
            [step.split(" / ")[0] for step in DecompilerGeneric.SUPPRESSION_ORDER],
            ["finally_merge_offsets", "relocated_exit", "relocated_suppress", "with_exit_suppress_offsets",
             "finally_body_suppress", "handler_section_suppress", "wrapper_body_suppress"])

    def test_every_named_field_exists(self):
        fields = {f.name for f in dataclasses.fields(PrescanState)}
        for step in DecompilerGeneric.SUPPRESSION_ORDER:
            for name in step.split(" / "):
                if name != "relocated_exit":
                    self.assertIn(name, fields)

    def test_each_set_hides_its_instruction(self):
        for name in ("relocated_suppress", "with_exit_suppress_offsets", "finally_body_suppress",
                     "handler_section_suppress", "wrapper_body_suppress"):
            with self.subTest(name):
                getattr(self.decompiler.prescan, name).add(self.OFFSET)
                self.assertTrue(self.decompiler._suppressed_in_dispatch(_Instr(self.OFFSET)))
                getattr(self.decompiler.prescan, name).clear()
        self.assertFalse(self.decompiler._suppressed_in_dispatch(_Instr(self.OFFSET)))

    def test_a_merge_label_emits_before_the_finally_body_hides_it(self):
        self.decompiler.prescan.finally_merge_offsets.add(self.OFFSET)
        self.decompiler.prescan.finally_body_suppress.add(self.OFFSET)
        self.assertTrue(self.decompiler._suppressed_in_dispatch(_Instr(self.OFFSET)))
        self.assertEqual(self.emitted, [self.OFFSET])
        self.assertNotIn(self.OFFSET, self.decompiler.prescan.finally_merge_offsets)

    def test_a_merge_label_outside_the_finally_body_is_replayed(self):
        self.decompiler.prescan.except_only_merge_offsets.add(self.OFFSET)
        self.assertFalse(self.decompiler._suppressed_in_dispatch(_Instr(self.OFFSET)))
        # the handlers come first, then the instruction goes on through the relocated-exit step
        self.assertEqual(self.emitted, [self.OFFSET, ("relocated", self.OFFSET)])

    def test_a_merge_label_inside_a_with_exit_still_emits(self):
        # a try that ends a with body merges at the exit call
        self.decompiler.prescan.finally_merge_offsets.add(self.OFFSET)
        self.decompiler.prescan.with_exit_suppress_offsets.add(self.OFFSET)
        self.assertTrue(self.decompiler._suppressed_in_dispatch(_Instr(self.OFFSET)))
        self.assertEqual(self.emitted[0], self.OFFSET)

    def test_a_relocated_exit_is_emitted_before_the_sets_are_read(self):
        self.decompiler.prescan.relocated_suppress.add(self.OFFSET)
        self.assertTrue(self.decompiler._suppressed_in_dispatch(_Instr(self.OFFSET)))
        self.assertEqual(self.emitted, [("relocated", self.OFFSET)])

    def test_the_finally_body_is_emitted_once(self):
        out = decompile(_SOURCE)
        self.assertEqual(out.count("print('done')"), 1, out)


if __name__ == "__main__":
    unittest.main()
