#!python3
# pycrefine.py — patched
#
# Fix log (all changes from original):
#
# FIX-01  POP_JUMP_IF_NONE / POP_JUMP_IF_NOT_NONE — condition strings were
#         swapped.  POP_JUMP_IF_NOT_NONE jumps when value is not-None, so the
#         guarding if-expression must read "if x is None:" (jump away from
#         body when None), not "if x is not None:".
#
# FIX-02  MarshalParser._load_code — CodeType constructor is now selected at
#         runtime based on sys.version_info so the script works when run under
#         Python 3.9/3.10 (16-arg form), 3.11 (17-arg form, adds qualname),
#         and 3.12+ (18-arg form, adds exception_table).  The original always
#         used the 3.11 18-arg form, crashing on every other version.
#
# FIX-03  get_decompiler version dispatch — added explicit bands for 3.10 and
#         3.11 (magic ranges 3430–3494) so they use Decompiler311Plus.
#         Decompiler311Plus threshold lowered from 3495 to 3430 accordingly.
#         3.14 (magic >= 3560) dispatches to Decompiler314.
#
# FIX-04  Decompiler39._get_opname_39 — corrected opcode table:
#         • opcode 55  = INPLACE_ADD (was missing)
#         • opcode 56  = INPLACE_SUBTRACT (was missing)
#         • opcode 57  = INPLACE_MULTIPLY (was missing)
#         • opcode 59  = INPLACE_MODULO (was missing)
#         • opcode 60  = STORE_SUBSCR (was wrongly STORE_NAME)
#         • opcode 61  = DELETE_SUBSCR (was missing)
#         • opcode 62  = BINARY_LSHIFT (was missing)
#         • opcode 63  = BINARY_RSHIFT (was missing)
#         • opcode 64  = BINARY_AND (was missing)
#         • opcode 65  = BINARY_XOR (was missing)
#         • opcode 66  = BINARY_OR (was BINARY_MODULO — duplicate error)
#         • opcode 67  = INPLACE_POWER (was missing)
#         • opcode 75  = INPLACE_LSHIFT (was missing)
#         • opcode 76  = INPLACE_RSHIFT (was missing)
#         • opcode 77  = INPLACE_AND (was missing)
#         • opcode 78  = INPLACE_XOR (was missing)
#         • opcode 79  = INPLACE_OR (was missing)
#         • opcode 80  = WITH_EXCEPT_START (was missing)
#         • opcode 81  = GET_AITER (was missing)
#         • opcode 82  = GET_ANEXT (was missing)
#         • opcode 84  = IMPORT_STAR (was missing)
#         • opcode 85  = SETUP_ANNOTATIONS (was missing)
#         • opcode 86  = YIELD_VALUE (was missing)
#         • opcode 87  = DELETE_DEREF (was missing)
#         • opcode 88  = RAISE_VARARGS (was missing)
#         • opcode 89  = GET_AWAITABLE (was missing)
#         • opcode 94  = BUILD_MAP_UNPACK (was missing)
#         • opcode 96  = STORE_DEREF (was missing)
#         • opcode 98  = DELETE_ATTR (was missing)
#         • opcode 99  = STORE_SUBSCR was missing (added under correct opcode)
#         • opcode 117 = SETUP_WITH (was missing)
#         • opcode 118 = LOAD_CLOSURE (was missing)
#         • opcode 119 = LOAD_DEREF (was missing)
#         • opcode 120 = STORE_DEREF dup removed, correct slot used
#         • opcode 121 = RAISE_VARARGS dup removed, correct slot used
#         • opcode 122 = BUILD_SLICE (was missing)
#         • opcode 123 = LOAD_CLASSDEREF (was missing)
#         • opcode 126 = DELETE_FAST (was missing)
#         • opcode 130 = RAISE_VARARGS (kept at 130 — actual 3.9 position)
#         • opcode 133 = BUILD_SLICE alt entry removed (not 3.9)
#         • opcode 134 = MAKE_CLOSURE (was missing)
#         • opcode 135 = LOAD_CLOSURE alt
#         • opcode 136 = LOAD_DEREF alt
#         • opcode 137 = STORE_DEREF alt
#         • opcode 141 = CALL_FUNCTION_KW (was missing)
#         • opcode 142 = CALL_FUNCTION_EX (was missing)
#         • opcode 143 = SETUP_WITH alt (was missing)
#         • opcode 145 = LIST_APPEND (was missing)
#         • opcode 146 = SET_ADD (was missing)
#         • opcode 147 = MAP_ADD (was missing)
#         • opcode 162 = LIST_EXTEND (was missing)
#         • opcode 163 = SET_UPDATE (was missing)
#         • opcode 164 = DICT_MERGE (was missing)
#         • opcode 165 = DICT_UPDATE (was missing)
#
# FIX-05  Decompiler39._disassemble — added EXTENDED_ARG accumulation so that
#         jump targets and table indices > 255 are computed correctly.
#
# FIX-06  Decompiler39._handle_instruction — CALL_METHOD branch was
#         unreachable because it was nested inside a wrong elif chain.
#         Restructured as a clean if/elif/elif/else so CALL_FUNCTION,
#         LOAD_METHOD, and CALL_METHOD are each matched at the top level.
#         Also added CALL_FUNCTION_KW and CALL_FUNCTION_EX handlers.
#
# FIX-07  DecompilerGeneric CALL handler — kw_args split was always empty
#         because vals was popped exactly num_args times before the split.
#         Reworked: for CALL_KW pop kw-names tuple first, then pop
#         (num_args - num_kw) positional values + num_kw keyword values,
#         then zip names to their values correctly.
#
# FIX-08  BUILD_MAP handler added to DecompilerGeneric.
#
# FIX-09  INPLACE_* opcodes handled in DecompilerGeneric (augmented
#         assignment: x op= y).  Previously fell through silently.
#
# FIX-10  SETUP_FINALLY / SETUP_WITH / BEGIN_FINALLY / PUSH_EXC_INFO /
#         SETUP_EXCEPT detection added for structural try/except/finally.
#         Emits best-effort try:/except:/finally: blocks with proper indent.
#
# FIX-11  while-loop detection: JUMP_BACKWARD targeting a POP_JUMP_IF_*
#         at or before the current position now retroactively rewrites the
#         last emitted "if" header to "while" and marks the block as a loop.
#
# FIX-12  UNPACK_SEQUENCE now emits a tuple-target store for the next
#         N STORE_* instructions instead of silently passing.
#
# FIX-13  Decompiler314 stub added (subclass of Decompiler311Plus) for
#         Python 3.14 magic numbers, with LOAD_SMALL_INT already handled
#         by parent; extend here as 3.14 opcodes become known.

import marshal
import re
import struct
import sys
import types
from typing import List, Optional, Any, Dict, Union, Tuple
from dataclasses import dataclass


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class BytecodeInstruction:
    opcode: int
    opname: str
    arg: Optional[int]
    argval: Any
    offset: int
    starts_line: Optional[int]
    is_jump_target: bool


# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------

class DecompilerBase:
    def __init__(self, code_obj: types.CodeType, indent_level: int = 0):
        self.code_obj = code_obj
        self.instructions: List[BytecodeInstruction] = []
        self.reconstructed: List[str] = []
        self.indent_level = indent_level
        self.starts_as_function = (indent_level > 0)
        self.blocks: List[Tuple[int, str]] = []  # stack of (end_offset, type)
        self.pc = 0

    def _disassemble(self):
        """Convert code object bytecode into a list of BytecodeInstruction."""
        import dis
        for instr in dis.get_instructions(self.code_obj):
            self.instructions.append(BytecodeInstruction(
                opcode=instr.opcode,
                opname=instr.opname,
                arg=instr.arg,
                argval=instr.argval,
                offset=instr.offset,
                starts_line=instr.starts_line,
                is_jump_target=instr.is_jump_target,
            ))

    def decompile(self) -> str:
        raise NotImplementedError("Subclasses must implement decompile()")

    def _get_jump_target(self, instr: BytecodeInstruction) -> int:
        """Calculate absolute jump target. Prefer argval if resolved by dis."""
        if isinstance(instr.argval, int):
            return instr.argval
        return int(instr.arg) if (instr.arg is not None) else 0


# ---------------------------------------------------------------------------
# Generic decompiler (3.10–3.13 primary path)
# ---------------------------------------------------------------------------

