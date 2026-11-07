"""Base class holding the state every decompiler backend shares."""
import ast
import dis
import sys
import types
from typing import Any, Dict, List, Optional, Set, Tuple
from .state import PrescanState
from ..analysis import parse_exception_table
from ..diagnostics import INTERNAL_ERROR, STACK_UNDERFLOW, DecompilationError, Diagnostic
from ..expr import UnknownExpression
from ..codeobj import _is_code
from ..instructions import BytecodeInstruction
from ..opcodes.decode import decode_instructions, opcodes_for, stack_effect
from ..versions import supports

# Marks "no fallback given" for _pop(), since None is a fallback callers use.
_UNSET: Any = object()

# Stem of the name used for a for-loop item that a later unpacking stores.
_LOOP_PLACEHOLDER = "_item"
_SPLIT_PIECES = frozenset(("STORE_FAST", "LOAD_FAST"))   # what split_superinstructions makes of STORE_FAST_LOAD_FAST


class DecompilerBase:
    # Created by DecompilerGeneric.__init__; declared here for the shared helpers.
    prescan: PrescanState

    def __init__(self, code_obj: types.CodeType, indent_level: int = 0, beautification_level: str = 'core', target_version: Optional[Tuple[int, int]] = None, strict: bool = False):
        self.code_obj = code_obj
        # Where reconstruction was uncertain (see diagnostics.py). Decompilers of nested
        # code share one list; in strict mode the first entry raises instead.
        self.strict = strict
        self.diagnostics: List[Diagnostic] = []
        self._current_instr: Optional[BytecodeInstruction] = None
        # Exception table, filled in on first use by _exception_entries(); False = not yet read.
        self._exception_entries_cache: Any = False
        # The offsets of the BEFORE_WITH instructions that were an `async with`'s BEFORE_ASYNC_WITH
        self._async_with_offsets: Set[int] = set()
        self._async_for_offsets: Set[int] = set()
        # {offset in the disassembly: offset now} once the instructions have been numbered again (None: they have not)
        self._offset_map: Optional[Dict[int, int]] = None
        # The offsets of the CLEANUP_THROW handlers that were folded away with a `yield from` or `await` (3.12+)
        self._throw_handlers: Set[int] = set()
        # 3.10: the exception table read from the block-based handlers (block_handlers)
        self._block_entries: Optional[list] = None
        self.instructions: List[BytecodeInstruction] = []
        # The instructions as compiled, kept as `decompile()` starts: `instructions` is rewritten
        # as comprehensions are rebuilt inline, and `_scope_accesses` needs what the compiler wrote.
        self._compiled_instructions: List[BytecodeInstruction] = []
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
        # Names _fresh_accum_name has already handed out for this code object.
        self._issued_temp_names: Set[str] = set()
        self._unpack_groups: List[List[str]] = []
        # Loop-item placeholders _loop_placeholder chose because "_item" was already in use.
        self._loop_placeholder_names: Set[str] = set()
        self._code_names_cache: Optional[Set[str]] = None

    def _supports(self, capability: str) -> bool:
        """Whether the bytecode of the version being decompiled has *capability* (see ``versions.CAPABILITIES``)."""
        return supports(capability, tuple(self.target_version))

    def _report(self, kind: str, reason: str, instr: Optional[BytecodeInstruction] = None) -> None:
        """
        Record that reconstruction was uncertain here; in strict mode, stop instead.

        *instr* defaults to the instruction being handled.
        """
        instr = instr or self._current_instr
        diagnostic = Diagnostic(
            kind, reason, tuple(self.target_version),  # type: ignore[arg-type]
            opcode=instr.opname if instr is not None else None,
            offset=instr.offset if instr is not None else None,
            code_name=getattr(self.code_obj, "co_qualname", None) or getattr(self.code_obj, "co_name", None))
        if diagnostic not in self.diagnostics:
            self.diagnostics.append(diagnostic)
        if self.strict:
            raise DecompilationError(diagnostic)

    def _share_diagnostics_with(self, sub: "DecompilerBase") -> None:
        """Make *sub*, a decompiler of part of this code whose output is kept, report here."""
        sub.diagnostics = self.diagnostics
        sub.strict = self.strict

    def _unknown(self, reason: str, text: str = "?") -> UnknownExpression:
        """A placeholder for an expression that cannot be determined here, tagged with where it came from."""
        instr = self._current_instr
        return UnknownExpression(reason, instr.opname if instr is not None else None,
                                 instr.offset if instr is not None else None, text)

    def _pop(self, fallback: Any = _UNSET, reason: str = "an operand is missing from the stack") -> Any:
        """
        Pop the top of the expression stack. If it is empty, that is an invalid stack
        transition: record a stack underflow (an error in strict mode) and carry on with
        *fallback*, or with an :class:`UnknownExpression` when none is given.
        """
        if self.stack:
            return self.stack.pop()
        self._report(STACK_UNDERFLOW, reason)
        return self._unknown(reason) if fallback is _UNSET else fallback

    def _receiver(self, value: Any) -> str:
        """
        Text of *value* as the object of an attribute, subscript or ``del`` target.

        A negative number, comparison, conditional, lambda and the like binds more loosely than ``.`` and
        ``[]``, so ``(a < b).x`` would otherwise come out as ``a < b.x``. Text that does not
        parse, or is already parenthesized, is returned unchanged.
        """
        text = self._wrap_negative_literal(value)
        if " " not in text:
            return text
        try:
            node = ast.parse(text.strip(), mode="eval").body
        except SyntaxError:
            return text
        if isinstance(node, (ast.Compare, ast.IfExp, ast.Lambda, ast.NamedExpr, ast.BoolOp,
                             ast.BinOp, ast.UnaryOp, ast.Await, ast.Yield, ast.YieldFrom)):
            # ast drops redundant parentheses, but keeps the node's offsets: a node that
            # starts after column 0 is already wrapped.
            if node.col_offset == 0:
                return f"({text})"
        return text

    def _comparand(self, value: Any) -> str:
        """
        Text of *value* as an operand of a comparison, ``is`` or ``in``: a conditional expression, a lambda or an
        assignment expression binds more loosely, so ``(a if c else b) > 0`` would otherwise come out as ``a if c else b > 0``.
        """
        text = str(value)
        if " if " not in text and not text.startswith("lambda") and ":=" not in text:
            return text
        try:
            node = ast.parse(text.strip(), mode="eval").body
        except SyntaxError:
            return text
        if isinstance(node, (ast.IfExp, ast.Lambda, ast.NamedExpr)) and node.col_offset == 0:
            return f"({text})"
        return text

    def _disassemble(self):
        """Convert code object bytecode into a list of BytecodeInstruction."""
        if self._decodes_foreign_bytecode():
            # bytecode of another Python version: ``dis`` would read it with this host's tables (#94)
            self.instructions.extend(decode_instructions(self.code_obj, tuple(self.target_version)))
            return
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

    def _decodes_foreign_bytecode(self) -> bool:
        """True when the code was written by another Python than this host and the packaged opcode tables read it."""
        return self._supports("opcode_tables") and tuple(self.target_version) != sys.version_info[:2]

    def _native_instructions(self) -> list:
        """The instructions of the code object, as the ``dis`` of the Python version that wrote it reads them."""
        if self._decodes_foreign_bytecode():
            return decode_instructions(self.code_obj, tuple(self.target_version))
        return list(dis.Bytecode(self.code_obj))

    def _opcode_number(self, name: str) -> int:
        """The number of opcode *name* in the Python version that wrote the code (110 for JUMP_FORWARD when unknown)."""
        if self._decodes_foreign_bytecode():
            return opcodes_for(tuple(self.target_version)).numbers.get(name, 110)
        return dis.opmap.get(name, 110)

    def _stack_effect(self, ins: Any) -> int:
        """``dis.stack_effect(..., jump=False)`` of *ins* under the version that wrote it; ValueError when unknown."""
        name, arg = ins.opname, ins.arg
        if arg is None and name in _SPLIT_PIECES:
            # the halves of a split STORE_FAST_LOAD_FAST carry their variable in argval; the effect does not depend on it
            arg = 0
        elif ins.opcode < 0:
            raise ValueError(f"no stack effect for the synthetic {name}")   # dis has no number for it either
        if self._decodes_foreign_bytecode():
            return stack_effect(tuple(self.target_version), name, arg)
        number = dis.opmap[name] if name in _SPLIT_PIECES and ins.arg is None else ins.opcode
        return dis.stack_effect(number, arg if number >= dis.HAVE_ARGUMENT else None, jump=False)

    def decompile(self) -> str:
        """
        Decompile the stored code object into a human-readable Python source string.

        Returns:
            source (str): Reconstructed Python source for the decompiled code object.
        """
        raise NotImplementedError("Subclasses must implement decompile()")

    def _code_object_names(self) -> Set[str]:
        """
        Every identifier this code object, or any function, class or comprehension
        nested in it, can bind or read: locals, globals and attributes, cell and free
        variables. A generated temporary must avoid all of them, because a nested
        scope may read the name as a global of ours.
        """
        if self._code_names_cache is None:
            names: Set[str] = set()
            pending = [self.code_obj]
            while pending:
                code = pending.pop()
                for attr in ('co_varnames', 'co_names', 'co_cellvars', 'co_freevars'):
                    names.update(getattr(code, attr, ()))
                pending.extend(c for c in getattr(code, 'co_consts', ())
                               if _is_code(c))
            self._code_names_cache = names
        return self._code_names_cache

    def _fresh_accum_name(self, base_name: str = "_res") -> str:
        """
        Generate a temporary variable name that is unique within this code object.

        A name is never returned twice, so two temporaries that are live at the same time
        (for example the outer and inner sequence of a nested unpacking) cannot overwrite
        each other. Names the code object already uses (see _code_object_names) are
        skipped too.

        Args:
            base_name: Stem of the name; later calls add a numeric suffix to it.

        Returns:
            str: A name like "_res", "_res0", "_res1", etc. (or the same with another stem).
        """
        taken = self._code_object_names() | self._issued_temp_names
        candidate = base_name
        while candidate in taken:
            candidate = f"{base_name}{self._accum_counter}"
            self._accum_counter += 1
        self._issued_temp_names.add(candidate)
        return candidate

    def _loop_placeholder(self) -> str:
        """
        Name standing for a for-loop's item when the loop target is stored by the
        instructions that follow (an unpacking into attributes or subscripts).

        This is "_item" unless the code uses that name itself, in which case the loop
        would overwrite the program's own variable, so a unique name is chosen instead.
        """
        if _LOOP_PLACEHOLDER not in self._code_object_names():
            return _LOOP_PLACEHOLDER
        name = self._fresh_accum_name(_LOOP_PLACEHOLDER)
        self._loop_placeholder_names.add(name)
        return name

    def _is_loop_placeholder(self, name: Any) -> bool:
        """True if ``name`` is a loop-item placeholder chosen by _loop_placeholder."""
        text = str(name)
        if text == _LOOP_PLACEHOLDER:
            return _LOOP_PLACEHOLDER not in self._code_object_names()
        return text in self._loop_placeholder_names

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
        Parenthesize a numeric literal that is the receiver of an attribute access or method call.

        A negative number needs it for precedence (``(-1).bit_length()``) and an integer for
        the tokenizer, which reads ``1024.`` as the start of a float (``(1024).to_bytes()``).
        Non-negative floats and imaginary numbers (``1.5.hex()``, ``2j.real``) are fine as they
        are; a negative one is parenthesized like any other negative number.
        """
        s = str(value)
        if s.startswith("-") or s.isdigit():
            try:
                evaluated = ast.literal_eval(s)
                if isinstance(evaluated, (int, float, complex)) and not isinstance(evaluated, bool):
                    if s.startswith("-") or isinstance(evaluated, int):
                        return f"({s})"
            except (ValueError, SyntaxError):
                pass
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

    def _has_exception_table(self) -> bool:
        """True if the code has an exception table: 3.11+, or 3.10 handlers read into one (block_handlers)."""
        return self._exception_entries() is not None

    def _exception_entries(self) -> Optional[list]:
        """
        The code object's exception table (3.11+), computed once; None before 3.11, whose
        code objects have none (``dis.Bytecode`` has no ``exception_entries`` there).
        """
        cached = self._exception_entries_cache
        if cached is False:
            cached = None
            if self._supports("normalised_block_stack"):
                # computed from the block stack when the instructions were read (block_handlers)
                cached = self._block_entries
                if cached is not None and self._throw_handlers:
                    cached = [e for e in cached if e.target not in self._throw_handlers]
            elif self._supports("exception_table"):
                try:
                    at = {ins.offset: ins for ins in self._native_instructions()}
                    entries = self._remap_entries([e for e in parse_exception_table(self.code_obj.co_exceptiontable)
                                                   if not self._is_generator_wrapper(e, at)])
                    cached = self._joined_entries([e for e in entries if e.target not in self._throw_handlers])
                except (AttributeError, TypeError, ValueError, IndexError) as exc:
                    self._report(INTERNAL_ERROR, f"could not read the exception table: {type(exc).__name__}: {exc}")
            self._exception_entries_cache = cached
        return cached

    def _remap_entries(self, entries: list) -> list:
        """
        *entries* in the numbering of the instructions as they stand, once they have been numbered again
        (_renumber_offsets): a range covers the same instructions, so it becomes one range for each run of them.
        """
        mapping = self._offset_map
        if mapping is None:
            return entries
        by_old = sorted(mapping.items())
        remapped: list = []
        for entry in entries:
            target = mapping.get(entry.target, -1 - entry.target)
            covered = sorted(new for old, new in by_old if entry.start <= old < entry.end)
            run_start = previous = None
            for new in covered + [None]:
                if run_start is not None and (new is None or new != previous + 2):
                    remapped.append(entry._replace(start=run_start, end=previous + 2, target=target))
                    run_start = None
                if new is not None and run_start is None:
                    run_start = new
                previous = new
        return remapped

    def _joined_entries(self, entries: list) -> list:
        """
        *entries* with the ones that only a folded `yield from` or `await` held apart joined: neighbours that
        protect the same way and have no instruction left between them are one range again. A range that
        protected only folded code (the throw handlers) goes.
        """
        if not self._throw_handlers:
            return entries
        kept = {ins.offset for ins in self.instructions}
        joined: list = []
        for entry in sorted(entries, key=lambda e: e.start):
            last = joined[-1] if joined else None
            if (last is not None and (last.target, last.depth, last.lasti) == (entry.target, entry.depth, entry.lasti)
                    and not any(last.end <= off < entry.start for off in kept)):
                joined[-1] = last._replace(end=max(last.end, entry.end))
            else:
                joined.append(entry)
        return [e for e in joined if any(e.start <= off < e.end for off in kept)]

    def _only_generator_wrapper_follows(self, instr_index: int) -> bool:
        """True if all that follows the instruction at *instr_index* is the StopIteration handler of a generator (3.12+)."""
        rest = self.instructions[instr_index + 1:]
        return (len(rest) == 2 and rest[0].opname == "CALL_INTRINSIC_1" and rest[0].arg == 3
                and rest[1].opname == "RERAISE")

    @staticmethod
    def _is_generator_wrapper(entry: Any, at: dict) -> bool:
        """
        True for the table entry that covers a whole generator or coroutine (3.12+), whose handler turns a
        StopIteration into a RuntimeError (``CALL_INTRINSIC_1 3; RERAISE``). It protects no `try` of the source.
        *at* maps an offset to the instruction there.
        """
        handler = at.get(entry.target)
        return handler is not None and handler.opname == "CALL_INTRINSIC_1" and handler.arg == 3

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
        if instr.offset in self.prescan.explicit_returns:
            return False
        # Save current state for lookahead
        old_pc = self.pc
        self.pc = instr_index + 1
        effectively_last = self.is_effectively_last() or self._only_generator_wrapper_follows(instr_index)
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
