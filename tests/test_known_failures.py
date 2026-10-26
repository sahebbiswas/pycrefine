"""tests/known_failures.json stays well formed and points at tests that exist."""
import ast
import re
import unittest
from pathlib import Path

from .conftest import KNOWN_FAILURE_MODES, load_known_failures

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


class TestKnownFailures(unittest.TestCase):

    def test_entries_are_well_formed(self):
        """Each group names a Python minor version, an issue, a reason and its tests."""
        known = load_known_failures()
        self.assertTrue(known)
        for tag, groups in known.items():
            self.assertRegex(tag, r"^3\.\d+$")
            for group in groups:
                with self.subTest(version=tag, issue=group.get("issue")):
                    self.assertIsInstance(group["issue"], int)
                    self.assertTrue(group["reason"].strip())
                    self.assertIn(group.get("mode", "xfail"), KNOWN_FAILURE_MODES)
                    self.assertTrue(group["tests"])
                    for node_id in group["tests"]:
                        self.assertRegex(node_id, r"^tests/test_\w+\.py::\w+::test_\w+$")

    def test_every_entry_names_an_existing_test(self):
        """A renamed or deleted test must not leave a stale entry behind."""
        seen = set()
        for tag, groups in load_known_failures().items():
            for group in groups:
                for node_id in group["tests"]:
                    with self.subTest(version=tag, test=node_id):
                        self.assertTrue(_test_exists(node_id), f"{node_id} does not exist")
                        self.assertNotIn((tag, node_id), seen, "listed twice")
                        seen.add((tag, node_id))

    def test_each_test_is_listed_once_per_version(self):
        """A test belongs to one issue per version, so fixing the issue removes the entry."""
        for tag, groups in load_known_failures().items():
            ids = [node_id for group in groups for node_id in group["tests"]]
            self.assertEqual(len(ids), len(set(ids)), tag)
            self.assertFalse([i for i in ids if not re.match(r"^tests/", i)])


if __name__ == "__main__":
    unittest.main()