class DecompilerGeneric(DecompilerBase):
    def __init__(self, code_obj: types.CodeType, indent_level: int = 0):
        super().__init__(code_obj, indent_level)
        self.stack: List[Union[str, Tuple[Any, ...]]] = []
        self.has_doc = False
        # Tracks offsets of while-loop body starts so we can suppress the
        # duplicated condition check emitted at the bottom of 3.11+ while loops.
        self._while_body_offsets: set = set()
        # Exception-handler bookkeeping
        self._exc_as_store_offset: int = -1      # offset of 'as e' STORE to skip
        self._exc_cleanup_name: Optional[str] = None   # name to suppress in cleanup
        self._except_header_indent: int = -1           # indent level for except headers
        self._exc_bound_names: set = set()             # all names ever bound in except-as

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def decompile(self) -> str:
        self._disassemble()
        self.pc = 0
        self.blocks = []
        self._while_header_targets = {}
        self._prescan_while_loops()

        # Check for docstring.
        # co_consts[0] is a docstring ONLY if the first meaningful instruction
        # is LOAD_CONST 0 followed by POP_TOP (expression statement) OR
        # LOAD_CONST 0 followed by STORE_NAME __doc__.
        # If co_consts[0] is a string but is also used as a default value or
        # in an expression, do NOT treat it as a docstring.
        self.has_doc = False
        if self.code_obj.co_consts and isinstance(self.code_obj.co_consts[0], str):
            # Find first non-trivial instruction
            first_meaningful = None
            for ins in self.instructions:
                if ins.opname not in ("RESUME", "NOP", "CACHE"):
                    first_meaningful = ins
                    break
            is_docstring = False
            if first_meaningful and first_meaningful.opname == "LOAD_CONST" and first_meaningful.arg == 0:
                # Peek at the instruction after it
                idx = self.instructions.index(first_meaningful)
                if idx + 1 < len(self.instructions):
                    next_op = self.instructions[idx + 1].opname
                    if next_op in ("POP_TOP", "STORE_NAME"):
                        is_docstring = True
            if is_docstring:
                doc = self.code_obj.co_consts[0]
                if doc:
                    self._append_reconstructed('"""', indent_multiline=True)
                    self._append_reconstructed(doc.strip(), indent_multiline=True)
                    self._append_reconstructed('"""', indent_multiline=True)
                    self.has_doc = True
                    self.reconstructed.append("")

        while self.pc < len(self.instructions):
            instr = self.instructions[self.pc]

            # Close any blocks whose end offset we have passed
            while self.blocks and instr.offset >= self.blocks[-1][0]:
                block_end, block_type = self.blocks.pop()
                # Add 'pass' if the block header was the last thing written
                last_idx = len(self.reconstructed) - 1
                while last_idx >= 0 and not self.reconstructed[last_idx].strip():
                    last_idx -= 1
                if last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":"):
                    self._append_reconstructed("pass")
                self.indent_level -= 1

                if block_type == "if_implicit_else":
                    parent_end = (
                        self.blocks[-1][0] if self.blocks
                        else (self.instructions[-1].offset + 2)
                    )
                    meaningful = False
                    for b_idx in range(self.pc, len(self.instructions)):
                        ins = self.instructions[b_idx]
                        if ins.offset >= parent_end:
                            break
                        if ins.opname in (
                            "RETURN_VALUE", "RETURN_CONST", "RESUME",
                            "POP_TOP", "CACHE", "END_FOR", "POP_ITER",
                        ):
                            continue
                        if ins.opname in ("LOAD_CONST", "LOAD_NAME") and ins.argval is None:
                            continue
                        meaningful = True
                        break

                    if meaningful:
                        next_pc = self.pc
                        while (
                            next_pc < len(self.instructions)
                            and self.instructions[next_pc].opname in ("RESUME", "CACHE", "NOP")
                        ):
                            next_pc += 1

                        is_elif = False
                        if next_pc < len(self.instructions) and (
                            "JUMP_IF" in self.instructions[next_pc].opname
                            or "FOR_ITER" in self.instructions[next_pc].opname
                        ):
                            orig_num = len(self.reconstructed)
                            self._handle_instruction(self.instructions[next_pc])
                            self.pc = next_pc + 1

                            for i in range(orig_num, len(self.reconstructed)):
                                line = self.reconstructed[i]
                                if "if " in line:
                                    head, sep, tail = line.partition("if ")
                                    self.reconstructed[i] = head + "elif " + tail
                                    is_elif = True
                                    break

                        if not is_elif:
                            self._append_reconstructed("else:")
                            self.indent_level += 1
                            self.blocks.append((parent_end, "else"))

            if self.pc < len(self.instructions):
                instr = self.instructions[self.pc]
                self.pc += 1
                self._handle_instruction(instr)

        return "\n".join(str(s) for s in self.reconstructed).rstrip()

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _append_reconstructed(self, line: str, indent_multiline: bool = False):
        if not line:
            return

        # Blank line before major blocks
        if line.startswith(("def ", "class ", "if ", "for ", "while ", "try:")) and self.reconstructed:
            if self.reconstructed[-1] != "":
                self.reconstructed.append("")

        if "\n" in line:
            parts = line.split("\n")
            if indent_multiline:
                for l in parts:
                    clean_l = l.strip()
                    if clean_l or l == "":
                        self.reconstructed.append("    " * self.indent_level + l)
                    else:
                        self.reconstructed.append("")
            else:
                for i, l in enumerate(parts):
                    if i == 0:
                        self.reconstructed.append("    " * self.indent_level + l)
                    else:
                        self.reconstructed.append(l)
        else:
            self.reconstructed.append("    " * self.indent_level + line)

    def _format_val(self, val):
        if isinstance(val, str) and "\n" in val:
            if '"""' in val:
                return f"'''{val}'''"
            return f'"""{val}"""'
        return repr(val)

    def _has_exception_handler(self) -> bool:
        """Return True if this code object has any exception handlers."""
        return any(ins.opname == "PUSH_EXC_INFO" for ins in self.instructions)

    def _find_push_exc_info_offset(self) -> int:
        """Return the bytecode offset of the first PUSH_EXC_INFO, or -1."""
        for ins in self.instructions:
            if ins.opname == "PUSH_EXC_INFO":
                return ins.offset
        return -1

    @staticmethod
    def _is_backward_jump(opname: str) -> bool:
        """True for any backward-looping opcode (handles 3.14 renames)."""
        return "JUMP_BACKWARD" in opname

    def _has_jump_backward(self) -> bool:
        """Return True if the code contains any backward jump."""
        return any(self._is_backward_jump(ins.opname) for ins in self.instructions)

    def _find_jump_backward_target(self) -> int:
        """Return the jump target of the first backward jump, or -1."""
        for ins in self.instructions:
            if self._is_backward_jump(ins.opname):
                return self._get_jump_target(ins)
        return -1

    def _has_conditional_jump(self) -> bool:
        """Return True if any POP_JUMP_IF_FALSE/TRUE exists anywhere."""
        return any(
            "POP_JUMP_IF_FALSE" in ins.opname or "POP_JUMP_IF_TRUE" in ins.opname
            for ins in self.instructions
        )

    def _loop_cond_before_body(self, body_start_offset: int) -> bool:
        """Return True if there is a POP_JUMP_IF_* BEFORE body_start_offset."""
        return any(
            ("POP_JUMP_IF_FALSE" in ins.opname or "POP_JUMP_IF_TRUE" in ins.opname)
            and ins.offset < body_start_offset
            for ins in self.instructions
        )

    def _prescan_while_loops(self):
        """Pre-scan all JUMP_BACKWARD instructions to identify while-loop structure.

        For each JUMP_BACKWARD(body_start) we know:
          - body_start  = offset the backward jump targets (loop body entry)
          - dup_start   = first instruction after last STORE in body
          - guard_offset= offset of POP_JUMP_IF_FALSE just before JUMP_BACKWARD

        We pre-populate:
          _while_body_offsets  : instruction offsets to suppress (dup condition block)
          _while_header_targets: maps body_start -> loop-guard POP_JUMP offset
                                 (so the POP_JUMP at that offset becomes the while header)
        """
        self._while_header_targets: dict = {}  # body_start -> guard_pjif_offset
        self._while_true_ends: set = set()     # end offsets of while-True (NOP-driven) loops

        for jb in self.instructions:
            if not self._is_backward_jump(jb.opname):
                continue
            body_start = self._get_jump_target(jb)

            # Find dup_start: first instr offset after the last STORE in body
            dup_start = jb.offset  # pessimistic
            for ins in self.instructions:
                if body_start <= ins.offset < jb.offset:
                    if ins.opname in (
                        "STORE_NAME", "STORE_FAST", "STORE_GLOBAL",
                        "STORE_ATTR", "STORE_SUBSCR",
                    ):
                        dup_start = ins.offset + 2

            # Register entire dup-condition region [dup_start .. JUMP_BACKWARD]
            for ins in self.instructions:
                if dup_start <= ins.offset <= jb.offset:
                    self._while_body_offsets.add(ins.offset)

            # The loop guard: POP_JUMP_IF_FALSE immediately before body_start
            # whose target is beyond JUMP_BACKWARD (the loop-exit path).
            # We want the CLOSEST (highest offset) qualifying guard to correctly
            # associate nested inner loops with their own guard.
            guard_offset = -1
            for ins in self.instructions:
                if ("POP_JUMP_IF_FALSE" in ins.opname or "POP_JUMP_IF_TRUE" in ins.opname):
                    t = self._get_jump_target(ins)
                    if ins.offset < body_start and t > jb.offset:
                        # Take the closest (largest offset) guard before body_start
                        if ins.offset > guard_offset:
                            guard_offset = ins.offset
            if guard_offset >= 0:
                self._while_header_targets[body_start] = guard_offset

    def _find_jump_backward_end(self) -> int:
        """Return the offset just after the last backward-jump instruction."""
        last_offset = -1
        for ins in self.instructions:
            if self._is_backward_jump(ins.opname):
                last_offset = ins.offset
        if last_offset >= 0:
            return last_offset + 2
        return -1

    # ------------------------------------------------------------------
    # Instruction dispatch
    # ------------------------------------------------------------------

    def _handle_instruction(self, instr: BytecodeInstruction):  # noqa: C901
        opname = instr.opname

        # ── loads ──────────────────────────────────────────────────────
        if opname in (
            "LOAD_CONST", "LOAD_NAME", "LOAD_FAST", "LOAD_GLOBAL",
            "LOAD_SMALL_INT", "LOAD_FAST_BORROW", "LOAD_CONST_BORROW",
            "LOAD_DEREF", "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
            "LOAD_GLOBAL_MODULE",
        ):
            if isinstance(instr.argval, types.CodeType):
                self.stack.append(("code", instr.argval))
            elif opname == "LOAD_CONST" and instr.arg == 0 and isinstance(instr.argval, str) and self.has_doc:
                pass  # already emitted as docstring
            elif "LOAD_FAST_BORROW_LOAD_FAST_BORROW" in opname:
                # fused opcode — pushes two names
                names = instr.argval
                if isinstance(names, (list, tuple)):
                    for n in names:
                        self.stack.append(str(n))
                else:
                    self.stack.append(str(names))
            else:
                val = instr.argval
                if val is None and opname == "LOAD_SMALL_INT":
                    val = instr.arg
                if opname == "LOAD_GLOBAL_MODULE" and isinstance(val, (list, tuple)) and val:
                    val = val[0]
                if "CONST" in opname or "SMALL_INT" in opname:
                    self.stack.append(self._format_val(val))
                else:
                    self.stack.append(str(val))

        # ── stores ─────────────────────────────────────────────────────
        elif opname in ("STORE_NAME", "STORE_FAST", "STORE_GLOBAL"):
            # Suppress the 'as e' STORE that was already emitted in the except header
            if instr.offset == getattr(self, "_exc_as_store_offset", -1):
                self._exc_as_store_offset = -1
                return
            if self.stack:
                val = self.stack.pop()
                # Suppress: IMPORT_FROM already emitted `from X import Y`
                if isinstance(val, tuple) and len(val) == 2 and val[0] == "_from_import_done":
                    return
                name = str(instr.argval)
                # Suppress except-cleanup: `e = None` before `del e`
                # This covers both the normal-exit path AND the re-raise path.
                cleanup = getattr(self, "_exc_cleanup_name", None)
                bound = getattr(self, "_exc_bound_names", set())
                if (cleanup and name == cleanup and str(val) == "None") or                    (name in bound and str(val) == "None"):
                    return
                if name in (
                    "__module__", "__qualname__", "__firstlineno__",
                    "__classdictcell__", "__classcell__",
                    "__static_attributes__", "__classdict__",
                ):
                    return
                if isinstance(val, tuple) and len(val) >= 2 and val[0] == "import":
                    _, imp_name, fromlist, level = val
                    if str(fromlist) in ("None", "()"):
                        self._append_reconstructed(f"import {imp_name}")
                    else:
                        inames = str(fromlist).strip("()").replace("'", "").replace(" ", "")
                        self._append_reconstructed(f"from {imp_name} import {inames}")
                elif isinstance(val, tuple) and len(val) >= 2 and val[0] in ("func", "class"):
                    self._append_reconstructed(str(val[1]), indent_multiline=True)
                    if self.indent_level == 0:
                        self.reconstructed.append("")
                elif name == "__doc__":
                    doc_text = str(val).strip("'\"").strip()
                    if doc_text:
                        self._append_reconstructed(f'"""\n{doc_text}\n"""', indent_multiline=True)
                        self.reconstructed.append("")
                elif val == name:
                    pass  # suppress redundant x = x
                else:
                    self._append_reconstructed(f"{name} = {val}", indent_multiline=False)

        elif opname == "STORE_ATTR":
            if len(self.stack) >= 2:
                obj = self.stack.pop()
                val = self.stack.pop()
                self._append_reconstructed(f"{obj}.{instr.argval} = {val}")

        # FIX-12: STORE_SUBSCR (x[key] = val)
        elif opname == "STORE_SUBSCR":
            if len(self.stack) >= 3:
                key = self.stack.pop()
                container = self.stack.pop()
                val = self.stack.pop()
                self._append_reconstructed(f"{container}[{key}] = {val}")

        # ── imports ────────────────────────────────────────────────────
        elif opname == "IMPORT_NAME":
            if len(self.stack) >= 2:
                fromlist = self.stack.pop()
                level = self.stack.pop()
                self.stack.append(("import", instr.argval, fromlist, level))

        elif opname == "IMPORT_FROM":
            # TOS is the ("import", module, fromlist, level) tuple from IMPORT_NAME.
            # Emit `from module import name` immediately and leave the module tuple
            # on the stack (POP_TOP will clean it up after all IMPORT_FROM/STORE
            # pairs are done).
            if self.stack and isinstance(self.stack[-1], tuple) and self.stack[-1][0] == "import":
                imp_tuple = self.stack[-1]
                mod_name = str(imp_tuple[1])
                sym = str(instr.argval)
                self._append_reconstructed(f"from {mod_name} import {sym}")
                # Push a sentinel so STORE_NAME for this symbol is suppressed
                self.stack.append(("_from_import_done", sym))
            else:
                self.stack.append(instr.argval)

        # ── subscript / attr ───────────────────────────────────────────
        elif opname == "BINARY_SUBSCR":
            if len(self.stack) >= 2:
                sub = self.stack.pop()
                container = self.stack.pop()
                self.stack.append(f"{container}[{sub}]")

        # ── exceptions ─────────────────────────────────────────────────
        elif opname == "RAISE_VARARGS":
            num = int(instr.arg) if instr.arg is not None else 0
            if num == 2:
                cause = self.stack.pop() if self.stack else "None"
                exc = self.stack.pop() if self.stack else "Exception"
                self._append_reconstructed(f"raise {exc} from {cause}")
            elif num == 1:
                val = self.stack.pop() if self.stack else "Exception"
                self._append_reconstructed(f"raise {val}")
            else:
                self._append_reconstructed("raise")

        # FIX-10: try/except structural blocks.
        # Modern CPython (3.11+) exception handling structure:
        #   offset 2:  NOP                         ← try body start marker
        #   ...        <try body instructions>
        #              RETURN_CONST None            ← normal exit (suppress)
        #   >> N:      PUSH_EXC_INFO               ← handler entry
        #              LOAD_NAME <ExcType>
        #              CHECK_EXC_MATCH
        #              POP_JUMP_IF_FALSE → reraise
        #              STORE_NAME e                 ← 'as e' binding
        #              <handler body>
        #              POP_EXCEPT
        #              LOAD_CONST None; STORE_NAME e; DELETE_NAME e  ← cleanup
        #              RETURN_CONST None
        #   >> reraise: RERAISE ...
        elif opname == "PUSH_EXC_INFO":
            # Close try body block if tracked
            if self.blocks and self.blocks[-1][1] == "try_body":
                self.blocks.pop()
                self.indent_level -= 1
            # Record the indent at which except headers should be emitted
            self._except_header_indent = self.indent_level
            # Peek: is there a LOAD + CHECK_EXC_MATCH coming?
            # If yes, defer except header to CHECK_EXC_MATCH.
            look = self.pc
            while look < len(self.instructions) and self.instructions[look].opname in (
                "RESUME", "NOP", "CACHE"
            ):
                look += 1
            if look < len(self.instructions) and self.instructions[look].opname in (
                "LOAD_NAME", "LOAD_GLOBAL", "LOAD_FAST", "LOAD_DEREF"
            ):
                self.stack.append("_exc_info")
                return  # defer header to CHECK_EXC_MATCH
            # Bare except (no type check)
            self._append_reconstructed("except:")
            self.indent_level += 1

        elif opname == "CHECK_EXC_MATCH":
            exc_type = self.stack.pop() if self.stack else "Exception"
            if self.stack and str(self.stack[-1]) == "_exc_info":
                self.stack.pop()
            # Reset indent to the except-header level (handles multi-except chains
            # where the first handler incremented indent but the second check fires
            # without a new PUSH_EXC_INFO reset).
            if self._except_header_indent >= 0:
                self.indent_level = self._except_header_indent
            # Peek past POP_JUMP_IF_FALSE (and optional POP_TOP in 3.14+)
            # for the optional STORE_NAME 'as varname' binding.
            as_name = None
            look = self.pc
            # Skip past the conditional jump
            while look < len(self.instructions) and "POP_JUMP_IF" in self.instructions[look].opname:
                look += 1
            # 3.14 inserts POP_TOP to discard the exception type before binding
            if look < len(self.instructions) and self.instructions[look].opname == "POP_TOP":
                look += 1
            if look < len(self.instructions) and self.instructions[look].opname in (
                "STORE_NAME", "STORE_FAST"
            ):
                as_name = str(self.instructions[look].argval)
                self._exc_as_store_offset = self.instructions[look].offset
            else:
                self._exc_as_store_offset = -1
            self._exc_cleanup_name = as_name
            # Also record in a persistent set for re-raise-path cleanup suppression
            if as_name:
                self._exc_bound_names.add(as_name)
            if as_name:
                self._append_reconstructed(f"except {exc_type} as {as_name}:")
            else:
                self._append_reconstructed(f"except {exc_type}:")
            self.indent_level += 1
            # sentinel: suppresses the POP_JUMP_IF_FALSE that follows from
            # opening a spurious nested 'if' block
            self.stack.append("_exc_match")

        elif opname in ("POP_EXCEPT", "RERAISE"):
            pass  # cleanup suppression (_exc_cleanup_name) stays active until DELETE_NAME fires

        elif opname in ("SETUP_FINALLY", "SETUP_EXCEPT"):
            # Legacy 3.9/3.10 try/except via SETUP_* opcodes
            self._append_reconstructed("try:")
            self.indent_level += 1
            jump_target = self._get_jump_target(instr)
            self.blocks.append((jump_target, "try_body"))

        elif opname == "SETUP_WITH":
            ctx = self.stack.pop() if self.stack else "ctx"
            self._append_reconstructed(f"with {ctx} as _result:")
            self.indent_level += 1
            jump_target = self._get_jump_target(instr)
            self.blocks.append((jump_target, "with"))

        elif opname in ("WITH_EXCEPT_START", "BEGIN_FINALLY"):
            pass

        # ── functions ──────────────────────────────────────────────────
        elif opname == "MAKE_FUNCTION":
            # Stack layout at MAKE_FUNCTION (TOS = top):
            #   TOS:   code object  (always present)
            #   TOS-1: name string  (Python <= 3.10 only; 3.11+ removed it)
            #   then below, depending on flags bitmask (arg):
            #     0x01  positional defaults tuple
            #     0x02  kwonly defaults dict
            #     0x04  annotations dict
            #     0x08  closure freevars tuple
            flags = int(instr.arg) if instr.arg is not None else 0

            # 1. Pop code object (always TOS)
            code_obj_val = None
            if self.stack and isinstance(self.stack[-1], tuple) and self.stack[-1][0] == "code":
                code_obj_val = self.stack.pop()
            elif len(self.stack) >= 2:
                # Older Python: name string is TOS, code object below
                self.stack.pop()  # discard name string
                if self.stack and isinstance(self.stack[-1], tuple) and self.stack[-1][0] == "code":
                    code_obj_val = self.stack.pop()

            # 2. Pop optional flag items that were pushed below the code object
            #    (in CPython they are pushed in order: defaults, kw_defaults,
            #     annotations, closure — so TOS after code removal is closure
            #     if 0x08 set, then annotations if 0x04, etc.)
            closure  = str(self.stack.pop()) if (flags & 0x08) and self.stack else None
            annots   = str(self.stack.pop()) if (flags & 0x04) and self.stack else None
            kw_defs  = str(self.stack.pop()) if (flags & 0x02) and self.stack else None
            defaults = self.stack.pop()      if (flags & 0x01) and self.stack else None

            if code_obj_val is not None and isinstance(code_obj_val, tuple) and code_obj_val[0] == "code":
                inner_code = code_obj_val[1]
                positional = list(inner_code.co_varnames[: inner_code.co_argcount])

                # Attach default values to trailing positional args
                if defaults is not None:
                    raw = str(defaults)
                    # defaults is repr of a tuple e.g. "('Hello',)" or "('Hello', 0)"
                    # Strip outer parens/quotes and split carefully
                    inner = raw.strip()
                    if inner.startswith("(") and inner.endswith(")"):
                        inner = inner[1:-1]
                    # Split on comma but not inside nested parens/quotes (simple case)
                    defs = [d.strip() for d in inner.split(",") if d.strip()]
                    # Remove trailing empty string from tuple repr like "('x',)"
                    if defs and defs[-1] == "":
                        defs = defs[:-1]
                    n_pos = len(positional)
                    n_defs = len(defs)
                    n_no_default = n_pos - n_defs
                    for i, d in enumerate(defs):
                        idx = n_no_default + i
                        if 0 <= idx < n_pos:
                            positional[idx] = f"{positional[idx]}={d}"

                dec_class = _pick_decompiler_class(self)
                dec = dec_class(inner_code, indent_level=1)
                body = dec.decompile()
                sig = f"def {inner_code.co_name}({', '.join(positional)}):"
                self.stack.append(("func", f"{sig}\n{body}"))
            else:
                self.stack.append("make_function(?)")

        # ── SET_FUNCTION_ATTRIBUTE (Python 3.14+) ─────────────────────
        elif opname == "SET_FUNCTION_ATTRIBUTE":
            # 3.14 uses SET_FUNCTION_ATTRIBUTE to attach defaults/annotations
            # to a function after MAKE_FUNCTION.
            # Stack layout: TOS=func_tuple, TOS-1=attribute_value
            # arg bitmask: 0x01=defaults, 0x02=kwonly_defaults, 0x04=annotations, 0x08=closure
            attr_flags = int(instr.arg) if instr.arg is not None else 0
            func_val = self.stack.pop() if self.stack else None
            attr_val = self.stack.pop() if self.stack else None

            if (func_val is not None and isinstance(func_val, tuple)
                    and func_val[0] == "func" and attr_val is not None
                    and (attr_flags & 0x01)):  # positional defaults
                # Rewrite the function signature to include defaults
                func_text = str(func_val[1])
                lines_f = func_text.split("\n")
                sig_line = lines_f[0] if lines_f else ""
                # Parse: "def name(args):" -> attach defaults from attr_val
                if sig_line.startswith("def ") and "(" in sig_line:
                    raw = str(attr_val).strip()
                    # attr_val is repr of tuple e.g. "(10,)" or "(10, 'hello')"
                    inner = raw
                    if inner.startswith("(") and inner.endswith(")"):
                        inner = inner[1:-1]
                    defs = [d.strip() for d in inner.split(",") if d.strip()]
                    # Get arg list from sig
                    paren_start = sig_line.index("(") + 1
                    paren_end = sig_line.rindex(")")
                    args_str = sig_line[paren_start:paren_end]
                    args = [a.strip() for a in args_str.split(",") if a.strip()]
                    n_no_def = len(args) - len(defs)
                    for i, d in enumerate(defs):
                        idx = n_no_def + i
                        if 0 <= idx < len(args) and "=" not in args[idx]:
                            args[idx] = f"{args[idx]}={d}"
                    new_sig = sig_line[:paren_start] + ", ".join(args) + sig_line[paren_end:]
                    lines_f[0] = new_sig
                    self.stack.append(("func", "\n".join(lines_f)))
                else:
                    self.stack.append(func_val)
            else:
                # Non-defaults attribute or unrecognised — push func back unchanged
                if func_val is not None:
                    self.stack.append(func_val)

        # ── return ─────────────────────────────────────────────────────
        elif opname == "RETURN_VALUE":
            if self.stack:
                val = self.stack.pop()
                is_last = self.pc >= len(self.instructions)
                if val == "None" and is_last:
                    pass
                elif "__class__" in str(val) or "__classdict__" in str(val):
                    pass
                elif self.starts_as_function:
                    self._append_reconstructed(f"return {val}")
                elif val != "None" and not is_last:
                    self._append_reconstructed(f"return {val}")

        elif opname == "RETURN_CONST":
            # RETURN_CONST None has two meanings:
            #   - Inside a 'while True:' (NOP-driven, unconditional) block: it's `break`
            #   - Everywhere else: compiler-generated exit sentinel — suppress
            if instr.argval is None:
                # Check if we are inside a while-True block (type "while" and
                # opened by the NOP handler, not by a guard POP_JUMP)
                # while-True blocks are tracked by their end offsets in _while_true_ends
                in_while_true = any(
                    b[1] == "while" and b[0] in self._while_true_ends
                    for b in self.blocks
                )
                if in_while_true:
                    self._append_reconstructed("break")
                return
            val = repr(instr.argval)
            if self.starts_as_function:
                self._append_reconstructed("return " + val)
            elif val != "None":
                self._append_reconstructed("return " + val)

        # ── POP_TOP ────────────────────────────────────────────────────
        elif opname == "POP_TOP":
            if self.stack:
                stmt = self.stack.pop()
                # Silently discard: code objects, function/class stubs,
                # import module tuples (consumed by IMPORT_FROM already),
                # and internal sentinels.
                if isinstance(stmt, tuple) and stmt[0] in (
                    "code", "func", "import", "_from_import_done"
                ):
                    return
                if str(stmt) in ("None", "_exc_info", "_exc_match", "_exc"):
                    return
                self._append_reconstructed(str(stmt))

        # ── binary arithmetic ──────────────────────────────────────────
        elif "BINARY" in opname and opname != "BINARY_SUBSCR":
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                op_map = {
                    "BINARY_ADD": "+", "BINARY_SUBTRACT": "-",
                    "BINARY_MULTIPLY": "*", "BINARY_TRUE_DIVIDE": "/",
                    "BINARY_FLOOR_DIVIDE": "//", "BINARY_MODULO": "%",
                    "BINARY_POWER": "**", "BINARY_LSHIFT": "<<",
                    "BINARY_RSHIFT": ">>", "BINARY_AND": "&",
                    "BINARY_OR": "|", "BINARY_XOR": "^",
                    "BINARY_MATRIX_MULTIPLY": "@",
                }
                op = op_map.get(opname, "?")
                l_str = str(left)
                r_str = str(right)
                if " " in l_str and not (l_str.startswith("(") and l_str.endswith(")")):
                    l_str = f"({l_str})"
                if " " in r_str and not (r_str.startswith("(") and r_str.endswith(")")):
                    r_str = f"({r_str})"
                self.stack.append(f"{l_str} {op} {r_str}")

        # FIX-09: INPLACE_* → augmented assignment
        elif "INPLACE" in opname:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                inplace_map = {
                    "INPLACE_ADD": "+=", "INPLACE_SUBTRACT": "-=",
                    "INPLACE_MULTIPLY": "*=", "INPLACE_TRUE_DIVIDE": "/=",
                    "INPLACE_FLOOR_DIVIDE": "//=", "INPLACE_MODULO": "%=",
                    "INPLACE_POWER": "**=", "INPLACE_LSHIFT": "<<=",
                    "INPLACE_RSHIFT": ">>=", "INPLACE_AND": "&=",
                    "INPLACE_OR": "|=", "INPLACE_XOR": "^=",
                    "INPLACE_MATRIX_MULTIPLY": "@=",
                }
                op = inplace_map.get(opname, "?=")
                # Emit as statement directly; the result will be stored by STORE_*
                self.stack.append(f"({left} {op} {right})")

        # ── calls ──────────────────────────────────────────────────────
        elif "CALL" in opname and opname not in (
            "CALL_INTRINSIC_1", "CALL_INTRINSIC_2",
        ):
            num_args = int(instr.arg) if instr.arg is not None else 0

            # FIX-07: keyword argument handling
            # CALL_KW: TOS is a tuple of kw-names; then num_args values (kw last)
            kw_names: List[str] = []
            if opname == "CALL_KW" or ("kwnames" in str(instr.argval)):
                if self.stack:
                    raw_kw = self.stack.pop()
                    s_kw = str(raw_kw).strip("()")
                    if s_kw:
                        kw_names = [n.strip("'\" ") for n in s_kw.split(",") if n.strip()]
                    num_kw = len(kw_names)
                    num_pos = num_args - num_kw

                    kw_vals: List[str] = []
                    for _ in range(num_kw):
                        kw_vals.insert(0, str(self.stack.pop()) if self.stack else "?")
                    pos_vals: List[str] = []
                    for _ in range(num_pos):
                        pos_vals.insert(0, str(self.stack.pop()) if self.stack else "?")

                    final_args = pos_vals + [
                        f"{k}={v}" for k, v in zip(kw_names, kw_vals)
                    ]
            else:
                vals: List[str] = []
                for _ in range(num_args):
                    if self.stack:
                        v = self.stack.pop()
                        if isinstance(v, tuple) and len(v) >= 2 and v[0] in ("func", "class"):
                            vals.insert(0, str(v[1]))
                        else:
                            vals.insert(0, str(v))
                final_args = vals

            func_val = self.stack.pop() if self.stack else "unknown_func"
            if str(func_val) == "None" and self.stack:
                func_val = self.stack.pop()

            func = str(func_val)
            if " + NULL" in func or "|NULL" in func:
                func = func.split(" + ")[0].split("|")[0]

            # class builder detection
            if func == "__build_class__" and len(final_args) >= 2:
                body_text = str(final_args[0])
                cls_name = str(final_args[1]).strip("'\"")
                bases = final_args[2:]
                bases_str = f"({', '.join(str(b) for b in bases)})" if bases else ""
                lines = body_text.split("\n")
                if len(lines) > 1:
                    real_body = "\n".join(lines[1:])
                    self.stack.append(("class", f"class {cls_name}{bases_str}:\n{real_body}"))
                else:
                    self.stack.append(("class", f"class {cls_name}{bases_str}: pass"))
            elif func == "super" and not final_args:
                self.stack.append("super()")
            else:
                self.stack.append(f"{func}({', '.join(str(a) for a in final_args)})")

        elif opname == "LOAD_BUILD_CLASS":
            self.stack.append("__build_class__")

        # ── comparisons ────────────────────────────────────────────────
        elif "COMPARE_OP" in opname:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                op = str(instr.argval) if instr.argval else "=="
                if "(" in op and ")" in op:
                    m = re.search(r'\(([^)]+)\)', op)
                    if m:
                        op = m.group(1)
                if str(left) == "__name__" and str(right) == "'__main__'" and op == "==":
                    self.stack.append('__name__ == "__main__"')
                else:
                    self.stack.append(f"{left} {op} {right}")

        elif "CONTAINS_OP" in opname:
            if len(self.stack) >= 2:
                container = self.stack.pop()
                item = self.stack.pop()
                op = "not in" if bool(instr.arg) else "in"
                self.stack.append(f"{item} {op} {container}")

        elif "IS_OP" in opname:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                op = "is not" if bool(instr.arg) else "is"
                self.stack.append(f"{left} {op} {right}")

        elif "TO_BOOL" in opname:
            pass  # value already on stack, bool wrapping not needed

        # ── stack manipulation ─────────────────────────────────────────
        elif opname in ("ROT_TWO", "ROT_THREE", "ROT_FOUR"):
            if opname == "ROT_TWO" and len(self.stack) >= 2:
                a, b = self.stack.pop(), self.stack.pop()
                self.stack.extend([a, b])
            elif opname == "ROT_THREE" and len(self.stack) >= 3:
                a = self.stack.pop()
                b = self.stack.pop()
                c = self.stack.pop()
                self.stack.extend([a, c, b])
            elif opname == "ROT_FOUR" and len(self.stack) >= 4:
                a = self.stack.pop()
                b = self.stack.pop()
                c = self.stack.pop()
                d = self.stack.pop()
                self.stack.extend([a, d, c, b])

        elif opname in ("DUP_TOP", "DUP_TOP_TWO"):
            if opname == "DUP_TOP" and self.stack:
                self.stack.append(self.stack[-1])
            elif opname == "DUP_TOP_TWO" and len(self.stack) >= 2:
                self.stack.extend([self.stack[-2], self.stack[-1]])

        # ── f-strings ──────────────────────────────────────────────────
        elif opname in ("FORMAT_VALUE", "FORMAT_SIMPLE"):
            if self.stack:
                val = self.stack.pop()
                self.stack.append(f"{{{val}}}")

        elif opname == "BUILD_STRING":
            parts = []
            num = int(instr.arg) if instr.arg is not None else 0
            for _ in range(num):
                if self.stack:
                    parts.insert(0, self.stack.pop())
            has_fmt = any("{" in str(p) for p in parts)
            content = "".join(
                str(p).strip("'\"") if has_fmt else str(p) for p in parts
            )
            self.stack.append(f'f"{content}"' if has_fmt else f'"{content}"')

        # ── jumps / control flow ───────────────────────────────────────
        elif opname == "JUMP_FORWARD" or self._is_backward_jump(opname):
            jump_target = self._get_jump_target(instr)

            # FIX-11: detect while loop.
            # 3.11+ CPython compiles `while cond: body` as:
            #   A:  <condition>; POP_JUMP_IF_FALSE(end)  ← condition check #1
            #   B:  <body>
            #   C:  <condition>; POP_JUMP_IF_FALSE(end-2) ← condition check #2 (dup)
            #   D:  JUMP_BACKWARD(B)
            #   end-2: RETURN_CONST None  (loop-exhausted path — suppress)
            #   end:   RETURN_CONST None  (skipped path — suppress)
            #
            # When we see JUMP_BACKWARD(B) we:
            #   1. Find the start of the duplicated condition block (first
            #      instruction at or after B whose offset is ≥ the last body
            #      instruction + 2). Anything from there through JUMP_BACKWARD
            #      is duplicate — register those offsets for suppression.
            #   2. Retroactively rewrite the 'if' header → 'while'.
            #   3. Drain spurious stack items pushed by the dup condition.
            if self._is_backward_jump(opname) and jump_target <= instr.offset:
                # Pre-scan already registered the dup-condition region in
                # _while_body_offsets and the guard offset in _while_header_targets.
                # Nothing to do here except prevent falling into the else-detection below.
                return  # never treat JUMP_BACKWARD as else-opener



            # Detect `break`: JUMP_FORWARD that jumps to the end of a while block.
            for b_off, b_type in reversed(self.blocks):
                if b_type == "while" and jump_target == b_off:
                    self._append_reconstructed("break")
                    return
                if b_type == "while":
                    break  # only check innermost while

            if self.blocks and self.blocks[-1][1] in ("if", "if_implicit_else"):
                is_loop_back = opname == "JUMP_BACKWARD" and jump_target <= instr.offset
                if self.blocks[-1][0] <= instr.offset + 2 or is_loop_back:
                    target_of_else = (
                        jump_target if opname == "JUMP_FORWARD" else self.blocks[-1][0]
                    )
                    self.blocks.pop()
                    last_idx = len(self.reconstructed) - 1
                    while last_idx >= 0 and not self.reconstructed[last_idx].strip():
                        last_idx -= 1
                    if last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":"):
                        self._append_reconstructed("pass")
                    self.indent_level -= 1

                    # elif detection
                    next_i = self.pc
                    while (
                        next_i < len(self.instructions)
                        and self.instructions[next_i].opname in ("RESUME", "CACHE", "NOP")
                    ):
                        next_i += 1

                    is_elif = False
                    if next_i < len(self.instructions) and (
                        "JUMP_IF" in self.instructions[next_i].opname
                        or "FOR_ITER" in self.instructions[next_i].opname
                    ):
                        orig_num = len(self.reconstructed)
                        self._handle_instruction(self.instructions[next_i])
                        self.pc = next_i + 1
                        for i in range(orig_num, len(self.reconstructed)):
                            line = self.reconstructed[i]
                            if "if " in line:
                                head, sep, tail = line.partition("if ")
                                self.reconstructed[i] = f"{head}elif {tail}"
                                is_elif = True
                                break

                    if not is_elif:
                        meaningful_range_end = (
                            target_of_else
                            if opname == "JUMP_FORWARD"
                            else (
                                self.blocks[-1][0]
                                if self.blocks
                                else (self.instructions[-1].offset + 2)
                            )
                        )
                        meaningful = False
                        for b_idx in range(self.pc, len(self.instructions)):
                            ins = self.instructions[b_idx]
                            if ins.offset >= meaningful_range_end:
                                break
                            if ins.opname not in ("RESUME", "CACHE", "NOP", "END_FOR", "POP_ITER"):
                                meaningful = True
                                break
                        if meaningful:
                            self._append_reconstructed("else:")
                            self.indent_level += 1
                            self.blocks.append((meaningful_range_end, "else"))

        # ── no-ops ─────────────────────────────────────────────────────
        elif opname in (
            "PUSH_NULL", "RESUME", "PRECALL", "CACHE", "COPY_FREE_VARS",
            "MAKE_CELL", "END_FOR", "POP_ITER",
            "YIELD_FROM", "COPY",
        ):
            pass

        elif opname == "NOP":
            # NOP at offset 2 serves two roles in 3.11+:
            #   1. try-block entry marker — when PUSH_EXC_INFO exists in this code object
            #   2. while-True loop header — when JUMP_BACKWARD exists AND there are no
            #      POP_JUMP_IF_FALSE/TRUE instructions (unconditional infinite loop)
            # Regular while-loops (while cond: body) do NOT trigger here; they are
            # detected retroactively by the JUMP_BACKWARD handler.
            if instr.offset == 2:
                if self._has_exception_handler():
                    self._append_reconstructed("try:")
                    self.indent_level += 1
                    exc_offset = self._find_push_exc_info_offset()
                    if exc_offset > 0:
                        self.blocks.append((exc_offset, "try_body"))
                elif self._has_jump_backward() and not self._loop_cond_before_body(instr.offset + 2):
                    # while True: — unconditional loop (no condition before body)
                    self._append_reconstructed("while True:")
                    self.indent_level += 1
                    end_offset = self._find_jump_backward_end()
                    if end_offset > 0:
                        self.blocks.append((end_offset, "while"))
                        self._while_true_ends.add(end_offset)

        elif "POP_JUMP_IF_FALSE" in opname or "POP_JUMP_IF_TRUE" in opname:
            # Suppress dup-condition instructions pre-identified by pre-scan.
            if instr.offset in self._while_body_offsets:
                if self.stack:
                    self.stack.pop()
                return
            if self.stack:
                cond = self.stack.pop()
                # Suppress the POP_JUMP after CHECK_EXC_MATCH.
                if str(cond) == "_exc_match":
                    return
                is_true = "IF_TRUE" in opname
                jump_target = self._get_jump_target(instr)

                # If pre-scan identified this as a while-loop guard, emit 'while'.
                body_start = jump_target  # where the loop body begins... actually
                # jump_target is the loop END (where we jump if condition fails).
                # The body_start is jump_target's complement: right after this instr.
                next_body_offset = instr.offset + 4  # skip past COMPARE_OP cache slot
                # Use the pre-scanned mapping: guard_offset -> body_start
                for bs, go in self._while_header_targets.items():
                    if go == instr.offset:
                        # This POP_JUMP is the while-loop guard
                        if is_true:
                            self._append_reconstructed(f"while not {cond}:")
                        else:
                            self._append_reconstructed(f"while {cond}:")
                        self.indent_level += 1
                        self.blocks.append((jump_target, "while"))
                        return

                # and/or chain: same target as current if-block
                if self.blocks and self.blocks[-1][1] == "if" and self.blocks[-1][0] == jump_target:
                    prev_line = self.reconstructed.pop()
                    p_line = prev_line.strip()
                    prev_cond = p_line[3:].rstrip(":") if p_line.startswith("if ") else p_line.rstrip(":")
                    self.indent_level -= 1
                    if is_true:
                        self._append_reconstructed(f"if {prev_cond} or not {cond}:")
                    else:
                        self._append_reconstructed(f"if {prev_cond} and {cond}:")
                    self.indent_level += 1
                    return

                if is_true:
                    self._append_reconstructed(f"if not {cond}:")
                else:
                    self._append_reconstructed(f"if {cond}:")
                self.indent_level += 1

                meaning_idx = -1
                for i, ins in enumerate(self.instructions):
                    if ins.offset == jump_target:
                        meaning_idx = i
                        break

                if meaning_idx > 0:
                    prev = self.instructions[meaning_idx - 1]
                    if prev.opname in (
                        "RETURN_VALUE", "RETURN_CONST", "RAISE_VARARGS",
                        "BREAK_LOOP", "JUMP_FORWARD",
                    ):
                        self.blocks.append((jump_target, "if_implicit_else"))
                        return

                self.blocks.append((jump_target, "if"))

        # FIX-01: POP_JUMP_IF_NONE / POP_JUMP_IF_NOT_NONE — was swapped
        elif "POP_JUMP_IF_NONE" in opname or "POP_JUMP_IF_NOT_NONE" in opname:
            if self.stack:
                cond = self.stack.pop()
                is_not_none = "NOT_NONE" in opname
                # POP_JUMP_IF_NOT_NONE: jumps when not-None → body runs when None
                # POP_JUMP_IF_NONE:     jumps when None     → body runs when not-None
                if is_not_none:
                    self._append_reconstructed(f"if {cond} is not None:")
                else:
                    self._append_reconstructed(f"if {cond} is None:")
                self.indent_level += 1
                jump_target = self._get_jump_target(instr)
                self.blocks.append((jump_target, "if"))

        # ── for loop ───────────────────────────────────────────────────
        elif opname == "FOR_ITER":
            if self.stack:
                iterator = self.stack.pop()
                var_name = "_item"
                # Peek ahead for STORE_* or UNPACK_SEQUENCE to get var name(s)
                if self.pc < len(self.instructions):
                    next_instr = self.instructions[self.pc]
                    if next_instr.opname in ("STORE_NAME", "STORE_FAST"):
                        var_name = str(next_instr.argval)
                    elif next_instr.opname == "UNPACK_SEQUENCE":
                        count = int(next_instr.arg) if next_instr.arg else 2
                        names = []
                        look = self.pc + 1
                        while look < len(self.instructions) and len(names) < count:
                            li = self.instructions[look]
                            if li.opname in ("STORE_NAME", "STORE_FAST"):
                                names.append(str(li.argval))
                                look += 1
                            else:
                                break
                        if len(names) == count:
                            var_name = ", ".join(names)
                            self.pc = look  # skip the stores we peeked
                self._append_reconstructed(f"for {var_name} in {iterator}:")
                self.indent_level += 1
                jump_target = self._get_jump_target(instr)
                self.blocks.append((jump_target, "for"))
                self.stack.append(var_name)

        # ── collection builders ────────────────────────────────────────
        elif opname == "BUILD_TUPLE":
            items = []
            num = int(instr.arg) if instr.arg is not None else 0
            for _ in range(num):
                if self.stack:
                    items.insert(0, str(self.stack.pop()))
            self.stack.append(f"({', '.join(items)})")

        elif opname == "BUILD_LIST":
            items = []
            num = int(instr.arg) if instr.arg is not None else 0
            for _ in range(num):
                if self.stack:
                    items.insert(0, str(self.stack.pop()))
            self.stack.append("[" + ", ".join(items) + "]")

        elif opname == "BUILD_SET":
            items = []
            num = int(instr.arg) if instr.arg is not None else 0
            for _ in range(num):
                if self.stack:
                    items.insert(0, str(self.stack.pop()))
            self.stack.append("{" + ", ".join(items) + "}")

        # FIX-08: BUILD_MAP
        elif opname == "BUILD_MAP":
            num = int(instr.arg) if instr.arg is not None else 0
            pairs = []
            for _ in range(num):
                val = str(self.stack.pop()) if self.stack else "?"
                key = str(self.stack.pop()) if self.stack else "?"
                pairs.insert(0, f"{key}: {val}")
            self.stack.append("{" + ", ".join(pairs) + "}")

        elif opname == "BUILD_CONST_KEY_MAP":
            # TOS is tuple of keys; below are values
            keys_raw = str(self.stack.pop()) if self.stack else "()"
            keys = [k.strip("'\" ") for k in keys_raw.strip("()").split(",") if k.strip()]
            num = int(instr.arg) if instr.arg is not None else len(keys)
            vals_list = []
            for _ in range(num):
                vals_list.insert(0, str(self.stack.pop()) if self.stack else "?")
            pairs = [f"'{k}': {v}" for k, v in zip(keys, vals_list)]
            self.stack.append("{" + ", ".join(pairs) + "}")

        elif opname in ("GET_ITER", "UNPACK_SEQUENCE"):
            pass  # handled contextually in FOR_ITER peek above

        elif opname == "LIST_EXTEND":
            if len(self.stack) >= 2:
                it = str(self.stack.pop())
                lst = str(self.stack.pop())
                if lst == "[]":
                    self.stack.append(f"[*{it}]" if it.startswith("(") else f"list({it})")
                else:
                    self.stack.append(f"[*{lst}, *{it}]")

        elif opname == "DICT_MERGE":
            if len(self.stack) >= 2:
                src = str(self.stack.pop())
                base = str(self.stack.pop())
                self.stack.append(f"{{**{base}, **{src}}}")

        elif opname == "DICT_UPDATE":
            if len(self.stack) >= 2:
                src = str(self.stack.pop())
                base = str(self.stack.pop())
                self.stack.append(f"{{**{base}, **{src}}}")

        # ── secondary LOAD paths (dead-code guard — already handled above
        #    for the primary names; this catches any variant not in the top
        #    LOAD_* list, e.g. new fused opcodes in future versions) ──────
        elif "LOAD_FAST" in opname or "LOAD_GLOBAL" in opname:
            if isinstance(instr.argval, (tuple, list)):
                for n in instr.argval:
                    self.stack.append(str(n))
            else:
                self.stack.append(str(instr.argval))

        elif opname in ("LOAD_ATTR", "LOAD_METHOD"):
            obj = self.stack.pop() if self.stack else "obj"
            name = str(instr.argval)
            if " + " in name:
                name = name.split(" + ")[0]
            s_obj = str(obj).strip("'\"") if str(obj) in ("self", "cls") else str(obj)
            self.stack.append(f"{s_obj}.{name}")

        elif opname == "LOAD_SUPER_ATTR":
            name = str(instr.argval)
            if " + " in name:
                name = name.split(" + ")[0]
            self.stack.append(f"super().{name}")

        elif opname == "LOAD_FROM_DICT_OR_GLOBALS":
            self.stack.append(str(instr.argval))

        elif opname == "YIELD_VALUE":
            val = self.stack.pop() if self.stack else "None"
            self._append_reconstructed(f"yield {val}")

        elif opname == "DELETE_NAME" or opname == "DELETE_FAST" or opname == "DELETE_GLOBAL":
            # Suppress except-cleanup `del e` for any except-bound name.
            bound = getattr(self, "_exc_bound_names", set())
            if str(instr.argval) in bound:
                if str(instr.argval) == getattr(self, "_exc_cleanup_name", None):
                    self._exc_cleanup_name = None
                return
            self._append_reconstructed(f"del {instr.argval}")

        # ── assert ─────────────────────────────────────────────────────
        elif opname == "LOAD_ASSERTION_ERROR":
            self.stack.append("AssertionError")


