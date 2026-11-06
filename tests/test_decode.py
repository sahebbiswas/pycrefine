"""The decoder reads bytecode field for field as the host's ``dis`` does (#94); CI runs this on every version."""
import dis
import os
import sys
import types
import unittest
from pathlib import Path

from pycrefine.opcodes.decode import decode_instructions, line_starts, opcodes_for, stack_effect
from pycrefine.opcodes.tables import TABLES

HOST = sys.version_info[:2]
ROOT = Path(__file__).resolve().parent.parent


def _fields(ins):
    argval = "<unknown>" if type(ins.argval).__name__ == "_Unknown" else ins.argval   # (3.11's dis.UNKNOWN)
    return (ins.opcode, ins.opname, ins.arg, argval, ins.offset, ins.is_jump_target, ins.argrepr)


def _dis_line(ins):
    line = ins.starts_line
    if isinstance(line, bool):
        line = getattr(ins, "line_number", None) if line else None
    return line


def _walk(code):
    yield code
    for const in code.co_consts:
        if isinstance(const, types.CodeType):
            yield from _walk(const)


_EXTRA = {
    "async_for": "async def f(a):\n    async for x in a:\n        print(x)\n    return 1\n",
    "async_with_gen": "async def g(a):\n    async with a as b:\n        yield b\n    await a\n",
    "slices": "def h(a, b):\n    return a[1:2], a[::b], a[b:], a[1:2:3]\n",
}


def _sources():
    yield from _EXTRA.items()
    corpus = ROOT / "corpus"
    for path in sorted(corpus.glob("*.py")):
        yield path.name, path.read_text(encoding="utf-8")
    stdlib = Path(os.path.dirname(os.__file__))
    for name in ("textwrap.py", "json/decoder.py", "string.py", "bisect.py", "dataclasses.py", "enum.py",
                 "argparse.py", "contextlib.py", "functools.py"):
        path = stdlib / name
        if path.exists():
            yield name, path.read_text(encoding="utf-8")


@unittest.skipIf(HOST not in TABLES, "no opcode table for this host")
class TestDecodeMatchesDis(unittest.TestCase):

    def test_every_instruction_of_the_corpus_and_some_of_the_standard_library(self):
        checked = 0
        for name, text in _sources():
            try:
                module = compile(text, name, "exec")
            except SyntaxError:
                continue
            for code in _walk(module):
                with self.subTest(file=name, code=code.co_qualname if hasattr(code, "co_qualname") else code.co_name):
                    expected = list(dis.get_instructions(code))
                    got = decode_instructions(code, HOST)
                    self.assertEqual([_fields(i) for i in got], [_fields(i) for i in _normalised(expected)])
                    self.assertEqual([i.starts_line for i in got], [_dis_line(i) for i in expected])
                    checked += len(got)
        self.assertGreater(checked, 5000)

    def test_line_starts_match_findlinestarts(self):
        for name, text in _sources():
            try:
                module = compile(text, name, "exec")
            except SyntaxError:
                continue
            for code in _walk(module):
                self.assertEqual(sorted(line_starts(code, HOST).items()), sorted(dis.findlinestarts(code)),
                                 f"{name}:{code.co_name}")

    def test_stack_effect_matches(self):
        ops = opcodes_for(HOST)
        for name, number in ops.numbers.items():
            if name not in ops.table["stack"]:
                continue
            for arg in (None,) if not ops.has_arg(name) else (0, 1, 2, 3, 7, 20, 255, 256, 257, 259, 511, 65535, 70000):
                try:
                    expected = dis.stack_effect(number, arg, jump=False)
                except ValueError:
                    with self.assertRaises(ValueError):
                        stack_effect(HOST, name, arg)
                    continue
                self.assertEqual(stack_effect(HOST, name, arg), expected, f"{name} {arg}")


def _normalised(instructions):
    """The ``dis`` instructions as ``decode_instructions`` spells them (3.13's labels are per code object)."""
    return instructions


@unittest.skipIf(HOST not in TABLES, "no opcode table for this host")
class TestTablesAreCurrent(unittest.TestCase):

    def test_the_stored_table_of_the_host_is_what_its_own_dis_reports(self):
        import importlib.util
        import json
        spec = importlib.util.spec_from_file_location("generate_opcode_tables", ROOT / "quality" / "generate_opcode_tables.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        live = module.dump(sys.executable)
        live.pop("version")
        self.assertEqual(json.loads(json.dumps(TABLES[HOST])), live,
                         "opcodes/tables.py is out of date: run quality/generate_opcode_tables.py")


@unittest.skipIf(HOST not in TABLES, "no opcode table for this host")
class TestGeneratorRefusesIncompleteInput(unittest.TestCase):

    def _run(self, *pairs):
        import subprocess  # pylint: disable=import-outside-toplevel
        script = str(ROOT / "quality" / "generate_opcode_tables.py")
        return subprocess.run([sys.executable, "-I", script, "--check", *pairs], capture_output=True, text=True, check=False)

    def test_a_list_that_leaves_out_a_stored_version_is_refused(self):
        tag = f"{HOST[0]}.{HOST[1]}"
        run = self._run(f"{tag}={sys.executable}")
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("would lose", run.stderr)

    def test_two_interpreters_of_one_version_are_refused(self):
        tag = f"{HOST[0]}.{HOST[1]}"
        run = self._run(f"{tag}={sys.executable}", f"{tag}={sys.executable}")
        self.assertEqual(run.returncode, 2, run.stderr)
        self.assertIn("same version", run.stderr)


if __name__ == "__main__":
    unittest.main()
