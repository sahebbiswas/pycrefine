"""`_prescan_try_structure` is a pipeline of named steps in a fixed order (#120).

The steps depend on each other's results (a later one reads what an earlier one wrote), so the
order is part of the design; these tests keep the order written down in one place and each step small.
"""
import ast
import unittest
from pathlib import Path

GENERIC = Path(__file__).resolve().parent.parent / "src" / "pycrefine" / "decompiler" / "generic.py"

STEPS = (
    "_classify_exception_entries",
    "_classify_push_exc_info",
    "_classify_try_nops",
    "_find_finally_merge_labels",
    "_classify_handler_exit_jumps",
    "_map_handlers_to_merge_labels",
    "_suppress_reraise_wrappers",
    "_render_handler_sections",
    "_render_finally_bodies",
    "_map_try_nops",
    "_prescan_outer_finally",
    "_suppress_with_exit_epilogues",
)


def _methods():
    tree = ast.parse(GENERIC.read_text(encoding="utf-8"))
    return {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}


class TestTryStructureSteps(unittest.TestCase):
    def test_the_steps_run_in_the_documented_order(self):
        body = _methods()["_prescan_try_structure"]
        called = [c.func.attr for c in ast.walk(body)
                  if isinstance(c, ast.Call) and isinstance(c.func, ast.Attribute)
                  and isinstance(c.func.value, ast.Name) and c.func.value.id == "self"
                  and c.func.attr in STEPS]
        self.assertEqual(sorted(called, key=called.index), called)
        self.assertEqual(called, list(STEPS))

    def test_every_step_is_defined(self):
        methods = _methods()
        for name in STEPS:
            with self.subTest(step=name):
                self.assertIn(name, methods)

    def test_no_step_is_over_150_lines(self):
        methods = _methods()
        for name in ("_prescan_try_structure",) + STEPS:
            node = methods[name]
            with self.subTest(step=name):
                self.assertLessEqual(node.end_lineno - node.lineno + 1, 150)


if __name__ == "__main__":
    unittest.main()