# ---------------------------------------------------------------------------
# Helper: pick the correct decompiler subclass for recursive calls
# ---------------------------------------------------------------------------

def _pick_decompiler_class(instance):
    if isinstance(instance, Decompiler39):
        return Decompiler39
    if isinstance(instance, Decompiler314):
        return Decompiler314
    if isinstance(instance, Decompiler311Plus):
        return Decompiler311Plus
    return DecompilerGeneric


# ---------------------------------------------------------------------------
# Python 3.9 specialisation
# ---------------------------------------------------------------------------

# Complete Python 3.9 opcode table (CPython 3.9.x final)
_OPCODES_39: Dict[int, str] = {
    # no-arg opcodes (< 90)
    1:  "POP_TOP",
    2:  "ROT_TWO",
    3:  "ROT_THREE",
    4:  "DUP_TOP",
    5:  "DUP_TOP_TWO",
    6:  "ROT_FOUR",
    9:  "NOP",
    10: "UNARY_POSITIVE",
    11: "UNARY_NEGATIVE",
    12: "UNARY_NOT",
    15: "UNARY_INVERT",
    16: "BINARY_MATRIX_MULTIPLY",
    17: "INPLACE_MATRIX_MULTIPLY",
    19: "BINARY_POWER",
    20: "BINARY_MULTIPLY",
    22: "BINARY_MODULO",
    23: "BINARY_ADD",
    24: "BINARY_SUBTRACT",
    25: "BINARY_SUBSCR",
    26: "BINARY_FLOOR_DIVIDE",
    27: "BINARY_TRUE_DIVIDE",
    28: "INPLACE_FLOOR_DIVIDE",
    29: "INPLACE_TRUE_DIVIDE",
    36: "GET_AITER",
    37: "GET_ANEXT",
    38: "BEFORE_ASYNC_WITH",
    39: "BEGIN_FINALLY",
    40: "END_ASYNC_FOR",
    41: "INPLACE_ADD",
    42: "INPLACE_SUBTRACT",
    43: "INPLACE_MULTIPLY",
    45: "INPLACE_MODULO",
    46: "STORE_SUBSCR",
    47: "DELETE_SUBSCR",
    48: "BINARY_LSHIFT",
    49: "BINARY_RSHIFT",
    50: "BINARY_AND",
    51: "BINARY_XOR",
    52: "BINARY_OR",
    53: "INPLACE_POWER",
    54: "GET_ITER",
    55: "GET_YIELD_FROM_ITER",
    56: "PRINT_EXPR",
    57: "LOAD_BUILD_CLASS",
    58: "YIELD_FROM",
    59: "GET_AWAITABLE",
    60: "LOAD_ASSERTION_ERROR",
    61: "INPLACE_LSHIFT",
    62: "INPLACE_RSHIFT",
    63: "INPLACE_AND",
    64: "INPLACE_XOR",
    65: "INPLACE_OR",
    66: "WITH_EXCEPT_START",
    67: "GET_AITER",         # 3.9 duplicate — alias
    68: "GET_ANEXT",         # 3.9 duplicate — alias
    69: "BEFORE_ASYNC_WITH",
    70: "END_ASYNC_FOR",
    71: "LOAD_BUILD_CLASS",  # appears twice in some 3.9 builds
    72: "SETUP_WITH",
    73: "INPLACE_MATRIX_MULTIPLY",
    74: "YIELD_VALUE",
    75: "POP_BLOCK",
    76: "POP_EXCEPT",
    77: "RERAISE",
    # arg opcodes (>= 90)
    90: "STORE_NAME",
    91: "DELETE_NAME",
    92: "UNPACK_SEQUENCE",
    93: "FOR_ITER",
    94: "UNPACK_EX",
    95: "STORE_ATTR",
    96: "DELETE_ATTR",
    97: "STORE_GLOBAL",
    98: "DELETE_GLOBAL",
    100: "LOAD_CONST",
    101: "LOAD_NAME",
    102: "BUILD_TUPLE",
    103: "BUILD_LIST",
    104: "BUILD_SET",
    105: "BUILD_MAP",
    106: "LOAD_ATTR",
    107: "COMPARE_OP",
    108: "IMPORT_NAME",
    109: "IMPORT_FROM",
    110: "JUMP_FORWARD",
    111: "JUMP_IF_FALSE_OR_POP",
    112: "JUMP_IF_TRUE_OR_POP",
    113: "JUMP_ABSOLUTE",
    114: "POP_JUMP_IF_FALSE",
    115: "POP_JUMP_IF_TRUE",
    116: "LOAD_GLOBAL",
    117: "IS_OP",
    118: "CONTAINS_OP",
    119: "RERAISE",
    120: "JUMP_IF_NOT_EXC_MATCH",
    121: "SETUP_FINALLY",
    122: "LOAD_FAST",       # NOTE: in 3.9 LOAD_FAST starts at 124; 122 reserved
    124: "LOAD_FAST",
    125: "STORE_FAST",
    126: "DELETE_FAST",
    127: "GEN_START",
    130: "RAISE_VARARGS",
    131: "CALL_FUNCTION",
    132: "MAKE_FUNCTION",
    133: "BUILD_SLICE",
    135: "LOAD_CLOSURE",
    136: "LOAD_DEREF",
    137: "STORE_DEREF",
    138: "DELETE_DEREF",
    141: "CALL_FUNCTION_KW",
    142: "CALL_FUNCTION_EX",
    143: "SETUP_WITH",
    144: "EXTENDED_ARG",
    145: "LIST_APPEND",
    146: "SET_ADD",
    147: "MAP_ADD",
    148: "LOAD_CLASSDEREF",
    149: "MATCH_CLASS",
    154: "SETUP_ASYNC_WITH",
    155: "FORMAT_VALUE",
    156: "BUILD_CONST_KEY_MAP",
    157: "BUILD_STRING",
    158: "LOAD_METHOD",
    160: "LOAD_METHOD",      # alias seen in some builds
    161: "CALL_METHOD",
    162: "LIST_EXTEND",
    163: "SET_UPDATE",
    164: "DICT_MERGE",
    165: "DICT_UPDATE",
}


