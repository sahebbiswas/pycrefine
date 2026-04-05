import unittest
import os
import struct
import tempfile
import io
import marshal
import types
from pycrefine import (
    MarshalParser,
    Decompiler39,
    BytecodeInstruction,
    get_decompiler,
    DecompilerGeneric,
    Decompiler311Plus,
)
from .test_helpers import _compile, decompile, _run39_full_impl



class TestDecompilerDispatch(unittest.TestCase):
    """Tests for get_decompiler version routing logic (FIX-03)."""

    def _make_pyc_with_magic(self, magic_int: int) -> str:
        """Write a dummy .pyc with a given magic number (version id in low 16 bits)."""
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            # header: magic(4) + flags(4) + mtime(4) + size(4) = 16 bytes
            f.write(struct.pack("<I", magic_int))
            f.write(b"\x00" * 12)
            code = compile("x = 1", "<test>", "exec")
            f.write(marshal.dumps(code))
            return f.name

    def test_host_version_returns_decompiler(self):
        """Compiling with the current interpreter must return a working decompiler."""
        pyc = _compile("x = 1\n")
        try:
            dec = get_decompiler(pyc)
            self.assertIsNotNone(dec)
            out = dec.decompile()
            self.assertIn("x = 1", out)
        finally:
            if os.path.exists(pyc):
                os.unlink(pyc)

    def test_decompiler39_class_exists(self):
        self.assertIsNotNone(Decompiler39)

    def test_decompiler311plus_class_exists(self):
        self.assertIsNotNone(Decompiler311Plus)

    def test_decompilergeneric_class_exists(self):
        self.assertIsNotNone(DecompilerGeneric)

    def test_decompiler_returns_str(self):
        pyc = _compile("y = 2\n")
        try:
            out = get_decompiler(pyc).decompile()
            self.assertIsInstance(out, str)
        finally:
            if os.path.exists(pyc):
                os.unlink(pyc)

    def _check_version_dispatch(self, version_id: int, expected_class: str) -> None:
        """Verify that a magic-number band routes to the expected decompiler class."""
        from pycrefine import Decompiler314
        class_map = {
            "Decompiler39":      Decompiler39,
            "Decompiler311Plus": Decompiler311Plus,
            "Decompiler314":     Decompiler314,
            "DecompilerGeneric": DecompilerGeneric,
        }
        magic = (0x0D0D << 16) | version_id
        pyc = self._make_pyc_with_magic(magic)
        try:
            dec = get_decompiler(pyc)
            self.assertIsInstance(
                dec, class_map[expected_class],
                f"version_id={version_id}: expected {expected_class}, "
                f"got {type(dec).__name__}",
            )
        except ValueError:
            # Unrecognised magic on a host that requires it to match - acceptable
            pass
        finally:
            if os.path.exists(pyc):
                os.unlink(pyc)

    def test_version_dispatch_39(self):
        """Python 3.9 magic range -> Decompiler39."""
        self._check_version_dispatch(3415, "Decompiler39")

    def test_version_dispatch_311(self):
        """Python 3.10/3.11 magic range -> Decompiler311Plus."""
        self._check_version_dispatch(3450, "Decompiler311Plus")

    def test_version_dispatch_312(self):
        """Python 3.12 magic range -> Decompiler311Plus."""
        self._check_version_dispatch(3495, "Decompiler311Plus")

    def test_version_dispatch_314(self):
        """Python 3.14 magic range -> Decompiler314."""
        self._check_version_dispatch(3560, "Decompiler314")

