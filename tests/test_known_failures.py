"""tests/known_failures.json stays well formed and points at tests that exist."""
import ast
import re
import unittest
from pathlib import Path

from .conftest import load_known_failures

ROOT = Path(__file__).resolve().parent.parent


def _test_exists(node_id):
    """True when ``path::Class::method`` names a method of a class in that file."""
    path, class_name, method = node_id.split("::")
    source = (ROOT / path).read_text(encoding="utf-8")
    for node in ast.parse(source).body:
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            return any(isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == method
                       for item in node.body)
    return False


NODE_ID = r"^tests/test_\w+\.py::\w+::test_\w+$"


def _groups():
    for tag, groups in load_known_failures().items():
        for group in groups:
            yield tag, group


def _listed_tests(group):
    """Every test a group names, whole or by case."""
    return list(group.get("tests", [])) + list(group.get("cases", {}))


class TestKnownFailures(unittest.TestCase):

    def test_entries_are_well_formed(self):
        """Each group names a Python minor version, an issue, a reason and its tests or cases."""
        known = load_known_failures()
        self.assertTrue(known)
        for tag, group in _groups():
            self.assertRegex(tag, r"^3\.\d+$")
            with self.subTest(version=tag, issue=group.get("issue")):
                self.assertIsInstance(group["issue"], int)
                self.assertTrue(group["reason"].strip())
                self.assertTrue(_listed_tests(group))
                self.assertFalse(set(group) - {"issue", "reason", "tests", "cases"})
                for node_id in _listed_tests(group):
                    self.assertRegex(node_id, NODE_ID)
                for node_id, names in group.get("cases", {}).items():
                    self.assertTrue(names, node_id)
                    self.assertEqual(len(names), len(set(names)), node_id)
                    self.assertTrue(all(isinstance(n, str) and n for n in names), node_id)

    def test_every_entry_names_an_existing_test(self):
        """A renamed or deleted test must not leave a stale entry behind."""
        for tag, group in _groups():
            for node_id in _listed_tests(group):
                with self.subTest(version=tag, test=node_id):
                    self.assertTrue(_test_exists(node_id), f"{node_id} does not exist")

    def test_every_case_names_a_case_of_its_test(self):
        """A renamed case must not leave a stale entry behind; the case name is a string in the file."""
        for tag, group in _groups():
            for node_id, names in group.get("cases", {}).items():
                source = (ROOT / node_id.split("::")[0]).read_text(encoding="utf-8")
                for name in names:
                    with self.subTest(version=tag, test=node_id, case=name):
                        self.assertTrue(f'"{name}"' in source or f"'{name}'" in source,
                                        f"{name} is not a case in {node_id.split('::')[0]}")

    def test_each_test_and_case_is_listed_once_per_version(self):
        """A test or case belongs to one issue per version, so fixing the issue removes the entry."""
        for tag, groups in load_known_failures().items():
            tests = [node_id for group in groups for node_id in group.get("tests", [])]
            self.assertEqual(len(tests), len(set(tests)), tag)
            cases = [(node_id, name) for group in groups
                     for node_id, names in group.get("cases", {}).items() for name in names]
            self.assertEqual(len(cases), len(set(cases)), tag)
            # a test marked as failing as a whole has no cases that run
            self.assertFalse({node_id for node_id, _ in cases} & set(tests), tag)

    def test_expected_failing_cases_are_found_by_short_id(self):
        """The subTest hook looks cases up by (module, class, method)."""
        from .conftest import expected_failing_cases

        cases = expected_failing_cases("3.10")
        self.assertIn("R_for_try_if",
                      cases[("test_control_flow", "TestLoopContinueAndTailIf", "test_behavior_is_preserved")])
        self.assertEqual(expected_failing_cases("3.99"), {})


if __name__ == "__main__":
    unittest.main()