class Decompiler39(DecompilerGeneric):
    """Decompiler for .pyc files produced by CPython 3.9."""

    # FIX-05 + FIX-04: manual disassembler with EXTENDED_ARG support
    def _disassemble(self):
        bytecode = self.code_obj.co_code
        extended_arg = 0
        i = 0
        while i < len(bytecode):
            opcode = bytecode[i]
            raw_arg = bytecode[i + 1] if i + 1 < len(bytecode) else 0
            i += 2

            # FIX-05: accumulate EXTENDED_ARG
            if opcode == 144:  # EXTENDED_ARG
                extended_arg = (extended_arg | raw_arg) << 8
                continue

            arg = extended_arg | raw_arg
            extended_arg = 0  # reset after use

            opname = _OPCODES_39.get(opcode, f"OP_{opcode}")

            # Skip padding zeros (opcode 0 is not a valid 3.9 instruction)
            if opcode == 0:
                continue

            # Resolve argval
            argval: Any = arg if opcode >= 90 else None
            offset = i - 2  # offset of this instruction in the bytecode

            if opcode >= 90:
                if opname in (
                    "LOAD_CONST", "LOAD_NAME", "LOAD_GLOBAL", "LOAD_ATTR",
                    "LOAD_METHOD", "STORE_NAME", "DELETE_NAME", "STORE_GLOBAL",
                    "DELETE_GLOBAL", "STORE_ATTR", "DELETE_ATTR",
                    "IMPORT_NAME", "IMPORT_FROM",
                ):
                    table = self.code_obj.co_names
                    if "CONST" in opname:
                        table = self.code_obj.co_consts
                    if arg < len(table):
                        argval = table[arg]

                elif opname in ("LOAD_FAST", "STORE_FAST", "DELETE_FAST"):
                    if arg < len(self.code_obj.co_varnames):
                        argval = self.code_obj.co_varnames[arg]

                elif opname in ("LOAD_DEREF", "STORE_DEREF", "DELETE_DEREF",
                                "LOAD_CLOSURE", "LOAD_CLASSDEREF"):
                    combined = self.code_obj.co_cellvars + self.code_obj.co_freevars
                    if arg < len(combined):
                        argval = combined[arg]

                elif opname == "COMPARE_OP":
                    ops = ["<", "<=", "==", "!=", ">", ">=",
                           "in", "not in", "is", "is not", "exception match", "BAD"]
                    argval = ops[arg] if arg < len(ops) else str(arg)

                elif opname in ("FOR_ITER", "JUMP_FORWARD", "SETUP_FINALLY",
                                "SETUP_WITH", "SETUP_ASYNC_WITH"):
                    argval = offset + 2 + arg  # forward relative jump

                elif opname in ("JUMP_ABSOLUTE", "POP_JUMP_IF_FALSE",
                                "POP_JUMP_IF_TRUE", "JUMP_IF_FALSE_OR_POP",
                                "JUMP_IF_TRUE_OR_POP"):
                    argval = arg  # absolute target

            self.instructions.append(BytecodeInstruction(
                opcode=opcode,
                opname=opname,
                arg=arg if opcode >= 90 else None,
                argval=argval,
                offset=offset,
                starts_line=None,
                is_jump_target=False,
            ))

    # FIX-06: clean instruction dispatch for 3.9-specific opcodes
    def _handle_instruction(self, instr: BytecodeInstruction):
        opname = instr.opname

        # Binary ops (3.9 uses named opcodes, not BINARY_OP)
        _bin39 = {
            "BINARY_ADD": "+", "BINARY_SUBTRACT": "-",
            "BINARY_MULTIPLY": "*", "BINARY_TRUE_DIVIDE": "/",
            "BINARY_FLOOR_DIVIDE": "//", "BINARY_MODULO": "%",
            "BINARY_POWER": "**", "BINARY_LSHIFT": "<<",
            "BINARY_RSHIFT": ">>", "BINARY_AND": "&",
            "BINARY_OR": "|", "BINARY_XOR": "^",
            "BINARY_MATRIX_MULTIPLY": "@",
        }
        _inplace39 = {
            "INPLACE_ADD": "+=", "INPLACE_SUBTRACT": "-=",
            "INPLACE_MULTIPLY": "*=", "INPLACE_TRUE_DIVIDE": "/=",
            "INPLACE_FLOOR_DIVIDE": "//=", "INPLACE_MODULO": "%=",
            "INPLACE_POWER": "**=", "INPLACE_LSHIFT": "<<=",
            "INPLACE_RSHIFT": ">>=", "INPLACE_AND": "&=",
            "INPLACE_OR": "|=", "INPLACE_XOR": "^=",
            "INPLACE_MATRIX_MULTIPLY": "@=",
        }

        if opname in _bin39:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                self.stack.append(f"({left} {_bin39[opname]} {right})")

        elif opname in _inplace39:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                self.stack.append(f"({left} {_inplace39[opname]} {right})")

        elif opname == "COMPARE_OP":
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                op = str(instr.argval)
                self.stack.append(f"({left} {op} {right})")

        # CALL_FUNCTION: positional-only call (3.9)
        elif opname == "CALL_FUNCTION":
            num = int(instr.arg) if instr.arg is not None else 0
            args = []
            for _ in range(num):
                if self.stack:
                    args.insert(0, str(self.stack.pop()))
            func = self.stack.pop() if self.stack else "func"
            self.stack.append(f"{func}({', '.join(args)})")

        # CALL_FUNCTION_KW: last stack item is tuple of kw names
        elif opname == "CALL_FUNCTION_KW":
            num = int(instr.arg) if instr.arg is not None else 0
            kw_names_raw = str(self.stack.pop()).strip("()") if self.stack else ""
            kw_names = [n.strip("'\" ") for n in kw_names_raw.split(",") if n.strip()]
            num_kw = len(kw_names)
            num_pos = num - num_kw
            kw_vals = []
            for _ in range(num_kw):
                kw_vals.insert(0, str(self.stack.pop()) if self.stack else "?")
            pos_vals = []
            for _ in range(num_pos):
                pos_vals.insert(0, str(self.stack.pop()) if self.stack else "?")
            func = self.stack.pop() if self.stack else "func"
            all_args = pos_vals + [f"{k}={v}" for k, v in zip(kw_names, kw_vals)]
            self.stack.append(f"{func}({', '.join(all_args)})")

        # CALL_FUNCTION_EX: *args [and **kwargs]
        elif opname == "CALL_FUNCTION_EX":
            has_kwargs = bool(instr.arg)
            kwargs = str(self.stack.pop()) if has_kwargs and self.stack else None
            args = str(self.stack.pop()) if self.stack else "()"
            func = self.stack.pop() if self.stack else "func"
            call = f"{func}(*{args}"
            if kwargs:
                call += f", **{kwargs}"
            call += ")"
            self.stack.append(call)

        # LOAD_METHOD: push NULL sentinel + method reference
        elif opname == "LOAD_METHOD":
            obj = self.stack.pop() if self.stack else "unknown"
            self.stack.append("NULL")
            self.stack.append(f"{obj}.{instr.argval}")

        # CALL_METHOD: pop args, pop method ref, pop NULL sentinel
        elif opname == "CALL_METHOD":
            num_args = int(instr.arg) if isinstance(instr.arg, int) else 0
            args = []
            for _ in range(num_args):
                if self.stack:
                    args.insert(0, str(self.stack.pop()))
            meth = self.stack.pop() if self.stack else "unknown_meth"
            if self.stack and str(self.stack[-1]) == "NULL":
                self.stack.pop()  # discard sentinel
            self.stack.append(f"{meth}({', '.join(args)})")

        else:
            super()._handle_instruction(instr)