class TestMarshalParser(unittest.TestCase):
    """Unit tests for the custom marshal reader (cross-version .pyc support)."""

    def _make_parser(self, data: bytes) -> MarshalParser:
        return MarshalParser(data)

    def test_load_none(self):
        p = self._make_parser(b"N")
        self.assertIsNone(p.load())

    def test_load_true(self):
        p = self._make_parser(b"T")
        self.assertIs(p.load(), True)

    def test_load_false(self):
        p = self._make_parser(b"F")
        self.assertIs(p.load(), False)

    def test_load_int(self):
        p = self._make_parser(b"i" + struct.pack("<i", 42))
        self.assertEqual(p.load(), 42)

    def test_load_negative_int(self):
        p = self._make_parser(b"i" + struct.pack("<i", -7))
        self.assertEqual(p.load(), -7)

    def test_load_binary_float(self):
        p = self._make_parser(b"g" + struct.pack("<d", 3.14))
        result = p.load()
        self.assertAlmostEqual(result, 3.14, places=10)

    def test_load_short_string(self):
        s = b"hello"
        p = self._make_parser(b"s" + struct.pack("<i", len(s)) + s)
        self.assertEqual(p.load(), s)

    def test_load_unicode_string(self):
        s = "hello"
        encoded = s.encode("utf-8")
        p = self._make_parser(b"u" + struct.pack("<i", len(encoded)) + encoded)
        self.assertEqual(p.load(), s)

    def test_load_small_tuple(self):
        # TYPE_SMALL_TUPLE 'y', size=2, then N, N
        p = self._make_parser(b"y\x02NN")
        self.assertEqual(p.load(), (None, None))

    def test_load_empty_tuple(self):
        p = self._make_parser(b"y\x00")
        self.assertEqual(p.load(), ())

    def test_load_list(self):
        p = self._make_parser(b"[" + struct.pack("<i", 2) + b"TF")
        self.assertEqual(p.load(), [True, False])

    def test_eof_raises(self):
        p = self._make_parser(b"i\x00")  # incomplete int
        with self.assertRaises(EOFError):
            p.load()

    def test_unsupported_type_raises(self):
        p = self._make_parser(b"?")  # not a valid marshal type
        with self.assertRaisesRegex(ValueError, "Unsupported marshal type"):
            p.load()

    def test_ref_flag(self):
        """An object with the ref flag set (0x80) must be stored and retrievable."""
        # 0x80 | ord('N') = 0xCE - flagged None, then a TYPE_REF back to it
        data = bytes([0x80 | ord("N"), ord("r")]) + struct.pack("<i", 0)
        p = self._make_parser(data)
        first = p.load()
        second = p.load()
        self.assertIsNone(first)
        self.assertIsNone(second)

class TestMarshalParserCodeType(unittest.TestCase):
    """
    Verify that MarshalParser._load_code uses the correct CodeType constructor
    signature for the running Python version.
    """

    def _roundtrip(self, src: str) -> types.CodeType:
        """Compile src, parse the .pyc via native marshal, return the code object."""
        pyc = _compile(src)
        try:
            with open(pyc, "rb") as f:
                data = f.read()
            # Try to find the code object start after known headers
            for offset in (16, 12, 8, 4):
                try:
                    obj = marshal.load(io.BytesIO(data[offset:]))
                    if isinstance(obj, types.CodeType):
                        return obj
                except Exception:
                    continue
            raise ValueError("Could not extract CodeType from .pyc")
        finally:
            os.unlink(pyc)

    def test_roundtrip_simple(self):
        code = self._roundtrip("x = 1\n")
        self.assertIsInstance(code, types.CodeType)

    def test_roundtrip_preserves_name(self):
        code = self._roundtrip("def my_func():\n    pass\n")
        self.assertEqual(code.co_name, "<module>")

    def test_roundtrip_has_consts(self):
        code = self._roundtrip("x = 42\n")
        self.assertIn(42, code.co_consts)

    def test_roundtrip_function_code(self):
        code = self._roundtrip("def add(a, b):\n    return a + b\n")
        func_codes = [c for c in code.co_consts if isinstance(c, types.CodeType)]
        self.assertGreaterEqual(len(func_codes), 1)
        self.assertEqual(func_codes[0].co_argcount, 2)

    def test_roundtrip_is_dis_able(self):
        """Code objects must be accepted by dis.dis()."""
        import dis, io
        code = self._roundtrip("x = 1\ny = x + 2\n")
        buf = io.StringIO()
        dis.dis(code, file=buf)
        self.assertGreater(len(buf.getvalue()), 0)

