"""Bytecode written by one Python version is decompiled under a host of another (#94)."""
import ast
import json
import os
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

from pycrefine.codeobj import ForeignCode
from pycrefine.decompiler.base import DecompilerBase
from pycrefine.instructions import BytecodeInstruction
from pycrefine.loader import get_decompiler

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "foreign_pyc"
HOST = sys.version_info[:2]

_NATIVE = (
    "import sys\n"
    "sys.path.insert(0, sys.argv[1])\n"
    "from pycrefine.loader import get_decompiler\n"
    "sys.stdout.write(get_decompiler(sys.argv[2]).decompile())\n"
)


def _interpreter(version):
    """A Python of the given minor version on this machine, or None."""
    tag = f"{version[0]}.{version[1]}"
    found = shutil.which(f"python{tag}")
    if found:
        return found
    uv = Path.home() / ".local" / "share" / "uv" / "python"
    for candidate in sorted(uv.glob(f"cpython-{tag}.*/bin/python{tag}")):
        return str(candidate)
    return None


def _fixtures():
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    for name, entry in sorted(manifest["files"].items()):
        major, minor = (int(part) for part in entry["python"].split("."))
        if (major, minor) != HOST:
            yield name, (major, minor)


class TestCrossHost(unittest.TestCase):

    def test_every_fixture_of_another_version_decompiles_to_valid_source(self):
        count = 0
        for name, version in _fixtures():
            with self.subTest(fixture=name):
                source = get_decompiler(str(FIXTURES / name)).decompile()
                ast.parse(source)
                count += 1
        self.assertGreater(count, 0)

    def test_the_output_is_the_one_the_writing_version_gives(self):
        """Run the fixture under an interpreter of the version that wrote it, when there is one, and compare."""
        compared = 0
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
        for name, version in _fixtures():
            python = _interpreter(version)
            if python is None:
                continue
            with self.subTest(fixture=name):
                path = str(FIXTURES / name)
                run = subprocess.run([python, "-I", "-c", _NATIVE, str(ROOT / "src"), path],
                                     capture_output=True, text=True, env=env, check=False)
                self.assertEqual(run.returncode, 0, run.stderr)
                self.assertEqual(get_decompiler(path).decompile(), run.stdout)
                compared += 1
        if not compared:
            self.skipTest("no interpreter of another version is installed")

    def test_the_names_of_nested_foreign_code_are_reserved_for_temporaries(self):
        fields = dict(co_varnames=(), co_cellvars=(), co_freevars=(), co_consts=())
        inner = ForeignCode(co_name="g", **{**fields, "co_names": ("_item",)})
        outer = ForeignCode(co_name="f", **{**fields, "co_names": (), "co_consts": (inner,)})
        decompiler = DecompilerBase(outer)
        self.assertIn("_item", decompiler._code_object_names())  # pylint: disable=protected-access

    def test_a_synthetic_instruction_has_no_stack_effect_on_any_host(self):
        """The decompiler's own instructions (opcode -1) have none under the host's dis; foreign code agrees."""
        name = "simple.cpython-313.pyc" if HOST != (3, 13) else "simple.cpython-314.pyc"
        decompiler = get_decompiler(str(FIXTURES / name))
        synthetic = BytecodeInstruction(-1, "GET_ITER", None, 0, 0, None, False)
        with self.assertRaises(ValueError):
            decompiler._stack_effect(synthetic)  # pylint: disable=protected-access

    def test_the_halves_of_a_split_superinstruction_have_the_same_effect_on_every_host(self):
        for target in ((3, 13), (3, 14)):
            name = f"simple.cpython-{target[0]}{target[1]}.pyc"
            decompiler = get_decompiler(str(FIXTURES / name))
            with self.subTest(target=target):
                store = BytecodeInstruction(0, "STORE_FAST", None, "a", 0, None, False)
                load = BytecodeInstruction(0, "LOAD_FAST", None, "a", 1, None, False)
                self.assertEqual((decompiler._stack_effect(store), decompiler._stack_effect(load)),  # pylint: disable=protected-access
                                 (-1, 1))

    @unittest.skipIf(HOST < (3, 13), "STORE_FAST_LOAD_FAST starts with 3.13")
    def test_the_halves_of_a_split_superinstruction_on_their_own_host(self):
        import dis  # pylint: disable=import-outside-toplevel
        code = compile("x = 1", "<t>", "exec")
        host = DecompilerBase(code)
        number = dis.opmap["STORE_FAST_LOAD_FAST"]
        store = BytecodeInstruction(number, "STORE_FAST", None, "a", 0, None, False)
        load = BytecodeInstruction(number, "LOAD_FAST", None, "a", 1, None, False)
        self.assertEqual((host._stack_effect(store), host._stack_effect(load)), (-1, 1))  # pylint: disable=protected-access

    def test_a_missing_argument_is_refused_on_every_host(self):
        from pycrefine.opcodes.decode import stack_effect  # pylint: disable=import-outside-toplevel
        for version in ((3, 10), (3, 11), (3, 12), (3, 13), (3, 14)):
            with self.subTest(version=version), self.assertRaises(ValueError):
                stack_effect(version, "LOAD_CONST", None)


if __name__ == "__main__":
    unittest.main()
