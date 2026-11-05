"""The end of a loop is found once per loop head, not once per question (#223)."""
import os
import unittest

from pycrefine import get_decompiler

from .test_helpers import _compile, decompile

_SOURCE = """\
def f(items, n):
    total = 0
    while n > 0:
        n -= 1
        for x in items:
            if x:
                continue
            total += x
    return total
"""


_MODULE_LOOP = "n = 3\nwhile n > 0:\n    n -= 1\n"


def _decompiler(source=_SOURCE):
    pyc = _compile(source)
    try:
        decompiler = get_decompiler(pyc)
        decompiler.decompile()
    finally:
        os.unlink(pyc)
    return decompiler


class TestLoopEnds(unittest.TestCase):
    def test_the_answer_is_kept_per_loop_head(self):
        decompiler = _decompiler(_MODULE_LOOP)
        heads = list(decompiler.prescan.loop_ends)
        self.assertTrue(heads, "the loop's end was never asked for")
        for head in heads:
            self.assertEqual(decompiler.prescan.loop_ends[head], decompiler._loop_end_after(head))

    def test_a_second_question_does_not_scan_again(self):
        decompiler = _decompiler()
        head = 0
        decompiler.prescan.loop_ends = {}
        first = decompiler._loop_end_after(head)
        calls = []
        original = decompiler._is_backward_instruction
        decompiler._is_backward_instruction = lambda ins: calls.append(ins) or original(ins)
        self.assertEqual(decompiler._loop_end_after(head), first)
        self.assertEqual(calls, [])

    def test_a_new_prescan_clears_the_answers(self):
        decompiler = _decompiler()
        decompiler.prescan.loop_ends[123] = 456
        decompiler._prescan_while_loops()
        self.assertEqual(decompiler.prescan.loop_ends, {})

    def test_the_nested_loops_still_decompile_the_same(self):
        out = decompile(_SOURCE)
        self.assertIn("while n > 0:", out)
        self.assertIn("for x in items:", out)
        self.assertIn("continue", out)


if __name__ == "__main__":
    unittest.main()
