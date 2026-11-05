"""The version-dependent properties of the bytecode are named in ``versions.CAPABILITIES`` (#84).

The decompiler asks ``supports(name, version)`` and never compares the version with a tuple, so
what differs between Python versions is listed in one place. These tests keep that true.
"""
import ast
import re
import unittest
from pathlib import Path

from pycrefine.decompiler.generic import DecompilerGeneric
from pycrefine.versions import CAPABILITIES, VERSIONS, supports

SRC = Path(__file__).resolve().parent.parent / "src" / "pycrefine"
SUPPORTED = [row.version for row in VERSIONS if row.reconstructable]

# capability -> the versions (of those the decompiler rebuilds) that have it
EXPECTED = {
    "anext_opcode": [(3, 10), (3, 11), (3, 12), (3, 13), (3, 14)],
    "normalised_block_stack": [(3, 10)],
    "loop_condition_copy": [(3, 10), (3, 11)],
    "exception_table": [(3, 11), (3, 12), (3, 13), (3, 14)],
    "null_sentinels": [(3, 11), (3, 12), (3, 13), (3, 14)],
    "cold_handlers": [(3, 12), (3, 13), (3, 14)],
    "chain_comparison_values": [(3, 12), (3, 13), (3, 14)],
    "dedented_docstrings": [(3, 13), (3, 14)],
    "separate_iterator_pop": [(3, 13), (3, 14)],
    "lazy_annotations": [(3, 14)],
    "docstring_flag": [(3, 14)],
    "loop_head_condition": [(3, 9), (3, 14)],
}


def _sources():
    return {path: path.read_text(encoding="utf-8") for path in sorted((SRC / "decompiler").glob("*.py"))}


class TestCapabilityTable(unittest.TestCase):
    def test_each_capability_holds_for_the_expected_versions(self):
        for name, versions in EXPECTED.items():
            with self.subTest(capability=name):
                self.assertEqual([v for v in SUPPORTED if supports(name, v)], versions)

    def test_every_capability_is_described_and_has_an_interval(self):
        for name, capability in CAPABILITIES.items():
            with self.subTest(capability=name):
                self.assertTrue(capability.description.strip())
                self.assertTrue(capability.intervals)
                for since, until in capability.intervals:
                    if since is not None and until is not None:
                        self.assertLess(since, until)

    def test_an_unknown_capability_is_an_error_not_false(self):
        with self.assertRaises(KeyError):
            supports("cold_handler", (3, 12))

    def test_the_decompiler_asks_with_the_version_it_decompiles(self):
        decompiler = DecompilerGeneric(compile("x = 1\n", "<t>", "exec"), target_version=(3, 11))
        self.assertTrue(decompiler._supports("exception_table"))
        self.assertFalse(decompiler._supports("cold_handlers"))


class TestCodeUsesTheTable(unittest.TestCase):
    def test_every_name_the_code_asks_for_is_defined(self):
        asked = set()
        for text in _sources().values():
            asked.update(re.findall(r'_supports\("(\w+)"\)', text))
        self.assertTrue(asked)
        self.assertEqual(sorted(asked - set(CAPABILITIES)), [])

    def test_every_capability_is_asked_for(self):
        asked = set()
        for text in _sources().values():
            asked.update(re.findall(r'_supports\("(\w+)"\)', text))
        self.assertEqual(sorted(set(CAPABILITIES) - asked), [])

    def test_no_decompiler_code_compares_the_target_version_with_a_tuple(self):
        found = []
        for path, text in _sources().items():
            for node in ast.walk(ast.parse(text)):
                if not isinstance(node, ast.Compare):
                    continue
                operands = [node.left] + node.comparators
                mentions_target = any("target_version" in ast.dump(op) for op in operands)
                has_version_tuple = any(isinstance(op, ast.Tuple) and len(op.elts) == 2
                                        and all(isinstance(e, ast.Constant) and isinstance(e.value, int)
                                                for e in op.elts) for op in operands)
                if mentions_target and has_version_tuple:
                    found.append((path.name, node.lineno))
        self.assertEqual(found, [], "ask versions.supports(...) with a named capability instead")


if __name__ == "__main__":
    unittest.main()
