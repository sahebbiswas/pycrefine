"""Decompiler backend for CPython 3.9 bytecode."""
import sys
import types
from typing import Any
from .._constants import _GENERIC_SKIP_OPS, _MAX_TERNARY_MINI_SEARCH_WINDOW
from .generic import DecompilerGeneric
from ..instructions import BytecodeInstruction, _copy_signature, _match_finally_copy
from ..opcodes.py39 import _OPCODES_39


def _lnotab_line_starts(code: Any) -> dict:
    """``{offset: line}`` for each instruction that starts a source line (3.8-3.9 lnotab)."""
    if isinstance(code, types.CodeType):
        if sys.version_info >= (3, 10):
            return {}  # a native code object of a later Python: not a 3.9 table
        table = code.co_lnotab
    else:
        table = getattr(code, "co_linetable", b"") or b""
    starts: dict = {}
    code_len = len(getattr(code, "co_code", b"") or b"")
    line = getattr(code, "co_firstlineno", 0)
    last = None
    addr = 0
    for byte_incr, line_incr in zip(table[0::2], table[1::2]):
        if byte_incr:
            if line != last:
                starts[addr] = line
                last = line
            addr += byte_incr
            if addr >= code_len:
                return starts
        line += line_incr - 0x100 if line_incr >= 0x80 else line_incr
    if line != last and addr < code_len:
        starts[addr] = line
    return starts