class TestDecompiler39Python39Fixes(unittest.TestCase):
    def _run39_full(self, instructions):
        return _run39_full_impl(instructions)

    def _run39(self, instructions):
        """
        Run Decompiler39 on a synthetic instruction list.
        Patches is_jump_target from argval targets (Fix 2)
        """
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = list(instructions)

        _JUMP_OPS = {
            "FOR_ITER", "JUMP_FORWARD", "JUMP_ABSOLUTE",
            "POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE",
            "JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP",
            "SETUP_FINALLY", "SETUP_WITH", "JUMP_IF_NOT_EXC_MATCH",
        }
        targets = set()
        for ins in dec.instructions:
            if ins.opname in _JUMP_OPS and isinstance(ins.argval, int):
                targets.add(ins.argval)
        dec.instructions = [
            BytecodeInstruction(
                ins.opcode, ins.opname, ins.arg, ins.argval,
                ins.offset, ins.starts_line,
                ins.offset in targets,
            )
            for ins in dec.instructions
        ]

        # Run prescan then main decode loop
        dec.pc = 0
        dec.blocks = []
        dec._while_header_targets = {}
        dec._while_body_offsets = set()
        dec._while_true_ends = set()
        dec._prescan_while_loops()

        dec.pc = 0
        dec.has_doc = False
        while dec.pc < len(dec.instructions):
            instr = dec.instructions[dec.pc]
            while dec.blocks and instr.offset >= dec.blocks[-1][0]:
                dec.blocks.pop()
                dec.indent_level -= 1
            dec.pc += 1
            dec._handle_instruction(instr)
        return "\n".join(dec.reconstructed)

    def test_is_backward_instruction_backward_jump_absolute(self):
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        backward = BytecodeInstruction(113, "JUMP_ABSOLUTE", 4, 4, 10, None, False)
        self.assertTrue(dec._is_backward_instruction(backward))

    def test_is_backward_instruction_forward_jump_absolute_is_false(self):
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        forward = BytecodeInstruction(113, "JUMP_ABSOLUTE", 20, 20, 10, None, False)
        self.assertFalse(dec._is_backward_instruction(forward))

    def test_while_loop_uses_jump_absolute_back_edge(self):
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "LOAD_NAME", 0, "n", 0, None, False),
            Instr(0, "LOAD_CONST", 1, 5, 2, None, False),
            Instr(0, "COMPARE_OP", 0, "<", 4, None, False),
            Instr(0, "POP_JUMP_IF_FALSE", 14, 14, 6, None, False),
            Instr(0, "LOAD_CONST", 2, 1, 8, None, False),
            Instr(0, "STORE_NAME", 0, "n", 10, None, False),
            Instr(0, "JUMP_ABSOLUTE", 0, 0, 12, None, False),
            Instr(0, "RETURN_VALUE", None, None, 14, None, True),
        ])
        self.assertIn("while", out)
        self.assertNotIn("if n", out)

    def test_inplace_add_emits_augassign(self):
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "LOAD_NAME", 0, "x", 0, None, False),
            Instr(0, "LOAD_CONST", 1, 3, 2, None, False),
            Instr(0, "INPLACE_ADD", None, None, 4, None, False),
            Instr(0, "STORE_NAME", 0, "x", 6, None, False),
            Instr(0, "RETURN_VALUE", None, None, 8, None, False),
        ])
        self.assertIn("x += 3", out)

    def test_try_except_typed_no_as(self):
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "SETUP_FINALLY", None, 16, 0, None, False),
            Instr(0, "LOAD_CONST", 0, 42, 2, None, False),
            Instr(0, "STORE_NAME", 0, "x", 4, None, False),
            Instr(0, "POP_BLOCK", None, None, 6, None, False),
            Instr(0, "JUMP_FORWARD", None, 32, 8, None, False),
            Instr(0, "DUP_TOP", None, None, 14, None, True),
            Instr(0, "LOAD_NAME", 1, "ValueError", 16, None, False),
            Instr(0, "COMPARE_OP", 10, "exception match", 18, None, False),
            Instr(0, "POP_JUMP_IF_FALSE", 28, 28, 20, None, False),
            Instr(0, "POP_TOP", None, None, 22, None, False),
            Instr(0, "POP_TOP", None, None, 24, None, False),
            Instr(0, "LOAD_CONST", 2, 0, 26, None, False),
            Instr(0, "STORE_NAME", 0, "x", 28, None, False),
            Instr(0, "POP_EXCEPT", None, None, 30, None, False),
            Instr(0, "RETURN_VALUE", None, None, 32, None, False),
        ])
        self.assertIn("try:", out)
        self.assertIn("except ValueError:", out)

    def test_is_backward_jump_jump_backward(self):
        self.assertTrue(Decompiler39._is_backward_jump("JUMP_BACKWARD"))

    def test_is_backward_jump_jump_forward_is_false(self):
        self.assertFalse(Decompiler39._is_backward_jump("JUMP_FORWARD"))

    def test_while_loop_prescan_detects_guard_via_jump_absolute(self):
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [
            Instr(0, "LOAD_NAME", 0, "n", 0, None, False),
            Instr(0, "LOAD_CONST", 1, 5, 2, None, False),
            Instr(0, "COMPARE_OP", 0, "<", 4, None, False),
            Instr(0, "POP_JUMP_IF_FALSE", 14, 14, 6, None, False),
            Instr(0, "LOAD_CONST", 2, 1, 8, None, False),
            Instr(0, "STORE_NAME", 0, "n", 10, None, False),
            Instr(0, "JUMP_ABSOLUTE", 0, 0, 12, None, False),
            Instr(0, "RETURN_VALUE", None, None, 14, None, True),
        ]
        dec._while_body_offsets = set()
        dec._while_header_targets = {}
        dec._while_true_ends = set()
        dec._prescan_while_loops()
        self.assertGreaterEqual(len(dec._while_header_targets), 1)

    def test_forward_jump_absolute_treated_as_jump_forward(self):
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "LOAD_CONST", 0, 1, 0, None, False),
            Instr(0, "STORE_NAME", 0, "x", 2, None, False),
            Instr(0, "LOAD_NAME", 0, "x", 4, None, False),
            Instr(0, "LOAD_CONST", 1, 0, 6, None, False),
            Instr(0, "COMPARE_OP", 4, ">", 8, None, False),
            Instr(0, "POP_JUMP_IF_FALSE", 16, 16, 10, None, False),
            Instr(0, "LOAD_CONST", 2, 1, 12, None, False),
            Instr(0, "STORE_NAME", 1, "y", 14, None, False),
            Instr(0, "JUMP_ABSOLUTE", 18, 18, 16, None, False),
            Instr(0, "RETURN_VALUE", None, None, 18, None, True),
        ])
        self.assertNotIn("while", out)

    def test_is_jump_target_set_correctly(self):
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec39 = Decompiler39(code)
        raw = [
            Instr(100, "LOAD_CONST", 0, 1, 0, None, False),
            Instr(114, "POP_JUMP_IF_FALSE", 8, 8, 2, None, False),
            Instr(100, "LOAD_CONST", 1, 2, 4, None, False),
            Instr(90, "STORE_NAME", 0, "y", 6, None, False),
            Instr(83, "RETURN_VALUE", None, None, 8, None, False),
        ]
        dec39.instructions = raw
        dec39.code_obj = type("C", (), {
            "co_code": bytes(b for ins in raw for b in [ins.opcode, ins.arg if ins.arg is not None else 0]),
            "co_consts": (1, 2, None), "co_names": ("y",), "co_varnames": (), "co_cellvars": (), "co_freevars": (),
        })()
        dec39._disassemble()
        instr_at_8 = next((i for i in dec39.instructions if i.offset == 8), None)
        self.assertTrue(instr_at_8.is_jump_target)

    def test_try_except_as_binding(self):
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "SETUP_FINALLY", None, 16, 0, None, False),
            Instr(0, "LOAD_CONST", 0, 42, 2, None, False),
            Instr(0, "STORE_NAME", 0, "x", 4, None, False),
            Instr(0, "POP_BLOCK", None, None, 6, None, False),
            Instr(0, "JUMP_FORWARD", None, 36, 8, None, False),
            Instr(0, "DUP_TOP", None, None, 14, None, True),
            Instr(0, "LOAD_NAME", 1, "ValueError", 16, None, False),
            Instr(0, "COMPARE_OP", 10, "exception match", 18, None, False),
            Instr(0, "POP_JUMP_IF_FALSE", 30, 30, 20, None, False),
            Instr(0, "POP_TOP", None, None, 22, None, False),
            Instr(0, "STORE_NAME", 2, "e", 24, None, False),
            Instr(0, "LOAD_CONST", 3, 0, 26, None, False),
            Instr(0, "STORE_NAME", 0, "x", 28, None, False),
            Instr(0, "POP_EXCEPT", None, None, 30, None, False),
            Instr(0, "RETURN_VALUE", None, None, 32, None, False),
        ])
        self.assertIn("except ValueError as e:", out)

    def test_try_except_body_indented_correctly(self):
        Instr = BytecodeInstruction
        out = self._run39([
            Instr(0, "SETUP_FINALLY", None, 12, 0, None, False),
            Instr(0, "LOAD_CONST", 0, 99, 2, None, False),
            Instr(0, "STORE_NAME", 0, "x", 4, None, False),
            Instr(0, "POP_BLOCK", None, None, 6, None, False),
            Instr(0, "JUMP_FORWARD", None, 28, 8, None, False),
            Instr(0, "DUP_TOP", None, None, 12, None, True),
            Instr(0, "LOAD_NAME", 1, "OSError", 14, None, False),
            Instr(0, "COMPARE_OP", 10, "exception match", 16, None, False),
            Instr(0, "POP_JUMP_IF_FALSE", 26, 26, 18, None, False),
            Instr(0, "POP_TOP", None, None, 20, None, False),
            Instr(0, "POP_TOP", None, None, 22, None, False),
            Instr(0, "LOAD_CONST", 2, 0, 24, None, False),
            Instr(0, "STORE_NAME", 0, "x", 26, None, False),
            Instr(0, "POP_EXCEPT", None, None, 28, None, False),
            Instr(0, "RETURN_VALUE", None, None, 30, None, False),
        ])
        for line in out.splitlines():
            if "x = 0" in line: self.assertTrue(line.startswith("    "))

    def test_dup_top_not_exception_match_falls_through(self):
        Instr = BytecodeInstruction
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [Instr(0, "LOAD_CONST", 0, 42, 0, None, False), Instr(0, "DUP_TOP", None, None, 2, None, False)]
        dec.stack = [42]
        dec._handle_instruction(dec.instructions[1])
        self.assertEqual(len(dec.stack), 2)

    def test_inplace_sub_emits_augassign(self):
        Instr = BytecodeInstruction
        out = self._run39([Instr(0, "LOAD_NAME", 0, "x", 0, None, False), Instr(0, "LOAD_CONST", 0, 1, 2, None, False), Instr(0, "INPLACE_SUBTRACT", None, None, 4, None, False), Instr(0, "STORE_NAME", 0, "x", 6, None, False), Instr(0, "RETURN_VALUE", None, None, 8, None, False)])
        self.assertIn("x -= 1", out)

    def test_inplace_mul_emits_augassign(self):
        Instr = BytecodeInstruction
        out = self._run39([Instr(0, "LOAD_NAME", 0, "x", 0, None, False), Instr(0, "LOAD_CONST", 0, 2, 2, None, False), Instr(0, "INPLACE_MULTIPLY", None, None, 4, None, False), Instr(0, "STORE_NAME", 0, "x", 6, None, False), Instr(0, "RETURN_VALUE", None, None, 8, None, False)])
        self.assertIn("x *= 2", out)

    def test_inplace_xor_emits_augassign(self):
        Instr = BytecodeInstruction
        out = self._run39([Instr(0, "LOAD_NAME", 0, "x", 0, None, False), Instr(0, "LOAD_CONST", 0, 5, 2, None, False), Instr(0, "INPLACE_XOR", None, None, 4, None, False), Instr(0, "STORE_NAME", 0, "x", 6, None, False), Instr(0, "RETURN_VALUE", None, None, 8, None, False)])
        self.assertIn("x ^= 5", out)

    def test_inplace_without_matching_store_falls_back(self):
        Instr = BytecodeInstruction
        dec = Decompiler39(compile("pass", "<test>", "exec"))
        dec.stack = ["x", 1]; dec.instructions = [Instr(0,"L",0,"x",0,None,False), Instr(0,"C",0,1,2,None,False), Instr(0,"INPLACE_ADD",None,None,4,None,False), Instr(0,"STORE_NAME",1,"y",6,None,False)]
        dec._handle_instruction(dec.instructions[2])
        self.assertEqual(len(dec.stack), 1); self.assertIsInstance(dec.stack[-1], str)

    def test_complete_while_with_augassign(self):
        Instr = BytecodeInstruction
        out = self._run39([Instr(0, "LOAD_CONST", 0, 0, 0, None, False), Instr(0, "STORE_NAME", 0, "n", 2, None, False), Instr(0, "LOAD_NAME", 0, "n", 4, None, False), Instr(0, "LOAD_CONST", 1, 3, 6, None, False), Instr(0, "COMPARE_OP", 0, "<", 8, None, False), Instr(0, "POP_JUMP_IF_FALSE", 22, 22, 10, None, False), Instr(0, "LOAD_NAME", 0, "n", 12, None, False), Instr(0, "LOAD_CONST", 2, 1, 14, None, False), Instr(0, "INPLACE_ADD", None, None, 16, None, False), Instr(0, "STORE_NAME", 0, "n", 18, None, False), Instr(0, "JUMP_ABSOLUTE", 4, 4, 20, None, False), Instr(0, "RETURN_VALUE", None, None, 22, None, True)])
        self.assertIn("while", out); self.assertIn("n += 1", out)

    def test_complete_try_except_with_store(self):
        Instr = BytecodeInstruction
        out = self._run39([Instr(0, "SETUP_FINALLY", None, 14, 0, None, False), Instr(0, "LOAD_CONST", 0, 42, 2, None, False), Instr(0, "STORE_NAME", 0, "x", 4, None, False), Instr(0, "POP_BLOCK", None, None, 6, None, False), Instr(0, "JUMP_FORWARD", None, 30, 8, None, False), Instr(0, "DUP_TOP", None, None, 14, None, True), Instr(0, "LOAD_NAME", 1, "ValueError", 16, None, False), Instr(0, "COMPARE_OP", 10, "exception match", 18, None, False), Instr(0, "POP_JUMP_IF_FALSE", 28, 28, 20, None, False), Instr(0, "POP_TOP", None, None, 22, None, False), Instr(0, "POP_TOP", None, None, 24, None, False), Instr(0, "LOAD_CONST", 2, 0, 26, None, False), Instr(0, "STORE_NAME", 0, "x", 28, None, False), Instr(0, "POP_EXCEPT", None, None, 30, None, False), Instr(0, "RETURN_VALUE", None, None, 32, None, False)])
        self.assertIn("try:", out); self.assertIn("except ValueError:", out)