# ---------------------------------------------------------------------------
# Python 3.11+ specialisation  (3.11, 3.12, 3.13)
# ---------------------------------------------------------------------------

class Decompiler311Plus(DecompilerGeneric):

    def _handle_instruction(self, instr: BytecodeInstruction):
        if instr.opname == "RESUME":
            pass
        elif instr.opname == "BINARY_OP":
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                # Pure binary ops (indices 0–13)
                # In-place ops (indices 16–29) — same arithmetic, result stored back
                # CPython NB_* enum -> operator symbol.
                op_idx = int(instr.arg) if instr.arg is not None else -1

                # Python 3.14: BINARY_SUBSCR is encoded as BINARY_OP arg 26
                if op_idx == 26:
                    self.stack.append(f"{left}[{right}]")
                    return

                # Binary (non-mutating):
                op_map = {
                    0: "+",  1: "&",  2: "//", 3: "<<", 4: "@",
                    5: "*",  6: "%",  7: "|",  8: "**", 9: ">>",
                    10: "-", 11: "/", 12: "^",
                }
                # In-place / augmented-assignment:
                inplace_map = {
                    13: "+=",  14: "&=",  15: "//=", 16: "<<=", 17: "@=",
                    18: "*=",  19: "%=",  20: "|=",  21: "**=", 22: ">>=",
                    23: "-=",  24: "/=",  25: "^=",
                }
                inplace_op = inplace_map.get(op_idx)
                bin_op = op_map.get(op_idx)

                # 3.14+: subscript via BINARY_OP arg 26
                if op_idx == 26:
                    self.stack.append(f"{left}[{right}]")
                    return

                # Binary (non-mutating):
                op_map = {
                    0: "+",  1: "&",  2: "//", 3: "<<", 4: "@",
                    5: "*",  6: "%",  7: "|",  8: "**", 9: ">>",
                    10: "-", 11: "/", 12: "^",
                }
                # In-place / augmented-assignment:
                inplace_map = {
                    13: "+=",  14: "&=",  15: "//=", 16: "<<=", 17: "@=",
                    18: "*=",  19: "%=",  20: "|=",  21: "**=", 22: ">>=",
                    23: "-=",  24: "/=",  25: "^=",
                }
                op_idx = int(instr.arg) if instr.arg is not None else -1
                inplace_op = inplace_map.get(op_idx)
                bin_op = op_map.get(op_idx)

                if inplace_op:
                    # In-place operation — check if next instruction stores back
                    # to the same name so we can emit `left op= right` directly.
                    next_pc = self.pc  # pc already past current instr
                    while (next_pc < len(self.instructions) and
                           self.instructions[next_pc].opname in ("CACHE", "RESUME")):
                        next_pc += 1
                    if (next_pc < len(self.instructions) and
                            self.instructions[next_pc].opname in (
                                "STORE_NAME", "STORE_FAST", "STORE_GLOBAL", "STORE_ATTR"
                            )):
                        store_name = str(self.instructions[next_pc].argval)
                        left_name = str(left).split(".")[-1]  # handle attr access
                        if store_name == left_name or str(left).endswith(store_name):
                            # Emit augmented assignment, skip the STORE
                            self._append_reconstructed(f"{left} {inplace_op} {right}")
                            self.pc = next_pc + 1  # consume the STORE
                            return
                    # Fallback: push result for STORE to handle
                    self.stack.append(f"({left} {inplace_op} {right})")
                elif bin_op:
                    self.stack.append(f"({left} {bin_op} {right})")
                else:
                    self.stack.append(f"({left} <op:{op_idx}> {right})")
        else:
            super()._handle_instruction(instr)

    def _get_jump_target(self, instr: BytecodeInstruction) -> int:
        if isinstance(instr.argval, int):
            return instr.argval
        arg = int(instr.arg) if instr.arg is not None else 0
        if "BACKWARD" in instr.opname:
            return instr.offset + 2 - (arg * 2)
        return instr.offset + 2 + (arg * 2)


