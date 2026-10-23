"""Base class holding the state every decompiler backend shares."""
import ast
import dis
import sys
import types
from typing import Any, List, Optional, Tuple
from ..instructions import BytecodeInstruction


class DecompilerBase:
    def __init__(self, code_obj: types.CodeType, indent_level: int = 0, beautification_level: str = 'core', target_version: Optional[Tuple[int, int]] = None):
        self.code_obj = code_obj
        # Exception table, filled in on first use by _exception_entries(); False = not yet read.
        self._exception_entries_cache: Any = False
        self.instructions: List[BytecodeInstruction] = []
        self.reconstructed: List[str] = []
        self.indent_level = indent_level
        self.stack: List[Any] = []
        self.instructions = list(code_obj.instructions) if hasattr(code_obj, 'instructions') else []
        self.starts_as_function = indent_level > 0
        self.blocks: List[Tuple[int, str]] = []  # stack of (end_offset, type)
        self.pc = 0
        self.beautification_level = beautification_level
        self.target_version = target_version or sys.version_info[:2]
        self._accum_counter = 0

    def _disassemble(self):
        """Convert code object bytecode into a list of BytecodeInstruction."""
        for instr in dis.get_instructions(self.code_obj):
            starts_line = instr.starts_line
            if isinstance(starts_line, bool):
                # 3.13+: a flag, with the number in ``line_number``.
                starts_line = getattr(instr, "line_number", None) if starts_line else None
            self.instructions.append(BytecodeInstruction(
                opcode=instr.opcode,
                opname=instr.opname,
                arg=instr.arg,
                argval=instr.argval,
                offset=instr.offset,
                starts_line=starts_line,
                is_jump_target=instr.is_jump_target,
                argrepr=instr.argrepr,
            ))

    def decompile(self) -> str:
        """
        Decompile the stored code object into a human-readable Python source string.

        Returns:
            source (str): Reconstructed Python source for the decompiled code object.
        """
        raise NotImplementedError("Subclasses must implement decompile()")

    def _fresh_accum_name(self) -> str:
        """
        Generate a fresh accumulator variable name that doesn't collide with existing names.

        Returns:
            str: A unique accumulator name like "_res", "_res1", "_res2", etc.
        """
        base_name = "_res"
        # Collect all existing names from the code object
        existing_names = set()
        if hasattr(self.code_obj, 'co_varnames'):
            existing_names.update(self.code_obj.co_varnames)
        if hasattr(self.code_obj, 'co_names'):
            existing_names.update(self.code_obj.co_names)

        # Try base name first
        if base_name not in existing_names:
            return base_name

        # Otherwise use counter to generate unique name
        while True:
            candidate = f"{base_name}{self._accum_counter}"
            self._accum_counter += 1
            if candidate not in existing_names:
                return candidate

    def _get_jump_target(self, instr: BytecodeInstruction) -> int:
        """
        Determine the absolute jump target offset for the given instruction.

        @param instr: The disassembled instruction whose jump target should be resolved.
        @returns: The target bytecode offset as an integer; returns 0 when no target is available.
        """
        if isinstance(instr.argval, int):
            return instr.argval
        return int(instr.arg) if (instr.arg is not None) else 0

    def is_effectively_last(self) -> bool:
        """
        Determine whether the remaining instructions from the current program counter are only epilogue/handler or otherwise ignorable operations.

        Scans instructions starting at self.pc and treats common epilogue/exception-handler/filler opcodes and trailing `return None` patterns as non-meaningful; returns False if any remaining instruction is considered meaningful.

        Returns:
            True if no meaningful instructions remain after the current program counter, False otherwise.
        """
        # Code the prescans already placed elsewhere (a finally body's re-raise copy,
        # handler sections rendered at their merge label) is not on the fall-through path.
        placed = set()
        for name in ("_finally_body_suppress", "_wrapper_body_suppress", "_handler_section_suppress"):
            placed |= getattr(self, name, set())
        # Search ahead from current PC.
        for i in range(self.pc, len(self.instructions)):
            instr = self.instructions[i]
            op = instr.opname
            if instr.offset in placed:
                continue
            # Skip re-raise epilogue, filler, and terminal stack-clearing instructions
            # common in modern Python (3.11-3.14) exception handlers and epilogues.
            if op in (
                "COPY", "POP_EXCEPT", "RERAISE", "CACHE", "RESUME", "PUSH_EXC_INFO",
                "POP_TOP", "LOAD_CONST", "EXTENDED_ARG", "CHECK_EXC_MATCH",
                "NOT_TAKEN", "TO_BOOL", "POP_JUMP_IF_TRUE", "POP_JUMP_IF_FALSE",
                "JUMP_BACKWARD", "JUMP_BACKWARD_NO_INTERRUPT", "NOP"
            ):
                continue
            # Skip subsequent None returns (common in 3.14 multiple exit paths)
            stack = getattr(self, "stack", None)
            if op == "RETURN_VALUE" and stack and str(stack[-1]) == "None":
                continue
            if op == "RETURN_CONST" and str(instr.argval) == "None":
                continue
            return False
        return True

    def _wrap_negative_literal(self, value: Any) -> str:
        """
        Wrap negative numeric literals in parentheses to ensure valid syntax for attribute access or method calls.

        Example: -1 -> (-1). bit_length()
        """
        s = str(value)
        if s.startswith("-"):
            try:
                evaluated = ast.literal_eval(s)
                if isinstance(evaluated, (int, float, complex)):
                    return f"({s})"
            except (ValueError, SyntaxError):
                pass
        # Also wrap variables with explicit unary minus like (-x) if needed?
        # Only focus on numeric/complex literals that break attr access:
        if s.startswith("(-") and s.endswith(")"):
            return s
        return s

    def _module_level_return_none(self) -> bool:
        """
        Handle a `return None` in code that is not a function body.

        Module and class bodies cannot contain a `return` statement, so every
        `return None` there is compiler-generated: 3.12+ copies the implicit
        final return to the end of each branch, and compiles a `break` out of a
        loop at the end of the module as `return None`. Emits `break` inside a
        loop and nothing elsewhere.

        Returns:
            True if the return was handled here, False for function bodies.
        """
        if getattr(self.code_obj, "co_flags", 0) & 0x1:   # CO_OPTIMIZED: a function
            return False
        here = self.instructions[self.pc - 1].offset if 0 < self.pc <= len(self.instructions) else -1
        # A loop whose end is at or before this return has already finished (it is
        # only still on the stack because a block opened inside it ends later).
        if any(btype in ("for", "while") and end > here for end, btype in getattr(self, "blocks", [])):
            self._append_reconstructed("break")
        return True

    def _exception_entries(self) -> Optional[list]:
        """
        The code object's exception table (3.11+), computed once; None before 3.11, whose
        code objects have none (``dis.Bytecode`` has no ``exception_entries`` there).
        """
        cached = self._exception_entries_cache
        if cached is False:
            try:
                cached = list(dis.Bytecode(self.code_obj).exception_entries)
            except (AttributeError, TypeError, ValueError, IndexError):
                cached = None
            self._exception_entries_cache = cached
        return cached

    def _ends_try_before_handlers(self, instr_index: int) -> bool:
        """
        True if the return at *instr_index* is the normal exit of a `try` that ends the
        function: the exception handlers start right after it (3.11+ lays them out after
        the code they protect), so nothing but handler code follows on any path.
        """
        instrs = self.instructions
        nxt = next((ins for ins in instrs[instr_index + 1:]
                    if ins.opname not in ("CACHE", "NOP", "EXTENDED_ARG")), None)
        if nxt is None or nxt.opname != "PUSH_EXC_INFO":
            return False
        # A handler that completes jumps to the code after the try statement. If that
        # is more than the implicit `return None`, the try does not end the function
        # (``try: ... except E: ... else: return None`` followed by more code).
        by_offset = {ins.offset: i for i, ins in enumerate(instrs)}
        for ins in instrs[instr_index + 1:]:
            if ins.opname not in ("JUMP_FORWARD", "JUMP", "JUMP_NO_INTERRUPT"):
                continue
            k = by_offset.get(self._get_jump_target(ins))
            while k is not None and k < len(instrs) and instrs[k].opname in ("CACHE", "NOP", "EXTENDED_ARG"):
                k += 1
            if k is None or k >= len(instrs):
                continue
            target = instrs[k]
            implicit = (target.opname == "RETURN_CONST" and target.argval is None) or (
                target.opname == "LOAD_CONST" and target.argval is None
                and k + 1 < len(instrs) and instrs[k + 1].opname == "RETURN_VALUE")
            if not implicit:
                return False
        return True

    def is_compiler_generated_return(self, instr_index: int) -> bool:
        """
        Determine whether the RETURN at the given instruction index is the implicit compiler-inserted `return None`.

        Performs conservative checks: the instruction must be effectively the final unbranched exit, must not reside inside user-level control-flow blocks (e.g., `if`, `for`, `while`, `try`, `except`, `finally`, `with`), and — when available — must not lie inside an exception-table protected range (Python 3.11+). These checks aim to distinguish a compiler-generated trailing `return None` from an explicit return written in source.

        Parameters:
            instr_index (int): Index into `self.instructions` identifying the RETURN instruction to evaluate.

        Returns:
            `true` if the RETURN is likely the compiler-inserted implicit `return None`, `false` otherwise.
        """
        instr = self.instructions[instr_index]
        if instr.offset in getattr(self, "_explicit_returns", ()):
            return False
        # Save current state for lookahead
        old_pc = self.pc
        self.pc = instr_index + 1
        effectively_last = self.is_effectively_last()
        self.pc = old_pc

        # Robust check for Python 3.11+: if the return is within a protected
        # range in the exception table, it's explicit. Compiler-generated
        # returns for with/try blocks are always outside the protected range.
        entries = self._exception_entries()
        if entries and any(e.start <= instr.offset < e.end for e in entries):
            return False

        if not effectively_last and not (entries and self._ends_try_before_handlers(instr_index)):
            return False

        # If we're inside any user-facing control flow block, it's likely
        # an explicit return in the source. A `try` or `with` block is still open
        # at the implicit return that follows it (its handlers come after), but
        # that return lies outside every protected range, which the check above
        # established: those blocks do not make it explicit.
        user_blocks = {"if", "else", "for", "while", "try_body", "except", "finally_body", "with", "with_body"}
        if entries:
            user_blocks -= {"try_body", "with", "with_body"}
        blocks = getattr(self, "blocks", [])
        for _, btype in blocks:
            if btype in user_blocks:
                return False

        return True