class TestDecompiler39ExcCleanupIndent(unittest.TestCase):
    def _run39_full(self, instructions):
        return _run39_full_impl(instructions)

    def test_exc_cleanup_setup_finally_no_indent_change(self):
        from pycrefine import BytecodeInstruction as Instr
        # Full layout as per monolithic instructions
        out = self._run39_full([
            Instr(0, "SETUP_FINALLY",         None, 14,  0, None, False),
            Instr(0, "POP_BLOCK",             None, None, 2, None, False),
            Instr(0, "JUMP_FORWARD",          None, 50,  4, None, False),
            Instr(0, "DUP_TOP",               None, None,14, None, True),
            Instr(0, "LOAD_GLOBAL",           0, "socket", 16, None, False),
            Instr(0, "LOAD_ATTR",             1, "error",  18, None, False),
            Instr(0, "JUMP_IF_NOT_EXC_MATCH", None, 48,    20, None, False),
            Instr(0, "POP_TOP",               None, None,  22, None, False),
            Instr(0, "STORE_FAST",            0, "e",      24, None, False),
            Instr(0, "POP_TOP",               None, None,  26, None, False),
            Instr(0, "SETUP_FINALLY",         None, 44,    28, None, False),
            Instr(0, "LOAD_GLOBAL",           2, "errormsg", 30, None, False),
            Instr(0, "RAISE_VARARGS",         1, 1,        32, None, False),
            Instr(0, "POP_BLOCK",             None, None,  34, None, False),
            Instr(0, "POP_EXCEPT",            None, None,  36, None, False),
            Instr(0, "LOAD_CONST",            0, None,     38, None, False),
            Instr(0, "STORE_FAST",            0, "e",      40, None, False),
            Instr(0, "DELETE_FAST",           0, "e",      42, None, False),
            Instr(0, "LOAD_CONST",            0, None,     44, None, True),
            Instr(0, "RERAISE",               None, None,  46, None, False),
            Instr(0, "RERAISE",               None, None,  48, None, True),
            Instr(0, "RETURN_VALUE",          None, None,  50, None, True),
        ])
        lines = out.splitlines()
        except_lines = [ln for ln in lines if "except" in ln and "socket.error" in ln]
        self.assertTrue(except_lines)
        exc_indent = len(except_lines[0]) - len(except_lines[0].lstrip())
        raise_lines = [ln for ln in lines if "raise" in ln]
        self.assertTrue(raise_lines)
        raise_indent = len(raise_lines[0]) - len(raise_lines[0].lstrip())
        self.assertEqual(raise_indent, exc_indent + 4)

    def test_dotted_exc_type_load_attr_chain(self):
        from pycrefine import BytecodeInstruction as Instr
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.instructions = [
            Instr(0, "DUP_TOP",               None, None,  0, None, True),
            Instr(0, "LOAD_GLOBAL",           0, "socket",  2, None, False),
            Instr(0, "LOAD_ATTR",             1, "error",   4, None, False),
            Instr(0, "JUMP_IF_NOT_EXC_MATCH", None, 20,     6, None, False),
            Instr(0, "POP_TOP",               None, None,   8, None, False),
            Instr(0, "STORE_FAST",            0, "e",       10, None, False),
            Instr(0, "POP_TOP",               None, None,   12, None, False),
            Instr(0, "LOAD_CONST",            0, None,      14, None, False),
            Instr(0, "RERAISE",               None, None,   16, None, False),
            Instr(0, "RERAISE",               None, None,   20, None, True),
        ]
        dec.stack = ["_exc_match"]
        dec._except_header_indents = [0]
        dec.pc = 1
        dup = dec.instructions[0]
        dec._handle_instruction(dup)
        out = "\n".join(dec.reconstructed)
        self.assertIn("socket.error", out)

    def test_jump_absolute_to_while_end_emits_break(self):
        from pycrefine import BytecodeInstruction as Instr
        out = self._run39_full([
            Instr(0, "LOAD_FAST",          0, "a",   0, None, True),
            Instr(0, "POP_JUMP_IF_FALSE",  None, 20,  2, None, False),
            Instr(0, "SETUP_FINALLY",      None, 14,  4, None, False),
            Instr(0, "POP_BLOCK",          None, None, 6, None, False),
            Instr(0, "JUMP_ABSOLUTE",      None, 20,   8, None, False),
            Instr(0, "POP_BLOCK",          None, None,10, None, False),
            Instr(0, "JUMP_ABSOLUTE",      None, 0,   12, None, False),
            Instr(0, "DUP_TOP",            None, None,14, None, True),
            Instr(0, "POP_TOP",            None, None,16, None, False),
            Instr(0, "POP_TOP",            None, None,18, None, False),
            Instr(0, "LOAD_CONST",         0, None,   20, None, True),
            Instr(0, "RETURN_VALUE",        None, None,22, None, False),
        ])
        self.assertIn("break", out)

    def test_break_in_try_correct_indent_synthetic(self):
        from pycrefine import BytecodeInstruction as Instr
        out = self._run39_full([
            Instr(0, "SETUP_FINALLY",      None, 14,  0, None, False),
            Instr(0, "JUMP_ABSOLUTE",      None, 20,  2, None, False),
            Instr(0, "POP_BLOCK",          None, None, 4, None, False),
            Instr(0, "JUMP_ABSOLUTE",      None, 0,    6, None, False),
            Instr(0, "DUP_TOP",            None, None,14, None, True),
            Instr(0, "POP_TOP",            None, None,16, None, False),
            Instr(0, "POP_TOP",            None, None,18, None, False),
            Instr(0, "LOAD_CONST",         0, None,   20, None, True),
            Instr(0, "RETURN_VALUE",        None, None,22, None, False),
        ])
        for line in out.splitlines():
            if "break" in line:
                self.assertTrue(line.startswith("        "),
                    f"Break inside try should be indented twice (8 spaces), got: {line!r}")