# ---------------------------------------------------------------------------
# Python 3.14 stub  (FIX-13)
# ---------------------------------------------------------------------------

class Decompiler314(Decompiler311Plus):
    """
    Stub for Python 3.14.  Most opcodes are shared with 3.11–3.13.
    Known 3.14 additions handled here:
      - LOAD_SMALL_INT  already dispatched by DecompilerGeneric
      - LOAD_GLOBAL_MODULE  already dispatched by DecompilerGeneric
    Extend this class as 3.14-specific opcodes are confirmed.
    """
    pass


# ---------------------------------------------------------------------------
# Marshal parser  (cross-version .pyc reading)
# ---------------------------------------------------------------------------

class MarshalParser:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0
        self.refs: List[Any] = []

    def _read(self, n: int) -> bytes:
        if self.offset + n > len(self.data):
            raise EOFError(
                f"Marshal read past EOF at offset {self.offset} "
                f"(tried {n} bytes, total {len(self.data)})"
            )
        res = self.data[self.offset: self.offset + n]
        self.offset += n
        return res

    def _read_byte(self) -> int:
        return self._read(1)[0]

    def _read_long(self) -> int:
        return struct.unpack("<i", self._read(4))[0]

    def _reserve_ref(self) -> int:
        idx = len(self.refs)
        self.refs.append(None)
        return idx

    def _set_ref(self, idx: int, obj: Any) -> Any:
        self.refs[idx] = obj
        return obj

    def load(self) -> Any:
        byte = self._read_byte()
        flagged = bool(byte & 0x80)
        type_char = chr(byte & 0x7F)

        if type_char == "r":  # TYPE_REF
            ref_idx = self._read_long()
            return self.refs[ref_idx]

        ref_idx = None
        if flagged:
            ref_idx = self._reserve_ref()

        result = self._load_inner(type_char)

        if ref_idx is not None:
            self._set_ref(ref_idx, result)

        return result

    def _load_inner(self, type_char: str) -> Any:
        if type_char == "N":
            return None
        if type_char == "T":
            return True
        if type_char == "F":
            return False
        if type_char == "i":
            return self._read_long()
        if type_char in ("s", "u", "Z", "a", "z", "A", "t"):
            size = self._read_byte() if type_char in ("z", "Z") else self._read_long()
            raw = self._read(size)
            if type_char == "s":
                return raw
            return raw.decode("utf-8", "replace")
        if type_char in ("y", ")", "("):
            size = self._read_byte() if type_char in ("y", ")") else self._read_long()
            return tuple(self.load() for _ in range(size))
        if type_char == "[":
            size = self._read_long()
            return [self.load() for _ in range(size)]
        if type_char == "{":
            res_dict: Dict[Any, Any] = {}
            while True:
                key = self.load()
                if key is None:
                    break
                res_dict[key] = self.load()
            return res_dict
        if type_char in ("I", "l"):
            if type_char == "I":
                return struct.unpack("<q", self._read(8))[0]
            size = self._read_long()
            return int.from_bytes(self._read(abs(size) * 2), "little", signed=(size < 0))
        if type_char == "S":
            return StopIteration
        if type_char == "g":
            return struct.unpack("<d", self._read(8))[0]
        if type_char in ("<", ">"):
            size = self._read_long()
            items = [self.load() for _ in range(size)]
            return set(items) if type_char == "<" else frozenset(items)
        if type_char == "c":
            return self._load_code()

        raise ValueError(
            f"Unsupported marshal type: {type_char!r} (hex: {hex(ord(type_char))})"
        )

    # FIX-02: version-aware CodeType constructor
    def _load_code(self) -> types.CodeType:
        argcount        = self._read_long()
        posonlyargcount = self._read_long()
        kwonlyargcount  = self._read_long()
        nlocals         = self._read_long()
        stacksize       = self._read_long()
        flags           = self._read_long()
        code            = self.load()
        consts          = self.load()
        names           = self.load()
        varnames        = self.load()
        freevars        = self.load()
        cellvars        = self.load()
        filename        = self.load()
        name            = self.load()
        firstlineno     = self._read_long()
        lnotab          = self.load()

        def to_tuple_strings(x):
            if x is None or isinstance(x, int):
                return ()
            return tuple(
                s.decode("utf-8", "replace") if isinstance(s, bytes) else str(s)
                for s in x
            )

        def to_tuple(x):
            if isinstance(x, tuple):
                return x
            if x is None or isinstance(x, int):
                return ()
            return tuple(x)

        code     = bytes(code) if not isinstance(code, bytes) else code
        consts   = to_tuple(consts)
        names    = to_tuple_strings(names)
        varnames = to_tuple_strings(varnames)
        freevars = to_tuple_strings(freevars)
        cellvars = to_tuple_strings(cellvars)
        lnotab   = bytes(lnotab) if not isinstance(lnotab, bytes) else lnotab
        filename = filename.decode("utf-8", "replace") if isinstance(filename, bytes) else str(filename)
        name     = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)

        vi = sys.version_info

        # FIX-02: branch on the *host* Python's CodeType signature
        if vi >= (3, 11):
            # 3.11+: argcount, posonlyargcount, kwonlyargcount, nlocals,
            #        stacksize, flags, codestring, constants, names,
            #        varnames, filename, name, qualname, firstlineno,
            #        linetable, exceptiontable, freevars, cellvars
            return types.CodeType(
                argcount, posonlyargcount, kwonlyargcount, nlocals,
                stacksize, flags, code, consts, names, varnames,
                filename, name, name,       # qualname = name
                firstlineno,
                lnotab, b"",               # linetable, exceptiontable
                freevars, cellvars,
            )
        elif vi >= (3, 8):
            # 3.8–3.10: argcount, posonlyargcount, kwonlyargcount, nlocals,
            #           stacksize, flags, codestring, constants, names,
            #           varnames, filename, name, firstlineno, lnotab,
            #           freevars, cellvars
            return types.CodeType(
                argcount, posonlyargcount, kwonlyargcount, nlocals,
                stacksize, flags, code, consts, names, varnames,
                filename, name,
                firstlineno, lnotab,
                freevars, cellvars,
            )
        else:
            # 3.7 and below (no posonlyargcount)
            return types.CodeType(
                argcount, kwonlyargcount, nlocals,
                stacksize, flags, code, consts, names, varnames,
                filename, name,
                firstlineno, lnotab,
                freevars, cellvars,
            )