class Decompiler39(DecompilerGeneric):
    """Decompiler for .pyc files produced by CPython 3.9."""

    # manual disassembler with EXTENDED_ARG support
    def _disassemble(self):
        """
        Disassembles the contained code object's bytecode (Python 3.9 format) into self.instructions.

        Parses co_code into a sequence of BytecodeInstruction entries with resolved argument values (for names, consts, varnames, free/cell vars, comparison operators and jump targets), handles EXTENDED_ARG accumulation, skips padding zero bytes, and then marks instructions that are jump targets by scanning jump opcodes' resolved targets.
        """
        bytecode = self.code_obj.co_code
        extended_arg = 0
        i = 0
        while i < len(bytecode):
            opcode = bytecode[i]
            raw_arg = bytecode[i + 1] if i + 1 < len(bytecode) else 0
            i += 2

            # accumulate EXTENDED_ARG
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
                is_jump_target=False,   # patched below after full pass
            ))

        # Line starts from the 3.9 line-number table (co_lnotab; a ForeignCode keeps
        # the same bytes in co_linetable), as dis.findlinestarts reads it on 3.9.
        line_starts = _lnotab_line_starts(self.code_obj)
        if line_starts:
            self.instructions = [
                BytecodeInstruction(ins.opcode, ins.opname, ins.arg, ins.argval, ins.offset,
                                    line_starts.get(ins.offset), ins.is_jump_target)
                for ins in self.instructions
            ]

        # Patch is_jump_target: collect all argval targets for jump opcodes
        _jump_ops = {
            "FOR_ITER", "JUMP_FORWARD", "JUMP_ABSOLUTE",
            "POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE",
            "JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP",
            "SETUP_FINALLY", "SETUP_WITH", "SETUP_ASYNC_WITH",
            "JUMP_IF_NOT_EXC_MATCH",
        }
        _target_offsets: set = set()
        for ins in self.instructions:
            if ins.opname in _jump_ops and isinstance(ins.argval, int):
                _target_offsets.add(ins.argval)
        # Rebuild list with correct is_jump_target
        self.instructions = [
            BytecodeInstruction(
                ins.opcode, ins.opname, ins.arg, ins.argval,
                ins.offset, ins.starts_line,
                ins.offset in _target_offsets,
            )
            for ins in self.instructions
        ]

    def _prescan_try_structure(self) -> None:
        """
        Pre-scan bytecode to classify SETUP_FINALLY targets and mark finally-body instruction ranges to suppress.

        Calls the superclass prescan then records non-exception SETUP_FINALLY targets in self.prescan.finally_targets and merges or creates self.prescan.finally_body_suppress to include instruction offsets that belong to the exception-path copy of a finally body. The method detects exception-handler entries (typed or bare), with-exit handlers, and tiny cleanup-only handlers (e.g., `LOAD_CONST None` followed by delete + `RERAISE`) and treats those as exception handlers rather than user `finally:` blocks; any SETUP_FINALLY target not classified as an exception handler is treated as a user `finally` and its exception-path copy is added to self.prescan.finally_body_suppress.

        Side effects:
        - Ensures self.prescan.finally_targets is a set of offsets that are finalizer entry targets.
        - Ensures self.prescan.finally_body_suppress exists and is extended with offsets to skip when emitting the exception-path copy of finally bodies.
        """
        super()._prescan_try_structure()
        # NOTE: do NOT reset self.prescan.finally_body_suppress here — super() may have
        # populated it via the generic deferred-finally mechanism for PUSH_EXC_INFO
        # patterns (even though 3.9 uses SETUP_FINALLY, the super call is harmless
        # and we must preserve whatever it set).
        self.prescan.finally_targets = set()
        self.prescan.finally_from_handler = {}  # handler offset -> its RERAISE, see _elide_early_finally_copies
        # Merge with any suppress set from super(), don't overwrite it.

        for i, ins in enumerate(self.instructions):
            if ins.opname != "SETUP_FINALLY":
                continue
            target = self._get_jump_target(ins)
            t_idx = next(
                (idx for idx, x in enumerate(self.instructions) if x.offset == target), None
            )
            if t_idx is None:
                continue
            target_ins = self.instructions[t_idx]

            # Classify what the SETUP_FINALLY target is:
            is_except = False
            # Target is an except-handler entry (typed or bare):
            if target_ins.opname == "DUP_TOP":
                is_except = True
            elif target_ins.opname == "POP_TOP":
                # Bare-except: three consecutive POP_TOPs
                if (t_idx + 2 < len(self.instructions)
                        and self.instructions[t_idx + 1].opname == "POP_TOP"
                        and self.instructions[t_idx + 2].opname == "POP_TOP"):
                    is_except = True
            # Target is the with-block exit handler:
            elif target_ins.opname == "WITH_EXCEPT_START":
                is_except = True
            # Target is an exception-handler cleanup entry (3.9 'as e' cleanup):
            elif target_ins.opname == "POP_EXCEPT":
                is_except = True
            # Target is a tiny 'as e' cleanup block (LOAD_CONST None; STORE; DELETE; RERAISE):
            # These are nested SETUP_FINALLY blocks for the 'except X as e:' binding.
            # Recognise them by: the target is a LOAD_CONST None followed within 3 steps
            # by a DELETE_* or RERAISE (with no user body in between).
            elif target_ins.opname == "LOAD_CONST" and target_ins.argval is None:
                # Scan forward: if we hit DELETE_FAST/DELETE_NAME or RERAISE without
                # any non-trivial instruction in between, treat as cleanup-only.
                is_cleanup = False
                for k in range(t_idx, min(t_idx + _MAX_TERNARY_MINI_SEARCH_WINDOW, len(self.instructions))):
                    op = self.instructions[k].opname
                    if op in ("DELETE_FAST", "DELETE_NAME", "DELETE_GLOBAL", "DELETE_DEREF", "RERAISE"):
                        is_cleanup = True
                        break
                    elif op not in ("LOAD_CONST", "STORE_FAST", "STORE_NAME",
                                    "STORE_GLOBAL", "STORE_DEREF", "POP_BLOCK", "NOP"):
                        break
                if is_cleanup:
                    is_except = True   # treat as cleanup, not a user finally

            if not is_except:
                # Potential finally block. In 3.9, a SETUP_FINALLY for a user finally
                # typically covers the try body, and the target is the exception-path
                # copy of the finally body WHICH ENDS IN RERAISE.
                # If we find a RERAISE reachable from here without an intervening SETUP,
                # then this is definitely a finally block.
                is_finally = False
                end_reraise = None
                level = 0
                for j in range(t_idx, len(self.instructions)):
                    jx = self.instructions[j]
                    if jx.opname in ("SETUP_FINALLY", "SETUP_WITH", "SETUP_EXCEPT"):
                        level += 1
                    elif jx.opname == "POP_BLOCK":
                        level -= 1
                    elif jx.opname == "RERAISE" and level <= 0:
                        is_finally = True
                        end_reraise = jx.offset
                        break

                if is_finally:
                    self.prescan.finally_targets.add(target)
                    # Mark the exception-path copy for suppression
                    for j in range(t_idx, len(self.instructions)):
                        self.prescan.finally_body_suppress.add(self.instructions[j].offset)
                        if self.instructions[j].offset == end_reraise:
                            break
                    self._elide_early_finally_copies(i, t_idx, end_reraise)

            if is_except:
                try_end_idx = next((k for k, x in enumerate(self.instructions) if x.offset == target), -1) - 1
                if try_end_idx >= 0:
                    try_jump = self.instructions[try_end_idx]
                    if try_jump.opname in ("JUMP_FORWARD", "JUMP_ABSOLUTE"):
                        else_start = getattr(self, "_get_jump_target")(try_jump)
                        
                        # Guard: If else_start is the end of a FOR_ITER loop (indicating a `break` out of a loop),
                        # then it is NOT an else clause.
                        is_break = else_start in self.prescan.for_iter_targets
                        
                        if else_start > target and not is_break:
                            post_except_targets = []
                            for k in range(try_end_idx + 1, len(self.instructions)):
                                if self.instructions[k].offset >= else_start:
                                    break

                                inner_ins = self.instructions[k]
                                if inner_ins.opname in ("JUMP_FORWARD", "JUMP_ABSOLUTE"):
                                    jt = getattr(self, "_get_jump_target")(inner_ins)
                                    if jt > else_start:
                                        post_except_targets.append(jt)
                            if post_except_targets:
                                else_end = min(post_except_targets)
                                self.prescan.else_starts[else_start] = else_end

                # ── Populate _exc_handler_jump_offsets for 3.9 ─────────────────
                # In Python 3.9, a JUMP_ABSOLUTE inside a SETUP_FINALLY handler
                # body that jumps backward to a FOR_ITER loop head (is_jump_target)
                # represents an explicit 'continue' statement.  Mark those offsets
                # so _op_jump treats them as handler exits (not while-loop back-edges)
                # and _op_cleanup can emit 'continue' instead of 'pass'.
                # The handler body starts at `target` (DUP_TOP or POP_TOP*3).
                # Collect jump-target offsets of FOR_ITER instructions so we know
                # which backward destinations are loop heads.
                # In Python 3.9, JUMP_ABSOLUTE for a loop 'continue' jumps back
                # to the FOR_ITER instruction's own offset (not to its target).
                # So we collect FOR_ITER *offsets* as the set of valid loop heads.
                for_iter_targets = {
                    fi.offset
                    for fi in self.instructions
                    if fi.opname == "FOR_ITER" and fi.offset < target
                }
                # The handler section runs from `target` until the first
                # POP_EXCEPT or RERAISE at this nesting level (level=0).
                t_idx2 = next(
                    (k for k, x in enumerate(self.instructions) if x.offset == target), None
                )
                if t_idx2 is not None:
                    level2 = 0
                    for k in range(t_idx2, len(self.instructions)):
                        kx = self.instructions[k]
                        if kx.opname in ("SETUP_FINALLY", "SETUP_WITH", "SETUP_EXCEPT"):
                            level2 += 1
                        elif kx.opname == "POP_BLOCK":
                            level2 -= 1
                        elif kx.opname in ("POP_EXCEPT", "RERAISE") and level2 <= 0:
                            break  # handler section ends
                        # A backward JUMP_ABSOLUTE/JUMP_FORWARD to a loop head
                        # inside this handler is an explicit 'continue'.
                        if kx.opname in ("JUMP_ABSOLUTE", "JUMP_FORWARD"):
                            jt = self._get_jump_target(kx)
                            if (isinstance(jt, int)
                                    and jt < kx.offset
                                    and (jt in for_iter_targets or jt in self.prescan.while_header_targets.values())):
                                self.prescan.exc_handler_jump_offsets.add(kx.offset)
                                self.prescan.exc_handler_continue_offsets.add(kx.offset)

    def _elide_early_finally_copies(self, setup_idx: int, handler_idx: int, reraise_offset: int) -> None:
        """
        Suppress the copies of a `finally` body that the compiler inlines at early exits.

        A `continue`, `break` or `return` inside `try`/`finally` compiles to
        `POP_BLOCK`, a copy of the finally body, then the exit itself. Only the exit is
        source code (the finally body runs when the exit leaves the `try`), so the
        `POP_BLOCK` and the copy are suppressed and the exit renders as the plain
        statement it is. The copy on the normal path, which ends right at the
        exception-path copy, is the one that becomes the `finally:` block and is kept.

        Parameters:
            setup_idx (int): Index of the SETUP_FINALLY that opens the protected region.
            handler_idx (int): Index of the first instruction of the exception-path copy.
            reraise_offset (int): Offset of the RERAISE that ends the exception-path copy.
        """
        instrs = self.instructions
        reraise_idx = next((k for k in range(handler_idx, len(instrs)) if instrs[k].offset == reraise_offset), None)
        if reraise_idx is None:
            return
        template = _copy_signature(instrs[handler_idx:reraise_idx])
        if not template and any(
                instrs[k].opname in ("SETUP_FINALLY", "SETUP_WITH", "SETUP_EXCEPT")
                for k in range(setup_idx + 1, handler_idx)):
            # An empty finally body leaves nothing to tell its POP_BLOCK from a nested
            # statement's, so only a try body without nested blocks can be handled.
            return
        handler_offset = instrs[handler_idx].offset
        normal_path = False
        for k in range(setup_idx + 1, handler_idx):
            if instrs[k].opname != "POP_BLOCK":
                continue
            end = _match_finally_copy(instrs, k + 1, template)
            if end is None or end >= len(instrs):
                continue
            tail = end + 1 if instrs[end].opname in ("JUMP_ABSOLUTE", "JUMP_FORWARD") else end
            if tail < len(instrs) and instrs[tail].offset == handler_offset:
                # The normal path: this copy is the `finally:` body. The jump after it is
                # the loop's own back-edge, not a `continue` written inside the finally.
                if tail > end and instrs[end].opname == "JUMP_ABSOLUTE" and instrs[end].argval < instrs[end].offset:
                    self.prescan.finally_body_suppress.add(instrs[end].offset)
                normal_path = True
                continue
            # Leaving several nested blocks pops them all, one POP_BLOCK each.
            first = k
            while first - 1 > setup_idx and instrs[first - 1].opname == "POP_BLOCK":
                first -= 1
            for idx in range(first, end):
                self.prescan.finally_body_suppress.add(instrs[idx].offset)
        if not normal_path and template:
            # Every way out of the try body is a return, raise, break or continue, so
            # there is no normal-path copy to become the `finally:` block: the copy in
            # the exception handler is the only one left, and it is rendered in place.
            for idx in range(handler_idx, reraise_idx):
                self.prescan.finally_body_suppress.discard(instrs[idx].offset)
            self.prescan.finally_from_handler[handler_offset] = instrs[reraise_idx].offset

    def _open_finally_from_handler(self, instr: BytecodeInstruction) -> None:
        """Emit `finally:` at the handler copy of a finally body that has no normal-path copy."""
        end = self.prescan.finally_from_handler.pop(instr.offset)
        if self.emit.except_header_indents:
            self.indent_level = self.emit.except_header_indents[-1]
        self._append_reconstructed("finally:")
        self.indent_level += 1
        # The RERAISE that ends the copy closes the block (and is skipped as usual).
        self.blocks.append((end, "finally_body"))

    # clean instruction dispatch for 3.9-specific opcodes
    def _dispatch_instruction(self, instr: BytecodeInstruction):
        # Suppress exception-path finally bodies
        """
        Process a single disassembled instruction and update the decompiler's internal state and reconstructed output accordingly.

        This method dispatches on the instruction's opname to mutate the expression stack, emit or suppress reconstructed source lines, open/close control-flow blocks, adjust indentation and block stacks, modify prescan/suppression tables, and advance or adjust the program counter as needed. It implements numerous 3.9-specific and generic bytecode-to-source translations (for example: binary/inplace ops, comparisons, various CALL/CALL_* forms including class and decorator patterns, while-loop guards and while True headers, unpacking/call/attribute/method patterns, and try/except/finally handler and with/cleanup machinery). The handler delegates to the superclass for opcodes it does not special-case.

        Parameters:
            instr (BytecodeInstruction): The instruction to handle; uses fields such as opname, arg, argval, offset, and is_jump_target.
        """
        if instr.offset in self.prescan.finally_from_handler:
            self._open_finally_from_handler(instr)
        if self.prescan.finally_body_suppress and instr.offset in self.prescan.finally_body_suppress:
            return

        opname = instr.opname

        # Scoped suppression: clear except-zone state when we exit the handler scope.
        if self.emit.except_end_offsets and instr.offset >= self.emit.except_end_offsets[-1]:
            self.emit.except_end_offsets.pop()

        if instr.offset in self.prescan.else_starts:
            self._append_reconstructed("else:")
            self.indent_level += 1
            self.blocks.append((self.prescan.else_starts[instr.offset], "else"))

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

        # while-True header: if this instruction is the body_start of a while-True
        # loop (sentinel guard_offset == -1), emit 'while True:' once and push a
        # while block covering the loop back-edge.
        # This fires on the very first instruction of the loop body.
        _while_header_targets = self.prescan.while_header_targets
        _while_true_body_starts = {
            bs for bs, go in _while_header_targets.items() if go == -1
        }
        if instr.offset in _while_true_body_starts:
            self._append_reconstructed("while True:")
            self.indent_level += 1
            # Find the loop end: offset just after the last backward jump that
            # targets this body_start.
            loop_end = instr.offset + 2
            for ins in self.instructions:
                if (self._is_backward_instruction(ins)
                        and self._get_jump_target(ins) == instr.offset):
                    loop_end = max(loop_end, ins.offset + 2)
            self.blocks.append((loop_end, "while"))
            _wte = self.prescan.while_true_ends
            _wte.add(loop_end)
            self.prescan.while_true_ends = _wte

        # POP_JUMP_IF_FALSE/TRUE with a BACKWARD target (target <= current offset):
        # In a 3.9 while-True loop, this is the inner 'if cond: break' guard.
        # The bytecode says "if the condition is FALSE, jump back to start" which is
        # the NOT-break path (continue the loop).  The break path is a JUMP_ABSOLUTE
        # forward.  Emit 'if <cond>:' (the break body is the following JUMP_ABSOLUTE).
        if ("POP_JUMP_IF_FALSE" in opname or "POP_JUMP_IF_TRUE" in opname):
            jump_target = self._get_jump_target(instr)

            # --- Assertion detection (Python 3.9) ---
            # Pattern: POP_JUMP_IF_TRUE/FALSE skipping over a RAISE_VARARGS(AssertionError)
            # Find the instruction just BEFORE the jump target
            target_idx = next((i for i, x in enumerate(self.instructions) if x.offset == jump_target), -1)
            if target_idx > 0:
                raise_instr = self.instructions[target_idx - 1]
                if raise_instr.opname == "RAISE_VARARGS" and raise_instr.arg == 1:
                    # Check if stack has AssertionError (usually 2rd or 3th item before raise)
                    # For simplicity, we just check if it's a likely candidate path
                    # 3.9 assert pattern: if (not) cond: raise AssertionError(msg)
                    if self.stack:
                        cond = self.stack.pop()
                        # We'll emit as a generic if for now if we can't be sure,
                        # but here we can try to be bold.
                        prefix = "assert " if "FALSE" in opname else "assert not "
                        # Peek for the message
                        msg = None
                        # This is a bit complex for a single handler, so we'll let
                        # DecompilerGeneric handle it as a block if we return.
                        # Actually, let's keep it simple and just fix the basic emission.
                        self.stack.append(cond)  # Put it back and let super() or generic handle it

            if (jump_target <= instr.offset  # backward target → inside while-True
                    and instr.offset not in self.prescan.while_header_targets.values()
                    and any(b[1] == "while" for b in self.blocks)):
                # This is the break-guard inside a while-True.
                if self.stack:
                    cond = self.stack.pop()
                    if str(cond) not in ("_exc_match", "_exc_info"):
                        is_true = "IF_TRUE" in opname
                        if is_true:
                            self._append_reconstructed(f"if not {cond}:")
                        else:
                            self._append_reconstructed(f"if {cond}:")
                        self.indent_level += 1
                        while_end = next(
                            (b[0] for b in reversed(self.blocks) if b[1] == "while"), -1
                        )
                        if while_end > instr.offset:
                            self.blocks.append((while_end, "if"))
                return

            # Pattern for 3.9 asserts: if not cond: raise AssertionError
            # We skip explicit emit here and let DecompilerGeneric._op_pop_jump_if_false handle it
            # if it's not a while-break. This prevents double-emission or mis-indentation.
            super()._dispatch_instruction(instr)
            return

        if opname in _bin39:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                # Wrap the right operand in parens when it contains a ternary
                # expression (e.g. 'x if cond else y') to prevent operator-
                # precedence ambiguity (% binds tighter than if...else).
                r_str = str(right)
                if ' if ' in r_str and ' else ' in r_str:
                    r_str = f'({r_str})'
                # Wrap the left operand in parens when it contains a ternary
                l_str = str(left)
                if ' if ' in l_str and ' else ' in l_str:
                    l_str = f'({l_str})'
                self.stack.append(f"({l_str} {_bin39[opname]} {r_str})")

        elif opname in _inplace39:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                op = _inplace39[opname]
                # Peek ahead: if next instruction is a STORE back to the same
                # name, emit `left op= right` directly and consume the STORE.
                next_pc = self.pc
                while (next_pc < len(self.instructions)
                       and self.instructions[next_pc].opname in (
                           "CACHE", "RESUME", "NOP", "NOT_TAKEN", "ROT_TWO", "ROT_THREE"
                )):
                    next_pc += 1
                
                if (next_pc < len(self.instructions)
                        and self.instructions[next_pc].opname in (
                            "STORE_NAME", "STORE_FAST", "STORE_GLOBAL", "STORE_ATTR", "STORE_DEREF", "STORE_SUBSCR"
                )):
                    instr_store = self.instructions[next_pc]
                    if self._match_augassign_store(str(left), instr_store):
                        if instr_store.opname == "STORE_ATTR" and self.stack:
                            self.stack.pop()  # pop defensive receiver
                        if instr_store.opname == "STORE_SUBSCR" and len(self.stack) >= 2:
                            self.stack.pop()
                            self.stack.pop()
                        self._append_reconstructed(f"{left} {op} {right}")
                        self.pc = next_pc + 1  # consume the STORE
                        return
                # Fallback: push as expression for STORE to handle
                # Wrap operands in parens when they contain a ternary
                r_str = str(right)
                if ' if ' in r_str and ' else ' in r_str:
                    r_str = f'({r_str})'
                l_str = str(left)
                if ' if ' in l_str and ' else ' in l_str:
                    l_str = f'({l_str})'
                self.stack.append(f"({l_str} {op} {r_str})")

        elif opname == "COMPARE_OP":
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                op = str(instr.argval)
                # No outer parens: tests expect 'if x > 0:' not 'if (x > 0):'
                self.stack.append(f"{left} {op} {right}")

        # POP_BLOCK: marks the clean exit from a try body in Python 3.9.
        # Close the try_body block and record the indent level for except headers.
        elif opname == "POP_BLOCK":
            is_finally_pop = False
            finally_target = None
            _closing_exc_cleanup = False

            # `continue` inside a try body: POP_BLOCK leaves the try, then the jump
            # re-enters the loop. It closes nothing; the jump emits the `continue`.
            if self.pc < len(self.instructions):
                _nxt = self.instructions[self.pc]
                if (_nxt.opname == "JUMP_ABSOLUTE" and _nxt.offset in self.prescan.inner_back_jumps
                        and any(b[1] == "try_body" for b in self.blocks)):
                    return

            # ── Early-exit: for-loop break inside if-within-try (Python 3.9) ──────
            # Pattern:
            #   for x in ...:
            #       try:
            #           if cond:
            #               ...
            #               break           ← POP_BLOCK fires here with 'if' on top
            #   POP_BLOCK  offset N         (top block = 'if', try_body below)
            #   POP_TOP    offset N+2       (pops the for-iterator off the stack)
            #   JUMP_ABSOLUTE for_iter_end  (the actual break jump)
            #   ...                         (NOPs / dead code)
            #   POP_BLOCK  offset M         (natural-continue path, if-false target) ← suppress
            #   JUMP_ABSOLUTE for_head      (natural-continue back-edge)            ← suppress
            #
            # This POP_BLOCK is NOT the try_body-exit POP_BLOCK – that one fires
            # at offset M (after the if-false fall-through).  We must emit 'break'
            # NOW (before the if block is closed) and suppress all the implementation
            # detail instructions, then return EARLY so the unconditional
            # _except_header_indents.append() at the bottom of this branch is NOT
            # reached (which would create a spurious ghost handler entry).
            if (self.blocks
                    and self.blocks[-1][1] == "if"
                    and any(b[1] == "try_body" for b in self.blocks)):
                look = self.pc
                while (look < len(self.instructions)
                       and self.instructions[look].opname in ("RESUME", "NOP", "CACHE")):
                    look += 1
                if (look < len(self.instructions)
                        and self.instructions[look].opname == "POP_TOP"):
                    look2 = look + 1
                    while (look2 < len(self.instructions)
                           and self.instructions[look2].opname in ("RESUME", "NOP", "CACHE")):
                        look2 += 1
                    if (look2 < len(self.instructions)
                            and self.instructions[look2].opname == "JUMP_ABSOLUTE"):
                        jmp = self.instructions[look2]
                        jmp_target = self._get_jump_target(jmp)
                        # Locate the nearest FOR_ITER whose end is beyond this instr.
                        for_iter_end = -1
                        for fi in self.instructions:
                            if fi.opname == "FOR_ITER":
                                t = self._get_jump_target(fi)
                                if isinstance(t, int) and t > instr.offset:
                                    if for_iter_end < 0 or t < for_iter_end:
                                        for_iter_end = t
                        if (isinstance(jmp_target, int)
                                and jmp_target > instr.offset
                                and for_iter_end > 0
                                and jmp_target >= for_iter_end):
                            # Emit break at current indentation (inside the if body).
                            self._append_reconstructed("break")
                            # Suppress break-path implementation instructions.
                            _fb = self.prescan.finally_body_suppress
                            _fb.add(self.instructions[look].offset)   # POP_TOP
                            _fb.add(jmp.offset)                        # JUMP_ABSOLUTE(break)
                            self.prescan.finally_body_suppress = _fb
                            # Return early: do NOT pop the 'if' block.
                            # The natural POP_BLOCK will follow and handle the try_body exit appropriately.
                            return

            _top_is_try = (self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup"))
            _inner_try = (
                self.blocks
                and self.blocks[-1][1] in ("for", "if", "while")
                and any(b[1] in ("try_body", "exc_cleanup") for b in self.blocks[:-1])
            )
            if _top_is_try or _inner_try:
                # ── Python 3.9 early-exit return from for-inside-try ───────────
                # Pattern:
                #   48 POP_TOP            ← pop DUP_TOP from chained comparison
                #   50 POP_BLOCK          ← THIS (VM cleanup for early-exit)
                #   52 LOAD_CONST False   ← return value loads
                #   54 RETURN_VALUE       ← actual return
                #   56 JUMP_ABSOLUTE 14   ← dead code
                #   58 POP_BLOCK          ← structural try_body exit (jump_target=True)
                # Strategy:
                #  (a) Emit `return <val>` at CURRENT indent (inside if body)
                #  (b) Suppress 52..56 from dispatch (push to _finally_body_suppress)
                #  (c) Return early; structural POP_BLOCK at 58 fires correctly.
                look = self.pc
                ret_idx = -1
                while look < len(self.instructions):
                    op = self.instructions[look].opname
                    if op in ("RETURN_VALUE", "RETURN_CONST"):
                        ret_idx = look
                        break
                    # Skip over ops that build the return expression
                    if ("LOAD_" in op or op in
                            ("POP_TOP", "RESUME", "NOP", "CACHE", "DUP_TOP", "ROT_THREE")):
                        look += 1
                    else:
                        break

                if ret_idx >= 0:
                    ret_instr = self.instructions[ret_idx]
                    if ret_instr.opname == "RETURN_CONST":
                        ret_val = self._format_val(ret_instr.argval)
                    else:
                        # The LOAD instructions between here and RETURN_VALUE
                        # haven't been dispatched yet — evaluate them into a
                        # temporary mini-stack to get the actual return value.
                        mini_stack = list(self.stack)  # copy current state
                        for mi in range(self.pc, ret_idx):
                            mi_instr = self.instructions[mi]
                            mi_op = mi_instr.opname
                            if mi_op in ("RESUME", "NOP", "CACHE", "POP_TOP"):
                                if mini_stack and mi_op == "POP_TOP":
                                    mini_stack.pop()
                            elif "LOAD_CONST" == mi_op:
                                mini_stack.append(self._format_val(mi_instr.argval))
                            elif "LOAD_" in mi_op:
                                mini_stack.append(str(mi_instr.argval))
                        ret_val = str(mini_stack[-1]) if mini_stack else "None"

                    is_compiler_gen = (ret_val == "None"
                                       and self.is_compiler_generated_return(ret_idx))
                    if not is_compiler_gen:
                        # If the last emitted line is an unclosed block header
                        # (e.g. 'if cond:') that never got a body, emit the
                        # return at one extra indent level to place it inside that block.
                        last_real = -1
                        for li in range(len(self.reconstructed) - 1, -1, -1):
                            if self.reconstructed[li].strip():
                                last_real = li
                                break
                        last_line = self.reconstructed[last_real].strip() if last_real >= 0 else ""
                        extra_indent = 0
                        if last_line.endswith(":") and last_line.startswith(("if ", "elif ", "else:", "while ")):
                            extra_indent = 1

                        saved_indent = self.indent_level
                        self.indent_level += extra_indent
                        self._append_reconstructed(f"return {ret_val}")
                        self.indent_level = saved_indent

                    # Suppress all instructions from pc up to ret_instr
                    # plus dead code until the next jump target.
                    _fb = self.prescan.finally_body_suppress
                    sup = self.pc
                    while sup < len(self.instructions):
                        ins_s = self.instructions[sup]
                        _fb.add(ins_s.offset)
                        sup += 1
                        if ins_s.offset == ret_instr.offset:
                            while (sup < len(self.instructions)
                                   and not self.instructions[sup].is_jump_target):
                                _fb.add(self.instructions[sup].offset)
                                sup += 1
                            break
                    self.prescan.finally_body_suppress = _fb
                    return

                # Peek ahead for break/loop patterns (existing logic)
                look = self.pc
                while (look < len(self.instructions)
                       and self.instructions[look].opname in ("RESUME", "NOP", "CACHE")):
                    look += 1
                if look < len(self.instructions):
                    nxt = self.instructions[look]
                    if nxt.opname == "JUMP_ABSOLUTE":
                        nxt_target = self._get_jump_target(nxt)
                        # Is this JUMP_ABSOLUTE a break to the while-end?
                        _while_end = next(
                            (b[0] for b in reversed(self.blocks) if b[1] == "while"), -1
                        )
                        if nxt_target > instr.offset and nxt_target == _while_end:
                            # Emit break while still inside the try body
                            self._append_reconstructed("break")
                            # Advance past the JUMP_ABSOLUTE
                            self.pc = look + 1
                            # Suppress the dead-code POP_BLOCK + JUMP_ABSOLUTE backward
                            # that follows (unreachable fallthrough path)
                            sup = self.pc
                            while (sup < len(self.instructions)
                                   and self.instructions[sup].opname in ("RESUME", "NOP", "CACHE")):
                                sup += 1
                            if (sup < len(self.instructions)
                                    and self.instructions[sup].opname == "POP_BLOCK"):
                                _fb = self.prescan.finally_body_suppress
                                _fb.add(self.instructions[sup].offset)
                                sup += 1
                                while (sup < len(self.instructions)
                                       and self.instructions[sup].opname in ("RESUME", "NOP", "CACHE")):
                                    sup += 1
                                if (sup < len(self.instructions)
                                        and self.instructions[sup].opname == "JUMP_ABSOLUTE"):
                                    _fb.add(self.instructions[sup].offset)
                                
                                dead = sup + 1
                                while dead < len(self.instructions) and not self.instructions[dead].is_jump_target:
                                    _fb.add(self.instructions[dead].offset)
                                    dead += 1

                                self.prescan.finally_body_suppress = _fb
                                # Fall through to pop the try_body block

                    # Python 3.9 for-loop break inside try: the break path is
                    # POP_BLOCK -> POP_TOP -> JUMP_ABSOLUTE(past FOR_ITER end).
                    # The POP_TOP pops the for-iterator off the operand stack.
                    elif nxt.opname == "POP_TOP":
                        look2 = look + 1
                        while (look2 < len(self.instructions)
                               and self.instructions[look2].opname in ("RESUME", "NOP", "CACHE")):
                            look2 += 1
                        if (look2 < len(self.instructions)
                                and self.instructions[look2].opname == "JUMP_ABSOLUTE"):
                            jmp = self.instructions[look2]
                            jmp_target = self._get_jump_target(jmp)
                            # Confirm this is a forward break jump that lands beyond or
                            # at the enclosing FOR_ITER end (i.e. exits the for loop).
                            _for_end = next(
                                (b[0] for b in reversed(self.blocks) if b[1] == "try_body"), -1
                            )
                            for_iter_end = -1
                            for fi in self.instructions:
                                if fi.opname == "FOR_ITER":
                                    t = self._get_jump_target(fi)
                                    if isinstance(t, int) and t > instr.offset:
                                        if for_iter_end < 0 or t < for_iter_end:
                                            for_iter_end = t
                            if (isinstance(jmp_target, int)
                                    and jmp_target > instr.offset
                                    and for_iter_end > 0
                                    and jmp_target >= for_iter_end):
                                # Emit break while still inside the try body
                                self._append_reconstructed("break")
                                # Suppress POP_TOP and JUMP_ABSOLUTE (they are implementation
                                # details of breaking out of a for-loop-within-try)
                                _fb = self.prescan.finally_body_suppress
                                _fb.add(nxt.offset)                    # POP_TOP
                                _fb.add(jmp.offset)                    # JUMP_ABSOLUTE
                                
                                dead = look2 + 1
                                while dead < len(self.instructions) and not self.instructions[dead].is_jump_target:
                                    _fb.add(self.instructions[dead].offset)
                                    dead += 1

                                self.prescan.finally_body_suppress = _fb
                                self.pc = look2 + 1
                                # Do NOT return here! This POP_BLOCK is the structural exit of the try_body
                                # for this execution path. Fall through to pop the block.

                finally_target = self.blocks[-1][0]
                _finally_ts = self.prescan.finally_targets
                if _finally_ts and finally_target in _finally_ts:
                    is_finally_pop = True

                # Check for 3.9 implicit finally (SETUP_FINALLY target reachable after POP_BLOCK fallthrough)
                if not is_finally_pop:
                    look = self.pc
                    while look < len(self.instructions) and self.instructions[look].opname in ("NOP", "RESUME", "CACHE"):
                        look += 1
                    if look < len(self.instructions) and self.instructions[look].offset == finally_target:
                        # Guard: if the target instruction looks like an except-handler entry
                        # (DUP_TOP for typed except, three POP_TOPs for bare except,
                        # or WITH_EXCEPT_START), this is NOT a finally block — it's the
                        # except clause that coincidentally lands right after POP_BLOCK.
                        target_op = self.instructions[look].opname
                        is_handler_entry = (
                            target_op == "DUP_TOP"
                            or target_op == "WITH_EXCEPT_START"
                            or (target_op == "POP_TOP"
                                and look + 2 < len(self.instructions)
                                and self.instructions[look + 1].opname == "POP_TOP"
                                and self.instructions[look + 2].opname == "POP_TOP")
                        )
                        if not is_handler_entry:
                            is_finally_pop = True

                _closing_exc_cleanup = self.blocks[-1][1] == "exc_cleanup"

                # ── Empty try body: emit `pass` if needed ────────────────────
                # Python 3.9 compiles `try: pass` to SETUP_FINALLY → POP_BLOCK with
                # no intervening instructions. The decompiler has opened the block
                # (emitted "try:") but never emitted a body line — the output would
                # be a bare "try:" with no body, which is a SyntaxError. Detect this
                # by checking whether the last non-blank reconstructed line is still
                # a block-opening header and we are about to close the block.
                if not _closing_exc_cleanup:
                    last_non_blank = next(
                        (self.reconstructed[li] for li in range(len(self.reconstructed) - 1, -1, -1)
                         if self.reconstructed[li].strip()),
                        ""
                    ).strip()
                    # Block headers that need at least one body statement
                    _BLOCK_HEADERS = ("try:", "if ", "elif ", "else:", "for ", "while ", "finally:", "with ")
                    if last_non_blank.endswith(":") and any(last_non_blank.startswith(h) for h in _BLOCK_HEADERS):
                        self._append_reconstructed("pass")

                self.blocks.pop()
                if not _closing_exc_cleanup:
                    self.indent_level -= 1


            elif self.blocks and self.blocks[-1][1] == "finally_wrapper":
                finally_target = self.blocks[-1][0]
                is_finally_pop = True
                self.blocks.pop()

            # Record the indent level where except/finally headers should appear.
            # In nested scenarios, each try body's POP_BLOCK records its parent's indent.
            # The `except X as e:` cleanup guard is not a try body: no header follows
            # it, and recording the handler body's indent there put a later `finally:`
            # inside the except clause.
            if _closing_exc_cleanup:
                return
            self.emit.except_header_indents.append(self.indent_level)
            # Peek ahead for JUMP_FORWARD at end of try block to establish scope.
            look = self.pc
            while (look < len(self.instructions) and
                   self.instructions[look].opname in ("RESUME", "NOP", "CACHE")):
                look += 1
            if look < len(self.instructions) and self.instructions[look].opname == "JUMP_FORWARD":
                self.emit.except_end_offsets.append(self._get_jump_target(self.instructions[look]))

            if is_finally_pop:
                # establish the correct indent for finally: header
                if self.emit.except_header_indents:
                    self.indent_level = self.emit.except_header_indents[-1]

                self._append_reconstructed("finally:")
                self.indent_level += 1
                self.blocks.append((finally_target, "finally_body"))
                self.emit.except_header_indents.pop()

        # JUMP_ABSOLUTE: in 3.9 this is used both as:
        #   (a) a loop back-edge (target <= current offset) — treat as JUMP_BACKWARD
        #   (b) a forward jump at end of except/if body — treat as JUMP_FORWARD
        #   (c) break: forward jump to while-end from inside a try: block
        elif opname == "JUMP_ABSOLUTE":
            jump_target = self._get_jump_target(instr)
            if jump_target <= instr.offset:
                # Backward JUMP_ABSOLUTE: delegate to the backward-jump handler
                # in super(). _is_backward_instruction returns True for this case.
                #
                # Special case: if this backward jump targets a FOR_ITER AND is
                # NOT itself a jump target (meaning it's unreachable dead code
                # after a try-body POP_BLOCK for the clean-exit path), suppress it.
                # These are CPython 3.9's implicit for-loop back-edges — not user
                # `continue` statements — and emitting them causes `continue` to
                # appear outside the `try` block, producing SyntaxError.
                body_start = jump_target
                body_instr = next(
                    (ins for ins in self.instructions if ins.offset == body_start), None
                )
                _exc_handler_jumps = self.prescan.exc_handler_jump_offsets
                # Find the previous instruction in bytecode order
                prev_instr = None
                for _pi, _ins in enumerate(self.instructions):
                    if _ins.offset == instr.offset and _pi > 0:
                        prev_instr = self.instructions[_pi - 1]
                        break
                if (body_instr
                        and body_instr.opname == "FOR_ITER"
                        and not instr.is_jump_target
                        and self.emit.except_header_indents
                        and instr.offset not in _exc_handler_jumps
                        and instr.offset not in self.prescan.inner_back_jumps  # a real `continue`
                        and prev_instr is not None
                        and prev_instr.opname == "POP_BLOCK"):
                    # Implicit for-loop continuation after try clean exit — suppress.
                    return
                super()._dispatch_instruction(instr)
            else:
                # Check for break: forward jump whose target matches the enclosing
                # while-loop end.  This must be tested BEFORE the _except_header_indents
                # suppression, because break inside try: still sets _except_header_indents
                # via POP_BLOCK, but the jump is semantically a break not an else:
                _is_break = False
                for _b_off, _b_type in reversed(self.blocks):
                    if _b_type == "while" and jump_target == _b_off:
                        _is_break = True
                        break
                    if _b_type == "while":
                        break  # innermost while has a different end
                if _is_break:
                    self._append_reconstructed("break")
                    return

                # Forward JUMP_ABSOLUTE: in try/except context, suppress else-detection
                # (same as JUMP_FORWARD suppression below).
                if self.emit.except_header_indents:
                    pass  # skip else-detection inside try/except context
                else:
                    fwd_instr = BytecodeInstruction(
                        instr.opcode, "JUMP_FORWARD",
                        instr.arg, instr.argval,
                        instr.offset, instr.starts_line, instr.is_jump_target,
                    )
                    super()._dispatch_instruction(fwd_instr)

        # JUMP_FORWARD: in try/except context (after POP_BLOCK) suppress the
        # if/else detection logic in the parent handler — it would create spurious
        # else blocks around the exception handler body.
        elif opname == "JUMP_FORWARD":
            if self.emit.except_header_indents:
                pass  # suppress else-detection inside try/except context
            else:
                super()._dispatch_instruction(instr)

        # DUP_TOP — Python 3.9 typed/bare except handler entry.
        # Real 3.9.13 bytecode layout for typed except (from dis_out_39.txt):
        #   DUP_TOP
        #   LOAD_NAME ExcType
        #   JUMP_IF_NOT_EXC_MATCH reraise_offset
        #   POP_TOP   } stack holds (exc_type, exc_value, traceback)
        #   POP_TOP   } discard all three
        #   POP_TOP   }
        #   [STORE_NAME e]  ← only present for 'except X as e:'
        #   <handler body>
        # For bare except, DUP_TOP is followed immediately by the handler body
        # (no LOAD_NAME/type-check sequence).
        elif opname == "DUP_TOP":
            look = self.pc
            while look < len(self.instructions) and self.instructions[look].opname in (
                "RESUME", "NOP", "CACHE"
            ):
                look += 1
            # --- Typed except: LOAD_NAME ExcType (or dotted LOAD_ATTR chain) follows ---
            if (look < len(self.instructions)
                    and self.instructions[look].opname in (
                        "LOAD_NAME", "LOAD_GLOBAL", "LOAD_FAST",
                        "LOAD_DEREF", "LOAD_ATTR",
            )):
                exc_types = [str(self.instructions[look].argval)]
                look += 1
                # Follow any LOAD_ATTR chain for dotted exception names like socket.error
                while (look < len(self.instructions)
                       and self.instructions[look].opname == "LOAD_ATTR"):
                    exc_types[-1] = f"{exc_types[-1]}.{self.instructions[look].argval}"
                    look += 1
                # Collect additional exception types in a tuple (except E1, E2, E3):
                # Pattern: LOAD E1; LOAD E2; ...; BUILD_TUPLE N; JUMP_IF_NOT_EXC_MATCH
                while (look < len(self.instructions)
                       and self.instructions[look].opname in (
                           "LOAD_NAME", "LOAD_GLOBAL", "LOAD_FAST",
                           "LOAD_DEREF", "LOAD_ATTR",
                )):
                    exc_types.append(str(self.instructions[look].argval))
                    look += 1
                    while (look < len(self.instructions)
                           and self.instructions[look].opname == "LOAD_ATTR"):
                        exc_types[-1] = f"{exc_types[-1]}.{self.instructions[look].argval}"
                        look += 1
                # Skip BUILD_TUPLE if present
                if (look < len(self.instructions)
                        and self.instructions[look].opname == "BUILD_TUPLE"):
                    look += 1
                # Format the exception type string
                if len(exc_types) == 1:
                    exc_type = exc_types[0]
                else:
                    exc_type = f"({', '.join(exc_types)})"
                # 3.9 type-match gate: JUMP_IF_NOT_EXC_MATCH or COMPARE_OP
                is_exc_match = (
                    look < len(self.instructions)
                    and self.instructions[look].opname in (
                        "JUMP_IF_NOT_EXC_MATCH", "COMPARE_OP"
                    )
                    and (
                        self.instructions[look].opname == "JUMP_IF_NOT_EXC_MATCH"
                        or str(self.instructions[look].argval) == "exception match"
                    )
                )
                if is_exc_match:
                    look += 1  # skip the gate
                    # Skip any residual POP_JUMP_IF_FALSE (legacy COMPARE_OP path)
                    while (look < len(self.instructions)
                           and "POP_JUMP_IF_FALSE" in self.instructions[look].opname):
                        look += 1
                    # Skip ALL consecutive POP_TOPs (exc_type, exc_value, traceback).
                    # Stop early if a STORE_NAME immediately follows a POP_TOP
                    # — that STORE_NAME is the 'as e' binding and must NOT be skipped.
                    as_name = None
                    while (look < len(self.instructions)
                           and self.instructions[look].opname == "POP_TOP"):
                        # Peek one ahead: if the very next instruction is STORE_NAME/FAST,
                        # the current POP_TOP discards the exc_type result (not the
                        # binding), so we skip this POP_TOP and then pick up STORE_NAME.
                        nxt = look + 1
                        if (nxt < len(self.instructions)
                                and self.instructions[nxt].opname in (
                                    "STORE_NAME", "STORE_FAST", "STORE_DEREF", "STORE_GLOBAL"
                        )):
                            look += 1  # skip this POP_TOP
                            break      # next iteration: handle STORE_NAME below
                        look += 1  # skip POP_TOP with no binding after it
                    # Now check for 'as e' STORE_NAME
                    if (look < len(self.instructions)
                            and self.instructions[look].opname in (
                                "STORE_NAME", "STORE_FAST", "STORE_DEREF", "STORE_GLOBAL"
                    )):
                        as_name = str(self.instructions[look].argval)
                        self.emit.exc_as_store_offset = self.instructions[look].offset
                        self._record_exc_cleanup_offsets(look, as_name)
                        self.emit.exc_bound_names.add(as_name)
                        look += 1

                    # Close any still-open try_body block
                    if self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup") and self.blocks[-1][0] == instr.offset:
                        self.blocks.pop()
                        self.indent_level -= 1

                    # Reset indent to except-header level
                    if self.emit.except_header_indents:
                        self.indent_level = self.emit.except_header_indents[-1]
                    else:
                        self.emit.except_header_indents.append(self.indent_level)

                    if as_name:
                        self._append_reconstructed(f"except {exc_type} as {as_name}:")
                    else:
                        self._append_reconstructed(f"except {exc_type}:")
                    self.indent_level += 1
                    self.stack.append("_exc_match")
                    self.pc = look
                    return
            # --- Bare except: DUP_TOP not followed by a LOAD (no type check) ---
            # In this case DUP_TOP is just the handler entry for a bare 'except:'.
            if self.emit.except_header_indents or (
                self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup") and self.blocks[-1][0] == instr.offset
            ):
                if self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup") and self.blocks[-1][0] == instr.offset:
                    self.blocks.pop()
                    self.indent_level -= 1
                if self.emit.except_header_indents:
                    self.indent_level = self.emit.except_header_indents[-1]
                else:
                    self.emit.except_header_indents.append(self.indent_level)
                self._append_reconstructed("except:")
                self.indent_level += 1
                self.stack.append("_exc_match")
                return
            # Not an exception-match pattern — real DUP_TOP: duplicate TOS
            if self.stack:
                self._duplicate_top()

        # JUMP_IF_NOT_EXC_MATCH: 3.9/3.10 typed-except gate.
        # This fires when DUP_TOP already consumed the LOAD+JUMP_IF_NOT_EXC_MATCH
        # sequence and advanced pc past it (pc = look after the gate).
        # If it arrives here, it means the DUP_TOP pattern didn't fire
        # (shouldn't happen in well-formed 3.9 bytecode, but handle gracefully).
        elif opname == "JUMP_IF_NOT_EXC_MATCH":
            exc_type = str(self._pop())
            if self.stack:
                self.stack.pop()  # exc_instance
            if self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup") and self.blocks[-1][0] == instr.offset:
                self.blocks.pop()
                self.indent_level -= 1
            if self.emit.except_header_indents:
                self.indent_level = self.emit.except_header_indents[-1]
            self._append_reconstructed(f"except {exc_type}:")
            self.indent_level += 1
            self.stack.append("_exc_match")

        # SETUP_FINALLY inside an except handler: Python 3.9 wraps the 'as e'
        # cleanup in a nested SETUP_FINALLY to guarantee 'e = None; del e' runs
        # on both normal and reraise paths.  We must NOT emit a second 'try:' here.
        # ALSO: when the target is a finally-block offset AND there's an immediately
        # following inner SETUP_FINALLY (the real try: body), this is a silent outer
        # wrapper.  For plain try/finally (no except), skip this path and use super().
        elif opname in ("SETUP_FINALLY", "SETUP_EXCEPT"):
            jump_target = self._get_jump_target(instr)
            if self.emit.except_header_indents:
                # Inside an except handler: check whether the jump target is a real
                # handler entry (DUP_TOP for typed, POP-POP-POP for bare except,
                # or a finally: target) = genuine nested try:, or cleanup machinery.
                target_idx = next(
                    (i for i, x in enumerate(self.instructions) if x.offset == jump_target), -1
                )
                is_nested_handler = False
                if target_idx >= 0:
                    target_ins = self.instructions[target_idx]
                    if target_ins.opname == "DUP_TOP":
                        is_nested_handler = True
                    elif (target_ins.opname == "POP_TOP"
                          and target_idx + 2 < len(self.instructions)
                          and self.instructions[target_idx + 1].opname == "POP_TOP"
                          and self.instructions[target_idx + 2].opname == "POP_TOP"):
                        is_nested_handler = True
                    elif jump_target in self.prescan.finally_targets:
                        is_nested_handler = True

                if is_nested_handler:
                    # Real nested try: inside an except — emit try: normally.
                    super()._dispatch_instruction(instr)
                else:
                    # 'as e' cleanup guard — silent, track boundary without try:.
                    self.blocks.append((jump_target, "exc_cleanup"))
            elif jump_target in self.prescan.finally_targets:
                # Check if the very next real instruction is another SETUP_FINALLY.
                # If so: this is the outer wrapper, inner will emit try:
                # If not: this is a plain try/finally — let super() emit try: normally.
                look = self.pc

                while look < len(self.instructions) and self.instructions[look].opname in _GENERIC_SKIP_OPS:
                    look += 1
                next_is_setup = (
                    look < len(self.instructions)
                    and self.instructions[look].opname in ("SETUP_FINALLY", "SETUP_EXCEPT")
                )
                if next_is_setup:
                    # Outer wrapper only — inner SETUP_FINALLY will emit try:
                    # Save current indent level so POP_BLOCK can correctly emit
                    # finally: at the right level even if _except_header_indents
                    # has been cleared by then.
                    self.emit.finally_wrapper_indent[jump_target] = self.indent_level
                    self.blocks.append((jump_target, "finally_wrapper"))
                else:
                    # Plain try/finally (no except) — emit try: normally.
                    # POP_BLOCK will see try_body with a finally_targets offset
                    # and emit finally:.
                    super()._dispatch_instruction(instr)
            else:
                super()._dispatch_instruction(instr)

        # POP_TOP at a handler jump-target: Python 3.9 bare except starts with
        # three consecutive POP_TOPs (exc_type, exc_value, traceback) instead of
        # DUP_TOP.  Detect when POP_TOP fires at the handler entry point.
        elif opname == "POP_TOP" and instr.is_jump_target and self.emit.except_header_indents:
            # Skip the other two POP_TOPs that follow (they discard exc_value
            # and traceback from the implicit exception tuple).
            skip = self.pc
            pops_skipped = 0
            while (skip < len(self.instructions)
                   and self.instructions[skip].opname == "POP_TOP"
                   and pops_skipped < 2):
                skip += 1
                pops_skipped += 1
            self.pc = skip
            # Emit bare except header at the correct indent level
            if self.emit.except_header_indents:
                self.indent_level = self.emit.except_header_indents[-1]
            self._append_reconstructed("except:")
            self.indent_level += 1
            self.stack.append("_exc_match")

        # RERAISE (opcode 48 in 3.9, no-arg): re-raises the current exception.
        # In handlers this appears after a failed JUMP_IF_NOT_EXC_MATCH as the
        # fall-through re-raise.  Nothing to emit — the decompiler has already
        # reached this via the jump target path, so just skip silently.
        elif opname == "POP_EXCEPT":
            _deferred = False
            if self.emit.except_header_indents:
                # Peek ahead: if the next instruction is a backward JUMP_ABSOLUTE
                # to a FOR_ITER loop head, this POP_EXCEPT is the exit from an
                # exception handler whose only action is 'continue'.
                # Emit 'continue' instead of 'pass' in that case.
                _emitted_continue = False
                look = self.pc
                if (look < len(self.instructions)
                        and self.instructions[look].opname in ("JUMP_ABSOLUTE", "JUMP_BACKWARD")
                        and isinstance(self.instructions[look].argval, int)
                        and self.instructions[look].argval < self.instructions[look].offset):
                    jmp = self.instructions[look]
                    jt = self._get_jump_target(jmp)
                    # Check if the target is a FOR_ITER instruction offset
                    _for_iter_offsets = {
                        fi.offset for fi in self.instructions if fi.opname == "FOR_ITER"
                    }
                    if isinstance(jt, int) and jt in _for_iter_offsets:
                        self._append_reconstructed("continue")
                        self.pc = look + 1   # consume the JUMP_ABSOLUTE
                        _emitted_continue = True

                if not _emitted_continue:
                    # ── Python 3.9 return-inside-except ─────────────────────────
                    # CPython 3.9 emits: LOAD_FAST x → ROT_FOUR → POP_EXCEPT → RETURN_VALUE
                    # The return value is on the stack but POP_EXCEPT fires first.
                    # Peek: if next instruction is RETURN_VALUE, emit now at handler indent.
                    look = self.pc
                    while (look < len(self.instructions)
                           and self.instructions[look].opname in ("RESUME", "NOP", "CACHE")):
                        look += 1
                    if (look < len(self.instructions)
                            and self.instructions[look].opname in ("RETURN_VALUE", "RETURN_CONST")):
                        ret_instr = self.instructions[look]
                        if ret_instr.opname == "RETURN_CONST":
                            ret_val = self._format_val(ret_instr.argval)
                        else:
                            # The return value was loaded before the ROT_FOUR and sits on stack
                            v = self.stack.pop()
                            if getattr(self, "target_version", sys.version_info) >= (3, 11):
                                while self._is_null_sentinel(v) and self.stack:
                                    v = self.stack.pop()
                            ret_val = str(v)
                        is_compiler_gen = (ret_val == "None"
                                           and self.is_compiler_generated_return(look))
                        if not is_compiler_gen:
                            self._append_reconstructed(f"return {ret_val}")
                        self.pc = look + 1  # consume RETURN_VALUE
                    elif self._defer_close_to_handler_return():
                        # The clause ends in `return` after its clean-up, or computes the
                        # returned value here: it stays open until that return (#165).
                        _deferred = True
                    else:
                        last_idx = len(self.reconstructed) - 1
                        while last_idx >= 0 and not self.reconstructed[last_idx].strip():
                            last_idx -= 1
                        if last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":"):
                            self._append_reconstructed("pass")

                if not _deferred:
                    self.indent_level = self.emit.except_header_indents.pop()
            if self.stack and self.stack[-1] == "_exc_match":
                self.stack.pop()
            elif _deferred and "_exc_match" in self.stack:
                # The returned value sits above the match sentinel.
                del self.stack[len(self.stack) - 1 - self.stack[::-1].index("_exc_match")]
        elif opname == "RERAISE":
            pass  # already handled by the JUMP target structure; no source emission

        else:
            super()._dispatch_instruction(instr)
