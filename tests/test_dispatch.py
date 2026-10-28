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


# Opcode names the table registers that the running interpreter may not have: they belong
# to another supported Python (CPython 3.9 to 3.14) or are specialized forms. A name that is
# neither here nor in this interpreter's `dis.opmap` is a typo or a made-up opcode. The list
# is the registered names that some supported version has and another lacks, so check a new
# entry against `dis.opmap` of the version it comes from (`LOAD_FAST_BORROW_LOAD_FAST_BORROW`,
# long as it is, is the 3.14 superinstruction). Keep it sorted.
OPCODES_OF_OTHER_VERSIONS = frozenset((
    "BEFORE_WITH",
    "BINARY_OP",
    "BINARY_SLICE",
    "BINARY_SUBSCR",
    "BUILD_CONST_KEY_MAP",
    "CACHE",
    "CALL",
    "CALL_FUNCTION",
    "CALL_FUNCTION_KW",
    "CALL_INTRINSIC_1",
    "CALL_KW",
    "CALL_METHOD",
    "CHECK_EXC_MATCH",
    "COPY",
    "COPY_FREE_VARS",
    "DUP_TOP",
    "DUP_TOP_TWO",
    "END_FOR",
    "FORMAT_SIMPLE",
    "FORMAT_VALUE",
    "JUMP_ABSOLUTE",
    "JUMP_BACKWARD",
    "JUMP_IF_FALSE_OR_POP",
    "JUMP_IF_NOT_EXC_MATCH",
    "JUMP_IF_TRUE_OR_POP",
    "KW_NAMES",
    "LOAD_ASSERTION_ERROR",
    "LOAD_CLASSDEREF",
    "LOAD_COMMON_CONSTANT",
    "LOAD_CONST_BORROW",
    "LOAD_FAST_BORROW",
    "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
    "LOAD_FROM_DICT_OR_GLOBALS",
    "LOAD_GLOBAL_MODULE",
    "LOAD_METHOD",
    "LOAD_SMALL_INT",
    "LOAD_SUPER_ATTR",
    "MAKE_CELL",
    "NOT_TAKEN",
    "POP_ITER",
    "POP_JUMP_IF_FALSE",
    "POP_JUMP_IF_NONE",
    "POP_JUMP_IF_NOT_NONE",
    "POP_JUMP_IF_TRUE",
    "PRECALL",
    "PUSH_EXC_INFO",
    "PUSH_NULL",
    "RESUME",
    "RETURN_CONST",
    "ROT_FOUR",
    "ROT_THREE",
    "ROT_TWO",
    "SETUP_FINALLY",
    "SETUP_WITH",
    "SET_FUNCTION_ATTRIBUTE",
    "SWAP",
    "UNARY_POSITIVE",
    "YIELD_FROM",
))
# Instructions the decompiler builds itself (opcode -1), not read from bytecode.
SYNTHETIC_OPCODES = frozenset(("INLINED_COMPREHENSION",))


class TestDispatchTable(unittest.TestCase):

    def test_every_entry_names_a_handler_method(self):
        """Each opcode maps to the name of an `_op_*` method the backend has."""
        for backend in BACKENDS:
            table = backend._DISPATCH
            self.assertTrue(table)
            for opname, method in table.items():
                with self.subTest(backend=backend.__name__, opname=opname):
                    self.assertIsInstance(method, str)
                    self.assertTrue(method.startswith("_op_"), method)
                    self.assertTrue(callable(getattr(backend, method, None)), method)

    def test_tables_are_built_once_per_class(self):
        """The table is class state, merged at class creation; instances do not build one."""
        for backend in BACKENDS:
            dec = backend(compile("x = 1\n", "<test>", "exec"))
            with self.subTest(backend=backend.__name__):
                self.assertNotIn("_dispatch", vars(dec))
                self.assertIs(dec._DISPATCH, backend._DISPATCH)

    def test_backend_overrides_are_merged_along_the_mra(self):
        class Probe(DecompilerGeneric):
            DISPATCH_OVERRIDES = {"RESUME": "_op_load"}

        class Child(Probe):
            DISPATCH_OVERRIDES = {"NOP": "_op_load"}

        self.assertEqual(DecompilerGeneric._DISPATCH["RESUME"], "_op_no_op")
        self.assertEqual(Probe._DISPATCH["RESUME"], "_op_load")
        self.assertEqual(Child._DISPATCH["RESUME"], "_op_load")
        self.assertEqual(Child._DISPATCH["NOP"], "_op_load")
        self.assertEqual(Probe._DISPATCH["NOP"], "_op_nop")

    def test_registered_names_are_real_opcodes(self):
        import dis

        known = set(dis.opmap) | OPCODES_OF_OTHER_VERSIONS | SYNTHETIC_OPCODES
        for backend in BACKENDS:
            for opname in backend._DISPATCH:
                with self.subTest(backend=backend.__name__, opname=opname):
                    self.assertIn(opname, known)

    def test_opcode_names_look_like_opcodes(self):
        for opname in DecompilerGeneric._DISPATCH:
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


class TestInstructionTemplate(unittest.TestCase):
    """`_handle_instruction` is one template; backends only override `_dispatch_instruction` (#119)."""

    def test_exactly_one_handle_instruction(self):
        found = []
        for path, text in _sources().items():
            for node in ast.walk(ast.parse(text)):
                if isinstance(node, ast.FunctionDef) and node.name == "_handle_instruction":
                    found.append((path.name, node.lineno))
        self.assertEqual(len(found), 1, f"expected one definition, found {found}")
        self.assertEqual(found[0][0], "generic.py")
        for backend in BACKENDS:
            self.assertIs(backend._handle_instruction, DecompilerGeneric._handle_instruction)

    def test_suppression_preamble_exists_once(self):
        text = "\n".join(_sources().values())
        self.assertEqual(len(re.findall(r"if instr\.offset in self\.prescan\.ternary_suppress:", text)), 1)
        self.assertEqual(len(re.findall(r"if instr\.offset in self\.prescan\.compound_suppress:", text)), 1)

    def _decompiler(self, backend):
        return backend(compile("x = 1\n", "<test>", "exec"))

    def _instr(self, offset):
        from pycrefine.decompiler.base import BytecodeInstruction
        return BytecodeInstruction(0, "NOP", None, None, offset, None, False)

    def test_ternary_suppression_comes_before_compound_suppression(self):
        """A ternary-suppressed instruction must not also pop the stack for a compound condition."""
        dec = self._decompiler(DecompilerGeneric)
        dec.stack.append("kept")
        dec.prescan.ternary_suppress.add(4)
        dec.prescan.compound_suppress.add(4)
        self.assertTrue(dec._suppressed_by_prescan(self._instr(4)))
        self.assertEqual(dec.stack, ["kept"])

    def test_compound_suppression_pops_the_clause_operand(self):
        dec = self._decompiler(DecompilerGeneric)
        dec.stack.extend(["a", "clause"])
        dec.prescan.compound_suppress.add(6)
        self.assertTrue(dec._suppressed_by_prescan(self._instr(6)))
        self.assertEqual(dec.stack, ["a"])

    def test_unsuppressed_instruction_is_dispatched_by_every_backend(self):
        for backend in BACKENDS:
            dec = self._decompiler(backend)
            calls = []
            dec._dispatch_instruction = calls.append
            dec.prescan.ternary_suppress.add(2)
            dec._handle_instruction(self._instr(2))
            dec._handle_instruction(self._instr(8))
            with self.subTest(backend=backend.__name__):
                self.assertEqual([i.offset for i in calls], [8])


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
