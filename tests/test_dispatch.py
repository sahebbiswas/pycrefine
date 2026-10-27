"""The dispatch table and the documentation that names code stay in step with the code (#123)."""
import ast
import re
import unittest
from pathlib import Path

from pycrefine.decompiler.generic import DecompilerGeneric
from pycrefine.decompiler.py39 import Decompiler39
from pycrefine.decompiler.py311 import Decompiler311Plus
from pycrefine.decompiler.py314 import Decompiler314

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "src" / "pycrefine"
BACKENDS = (DecompilerGeneric, Decompiler39, Decompiler311Plus, Decompiler314)


def _sources():
    return {path: path.read_text(encoding="utf-8") for path in PACKAGE.rglob("*.py")}


class TestDispatchTable(unittest.TestCase):

    def decompiler(self, backend):
        return backend(compile("x = 1\n", "<test>", "exec"))

    def test_every_entry_is_a_bound_handler(self):
        """Each opcode maps to a callable method of the decompiler that owns the table."""
        for backend in BACKENDS:
            dispatch = self.decompiler(backend)._dispatch
            self.assertTrue(dispatch)
            for opname, handler in dispatch.items():
                with self.subTest(backend=backend.__name__, opname=opname):
                    self.assertTrue(callable(handler))
                    self.assertTrue(handler.__name__.startswith("_op_"), handler.__name__)
                    self.assertTrue(hasattr(self.decompiler(backend), handler.__name__))

    def test_opcode_names_look_like_opcodes(self):
        for opname in self.decompiler(DecompilerGeneric)._dispatch:
            with self.subTest(opname=opname):
                self.assertRegex(opname, r"^[A-Z][A-Z0-9_]*$")

    def test_every_handler_the_code_refers_to_exists(self):
        """A ``self._op_x`` anywhere in the package names a method some decompiler defines."""
        defined = set()
        referred = {}
        for path, text in _sources().items():
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name.startswith("_op_"):
                    defined.add(node.name)
                if (isinstance(node, ast.Attribute) and node.attr.startswith("_op_")
                        and isinstance(node.value, ast.Name) and node.value.id == "self"):
                    referred.setdefault(node.attr, path.name)
        missing = {name: where for name, where in referred.items() if name not in defined}
        self.assertEqual(missing, {})

    def test_every_handler_is_registered_or_called(self):
        """A handler nobody can reach is dead code, or an opcode that was never registered."""
        text = "\n".join(_sources().values())
        for backend in BACKENDS:
            for name in vars(backend):
                if name.startswith("_op_"):
                    with self.subTest(handler=f"{backend.__name__}.{name}"):
                        self.assertGreater(len(re.findall(rf"\b{name}\b", text)), 1,
                                           f"{name} is defined but never referred to")


class TestDocumentationNamesRealCode(unittest.TestCase):
    """The guides name methods and files; renaming one must update the guide."""

    GUIDES = ("docs/architecture.md", "docs/extending.md")

    def test_methods_and_constants_named_in_the_guides_exist(self):
        code = "\n".join(_sources().values())
        for guide in self.GUIDES:
            text = (ROOT / guide).read_text(encoding="utf-8")
            names = set(re.findall(r"`(_[A-Za-z][A-Za-z0-9_]*)(?:\(\))?`", text))
            self.assertTrue(names, guide)
            for name in sorted(names):
                with self.subTest(guide=guide, name=name):
                    self.assertTrue(re.search(rf"\b{name}\b", code), f"{name} is not in the code")

    def test_paths_named_in_the_guides_exist(self):
        for guide in self.GUIDES:
            text = (ROOT / guide).read_text(encoding="utf-8")
            for path in sorted(set(re.findall(r"`((?:tests|quality|src|docs|corpus)/[\w./-]+?\.(?:py|md|json|yml))(?:::\w+)*`", text))):
                if "X" in path:  # a pattern such as quality/baselines/py3.X.json
                    continue
                with self.subTest(guide=guide, path=path):
                    self.assertTrue((ROOT / path).exists(), f"{path} does not exist")

    def test_test_ids_named_in_the_guides_exist(self):
        for guide in self.GUIDES:
            text = (ROOT / guide).read_text(encoding="utf-8")
            for path, cls in sorted(set(re.findall(r"`(tests/[\w.]+\.py)::(\w+)`", text))):
                with self.subTest(guide=guide, test=f"{path}::{cls}"):
                    self.assertRegex((ROOT / path).read_text(encoding="utf-8"), rf"(?m)^class {cls}\b")

    def test_guides_are_linked_from_the_index(self):
        index = (ROOT / "docs" / "README.md").read_text(encoding="utf-8")
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        for name in ("architecture.md", "extending.md"):
            self.assertIn(name, index)
            self.assertIn(name, readme)


if __name__ == "__main__":
    unittest.main()