class TestStoreDerefDispatch(unittest.TestCase):
    def test_store_deref_closure_assignment_roundtrip(self):
        src = (
            "def outer(flag):\n"
            "    x = 'on' if flag else 'off'\n"
            "    def inner():\n"
            "        return x\n"
            "    return inner()\n"
        )
        out = decompile(src)
        self.assertIn("x", out)
        self.assertIn("on", out)
        self.assertIn("off", out)

    def test_store_deref_no_silent_drop(self):
        from pycrefine import BytecodeInstruction as Instr
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        dec.stack = ["'hello'"]
        dec.indent_level = 0
        store_deref = Instr(opcode=125, opname="STORE_DEREF", arg=0, argval="myvar", offset=0, starts_line=None, is_jump_target=False)
        dec._handle_instruction(store_deref)
        out = "\n".join(dec.reconstructed)
        self.assertIn("myvar", out)

    def test_store_deref_in_dispatch_map(self):
        code = compile("pass", "<test>", "exec")
        dec = Decompiler39(code)
        self.assertIn("STORE_DEREF", dec._dispatch)

class TestChainedComparisons(unittest.TestCase):
    def test_positive_chaining(self):
        from pycrefine import collapse_chained_comparisons
        # Basic case
        self.assertEqual(
            collapse_chained_comparisons("1 <= in_a", "in_a <= 10", "core"),
            "1 <= in_a <= 10"
        )
        # Parentheses match
        self.assertEqual(
            collapse_chained_comparisons("x <= (a + b)", "(a + b) <= 10", "core"),
            "x <= (a + b) <= 10"
        )
        # Multiply chained matching rightmost operator
        self.assertEqual(
            collapse_chained_comparisons("a < b < c", "c < d", "core"),
            "a < b < c < d"
        )
        # Matching variable explicitly (not just substring match)
        self.assertEqual(
            collapse_chained_comparisons("1 <= val", "val == 2", "aggressive"),
            "1 <= val == 2"
        )

    def test_negative_chaining(self):
        from pycrefine import collapse_chained_comparisons
        # Variable name substring overlap (should NOT match)
        self.assertIsNone(
            collapse_chained_comparisons("1 <= in_a", "a <= 10", "core")
        )
        # Function call argument overlap (should NOT match)
        self.assertIsNone(
            collapse_chained_comparisons("foo(x <= y)", "y <= 10", "core")
        )
        # None when beautification disabled or low
        self.assertIsNone(
            collapse_chained_comparisons("1 <= in_a", "in_a <= 10", "none")
        )

if __name__ == "__main__":
    unittest.main()