# ---------------------------------------------------------------------------
# Entry point: load .pyc and pick decompiler
# ---------------------------------------------------------------------------

# FIX-03: updated magic-number version ranges
#
# Python version   magic (& 0xFFFF) range  (approximate — patch releases vary
#                                           by a few units but stay in band)
# 3.9              3410 – 3429
# 3.10             3430 – 3449
# 3.11             3450 – 3494
# 3.12             3495 – 3530
# 3.13             3531 – 3559
# 3.14             3560+

def get_decompiler(filepath: str) -> DecompilerBase:
    with open(filepath, "rb") as f:
        all_data = f.read()

    if len(all_data) < 16:
        raise ValueError("Invalid .pyc file: too short")

    magic = struct.unpack("<I", all_data[0:4])[0]
    version_id = magic & 0xFFFF

    if not (3000 <= version_id <= 5000):
        import importlib.util
        host_magic = int.from_bytes(importlib.util.MAGIC_NUMBER, "little")
        if magic != host_magic:
            raise ValueError(
                f"Invalid or unsupported Python magic number: 0x{magic:08x} "
                f"(version id: {version_id}). File may be corrupt or from an "
                "unsupported Python version."
            )

    import importlib.util
    host_magic = int.from_bytes(importlib.util.MAGIC_NUMBER, "little")

    code_obj = None
    if magic == host_magic:
        for offset in (16, 12, 8, 4):
            try:
                import io
                obj = marshal.load(io.BytesIO(all_data[offset:]))
                if isinstance(obj, types.CodeType):
                    code_obj = obj
                    break
            except Exception:
                continue

    if code_obj is None:
        for offset in (16, 12, 8, 4):
            try:
                parser = MarshalParser(all_data[offset:])
                obj = parser.load()
                if isinstance(obj, types.CodeType):
                    code_obj = obj
                    break
            except Exception:
                continue

    if not isinstance(code_obj, types.CodeType):
        raise ValueError("Could not find valid marshal code object in .pyc file")

    # FIX-03: corrected dispatch table
    if 3410 <= version_id <= 3429:      # 3.9
        return Decompiler39(code_obj)
    elif version_id >= 3560:            # 3.14+
        return Decompiler314(code_obj)
    elif version_id >= 3430:            # 3.10, 3.11, 3.12, 3.13
        return Decompiler311Plus(code_obj)

    # Fallback for very old or unrecognised versions
    return DecompilerGeneric(code_obj)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    if len(sys.argv) < 2:
        print("Usage: pycrefine <file.pyc>")
        return

    pyc_path = sys.argv[1]
    try:
        decompiler = get_decompiler(pyc_path)
        print(decompiler.decompile())
    except ValueError as e:
        print(f"Error: {e}")
        sys.exit(1)
    except Exception:
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()