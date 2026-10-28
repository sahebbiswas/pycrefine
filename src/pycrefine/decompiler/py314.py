"""Decompiler backend for CPython 3.13/3.14 bytecode."""
import dis
from .._constants import _VALUE_OPS
from ..expr import _UnpackAccessor
from .py311 import Decompiler311Plus
from ..instructions import BytecodeInstruction


# ---------------------------------------------------------------------------
# Python 3.14 stub
# ---------------------------------------------------------------------------

class Decompiler314(Decompiler311Plus):
    """
    Python 3.14+ specific decompiler logic.
    Handles the new LOAD_SPECIAL opcodes and structure for with statements.
    """

    def _remove_with_sentinel(self, with_id: int) -> None:
        """Remove the stack sentinel of the with body that starts at *with_id*, if present."""
        self.stack = [item for item in self.stack
                      if not (isinstance(item, tuple) and len(item) > 2
                              and item[0] == "_with_info" and item[2] == with_id)]

    def _close_blocks(self, offset: int):
        """Close blocks as usual, then drop the stack sentinel of every `with` body that ended."""
        super()._close_blocks(offset)
        still_open = []
        for end, with_id in getattr(self, "_open_with_sentinels", []):
            if offset >= end:
                # A body that only raises or returns never runs its __exit__ call inline,
                # so nothing else removes the sentinel it left on the stack.
                self._remove_with_sentinel(with_id)
            else:
                still_open.append((end, with_id))
        self._open_with_sentinels = still_open

    def _handle_instruction(self, instr: BytecodeInstruction):
        """
        Dispatch a single bytecode instruction to the decompiler, handling Python 3.14-specific with-statement preambles and cleanup suppression before delegating to the generic handler.

        This method:
        - Recognizes and suppresses instruction offsets reserved by prescans (ternary/compound-condition suppression and recorded with-exit cleanup offsets).
        - Detects Python 3.14 with-statement preambles (COPY 1 + LOAD_SPECIAL/CALL sequence), emits a corresponding `with <ctx>[ as name]:` header, increases indentation, pushes an internal with sentinel on the expression stack, and records/opens a with-body block based on exception-table-derived boundaries.
        - Records and suppresses normal-path __exit__ cleanup sequences by populating an internal _with_exit_suppress set so those instructions are ignored later.
        - Suppresses a common three-None cleanup pattern (LOAD_CONST None x2/3 + CALL 3 + POP_TOP) and removes any internal "_with_info" sentinel left on the stack.
        - Ensures any internal with-sentinel (`("_with_info", ...)`) is removed from the expression stack before emitting a return while preserving the actual return value.
        - Handles Python 3.14 combined-store `STORE_FAST_STORE_FAST` by emitting tuple-unpacking assignment text when possible.
        - Delegates all remaining or non-specialized opcodes to the superclass implementation for normal reconstruction.

        Parameters:
            instr (BytecodeInstruction): The disassembled instruction to process; its opname, opcode, arg, argval, and offset are used to match patterns and drive stack/block changes.

        Side effects:
            May mutate self.pc, self.stack, self.blocks, self.indent_level, self.reconstructed, and may create or update self._with_exit_suppress. Delegates further emission and handling to super()._handle_instruction when appropriate.
        """
        # Suppress then-branch instructions of detected ternary expressions
        if instr.offset in getattr(self, "_ternary_suppress", ()):
            return
        # Suppress intermediate instructions belonging to a compound boolean
        if instr.offset in getattr(self, "_compound_suppress", ()):
            if self.stack:
                self.stack.pop()
            return

        opname = instr.opname

        # Inlined __exit__ calls at the exits of a with body (see _prescan_with_exits)
        if instr.offset in getattr(self, "_with_exit_suppress_offsets", ()):
            owner = self._with_exit_owner.get(instr.offset)
            if owner is not None:
                self._remove_with_sentinel(owner)  # this exit left the with body
            if (instr.offset in getattr(self, "_finally_merge_offsets", ())
                    or instr.offset in getattr(self, "_except_only_merge_offsets", ())):
                # A try statement that ends the with body merges at its exit call: the
                # generic handler emits the deferred handlers/finally there, then hides
                # the call itself.
                super()._handle_instruction(instr)
            return
        # Suppress normal-path __exit__ body calls (identified by range scan)
        if getattr(self, "_with_exit_suppress", set()) and instr.offset in getattr(self, "_with_exit_suppress"):
            return

        # Handle Python 3.14 LOAD_SPECIAL (opcode 95)
        if instr.opcode == 95:
            # LOAD_SPECIAL is used in with/async-with preambles and cleanup.
            # We skip it primarily, unless it's part of a detected call.
            return

        # Python 3.14 with preamble starting at COPY 1
        if opname == "COPY" and instr.arg == 1:
            if self.pc + 4 < len(self.instructions):
                seq = self.instructions[self.pc: self.pc + 5]
                # Pattern: LOAD_SPECIAL 1 (__exit__), SWAP 2, SWAP 3, LOAD_SPECIAL 0 (__enter__), CALL 0
                if (seq[0].opcode == 95 and seq[0].arg == 1 and
                    seq[1].opname == "SWAP" and seq[1].arg == 2 and
                    seq[2].opname == "SWAP" and seq[2].arg == 3 and
                    seq[3].opcode == 95 and seq[3].arg == 0 and
                        seq[4].opname == "CALL" and seq[4].arg == 0):

                    ctx_mgr = str(self._pop("unknown_ctx"))
                    # Push internal sentinel representing EX and CM that stay on stack
                    body_start = self.instructions[self.pc + 5].offset if self.pc + 5 < len(self.instructions) else instr.offset
                    self.stack.append(("_with_info", ctx_mgr, body_start))

                    self.pc += 5
                    next_instr = self.instructions[self.pc] if self.pc < len(self.instructions) else None
                    body_end = self._with_body_end.get(next_instr.offset) if next_instr is not None else None
                    as_name = ""
                    if next_instr and next_instr.opname in ("STORE_NAME", "STORE_FAST", "STORE_GLOBAL", "STORE_ATTR"):
                        as_name = f" as {next_instr.argval}"
                        self.pc += 1
                    elif next_instr and next_instr.opname == "POP_TOP":
                        self.pc += 1

                    self._append_reconstructed(f"with {ctx_mgr}{as_name}:")
                    self.indent_level += 1

                    try_end = None
                    exit_body_start = None
                    try:
                        curr_offset = self.instructions[self.pc].offset if self.pc < len(
                            self.instructions) else getattr(instr, "offset", 0)
                        # We use the host Python's dis.Bytecode if it supports exception_entries (3.11+)
                        if hasattr(dis, "Bytecode"):
                            entries = dis.Bytecode(self.code_obj).exception_entries
                            handler_target = None
                            for e in entries:
                                # Re-sync with exception table: the protected range
                                # usually starts at the first real instruction after CALL.
                                if e.start >= instr.offset and e.start <= instr.offset + 20:
                                    handler_target = e.target
                                    break
                            if handler_target is not None:
                                try_end = handler_target
                                normal_ends = [e.end for e in entries if e.target ==
                                               handler_target and e.end < handler_target]
                                if normal_ends:
                                    exit_body_start = max(normal_ends)
                    except (AttributeError, IndexError, TypeError, ValueError):
                        pass

                    if body_end is not None:
                        # The prescan knows where the body really ends (after its last
                        # __exit__ call, or at the handler when that call carries a return).
                        try_end, exit_body_start = body_end, None
                    if try_end is not None:
                        self.blocks.append((try_end, "with_body"))
                        self._open_with_sentinels.append((try_end, body_start))
                    if exit_body_start is not None:
                        if not hasattr(self, "_with_exit_suppress"):
                            self._with_exit_suppress = set()
                        idx = next((i for i, x in enumerate(self.instructions) if x.offset == exit_body_start), None)
                        if idx is not None:
                            # Suppress up to the end of the cleanup sequence
                            for j in range(idx, len(self.instructions)):
                                instr_j = self.instructions[j]
                                op_j = instr_j.opname
                                self._with_exit_suppress.add(instr_j.offset)
                                # ONLY suppress until the end of the __exit__ cleanup call (POP_TOP).
                                # Do NOT include RETURN_VALUE or any following instructions.
                                if op_j == "POP_TOP":
                                    break
                                # Safety break if it looks like we're going too far
                                if op_j in ("RETURN_VALUE", "RETURN_CONST", "RESUME"):
                                    break
                    return

        # Suppress normal-path cleanup calls: LOAD_CONST None (x3) + CALL 3 + POP_TOP
        elif opname == "LOAD_CONST" and instr.argval is None:
            # Check if this is the start of a 3x None sequence. Not after a value: then
            # it is the arguments of a user call such as f(None, None, None).
            prev = self.instructions[self.pc - 2] if self.pc >= 2 else None
            after_value = prev is not None and prev.opname.startswith(_VALUE_OPS)
            if not after_value and self.pc + 3 < len(self.instructions):
                seq = self.instructions[self.pc: self.pc + 4]
                if (seq[0].opname == "LOAD_CONST" and seq[0].argval is None and
                    seq[1].opname == "LOAD_CONST" and seq[1].argval is None and
                    seq[2].opname == "CALL" and seq[2].arg == 3 and
                        seq[3].opname == "POP_TOP"):

                    # Suppress the sequence
                    self.pc += 4
                    # Pop the sentinel if it's there
                    if self.stack and isinstance(self.stack[-1], tuple) and self.stack[-1][0] == "_with_info":
                        self.stack.pop()
                    elif len(self.stack) >= 2 and isinstance(self.stack[-2], tuple) and self.stack[-2][0] == "_with_info":
                        # Sometimes a return value or None is on top
                        sav = self.stack.pop()
                        self.stack.pop()
                        self.stack.append(sav)
                    return

        # Before any return, ensure we've cleared the internal with-sentinel.
        # Python 3.14 often leaves _with_info on the stack; we must clear it
        # while preserving the actual return value for the parent handler.
        elif opname in ("RETURN_VALUE", "RETURN_CONST"):
            if any(isinstance(s, tuple) and s[0] == "_with_info" for s in self.stack):
                val = self.stack.pop()
                while self.stack:
                    s = self.stack.pop()
                    if isinstance(s, tuple) and s[0] == "_with_info":
                        break
                self.stack.append(val)
            # Fall through to super()

        elif opname == "STORE_FAST_STORE_FAST":
            # Python 3.14 combined store for tuple unpacking (after UNPACK_SEQUENCE)
            if (isinstance(instr.argval, (tuple, list)) and len(instr.argval) >= 2
                    and len(self.stack) >= len(instr.argval)
                    and all(isinstance(v, _UnpackAccessor) for v in self.stack[-len(instr.argval):])):
                # A fallback unpacking left one value per target. The fused store takes
                # the first name from the top of the stack and the next from below it.
                for name in instr.argval:
                    self._append_reconstructed(f"{name} = {self.stack.pop()}")
                return
            if isinstance(instr.argval, (tuple, list)) and len(instr.argval) >= 2:
                names = ", ".join(str(x) for x in instr.argval)
                expr = str(self._pop("?"))
                self._append_reconstructed(f"{names} = {expr}")
            else:
                expr = str(self._pop("?"))
                # Use argrepr which contains the original target text like "(foo, bar)"
                targets = instr.argrepr if instr.argrepr else "var1, var2"
                self._append_reconstructed(f"{targets} = {expr}")
            return

        super()._handle_instruction(instr)
