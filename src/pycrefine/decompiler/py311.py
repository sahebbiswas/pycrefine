"""Decompiler backend for CPython 3.10-3.12 bytecode (uses the host ``dis`` module)."""
from .generic import DecompilerGeneric
from ..diagnostics import AMBIGUOUS_EXPRESSION, INCOMPLETE_EXCEPTION
from ..instructions import BytecodeInstruction

# 3.10 sets up try, with and finally with block-stack instructions (3.11 replaced them
# with an exception table), and no backend rebuilds them yet (#179).
_BLOCK_SETUP_OPS = frozenset(("SETUP_FINALLY", "SETUP_WITH", "SETUP_ASYNC_WITH"))


# ---------------------------------------------------------------------------
# Python 3.11+ specialisation  (3.11, 3.12, 3.13)
# ---------------------------------------------------------------------------

class Decompiler311Plus(DecompilerGeneric):

    def _report_version_gaps(self) -> None:
        if self.target_version < (3, 11):
            first = next((ins for ins in self.instructions if ins.opname in _BLOCK_SETUP_OPS), None)
            if first is not None:
                self._report(
                    INCOMPLETE_EXCEPTION,
                    "try, except, finally and with are not decompiled on Python 3.10 "
                    "(block-based exception handlers, issue #179)", first)

    def _dispatch_instruction(self, instr: BytecodeInstruction):
        """
        Handle a single bytecode instruction, reconstructing binary and in-place operations and delegating other opcodes.

        Ternary and compound-condition suppression has already happened in the shared `_handle_instruction`, which calls this method. For BINARY_OP instructions this method:
        - Emits subscript expressions when the op index maps to subscript semantics.
        - Builds binary-expression strings for known binary operator indices and pushes them onto the expression stack.
        - Detects in-place operator indices and, when the following instruction stores back to the same target, emits an augmented-assignment statement and advances the program counter past the store; otherwise it pushes a parenthesized in-place expression onto the stack.
        - Falls back to a placeholder expression for unknown operator indices.

        For non-BINARY_OP/RESUME opcodes, handling is delegated to the superclass implementation.

        Parameters:
                instr (BytecodeInstruction): The disassembled instruction to process. This method may mutate the decompiler's `stack`, `pc`, and reconstructed output.
        """

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
                inplace_op = inplace_map.get(op_idx)
                bin_op = op_map.get(op_idx)

                if inplace_op:
                    # In-place operation — check if next instruction stores back
                    # to the same name so we can emit `left op= right` directly.
                    next_pc = self.pc  # pc already past current instr
                    while (next_pc < len(self.instructions) and
                           self.instructions[next_pc].opname in ("CACHE", "RESUME", "NOT_TAKEN", "SWAP")):
                        # Allow SWAP between BINARY_OP and STORE_* (e.g. for STORE_SUBSCR)
                        if self.instructions[next_pc].opname == "SWAP" and self.instructions[next_pc].arg not in (2, 3):
                            break
                        next_pc += 1

                    if (next_pc < len(self.instructions) and
                            self.instructions[next_pc].opname in (
                                "STORE_NAME", "STORE_FAST", "STORE_GLOBAL", "STORE_ATTR", "STORE_DEREF", "STORE_SUBSCR"
                    )):
                        instr_store = self.instructions[next_pc]
                        if self._match_augassign_store(str(left), instr_store):
                            # Emit augmented assignment, skip the STORE
                            if instr_store.opname == "STORE_ATTR" and self.stack:
                                self.stack.pop()  # pop defensive receiver
                            if instr_store.opname == "STORE_SUBSCR" and len(self.stack) >= 2:
                                self.stack.pop()
                                self.stack.pop()
                            self._append_reconstructed(f"{left} {inplace_op} {right}")
                            self.pc = next_pc + 1  # consume the STORE
                            return
                    # Fallback: push result for STORE to handle
                    self.stack.append(f"({left} {inplace_op} {right})")
                elif bin_op:
                    l_str = str(left)
                    r_str = str(right)
                    # Wrap ternary operands to prevent precedence bugs:
                    # e.g. 'post %s in input' % ('x' if cond else 'y')
                    if ' if ' in l_str and ' else ' in l_str:
                        l_str = f"({l_str})"
                    if ' if ' in r_str and ' else ' in r_str:
                        r_str = f"({r_str})"
                    self.stack.append(f"({l_str} {bin_op} {r_str})")
                else:
                    # Unknown BINARY_OP index — use ? as a safe placeholder
                    self._report(AMBIGUOUS_EXPRESSION, f"unknown binary operator ({instr.arg})", instr)
                    self.stack.append(f"({left} ? {right})")
        else:
            super()._dispatch_instruction(instr)

    def _get_jump_target(self, instr: BytecodeInstruction) -> int:
        if isinstance(instr.argval, int):
            return instr.argval
        arg = int(instr.arg) if instr.arg is not None else 0
        if "BACKWARD" in instr.opname:
            return instr.offset + 2 - (arg * 2)
        return instr.offset + 2 + (arg * 2)
