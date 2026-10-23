"""Version-independent decompiler: prescans, opcode handlers and block management."""
import ast
import dis
import re
import sys
import types
from typing import Any, Dict, List, Optional, Tuple
from .._constants import (
    _AUG_ASSIGN_MAP,
    _FILLER_OPS,
    _VALUE_OPS,
    _COMPOUND_EXPR_OPS,
    _COMPOUND_SKIP,
    _GENERIC_SKIP_OPS,
    _INPLACE_ASSIGN_MAP,
    _IS_COMP_GEN_SKIP,
    _IS_COMP_GEN_STOP,
    _MAX_TERNARY_SEARCH_WINDOW,
    _MAX_TERNARY_SHORT_SEARCH_WINDOW,
    _MAX_WITH_EXIT_SEARCH_WINDOW,
    _NO_INDENT_TYPES,
    _NO_PASS_TYPES,
    _STATEMENT_BOUNDARY_OPS,
    _TERNARY_PURE,
    _TERNARY_SKIP,
    _TERNARY_STORES,
    _TRY_HANDLER_EXIT_OPS,
    _TRY_LOAD_OPS,
    _TRY_LOOP_HEADS,
    _TRY_SKIP_NOP,
    _UNARY_SYMBOLS,
    _WHILE_TRIVIAL_OPS,
)
from ..codeobj import _is_code
from .base import DecompilerBase
from ..expr import StackNULL, _BoolExpr, _CollectionAccumSentinel, _unary_operand
from ..instructions import (BytecodeInstruction, _const_repr, _copy_signature, _is_anonymous_func_body,
                            _match_finally_copy, _render_func_tuple)
from ..postprocess import collapse_chained_comparisons, post_process_source


# ---------------------------------------------------------------------------
# Generic decompiler (3.10–3.13 primary path)
# ---------------------------------------------------------------------------


# CPython 3.14's LOAD_COMMON_CONSTANT table (opcode._common_constants), by name, so a
# 3.14 .pyc can be read on another interpreter. Only used when ``dis`` cannot resolve the
# constant itself; tests/test_basic.py (TestInlinedBuiltinCalls) checks it against the
# running 3.14's table, so a reordering in CPython fails the suite instead of passing silently.
_COMMON_CONSTANT_NAMES = ("AssertionError", "NotImplementedError", "tuple", "all", "any")

def _owned_handlers(handler: int, entries: list) -> set:
    """
    *handler* and the handlers of everything nested in the code it protects: a try in a
    with body is protected by its own handler, whose code the with's handler protects.
    """
    owned = {handler}
    targets = {e.target for e in entries}
    grew = True
    while grew:
        inner = {t for t in targets - owned
                 if any(e.target in owned and e.start <= t < e.end for e in entries)}
        owned |= inner
        grew = bool(inner)
    return owned


def _unpack_shape(ins: Optional[BytecodeInstruction]) -> Optional[Tuple[int, Optional[int]]]:
    """
    ``(number of targets, index of the starred one or None)`` for an unpacking
    instruction: ``UNPACK_SEQUENCE n``, or ``UNPACK_EX`` with the count before the star
    in the low byte of its operand and the count after it in the high byte (#156).
    """
    if ins is None or not isinstance(ins.arg, int):
        return None
    if ins.opname == "UNPACK_SEQUENCE":
        return ins.arg, None
    if ins.opname == "UNPACK_EX":
        before, after = ins.arg & 0xFF, ins.arg >> 8
        return before + 1 + after, before
    return None


# Instructions that make up a value-context `and`/`or` inside an expression.
_BOOLOP_PARTS = frozenset(("JUMP_IF_TRUE_OR_POP", "JUMP_IF_FALSE_OR_POP", "COPY", "TO_BOOL",
                           "POP_JUMP_IF_TRUE", "POP_JUMP_IF_FALSE", "POP_TOP"))


class DecompilerGeneric(DecompilerBase):
    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)

    def __init__(self, code_obj: types.CodeType, indent_level: int = 0, beautification_level: str = 'core', target_version: Optional[Tuple[int, int]] = None):
        """
        Initialize the decompiler state for generic Python bytecode reconstruction.

        Sets up expression stack, docstring tracking, loop and exception bookkeeping, and containers
        that _prescan_try_structure populates during decompilation.

        Attributes:
            stack: LIFO of partial expression strings or tuples used by the stack-driven renderer.
            has_doc (bool): True after emitting a module/function docstring.
            _while_body_offsets (set): Offsets that mark the start of while-loop bodies to suppress duplicated
                trailing condition checks emitted by some bytecode patterns (notably 3.11+).
            _exc_as_store_offset (int): Bytecode offset of a STORE_* that implements `except ... as name`
                and should be skipped during normal emission (-1 when none).
            _exc_cleanup_name (Optional[str]): Exception cleanup temporary name to suppress from output.
            _except_header_indents (List[int]): Stack of indentation levels for except headers.
            _except_end_offsets (List[int]): Stack of end-offsets for exception regions used to suppress intervening jumps.
            _exc_bound_names (set): Names that have been bound via `except ... as name`.
            _try_nop_offsets (set): Offsets of NOP markers that indicate try-entry points (filled by prescan).
            _suppress_push_exc_offsets (set): Offsets of PUSH_EXC_INFO instructions to suppress (filled by prescan).
            _finally_merge_offsets (set): Merge-label offsets where deferred finally/except content must be emitted.
            _exc_handler_jump_offsets (set): Jump offsets identified as exits from exception handlers.
            _with_exit_suppress_offsets (set): Offsets of with-context exit epilogue instructions to suppress.
            _finally_body_suppress (set): Instruction offsets inside finally bodies that should be skipped in main pass.
            _push_exc_to_finally_merge (dict): Maps PUSH_EXC_INFO offsets to corresponding finally-merge labels.
            _deferred_finally_lines (dict): Pre-rendered finally-body source lines keyed by merge offset.
            _deferred_except_lines (dict): Pre-rendered except-handler source lines keyed by merge offset.
            _handler_section_suppress (set): Offsets of inlined handler instructions to suppress in the primary dispatch.
            _wrapper_body_suppress (set): Offsets of wrapper/cleanup instruction ranges to suppress (e.g., re-raise wrappers).
            _pending_finally_merge (Optional[int]): Merge offset currently pending emission, if any.
            _nop_to_push_exc (dict): Mapping from try-entry NOP offsets to the following PUSH_EXC_INFO offset (populated by prescan).
        """
        super().__init__(code_obj, indent_level, beautification_level, target_version)
        self.has_doc = False
        self._doc_load_offset: Optional[int] = None    # LOAD_CONST of a module/class docstring
        self._class_doc_store_offsets: dict = {}       # STORE_NAME __doc__ of a class docstring
        self._doc_line_count = 0                       # output lines the docstring takes
        self._pei_indents: Dict[int, int] = {}
        # Tracks offsets of while-loop body starts so we can suppress the
        # duplicated condition check emitted at the bottom of 3.11+ while loops.
        self._while_body_offsets: set = set()
        self._inner_back_jumps: dict = {}  # inner back-jump offset -> offset of the closing back-edge
        # Value-context `and`/`or` (see _prescan_value_boolops).
        self._boolop_or_pop: dict = {}      # 3.9-3.10: offset -> (op, target) for *_OR_POP jumps
        self._boolop_copy: dict = {}        # 3.12+: COPY offset -> (op, target, resume offset)
        self._boolop_groups: dict = {}      # 3.9-3.10: plain POP_JUMP offset -> (op, target)
        self._boolop_pending: list = []     # open operators: [kind, left operand, join offset]
        self._dup_guard_exits: set = set()  # exit targets of suppressed bottom-of-loop condition copies
        # Exception-handler bookkeeping
        self._exc_as_store_offset: int = -1      # offset of 'as e' STORE to skip
        self._exc_cleanup_name: Optional[str] = None   # name to suppress in cleanup
        self._except_header_indents: List[int] = []    # stack of indent levels for except headers
        self._except_end_offsets: List[int] = []      # stack of end-offsets for exception zones
        self._exc_bound_names: set = set()             # all names ever bound in except-as
        self._handler_return_close: set = set()        # offsets of returns that end an except clause
        self._explicit_returns: set = set()            # `return None` offsets known to be in the source
        self._header_indent_by_cleanup: dict = {}      # handler clean-up target -> except header indent
        self._empty_try_handlers: dict = {}            # try NOP offset -> handler of a `try: pass`
        self._empty_try_skip: set = set()              # NOPs of such a try statement
        # The following sets are populated by _prescan_try_structure() which runs in decompile()
        self._try_nop_offsets: set = set()
        self._suppress_push_exc_offsets: set = set()
        self._finally_merge_offsets: set = set()
        self._exc_handler_jump_offsets: set = set()
        self._exc_handler_continue_offsets: set = set()
        self._with_exit_suppress_offsets: set = set()
        self._with_body_end: dict = {}  # first protected offset of a with body -> where it ends
        self._open_with_sentinels: list = []  # 3.14: (body end, body start) of open with bodies
        self._with_exit_owner: dict = {}  # POP_TOP ending an inlined __exit__ call -> its with's body start
        self._finally_body_suppress: set = set()
        self._push_exc_to_finally_merge: dict = {}
        self._deferred_finally_lines: dict = {}
        self._deferred_except_lines: dict = {}
        self._handler_section_suppress: set = set()
        self._wrapper_body_suppress: set = set()
        self._pending_finally_merge: Optional[int] = None
        self._nop_to_push_exc: dict = {}
        self._nop_to_finally_merge: dict = {}
        self._else_stack_snapshots: dict = {}  # second arm's offset -> stack to restore there
        self._finally_from_handler: dict = {}  # 3.9: finally rendered from its handler copy
        self._kw_names: Tuple[str, ...] = ()
        self._else_starts: dict = {}  # map of else_start_offset -> else_end_offset
        self._build_dispatch()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def _function_docstring(self) -> Optional[str]:
        """
        The docstring of a function body, or None (#166).

        No instruction loads it. Up to 3.13 the compiler reserves ``co_consts[0]`` of
        every function for it (None when there is none); 3.14 drops that slot and sets
        ``CO_HAS_DOCSTRING`` (0x4000000) when ``co_consts[0]`` is the docstring.
        """
        code = self.code_obj
        is_function = bool(code.co_flags & 0x1)  # CO_OPTIMIZED: a function body
        if self.indent_level <= 0 or not is_function or code.co_name.startswith("<") or not code.co_consts:
            return None
        if self.target_version >= (3, 14) and not code.co_flags & 0x4000000:
            return None
        first = code.co_consts[0]
        return first if isinstance(first, str) else None

    def _find_class_docstring(self) -> dict:
        """
        ``{offset of its STORE_NAME __doc__: text}`` for a class body's docstring: the
        first statement after the class preamble (``__module__``, ``__qualname__``, and
        on newer versions ``__firstlineno__`` and the ``__classdict__`` cell), a constant
        string stored to ``__doc__``.
        """
        if self.indent_level <= 0 or self.code_obj.co_flags & 0x1:
            return {}
        preamble = ("__module__", "__qualname__", "__firstlineno__", "__classdict__", "__type_params__",
                    ".type_params")
        instrs = [x for x in self.instructions
                  if x.opname not in ("RESUME", "NOP", "CACHE", "NOT_TAKEN", "MAKE_CELL", "COPY_FREE_VARS",
                                      "SETUP_ANNOTATIONS")]
        k = 0
        while (k + 1 < len(instrs) and instrs[k + 1].opname in ("STORE_NAME", "STORE_DEREF", "STORE_FAST")
               and instrs[k + 1].argval in preamble):
            k += 2
        if (k + 1 < len(instrs) and instrs[k].opname == "LOAD_CONST" and isinstance(instrs[k].argval, str)
                and instrs[k + 1].opname == "STORE_NAME" and instrs[k + 1].argval == "__doc__"):
            return {instrs[k + 1].offset: instrs[k].argval}
        return {}

    def _docstring_literal(self, doc: str) -> str:
        """
        Source text for the docstring *doc* that compiles back to exactly *doc*.

        A triple-quoted string keeps the text as written; when it cannot (the text has
        backslashes, carriage returns or other unprintable characters, or the quotes),
        ``repr`` does. 3.13+ strips the common indentation of a docstring's later lines
        when compiling, so for those the body indentation is put back, as in the source.
        """
        if not doc:
            return '""'
        text = doc
        if self.target_version >= (3, 13) and "\n" in text and self.indent_level > 0:
            pad = "    " * self.indent_level
            first, *rest = text.split("\n")
            rest = [pad + ln if ln else "" for ln in rest]
            if rest and not rest[-1]:
                rest[-1] = pad  # the closing quotes' own line
            text = "\n".join([first] + rest)
        printable = all(c.isprintable() or c in "\n\t" for c in text)
        for quote in ('"""', "'''"):
            if printable and "\\" not in text and quote not in text and not text.endswith(quote[0]):
                return quote + text + quote
        return repr(doc)

    def _emit_docstring(self, doc: str) -> None:
        """Emit *doc* as the body's first statement; later lines are written as they are."""
        before = len(self.reconstructed)
        self._append_reconstructed(self._docstring_literal(doc))
        self._doc_line_count = len(self.reconstructed) - before
        self.has_doc = True

    def _scope_declarations(self) -> List[str]:
        """
        The `global` and `nonlocal` statements a function or class body needs (#155).

        They compile to no instruction of their own, only to how names are stored: a body
        that assigns to or deletes a module-level name uses ``STORE_GLOBAL`` /
        ``DELETE_GLOBAL``, and one that assigns to or deletes a variable of an enclosing
        function uses ``STORE_DEREF`` / ``DELETE_DEREF`` on one of its free variables.
        Reads need no declaration. Names are listed in order of first use. Module code
        and the bodies rendered inline (lambdas, comprehensions, handler replays at indent
        0) get none: the module's own names are global already, and a comprehension's
        walrus target belongs to the scope it is written in.
        """
        code = self.code_obj
        if self.indent_level <= 0 or code.co_name.startswith("<"):
            return []
        freevars = set(code.co_freevars)
        globals_: List[str] = []
        nonlocals: List[str] = []
        for ins in self.instructions:
            name = ins.argval
            if not isinstance(name, str) or name.startswith("."):
                continue
            if ins.opname in ("STORE_GLOBAL", "DELETE_GLOBAL"):
                if name not in globals_:
                    globals_.append(name)
            elif ins.opname in ("STORE_DEREF", "DELETE_DEREF") and name in freevars:
                if name not in nonlocals:
                    nonlocals.append(name)
        lines = []
        if globals_:
            lines.append("global " + ", ".join(globals_))
        if nonlocals:
            lines.append("nonlocal " + ", ".join(nonlocals))
        return lines

    def _emit_scope_declarations(self) -> None:
        """Emit the body's `global`/`nonlocal` statements, after its docstring."""
        for line in self._scope_declarations():
            self._append_reconstructed(line)

    def _reachable_instructions(self, entries: list) -> List[bool]:
        """Which instructions control can reach, by fall-through, jumps and handlers."""
        instrs = self.instructions
        index_of = {x.offset: n for n, x in enumerate(instrs)}
        terminal = ("RETURN_VALUE", "RETURN_CONST", "RAISE_VARARGS", "RERAISE", "JUMP_FORWARD",
                    "JUMP_BACKWARD", "JUMP_BACKWARD_NO_INTERRUPT", "JUMP", "JUMP_NO_INTERRUPT")
        reached = [False] * len(instrs)
        todo = [0]
        handled: set = set()
        while todo:
            n = todo.pop()
            while n is not None and n < len(instrs) and not reached[n]:
                reached[n] = True
                ins = instrs[n]
                if "JUMP" in ins.opname or ins.opname in ("FOR_ITER", "SEND"):
                    todo.append(index_of.get(self._get_jump_target(ins), len(instrs)))
                for e in entries:
                    if e.start <= ins.offset < e.end and e.target not in handled:
                        handled.add(e.target)
                        todo.append(index_of.get(e.target, len(instrs)))
                n = None if ins.opname in terminal else n + 1
        return reached

    def _drop_unreachable_handlers(self) -> None:
        """
        Take out exception handlers that nothing can reach (3.11+, #167).

        ``try: pass`` protects no instruction, so no exception-table entry leads to its
        handlers, but the compiler still emits them; rendered in place they were an
        orphan ``except`` after the function's code. Such a handler, from its
        PUSH_EXC_INFO through the unreachable code after it, is removed from the
        instruction stream and rendered as ``try: pass`` with its clauses where the
        try statement's NOP is (``_empty_try_handlers``). Without line numbers to find
        that NOP it is left out, which is equivalent: the try body cannot raise.
        """
        entries = self._exception_entries()
        instrs = self.instructions
        if not entries or not any(x.opname == "PUSH_EXC_INFO" for x in instrs):
            return
        reached = self._reachable_instructions(entries)
        regions: List[List[int]] = []
        n = 0
        while n < len(instrs):
            if not reached[n] and instrs[n].opname == "PUSH_EXC_INFO":
                regions.append([])
                while n < len(instrs) and not reached[n]:
                    regions[-1].append(n)
                    n += 1
            else:
                n += 1
        if not regions:
            return
        for region in regions:
            nops = self._empty_try_nops(region[0])
            if nops is not None:
                self._empty_try_handlers[instrs[nops[0]].offset] = [instrs[k] for k in region]
                self._empty_try_skip.update(instrs[k].offset for k in nops)
        drop = {k for region in regions for k in region}
        self.instructions = [x for k, x in enumerate(instrs) if k not in drop]

    def _empty_try_nops(self, handler_idx: int) -> Optional[Tuple[int, ...]]:
        """
        Indexes of the NOPs of the `try` whose unreachable handler starts at
        *handler_idx*: the try's own line and, right after it, its `pass` body, the last
        NOP above the line of the first `except` clause.
        """
        instrs = self.instructions
        except_line = next((self._line_at(k) for k in range(handler_idx + 1, len(instrs))
                            if instrs[k].starts_line is not None), None)
        if except_line is None:
            return None
        nops = [k for k in range(handler_idx) if instrs[k].opname == "NOP"
                and instrs[k].starts_line is not None and instrs[k].starts_line < except_line]
        if not nops:
            return None
        body = nops[-1]
        return (body - 1, body) if body > 0 and instrs[body - 1].opname == "NOP" else (body,)

    def _emit_empty_try(self, offset: int) -> None:
        """Emit ``try: pass`` and the clauses of its unreachable handler."""
        region = self._empty_try_handlers.pop(offset)
        # The region left the instruction stream before the prescans ran: mark the
        # handlers' jumps back to a loop head (`except E: continue`) here.
        loop_heads = {x.offset for x in self.instructions if x.opname == "FOR_ITER"}
        loop_heads |= {self._get_jump_target(x) for x in self.instructions if self._is_backward_instruction(x)}
        for k, ins in enumerate(region):
            if (k and region[k - 1].opname == "POP_EXCEPT" and ins.opname.startswith("JUMP")
                    and self._get_jump_target(ins) in loop_heads):
                self._exc_handler_jump_offsets.add(ins.offset)
                self._exc_handler_continue_offsets.add(ins.offset)
        self._append_reconstructed("try:")
        self.indent_level += 1
        self._append_reconstructed("pass")
        self.indent_level -= 1
        for line in self._render_handler_section(region, 0):
            if line.strip():
                self._append_reconstructed(line)

    def _fold_inlined_builtin_calls(self) -> None:
        """
        Undo CPython 3.14's inlining of ``all``/``any``/``tuple`` over a generator.

        3.14 compiles ``any(x for x in xs)`` as a guard that checks the name is still the
        builtin, an inlined loop for that case, and the ordinary call as the fallback::

            LOAD_GLOBAL any; COPY 1; LOAD_COMMON_CONSTANT any; IS_OP 0
            POP_JUMP_IF_FALSE fallback; [NOT_TAKEN]; POP_TOP; <inlined loop ...>
            fallback: PUSH_NULL; <generator>; CALL 1

        Only the fallback is the source code, so the guard and the inlined loop are
        dropped and the call is decompiled as written.
        """
        instrs = self.instructions
        index_of = {ins.offset: n for n, ins in enumerate(instrs)}
        drop: set = set()
        for k, ins in enumerate(instrs):
            if ins.opname != "COPY" or ins.arg != 1 or k == 0 or k + 3 >= len(instrs):
                continue
            const, test, jump = instrs[k + 1], instrs[k + 2], instrs[k + 3]
            name = getattr(const.argval, "__name__", None)
            if name is None and isinstance(const.arg, int) and 0 <= const.arg < len(_COMMON_CONSTANT_NAMES):
                name = _COMMON_CONSTANT_NAMES[const.arg]
            guard = (const.opname, test.opname, test.arg, jump.opname)
            if guard != ("LOAD_COMMON_CONSTANT", "IS_OP", 0, "POP_JUMP_IF_FALSE") or name is None:
                continue
            loaded = instrs[k - 1]
            # The guard reloads the name the call uses (the builtin, or a shadowing local).
            if loaded.opname not in ("LOAD_GLOBAL", "LOAD_NAME", "LOAD_DEREF", "LOAD_FAST") \
                    or not isinstance(loaded.argval, str) or loaded.argval != name:
                continue
            end = index_of.get(self._get_jump_target(jump))
            if end is None or end <= k + 3 or instrs[end].opname != "PUSH_NULL":
                continue
            drop.update(range(k, end))
        if drop:
            self.instructions = [ins for n, ins in enumerate(instrs) if n not in drop]

    def decompile(self) -> str:
        """
        Decompile the stored code object into a human-readable Python source string.

        Performs bytecode disassembly, structural prescans (loops, ternaries, compound conditions, try/except/finally), reconstructs source lines (including a module docstring when present), closes open blocks, suppresses redundant module-level trailing `return None` when appropriate, and runs post-processing to normalize imports, parentheses, and spacing.

        Returns:
            The reconstructed Python source as a single string, post-processed and ending with a single trailing newline.
        """
        self._disassemble()
        self.instructions = self._split_superinstructions(self.instructions)
        self._collapse_inlined_comprehensions()
        self._fold_inlined_builtin_calls()
        self._drop_unreachable_handlers()
        self.pc = 0
        self.blocks = []
        self._while_header_targets = {}
        self._prescan_while_loops()
        self._prescan_try_structure()
        self._prescan_with_exits()
        self._prescan_ternaries()
        self._prescan_compound_conds()
        self._prescan_value_boolops()

        # Docstring (#166). A function's lives in its constants (no instruction loads
        # it); a module's or class's is the first statement, stored to __doc__.
        self.has_doc = False
        self._doc_load_offset = None
        doc = self._function_docstring()
        if doc is None:
            first = next((ins for ins in self.instructions
                          if ins.opname not in ("RESUME", "NOP", "CACHE", "NOT_TAKEN")), None)
            if first is not None and first.opname == "LOAD_CONST" and isinstance(first.argval, str):
                idx = self.instructions.index(first)
                nxt = self.instructions[idx + 1] if idx + 1 < len(self.instructions) else None
                if nxt is not None and nxt.opname == "STORE_NAME" and nxt.argval == "__doc__":
                    doc = first.argval
                    self._doc_load_offset = first.offset
        if doc is not None:
            self._emit_docstring(doc)
            if self.indent_level == 0:
                self.reconstructed.append("")
        self._class_doc_store_offsets = self._find_class_docstring() if doc is None else {}

        self._emit_scope_declarations()

        while self.pc < len(self.instructions):
            instr = self.instructions[self.pc]
            self.pc += 1
            self._decompile_step(instr)

        # Final block closure for anything spanning to the very end
        self._close_blocks(0x7fffffff)

        # Final cleanup: suppress redundant trailing 'return None' at the base indent
        if self.reconstructed:
            # Find last non-empty line
            last_idx = len(self.reconstructed) - 1
            while last_idx >= 0 and not self.reconstructed[last_idx].strip():
                last_idx -= 1

            if last_idx >= 0 and self.reconstructed[last_idx].strip() == "return None":
                line = self.reconstructed[last_idx]
                # FIX: Only suppress root-level 'return None'. Indented ones (inside if/with/etc)
                # are likely explicit and should be preserved.
                if not (line.startswith(" ") or line.startswith("\t")):
                    # Found a trailing return None at root level.
                    # We can safely remove it if there's other code.
                    # Anything besides the docstring (which may span several lines)?
                    doc_lines = self._doc_line_count if self.has_doc else 0
                    has_others = any(self.reconstructed[i].strip() for i in range(doc_lines, last_idx))

                    if has_others or getattr(self, "has_doc", False):
                        self.reconstructed.pop(last_idx)
                    else:
                        indent = len(line) - len(line.lstrip())
                        self.reconstructed[last_idx] = line[:indent] + "pass"

        if self.indent_level > 0 and not any(str(line).strip() for line in self.reconstructed):
            # A body that compiles to nothing but the implicit return: `pass` or `...`
            # (#167). A lambda always returns a value, so it gets an explicit one.
            self._append_reconstructed("return None" if self.code_obj.co_name == "<lambda>" else "pass")

        raw_source = "\n".join(str(s) for s in self.reconstructed).rstrip()
        return post_process_source(raw_source, beautification_level=self.beautification_level)

    def _decompile_step(self, instr: BytecodeInstruction) -> None:
        """Decompile one instruction of the main pass (``self.pc`` is already past it)."""
        # Close any blocks whose end offset we have passed
        self._close_blocks(instr.offset)
        snapshot = self._else_stack_snapshots.pop(instr.offset, None)
        if snapshot is not None:
            self.stack = list(snapshot)
        if self._boolop_step(instr):
            return
        if instr.offset in self._empty_try_skip:
            if instr.offset in self._empty_try_handlers:
                self._emit_empty_try(instr.offset)
            return
        self._handle_instruction(instr)
        if instr.offset in self._handler_return_close:
            # The `return` that ends an except clause has been emitted inside it.
            self._handler_return_close.discard(instr.offset)
            self._close_except_clause()

    def _close_blocks(self, offset: int):
        """
        Close code blocks whose end offset is at or before the given bytecode offset.

        For each closed block this may append a minimal body line (`pass`) when the last
        non-blank reconstructed line is a block header (ends with ':') unless the block
        type suppresses that emission. The function also decreases the current
        indentation level for closed blocks except for block types that prevent
        indentation changes.

        Parameters:
            offset (int): Bytecode offset used to determine which blocks have ended.
        """

        while self.blocks and offset >= self.blocks[-1][0]:
            block_end, block_type = self.blocks.pop()

            has_else = False
            else_end = None
            if block_type == "for":
                for ins in self.instructions:
                    if ins.offset >= block_end:
                        break
                    if "JUMP" in ins.opname:
                        target = self._get_jump_target(ins)
                        if target > block_end and not self._is_backward_instruction(ins):
                            if else_end is None or target > else_end:
                                else_end = target
                                has_else = True

            if block_type not in _NO_PASS_TYPES:
                last_idx = len(self.reconstructed) - 1
                while last_idx >= 0 and not self.reconstructed[last_idx].strip():
                    last_idx -= 1
                if last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":"):
                    self._append_reconstructed("pass")

            if has_else and else_end is not None:
                real_else = False
                for ins in self.instructions:
                    if block_end <= ins.offset < else_end and not ins.opname.startswith("POP_") and not "NOP" in ins.opname and "END_FOR" not in ins.opname:
                        real_else = True
                        break
                if not real_else:
                    has_else = False

            if has_else and else_end is not None:
                self.indent_level -= 1
                self._append_reconstructed("else:")
                self.indent_level += 1
                self.blocks.append((else_end, "else"))
            elif block_type not in _NO_INDENT_TYPES:
                self.indent_level -= 1
                # If this try_body has a deferred except-only handler (loop case),
                # emit it immediately — before outer loop blocks also pop at same offset.
                if block_type in ("try_body", "exc_cleanup"):
                    exc_only = getattr(self, "_except_only_merge_offsets", set())
                    if block_end in exc_only:
                        exc_only.discard(block_end)
                        # Emitted here; the merge-label hook must not emit it again.
                        getattr(self, "_finally_merge_offsets", set()).discard(block_end)
                        self._emit_deferred_finally(block_end, self.indent_level)
                    elif (block_end in getattr(self, "_finally_merge_offsets", ())
                          and self.blocks and offset >= self.blocks[-1][0]):
                        # A try body that always raises or exits ends where an enclosing
                        # block (a loop) ends too: emit its finally before that one closes.
                        self._finally_merge_offsets.discard(block_end)
                        self._emit_deferred_finally(block_end, self.indent_level)

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _append_reconstructed(self, line: str, indent_multiline: bool = False):
        """
        Append a reconstructed source line or block to the decompiler's output buffer with proper indentation and optional multi-line indentation.

        This method updates self.reconstructed in place. If `line` is empty it is ignored. When `line` begins a major top-level block header (starts with `def `, `class `, `if `, `for `, `while `, or `try:`) a blank line is inserted before it if the previous output line is not already blank. If `line` contains embedded newlines it is split on `\n`; when `indent_multiline` is True every produced line is prefixed with the current indentation, otherwise only the first line is indented. Empty lines are preserved.

        Parameters:
            line (str): Source text to append; may contain embedded newlines. An empty string is ignored.
            indent_multiline (bool): If True, apply indentation to every line produced by splitting `line`; if False, only the first line is indented.
        """
        if not line:
            return
        # A clause header directly after another header at the same indent means
        # the earlier clause has an empty body (e.g. `except A: pass` then `except B:`).
        if line.startswith(("except", "elif ", "else:", "finally:")):
            prev = next((r for r in reversed(self.reconstructed) if r.strip()), None)
            if (prev is not None and prev.rstrip().endswith(":")
                    and prev.lstrip().startswith(("except", "elif ", "else:", "if ", "try:"))
                    and len(prev) - len(prev.lstrip()) == 4 * self.indent_level
                    and not (line.startswith("except") and prev.lstrip().startswith("try:"))):
                self.reconstructed.append("    " * (self.indent_level + 1) + "pass")
        # Blank line before major blocks (top-level only)
        if self.indent_level == 0 and line.startswith(("def ", "class ", "if ", "for ", "while ", "try:")) and self.reconstructed:
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
        # Format slice objects as proper Python slice notation (e.g. slice(6, None, None) → '6:')
        """
        Format a constant value for insertion into reconstructed source.

        Converts values into a Python-syntax-friendly string: slices become Python slice notation like
        'start:stop' or 'start:stop:step' (omitting parts that are None), multiline strings are wrapped
        in triple quotes using the alternate quote style if the content contains triple double-quotes,
        repeated patterns in strings or bytes are shorthanded (e.g. '\x00' * 16),
        and all other values are represented using their `repr()`.

        Parameters:
            val: The constant value to format (e.g., slice, str, bytes, number, None, etc.).

        Returns:
            A string containing the value rendered in a form suitable for emitted Python source.
        """
        if isinstance(val, slice):
            start = '' if val.start is None else repr(val.start)
            stop = '' if val.stop is None else repr(val.stop)
            if val.step is None:
                return f'{start}:{stop}'
            step = '' if val.step is None else repr(val.step)
            return f'{start}:{stop}:{step}'
        if self.beautification_level in ('core', 'aggressive') and isinstance(val, (str, bytes)):
            val_len = len(val)
            for pat_len in range(1, 5):
                if val_len >= pat_len * 2 and val_len % pat_len == 0:
                    pat = val[:pat_len]
                    repeat_count = val_len // pat_len
                    if val == pat * repeat_count:
                        shorthand = f"{repr(pat)} * {repeat_count}"
                        if len(shorthand) < len(repr(val)):
                            return shorthand
                        break
        if isinstance(val, str) and "\n" in val:
            if '"""' in val:
                return f"'''{val}'''"
            return f'"""{val}"""'
        return _const_repr(val)

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
        """True for any opcode that is always a backward loop jump.

        This covers JUMP_BACKWARD (3.11+) and any renamed variant.
        JUMP_ABSOLUTE is NOT included here because it can be either forward
        or backward; use _is_backward_instruction(instr) for that check.
        """
        return "JUMP_BACKWARD" in opname

    def _is_backward_instruction(self, instr: "BytecodeInstruction") -> bool:
        """True if *instr* is a backward jump (loop back-edge).

        Handles both:
          - JUMP_BACKWARD / JUMP_BACKWARD_NO_INTERRUPT (3.11+): always backward
          - JUMP_ABSOLUTE (3.9/3.10): backward only when target <= offset
        """
        if self._is_backward_jump(instr.opname):
            return True
        if instr.opname == "JUMP_ABSOLUTE":
            target = self._get_jump_target(instr)
            return isinstance(target, int) and target <= instr.offset
        return False

    def _has_jump_backward(self) -> bool:
        """Return True if the code contains any backward jump."""
        return any(self._is_backward_instruction(ins) for ins in self.instructions)

    def _is_null_sentinel(self, v: Any) -> bool:
        """Check if v is a NULL sentinel (None or StackNULL). 3.11+ only."""
        if getattr(self, "target_version", sys.version_info) < (3, 11):
            return False
        if v is None: return True
        vs = str(v)
        return vs in ("NULL", "__SENTINEL_NULL__") or getattr(v, '__class__', None).__name__ == 'StackNULL'

    def _find_jump_backward_target(self) -> int:
        """Return the jump target of the first backward jump, or -1."""
        for ins in self.instructions:
            if self._is_backward_instruction(ins):
                return self._get_jump_target(ins)
        return -1

    def _prescan_ternaries(self) -> None:
        """
        Detect ternary (conditional) expressions encoded in the function bytecode and record them for later reconstruction.

        Populates two attributes used by the decompiler:
        - _ternary_jumps: dict mapping the offset of a conditional POP_JUMP_IF_* instruction to a tuple
          (store_name, then_instrs, else_instrs, is_true_jump, aug_op):
            - store_name (str): target variable name, or empty string when the ternary result is pushed
              for immediate consumption (no explicit store).
            - then_instrs / else_instrs (List[BytecodeInstruction]): instruction slices forming the
              true/false branch expressions (skip-list already removed).
            - is_true_jump (bool): True when the jump corresponds to the truthy branch (e.g., IF_TRUE).
            - aug_op (Optional[str]): augmented-assignment operator (e.g., "+=") when the ternary is
              part of an augmented-assignment pattern; otherwise None.
        - _ternary_suppress: set of instruction offsets that should be skipped during normal
          instruction processing because they belong to a recognized ternary pattern.

        Only high-level, reconstructible ternary patterns are recorded; instructions not matched remain unchanged.
        """

        self._ternary_jumps: dict = {}
        self._ternary_suppress: set = set()

        offset_to_idx = {ins.offset: i for i, ins in enumerate(self.instructions)}

        # Opcodes considered as ternary condition jumps (Python 3.9+):
        #  POP_JUMP_IF_FALSE / POP_JUMP_IF_TRUE (all versions)
        #  POP_JUMP_IF_NONE / POP_JUMP_IF_NOT_NONE (3.11+)
        _TERNARY_CJUMPS = (
            "POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE",
            "POP_JUMP_IF_NONE", "POP_JUMP_IF_NOT_NONE",
        )
        # Set of opcode name fragments that are "stacksink" join-point operations:
        # when a JUMP_FORWARD from the then-branch targets one of these, the ternary
        # result is consumed by that op (not stored). We still recognise the ternary.
        _STACKSINK_OPS = frozenset((
            "BINARY_OP", "BINARY_MODULO", "BINARY_ADD", "BINARY_SUBTRACT",
            "BINARY_MULTIPLY", "BINARY_TRUE_DIVIDE", "BINARY_FLOOR_DIVIDE",
            "BINARY_AND", "BINARY_OR", "BINARY_XOR", "BINARY_LSHIFT",
            "BINARY_RSHIFT", "BINARY_POWER", "BINARY_MATRIX_MULTIPLY",
            "CALL_FUNCTION", "CALL", "CALL_METHOD", "CALL_FUNCTION_KW",
            "RETURN_VALUE", "RETURN_CONST", "YIELD_VALUE",
            "LIST_APPEND", "SET_ADD", "MAP_ADD",
            "UNARY_NEGATIVE", "UNARY_POSITIVE", "UNARY_INVERT", "UNARY_NOT",
            "CALL_INTRINSIC_1", "TO_BOOL",
        ))
        for idx, ins in enumerate(self.instructions):
            if not self._is_compound_cjump(ins.opname):
                continue
            if ins.offset in self._ternary_suppress:
                continue  # an and/or inside a branch of a conditional already found
            jump_target = self._get_jump_target(ins)
            t_idx = offset_to_idx.get(jump_target)
            if t_idx is None or t_idx <= idx:
                continue  # backward jump

            # ── Collect then-branch instructions ─────────────────────────
            then_raw = self.instructions[idx + 1: t_idx]
            then_sig = [x for x in then_raw if x.opname not in _TERNARY_SKIP]

            if not then_sig:
                continue

            # Detect Pattern B: then-branch contains a forward jump that skips
            # the else-expression and lands directly at the STORE instruction.
            # On 3.12 this is JUMP_FORWARD; on 3.14 it may be a different opcode
            # (e.g. JUMP_FORWARD still, but with a different encoding).  Match any
            # opcode whose name contains "FORWARD" or "JUMP" (but not backward/IF).
            def _FWD_JUMP(op): return (
                "FORWARD" in op
                or (op == "JUMP" and "BACKWARD" not in op and "IF" not in op)
            )
            jf_in_then = [
                x for x in then_sig
                if _FWD_JUMP(x.opname) and "IF" not in x.opname
            ]
            if jf_in_then:
                # Pattern B: then-branch contains a forward jump.
                # Two sub-variants:
                # B1 (3.12 standard):  then-EXPR  JUMP_FORWARD(store)  >> else-EXPR  >> STORE
                # B2 (3.14 possible):  then-EXPR  STORE  JUMP_FORWARD(after)  >> else-EXPR  >> STORE
                # Detect B2 first (then-STORE exists before the jump).
                jf = jf_in_then[-1]
                jf_pos = next(
                    (i for i, x in enumerate(then_sig) if x.offset == jf.offset),
                    None,
                )
                if jf_pos is None:
                    continue
                before_jf = then_sig[:jf_pos]

                then_stores_before_jf = [x for x in before_jf if x.opname in _TERNARY_STORES]

                if then_stores_before_jf:
                    # ── B2: then-STORE is before the jump ────────────────
                    then_s = then_stores_before_jf[-1]
                    store_target = self._get_jump_target(jf)
                    st_idx = offset_to_idx.get(store_target)
                    # The JUMP_FORWARD may target the post-merge point AFTER the
                    # shared else-STORE rather than the STORE itself (e.g. the
                    # simple if/else assignment pattern: then-EXPR then-STORE
                    # JUMP_FORWARD(merge)  >> else-EXPR  STORE  >> merge).
                    # When the resolved offset is not a STORE, fall back to
                    # scanning forward from t_idx to find the real STORE.
                    if st_idx is not None and self.instructions[st_idx].opname not in _TERNARY_STORES:
                        st_idx = None
                    if st_idx is None:
                        for fi in range(t_idx, min(t_idx + _MAX_TERNARY_SEARCH_WINDOW, len(self.instructions))):
                            if self.instructions[fi].opname in _TERNARY_STORES:
                                st_idx = fi
                                break
                    if st_idx is None:
                        continue
                    store_instr = self.instructions[st_idx]
                    if store_instr.opname not in _TERNARY_STORES:
                        continue
                    if then_s.argval != store_instr.argval:
                        continue
                    ts_pos = next(
                        i for i, x in enumerate(before_jf)
                        if x.offset == then_s.offset
                    )
                    actual_then_expr = [
                        x for x in before_jf[:ts_pos] if x.opname not in _TERNARY_SKIP
                    ]
                    if not self._branch_is_expression(actual_then_expr, then_s.offset):
                        continue
                    else_raw = self.instructions[t_idx: st_idx]
                    else_instrs = [x for x in else_raw if x.opname not in _TERNARY_SKIP]
                    if not self._branch_is_expression(else_instrs, store_instr.offset):
                        continue
                    store_name = str(store_instr.argval)
                    is_true = "IF_TRUE" in ins.opname

                    # ── Augmented assignment detection (B2) ───────────────
                    aug_op = None
                    if actual_then_expr and else_instrs:
                        lt = actual_then_expr[-1]
                        le = else_instrs[-1]
                        if lt.opname == "BINARY_OP" and le.opname == "BINARY_OP" and lt.arg == le.arg:
                            aug_op = _AUG_ASSIGN_MAP.get(int(lt.arg) if lt.arg is not None else -1)
                        if not aug_op and "INPLACE_" in lt.opname and lt.opname == le.opname:
                            aug_op = _INPLACE_ASSIGN_MAP.get(lt.opname)
                        if aug_op:
                            # Heuristic: Avoid merging string concatenations containing newlines
                            if self._has_newline_const(actual_then_expr + else_instrs):
                                continue
                            actual_then_expr.pop()
                            else_instrs.pop()

                    self._ternary_jumps[ins.offset] = store_name, actual_then_expr, else_instrs, is_true, aug_op
                    for x in then_raw:
                        self._ternary_suppress.add(x.offset)
                    for x in else_instrs:
                        self._ternary_suppress.add(x.offset)
                    continue

                # ── B1: no then-STORE before the jump ────────────────────
                store_target = self._get_jump_target(jf)
                if not self._branch_is_expression(before_jf, store_target):
                    continue

                st_idx = offset_to_idx.get(store_target)
                is_inplace = False

                if st_idx is not None:
                    # Check the instruction AT st_idx or immediately BEFORE it.
                    # Some bytecode patterns land on the INPLACE op, others land
                    # directly on the STORE (meaning the INPLACE op is the last
                    # instruction of the else branch).
                    check_idxs = [st_idx]
                    if st_idx > 0:
                        check_idxs.insert(0, st_idx - 1)

                    for c_idx in check_idxs:
                        op = self.instructions[c_idx]
                        if "INPLACE_" in op.opname:
                            is_inplace = True
                            if c_idx == st_idx:
                                st_idx += 1
                            break
                        if op.opname == "BINARY_OP":
                            arg_val = op.arg
                            if arg_val is not None and 13 <= int(arg_val) <= 25:
                                is_inplace = True
                                if c_idx == st_idx:
                                    st_idx += 1
                                break

                # ── B1 sub-case: join-point is a STORE (regular ternary) ──
                is_stacksink = False
                if st_idx is None or self.instructions[st_idx].opname not in _TERNARY_STORES:
                    # Before giving up: check if the JUMP_FORWARD target is a
                    # 'stacksink' joinpoint (CALL / BINARY_OP).  In that case
                    # the ternary result is consumed by that op, not stored to
                    # a variable.  We still push the ternary onto the stack.
                    if (st_idx is not None
                            and self.instructions[st_idx].opname in _STACKSINK_OPS):
                        is_stacksink = True
                        # else_raw = from t_idx up to (but NOT including) the join op
                        else_end_ss = st_idx
                        else_raw_ss = self.instructions[t_idx: else_end_ss]
                        else_instrs_ss = [x for x in else_raw_ss
                                          if x.opname not in _TERNARY_SKIP]
                        # Validate: the else branch is an expression (and/or allowed)
                        if not self._branch_is_expression(else_instrs_ss, self.instructions[st_idx].offset):
                            is_stacksink = False
                        if is_stacksink:
                            is_true_ss = "IF_TRUE" in ins.opname
                            # store_name="" signals a stacksink (push to stack, no explicit store)
                            self._ternary_jumps[ins.offset] = "", before_jf, else_instrs_ss, is_true_ss, None
                            for x in then_raw:
                                self._ternary_suppress.add(x.offset)
                            for x in else_instrs_ss:
                                self._ternary_suppress.add(x.offset)
                            # Do NOT suppress the join-point op itself
                            continue

                    if not is_stacksink:
                        # Fall back: look for a downstream STORE
                        st_idx = None
                        for fi in range(t_idx, min(t_idx + _MAX_TERNARY_SEARCH_WINDOW,
                                                   len(self.instructions))):
                            if self.instructions[fi].opname in _TERNARY_STORES:
                                st_idx = fi
                                break
                if st_idx is None:
                    continue

                else_end = st_idx - 1 if is_inplace else st_idx
                else_raw = self.instructions[t_idx: else_end]
                else_instrs = [x for x in else_raw if x.opname not in _TERNARY_SKIP]
                if st_idx >= len(self.instructions):
                    continue
                if not self._branch_is_expression(else_instrs, self.instructions[else_end].offset):
                    continue

                store_instr = self.instructions[st_idx]
                if store_instr.opname not in _TERNARY_STORES:
                    continue

                store_name = str(store_instr.argval)
                is_true = "IF_TRUE" in ins.opname

                # ── Augmented assignment detection (B1) ───────────────
                aug_op = None
                if before_jf and else_instrs:
                    lt = before_jf[-1]
                    le = else_instrs[-1]
                    if lt.opname == "BINARY_OP" and le.opname == "BINARY_OP" and lt.arg == le.arg:
                        aug_op = _AUG_ASSIGN_MAP.get(int(lt.arg) if lt.arg is not None else -1)
                    if not aug_op and "INPLACE_" in lt.opname and lt.opname == le.opname:
                        aug_op = _INPLACE_ASSIGN_MAP.get(lt.opname)
                    if aug_op:
                        if self._has_newline_const(before_jf + else_instrs):
                            continue
                        before_jf.pop()
                        else_instrs.pop()

                self._ternary_jumps[ins.offset] = store_name, before_jf, else_instrs, is_true, aug_op
                for x in then_raw:
                    self._ternary_suppress.add(x.offset)
                for x in else_instrs:
                    self._ternary_suppress.add(x.offset)
                continue  # done with this POP_JUMP_IF

            # ── Pattern A: no JUMP_FORWARD ────────────────────────────────
            store_idxs = [i for i, x in enumerate(then_sig) if x.opname in _TERNARY_STORES]
            if len(store_idxs) != 1:
                continue
            store_pos = store_idxs[0]
            then_store = then_sig[store_pos]
            before_store = then_sig[:store_pos]

            if not self._branch_is_expression(before_store, then_store.offset):
                continue

            # Find the first STORE in the else-branch
            else_store = None
            for i in range(t_idx, min(t_idx + _MAX_TERNARY_SHORT_SEARCH_WINDOW, len(self.instructions))):
                xi = self.instructions[i]
                if xi.opname in _TERNARY_STORES:
                    else_store = xi
                    break
                if xi.opname not in _TERNARY_PURE and xi.opname not in _BOOLOP_PARTS:
                    break

            if else_store is None or else_store.argval != then_store.argval:
                continue

            es_idx = offset_to_idx[else_store.offset]
            # 3.12+ duplicates a short tail (`return y`) into both arms. Whatever follows
            # the then-store must be that same tail after the else-store; anything else
            # means the arms are separate statements, not one conditional expression.
            then_tail = [x for x in then_sig[store_pos + 1:] if x.opname not in ("NOP",)]
            else_tail = [x for x in self.instructions[es_idx + 1: es_idx + 1 + len(then_tail) * 2]
                         if x.opname not in _TERNARY_SKIP and x.opname != "NOP"][:len(then_tail)]
            if then_tail and _copy_signature(then_tail) != _copy_signature(else_tail):
                continue
            if self._uses_outer_stack(before_store):
                continue  # the arm finishes an expression begun before the test
            else_raw = self.instructions[t_idx: es_idx]
            else_instrs = [x for x in else_raw if x.opname not in _TERNARY_SKIP]
            if not self._branch_is_expression(else_instrs, else_store.offset):
                continue

            store_name = str(then_store.argval)
            is_true = "IF_TRUE" in ins.opname

            # ── Augmented assignment detection (Pattern A) ───────────────
            aug_op = None
            if before_store and else_instrs:
                lt = before_store[-1]
                le = else_instrs[-1]
                if lt.opname == "BINARY_OP" and le.opname == "BINARY_OP" and lt.arg == le.arg:
                    aug_op = _AUG_ASSIGN_MAP.get(int(lt.arg) if lt.arg is not None else -1)
                if not aug_op and "INPLACE_" in lt.opname and lt.opname == le.opname:
                    aug_op = _INPLACE_ASSIGN_MAP.get(lt.opname)
                if aug_op:
                    if self._has_newline_const(before_store + else_instrs):
                        continue
                    before_store.pop()
                    else_instrs.pop()

            self._ternary_jumps[ins.offset] = store_name, before_store, else_instrs, is_true, aug_op
            for x in then_raw:
                self._ternary_suppress.add(x.offset)
            for x in else_instrs:
                self._ternary_suppress.add(x.offset)

    def _has_newline_const(self, instrs: List[BytecodeInstruction]) -> bool:
        """Heuristic: Return True if any instruction loads a string constant containing a literal newline."""
        return any("CONST" in x.opname and isinstance(x.argval, str) and "\n" in x.argval for x in instrs)

    def _format_fstring_part(self, p: str) -> str:
        """Unwrap a string fragment (which might be a literal) and escape it for inclusion in an f-string."""
        is_literal = False
        if p.startswith('"""') and p.endswith('"""') and len(p) >= 6:
            p = p[3:-3]
            is_literal = True
        elif p.startswith("'''") and p.endswith("'''") and len(p) >= 6:
            p = p[3:-3]
            is_literal = True
        elif len(p) >= 2 and p[0] == p[-1] and p[0] in ("'", '"'):
            p = p[1:-1]
            is_literal = True
            
        if is_literal:
            p = p.replace("{", "{{").replace("}", "}}")
            p = p.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")
            p = p.replace('"', '\\"')
        return p

    def _match_augassign_store(self, left: str, instr_store: "BytecodeInstruction") -> bool:
        """Return True if *instr_store* writes back to the same target as *left*.

        Handles all store variants:
        - ``STORE_SUBSCR``: left must look like a subscript expression ``x[...]``.
        - All named stores (``STORE_FAST``, ``STORE_NAME``, ``STORE_GLOBAL``,
          ``STORE_ATTR``, ``STORE_DEREF``): store name must equal the last
          component of *left* (handles plain names and attribute chains).
        """
        if instr_store.opname == "STORE_SUBSCR":
            return "[" in left and left.endswith("]")
        store_name = str(instr_store.argval)
        left_tail = left.split(".")[-1]
        return store_name == left_tail or left == store_name or left.endswith("." + store_name)

    # ------------------------------------------------------------------
    # Compound boolean condition pre-scan
    # ------------------------------------------------------------------

    def _prescan_compound_conds(self) -> None:
        """Pre-scan for compound boolean conditions (and/or chains).

        Python compiles ``if A or B and C:`` into a sequence of conditional
        jumps that all share at most two distinct targets:

        * **body_target** – the first instruction of the if-body (where a
          short-circuit POP_JUMP_IF_TRUE lands, meaning "this clause is True,
          skip the rest and execute the body").
        * **end_target** – the instruction after the if-body (where a
          POP_JUMP_IF_FALSE lands, meaning "this clause is False, skip the
          body").

        The final (controlling) jump in the chain is always a
        POP_JUMP_IF_FALSE → end_target.  All earlier jumps are either:
          - POP_JUMP_IF_TRUE  → body_target  (short-circuit OR)
          - POP_JUMP_IF_FALSE → end_target   (short-circuit AND, same end)

        This method:
        1. Finds every consecutive run of POP_JUMP_IF_* instructions that all
           share the same end_target and optionally a shared body_target.
        2. Uses ``_eval_cond_expr`` to compute the expression string for each
           clause (the pure-expression instructions between adjacent jumps).
        3. Assembles the combined condition string using ``and``/``or``.
        4. Stores the result in ``_compound_cond_map[controlling_jump_offset]``.
        5. Adds all non-controlling intermediate instructions to
           ``_compound_suppress`` (so they are skipped during normal dispatch).
        """

        self._compound_cond_map: dict = {}   # controlling_offset -> combined_cond_str
        self._compound_suppress: set = set()  # instruction offsets to skip

        instrs = self.instructions
        n = len(instrs)
        offset_to_idx = {ins.offset: i for i, ins in enumerate(instrs)}

        # Ternary jumps must not be touched here (they are pure expression nodes)
        ternary_offsets = set(getattr(self, "_ternary_jumps", {}).keys())

        i = 0
        while i < n:
            ins = instrs[i]
            # Only start a group at a brand-new POP_JUMP_IF_*
            if not self._is_compound_cjump(ins.opname):
                i += 1
                continue
            if ins.offset in ternary_offsets:
                i += 1
                continue

            # -----------------------------------------------------------
            # Gather the group: starting at index i, collect consecutive
            # conditional-jump instructions.  Between each pair of jumps the
            # only instructions allowed are pure expression builders.
            # -----------------------------------------------------------
            group: list = []   # list of (jump_instr_idx, jump_instr, expr_instrs_before_it)
            start_i = i
            # Scan backwards from the first jump to find the start of the
            # first clause's expression (stop at any non-_COMPOUND_EXPR_OPS instruction,
            # including a previous jump, RESUME, STORE, etc.).
            first_expr_start = i
            scan_back = i - 1
            while scan_back >= 0:
                bop = instrs[scan_back].opname
                if bop in _COMPOUND_SKIP:
                    scan_back -= 1
                    continue
                if bop in _COMPOUND_EXPR_OPS:
                    first_expr_start = scan_back
                    scan_back -= 1
                else:
                    break  # hit a non-expression instruction

            prev_jump_end_idx = first_expr_start

            j = i
            while j < n:
                jinstr = instrs[j]
                is_cjump = self._is_compound_cjump(jinstr.opname)
                if jinstr.opname in _COMPOUND_SKIP:
                    j += 1
                    continue
                if is_cjump and jinstr.offset not in ternary_offsets:
                    # Collect expression instructions since the previous jump
                    expr_instrs = [
                        instrs[k] for k in range(prev_jump_end_idx, j)
                        if instrs[k].opname not in _COMPOUND_SKIP
                    ]
                    # All of them must be pure expression builders
                    if not all(op.opname in _COMPOUND_EXPR_OPS for op in expr_instrs):
                        break  # not a clean chain
                    # Start offset of this clause's expression
                    expr_start_off = expr_instrs[0].offset if expr_instrs else jinstr.offset
                    group.append((j, jinstr, expr_instrs, expr_start_off))
                    prev_jump_end_idx = j + 1
                    j += 1
                    continue
                if jinstr.opname in _COMPOUND_EXPR_OPS:
                    j += 1
                    continue
                break  # something else → end of group

            def get_logical_t(t_val):
                curr = t_val
                visited = set()
                while curr not in visited:
                    visited.add(curr)
                    idx = offset_to_idx.get(curr)
                    if idx is None:
                        break
                    ins = instrs[idx]
                    if ins.opname in getattr(self, "_GENERIC_SKIP_OPS", ("NOP", "RESUME", "CACHE")):
                        curr = instrs[idx+1].offset
                        continue
                    if ins.opname == "POP_TOP":
                        look = idx + 1
                        while look < len(instrs) and instrs[look].opname in getattr(self, "_GENERIC_SKIP_OPS", ("NOP", "RESUME", "CACHE")):
                            look += 1
                        if look < len(instrs) and instrs[look].opname in ("JUMP_FORWARD", "JUMP_ABSOLUTE"):
                            curr = self._get_jump_target(instrs[look])
                            continue
                    break
                return curr

            valid = False
            while len(group) >= 2:
                last_jump_idx = group[-1][0]
                body_target = -1
                for k in range(last_jump_idx + 1, n):
                    if instrs[k].opname not in _COMPOUND_SKIP:
                        body_target = get_logical_t(instrs[k].offset)
                        break
                end_target = get_logical_t(self._get_jump_target(group[-1][1]))
                jump_starts = {get_logical_t(g[3]) for g in group}

                valid = True
                for (_, jinstr, _, _) in group:
                    t = get_logical_t(self._get_jump_target(jinstr))
                    if t == body_target:
                        pass
                    elif t == end_target or t in jump_starts:
                        pass
                    else:
                        valid = False
                        break
                if valid:
                    break
                else:
                    group.pop()

            if not valid or len(group) < 2:
                i += 1
                continue

            # -----------------------------------------------------------
            # Build the combined condition string.
            # -----------------------------------------------------------
            parts: list = []  # list of (cond_str, is_or_connector, next_t)

            sub = _pick_decompiler_class(self)(self.code_obj, self.indent_level, self.beautification_level, target_version=self.target_version)

            for _, jinstr, expr_instrs, _ in group:
                for xi in expr_instrs:
                    sub._handle_instruction(xi)
                raw_expr = str(sub.stack.pop()) if sub.stack else "?"

                t = get_logical_t(self._get_jump_target(jinstr))
                op = jinstr.opname
                is_success_type = "IF_TRUE" in op

                if t == body_target:
                    is_or_jump = True
                elif t == end_target:
                    is_or_jump = False
                else:
                    is_or_jump = is_success_type

                if "IF_NONE" in op and "NOT" not in op:
                    cond_str = f"{raw_expr} is None" if is_or_jump else f"{raw_expr} is not None"
                elif "IF_NOT_NONE" in op:
                    cond_str = f"{raw_expr} is not None" if is_or_jump else f"{raw_expr} is None"
                elif "IF_TRUE" in op:
                    cond_str = raw_expr if is_or_jump else f"not {raw_expr}"
                else:
                    cond_str = f"not {raw_expr}" if is_or_jump else raw_expr

                parts.append((cond_str, is_or_jump, t))

            # Assemble with precedence and parentheses.
            # -------------------------------------------
            # Precedence: and > or.
            # We use a recursive builder that identifies subgroups based
            # on jump targets. A jump that targets a point before the current
            # "final exit" defines the boundary of a local subgroup.

            def join_flat(s_idx, e_idx):
                """Linearly joins parts[s_idx:e_idx] without recursion."""
                comb = parts[s_idx][0]
                h_or = False
                for k in range(s_idx + 1, e_idx):
                    c = "or" if parts[k-1][1] else "and"
                    if c == "and" and h_or:
                        comb = f"({comb})"
                        h_or = False

                    next_term = parts[k][0]
                    chained = None
                    if c == "and":
                        chained = collapse_chained_comparisons(comb, next_term, self.beautification_level)
                    if chained:
                        comb = chained
                    else:
                        comb = f"{comb} {c} {next_term}"

                    h_or = h_or or (c == "or")
                return comb, parts[e_idx-1][1], h_or

            def assemble(start_idx, end_idx, context_exit):
                """Assemble parts[start_idx:end_idx]. Returns (expr, next_conn_is_or, has_top_level_or)."""
                if start_idx + 1 == end_idx:
                    return parts[start_idx][0], parts[start_idx][1], False

                subs: list = []  # list of (expr, unit_is_or, has_or)
                i = start_idx
                while i < end_idx:
                    curr_expr, curr_is_or, curr_target = parts[i]
                    j = i + 1

                    if curr_target < context_exit:
                        # Deeper nesting detected.
                        while j < end_idx and group[j][3] < curr_target:
                            j += 1
                        sub_expr, sub_is_or, sub_has_or = assemble(i, j, curr_target)
                        if sub_has_or:
                            sub_expr = f"({sub_expr})"
                            sub_has_or = False
                        subs.append((sub_expr, sub_is_or, sub_has_or))
                    else:
                        # Shared-target grouping (non-recursive).
                        while j < end_idx and parts[j][2] == curr_target:
                            j += 1
                        if j > i + 1:
                            subs.append(join_flat(i, j))
                        else:
                            subs.append((curr_expr, curr_is_or, False))
                    i = j

                combined = subs[0][0]
                has_or = subs[0][2]

                for k in range(1, len(subs)):
                    next_expr, next_is_or, next_has_or = subs[k]
                    conn = "or" if subs[k-1][1] else "and"

                    if conn == "and" and has_or:
                        combined = f"({combined})"
                        has_or = False

                    if conn == "and" and next_has_or:
                        next_expr = f"({next_expr})"

                    chained = None
                    if conn == "and" and not has_or and not next_has_or:
                        chained = collapse_chained_comparisons(combined, next_expr, self.beautification_level)
                    if chained:
                        combined = chained
                    else:
                        combined = f"{combined} {conn} {next_expr}"
                    has_or = has_or or (conn == "or") or next_has_or

                return combined, subs[-1][1], has_or

            combined, _, _ = assemble(0, len(parts), end_target)

            # Register: controlling jump gets combined cond; all others are suppressed.
            # -----------------------------------------------------------
            controlling_idx, controlling_instr, _, _ = group[-1]
            self._compound_cond_map[controlling_instr.offset] = combined

            for _, ginstr, _, _ in group[:-1]:
                self._compound_suppress.add(ginstr.offset)

            i = controlling_idx + 1

    def _is_compound_cjump(self, opname: str) -> bool:
        """
        Identify conditional POP_JUMP_* opcode names that participate in compound boolean expressions.

        Parameters:
                opname (str): The opcode name to test (e.g., "POP_JUMP_IF_FALSE").

        Returns:
                True if `opname` is a POP_JUMP_* variant used for compound boolean conditions (`IF_FALSE`, `IF_TRUE`, `IF_NONE`, or `IF_NOT_NONE`), False otherwise.
        """
        return "POP_JUMP" in opname and (
            "IF_FALSE" in opname
            or "IF_TRUE" in opname
            or "IF_NONE" in opname
            or "IF_NOT_NONE" in opname
        )

    # ── PEP 709 inlined comprehensions (3.12+) ─────────────────────────

    _COMP_KINDS = {"BUILD_LIST": ("[", "]", "LIST_APPEND"),
                   "BUILD_SET": ("{", "}", "SET_ADD"),
                   "BUILD_MAP": ("{", "}", "MAP_ADD")}
    _COMP_SKIP = ("NOP", "CACHE", "NOT_TAKEN", "TO_BOOL")

    def _collapse_inlined_comprehensions(self):
        """
        Replace each PEP 709 inlined comprehension with one synthetic
        INLINED_COMPREHENSION instruction carrying its rendered text.

        Since 3.12 list/set/dict comprehensions are compiled into the enclosing
        code object instead of a nested <listcomp> function::

            <iterable> GET_ITER
            LOAD_FAST_AND_CLEAR x ...      save the comprehension variables
            SWAP; BUILD_LIST 0; SWAP       (3.13 adds a second GET_ITER)
            FOR_ITER end
              <target> <filters / nested fors> <element> LIST_APPEND
              JUMP_BACKWARD
            end: END_FOR [POP_TOP | POP_ITER]
            SWAP; STORE_FAST x ...         restore the saved variables

        plus a cleanup handler (SWAP, POP_TOP, SWAP, STORE_FAST..., RERAISE)
        that the exception table points at. Both the comprehension and its
        handler are removed from the instruction stream. Shapes that are not
        fully recognised are left untouched. Innermost comprehensions are
        collapsed first, so nested ones render recursively.
        """
        instrs = self.instructions
        if not any(ins.opname == "LOAD_FAST_AND_CLEAR" for ins in instrs):
            return
        try:
            entries = list(dis.Bytecode(self.code_obj).exception_entries)
        except Exception:
            entries = []
        i = len(instrs) - 1
        while i >= 0:
            if (instrs[i].opname == "LOAD_FAST_AND_CLEAR"
                    and (i == 0 or instrs[i - 1].opname != "LOAD_FAST_AND_CLEAR")):
                match = self._match_inlined_comprehension(i)
                if match is not None:
                    end, rendered, consumer = match
                    first, last = instrs[i].offset, instrs[end].offset
                    synth = BytecodeInstruction(
                        opcode=-1, opname="INLINED_COMPREHENSION", arg=None,
                        argval=rendered, offset=first,
                        starts_line=instrs[i].starts_line,
                        is_jump_target=instrs[i].is_jump_target,
                        argrepr=rendered[0] + "..." + rendered[3],
                    )
                    instrs[i:end + 1] = [synth] + ([consumer] if consumer else [])
                    targets = {e.target for e in entries if first <= e.start <= last}
                    self._drop_comprehension_handlers(targets)
                    i = min(i, len(instrs) - 1)
            i -= 1

    def _drop_comprehension_handlers(self, targets):
        """Remove the SWAP/POP_TOP/SWAP/STORE_FAST.../RERAISE cleanup blocks."""
        instrs = self.instructions
        for target in targets:
            idx = next((k for k, ins in enumerate(instrs) if ins.offset == target), None)
            if idx is None:
                continue
            k = idx
            while k < len(instrs) and instrs[k].opname in (
                    "SWAP", "POP_TOP", "STORE_FAST", "STORE_FAST_STORE_FAST"):
                k += 1
            if k < len(instrs) and instrs[k].opname == "RERAISE":
                del instrs[idx:k + 1]

    @staticmethod
    def _store_names(ins) -> Optional[List[str]]:
        if ins.opname in ("STORE_FAST", "STORE_DEREF", "STORE_NAME", "STORE_GLOBAL"):
            return [str(ins.argval)]
        if ins.opname == "STORE_FAST_STORE_FAST" and isinstance(ins.argval, (tuple, list)):
            return [str(n) for n in ins.argval]
        return None

    def _match_inlined_comprehension(self, start: int):
        """
        Match an inlined comprehension whose first LOAD_FAST_AND_CLEAR is at
        *start*. Returns (index of its last instruction, rendered parts) or
        None. Rendered parts are (open, element, clauses_after_iterable, close,
        first_target); the iterable itself is already on the stack. The third
        item is the instruction that consumes the result when the compiler
        moved it ahead of the restores (else None).
        """
        instrs = self.instructions
        n_ins = len(instrs)
        k = start
        saved: List[str] = []
        while k < n_ins and instrs[k].opname == "LOAD_FAST_AND_CLEAR":
            saved.append(str(instrs[k].argval))
            k += 1
        if not (k + 2 < n_ins and instrs[k].opname == "SWAP"
                and instrs[k + 1].opname in self._COMP_KINDS and not instrs[k + 1].arg
                and instrs[k + 2].opname == "SWAP"):
            return None
        open_, close, acc_op = self._COMP_KINDS[instrs[k + 1].opname]
        k += 3
        if k < n_ins and instrs[k].opname == "GET_ITER":   # 3.13
            k += 1
        if k >= n_ins or instrs[k].opname != "FOR_ITER":
            return None
        head = instrs[k]
        end_idx = next((j for j in range(k + 1, n_ins)
                        if instrs[j].offset == self._get_jump_target(head)), None)
        if end_idx is None or instrs[end_idx].opname != "END_FOR":
            return None
        body = instrs[k + 1:end_idx]

        # Restore sequence: [3.13+: POP_TOP | POP_ITER] then either SWAP (the result
        # stays on the stack) or, when the result is consumed at once, the
        # consuming store / POP_TOP -- followed by STORE_FAST of each saved name.
        j = end_idx + 1
        # 3.12's END_FOR pops the iterator itself; 3.13+ emit a separate pop.
        if (j < n_ins and self.target_version >= (3, 13)
                and instrs[j].opname in ("POP_TOP", "POP_ITER")):
            j += 1
        consumer = None
        if j < n_ins and instrs[j].opname == "SWAP":
            j += 1
        elif j < n_ins and (instrs[j].opname == "POP_TOP" or (
                self._store_names(instrs[j]) is not None
                and len(self._store_names(instrs[j])) == 1)):
            consumer = instrs[j]
            j += 1
        else:
            return None
        restored: List[str] = []
        while j < n_ins and len(restored) < len(saved):
            names = self._store_names(instrs[j])
            if names is None or instrs[j].opname not in ("STORE_FAST", "STORE_FAST_STORE_FAST"):
                break
            restored.extend(names)
            j += 1
        if sorted(restored) != sorted(saved):
            return None

        parsed = self._parse_comprehension_body(body, head, acc_op)
        if parsed is None:
            return None
        first_target, clauses, element = parsed
        return j - 1, (open_, element, clauses, close, first_target), consumer

    def _split_superinstructions(self, body: list) -> list:
        """
        Split 3.13+'s STORE_FAST_LOAD_FAST into a STORE_FAST and a LOAD_FAST.
        The LOAD_FAST gets the odd offset+1, which no jump can target, so
        offset-keyed lookups keep resolving to the STORE_FAST.
        """
        out = []
        for ins in body:
            if ins.opname == "STORE_FAST_LOAD_FAST" and isinstance(ins.argval, (tuple, list)):
                store, load = ins.argval
                out.append(BytecodeInstruction(ins.opcode, "STORE_FAST", None, store,
                                               ins.offset, ins.starts_line, ins.is_jump_target))
                out.append(BytecodeInstruction(ins.opcode, "LOAD_FAST", None, load,
                                               ins.offset + 1, None, False))
            else:
                out.append(ins)
        return out

    def _parse_comp_target(self, body: list, pos: int):
        """Parse a for-target (name or tuple unpacking). Returns (text, pos) or None."""
        pending: List[str] = []
        parsed = self._parse_store_target(body, pos, pending)
        return None if parsed is None or pending else (parsed[0], parsed[2])

    def _parse_store_target(self, body: list, pos: int, pending: Optional[List[str]] = None):
        """
        Parse the assignment target that starts at ``body[pos]``.

        A target is a name (``STORE_FAST``/``STORE_NAME``/``STORE_GLOBAL``/
        ``STORE_DEREF``), or an unpacking: ``UNPACK_SEQUENCE n`` followed by *n* targets,
        or ``UNPACK_EX`` for a starred one (#156), whose operand holds the number of
        targets before the star in its low byte and after it in the high byte, with the
        starred target in between. 3.13+'s ``STORE_FAST_STORE_FAST`` stores two names,
        which may belong to two consecutive targets; *pending* carries the second.

        Returns:
            ``(text, is_tuple, next_pos)``, or None when the code there is not a plain
            target (an attribute or subscript store, for example).
        """
        if pending is None:
            pending = []
        if pending:
            return pending.pop(0), False, pos
        while pos < len(body) and body[pos].opname in ("NOP", "CACHE", "NOT_TAKEN", "RESUME"):
            pos += 1
        ins = body[pos] if pos < len(body) else None
        names = self._store_names(ins) if ins is not None else None
        if names:
            pending.extend(names[1:])
            return names[0], False, pos + 1
        shape = _unpack_shape(ins)
        if shape is None:
            return None
        count, star = shape
        parts: List[str] = []
        pos += 1
        for i in range(count):
            sub = self._parse_store_target(body, pos, pending)
            if sub is None:
                return None
            text, nested, pos = sub
            if nested:
                text = f"({text})"
            parts.append("*" + text if i == star else text)
        text = ", ".join(parts) + ("," if count == 1 else "")
        return (text, True, pos) if not pending else None

    def _eval_comp_slice(self, instrs: list, count: int = 1, consumer=None) -> Optional[List[str]]:
        """
        Evaluate an instruction slice to *count* expressions. Jumps are allowed
        only when they stay inside the slice (conditional expressions), which
        the ternary prescan then reconstructs. *consumer* is the instruction
        that uses the values; the prescan sees it as context but it is not run.
        """
        if any(ins.opname in ("FOR_ITER", "RETURN_VALUE", "RETURN_CONST",
                              "RAISE_VARARGS", "RERAISE") for ins in instrs):
            return None
        offsets = {ins.offset for ins in instrs}
        end = consumer.offset if consumer is not None else (instrs[-1].offset + 2 if instrs else 0)
        has_jumps = False
        for ins in instrs:
            if "JUMP" in ins.opname:
                has_jumps = True
                if self._get_jump_target(ins) not in offsets and self._get_jump_target(ins) != end:
                    return None
        sub = _pick_decompiler_class(self)(self.code_obj, self.indent_level,
                                          self.beautification_level,
                                          target_version=self.target_version)
        try:
            run = [ins for ins in instrs if ins.opname not in self._COMP_SKIP]
            sub.instructions = run + ([consumer] if consumer is not None else [])
            if has_jumps:
                sub._prescan_ternaries()
            sub.pc = 0
            while sub.pc < len(run):
                ins = sub.instructions[sub.pc]
                sub.pc += 1
                sub._handle_instruction(ins)
        except Exception:
            return None
        if sub.reconstructed or len(sub.stack) != count:
            return None
        return [str(v) for v in sub.stack]

    def _parse_comprehension_body(self, body: list, head, acc_op: str):
        """
        Parse the loop body of an inlined comprehension into
        (first_target, clauses_text, element_text), or None.
        """
        body = list(body)
        heads = [head.offset]
        parsed = self._parse_comp_target(body, 0)
        if parsed is None:
            return None
        first_target, pos = parsed
        clauses: List[str] = []
        n = len(body)

        def is_back_to_head(ins):
            return ins.opname.startswith("JUMP_BACKWARD") and self._get_jump_target(ins) == heads[-1]

        def filter_form(k):
            """'keep' / 'skip' when body[k] is a comprehension filter jump, else None."""
            target = self._get_jump_target(body[k])
            m = k + 1
            while m < n and body[m].opname in self._COMP_SKIP:
                m += 1
            if m + 1 < n and is_back_to_head(body[m]) and body[m + 1].offset == target:
                return "keep"
            if target == heads[-1] or any(is_back_to_head(x) and x.offset == target for x in body):
                return "skip"
            return None   # a jump inside an expression (e.g. a conditional expression)

        while pos < n:
            # Find the next control instruction from pos.
            k = pos
            while k < n and not (
                    (body[k].opname.startswith("POP_JUMP_IF") and filter_form(k))
                    or body[k].opname == acc_op
                    or (body[k].opname == "GET_ITER" and k + 1 < n and body[k + 1].opname == "FOR_ITER")):
                k += 1
            if k >= n:
                return None
            ctl = body[k]
            if ctl.opname == acc_op:
                vals = self._eval_comp_slice(body[pos:k], 2 if acc_op == "MAP_ADD" else 1, ctl)
                if vals is None:
                    return None
                element = f"{vals[0]}: {vals[1]}" if acc_op == "MAP_ADD" else vals[0]
                # The rest must be the back-jumps closing each nested loop.
                rest = [x for x in body[k + 1:] if x.opname not in self._COMP_SKIP]
                expect = list(reversed(heads))
                closing = []
                for x in rest:
                    if x.opname in ("END_FOR", "POP_TOP", "POP_ITER"):
                        continue
                    if not x.opname.startswith("JUMP_BACKWARD"):
                        return None
                    closing.append(self._get_jump_target(x))
                if closing != expect:
                    return None
                return first_target, "".join(clauses), element
            if ctl.opname == "GET_ITER":
                vals = self._eval_comp_slice(body[pos:k])
                if vals is None:
                    return None
                inner_head = body[k + 1]
                tgt = self._parse_comp_target(body, k + 2)
                if tgt is None:
                    return None
                clauses.append(f" for {tgt[0]} in {vals[0]}")
                heads.append(inner_head.offset)
                pos = tgt[1]
                continue
            # Filter: cond POP_JUMP_IF_x  [NOT_TAKEN]  JUMP_BACKWARD head
            vals = self._eval_comp_slice(body[pos:k])
            if vals is None:
                return None
            cond = vals[0]
            target = self._get_jump_target(ctl)
            m = k + 1
            while m < n and body[m].opname in self._COMP_SKIP:
                m += 1
            op = ctl.opname
            jumps_when = {"POP_JUMP_IF_TRUE": f"{cond}", "POP_JUMP_IF_FALSE": f"not ({cond})",
                          "POP_JUMP_IF_NONE": f"{cond} is None",
                          "POP_JUMP_IF_NOT_NONE": f"{cond} is not None"}.get(op)
            if jumps_when is None:
                return None
            if (m < n and is_back_to_head(body[m]) and m + 1 < n
                    and body[m + 1].offset == target):
                # Jump skips the `continue` back-jump: the jump condition keeps the item.
                keep = jumps_when
                pos = m + 1
            elif target == heads[-1] or any(is_back_to_head(x) and x.offset == target for x in body):
                # Jump goes straight back to the loop head: it drops the item.
                keep = {"POP_JUMP_IF_TRUE": f"not ({cond})", "POP_JUMP_IF_FALSE": f"{cond}",
                        "POP_JUMP_IF_NONE": f"{cond} is not None",
                        "POP_JUMP_IF_NOT_NONE": f"{cond} is None"}[op]
                pos = k + 1
            else:
                return None
            keep = keep[5:-1] if keep.startswith("not (not ") and keep.endswith(")") else keep
            clauses.append(f" if {keep}")
        return None

    def _op_inlined_comprehension(self, instr: BytecodeInstruction):
        open_, element, clauses, close, target = instr.argval
        iterable = str(self.stack.pop()) if self.stack else "?"
        self.stack.append(f"{open_}{element} for {target} in {iterable}{clauses}{close}")

    def _eval_cond_expr(self, instrs: list) -> str:
        """
        Constructs a boolean expression string from a sequence of bytecode instructions.

        Takes a list of bytecode instructions that form a pure boolean sub‑expression and reconstructs the equivalent Python condition as a single expression string.

        Parameters:
            instrs (list): Sequence of instruction objects (bytecode slice) that comprise the boolean sub-expression.

        Returns:
            str: The reconstructed boolean expression, or "?" if the expression cannot be determined.
        """
        return self._eval_ternary_branch(instrs)

    _SHORT_CIRCUIT_WORDS = {"JUMP_IF_TRUE_OR_POP": "or", "JUMP_IF_FALSE_OR_POP": "and",
                            "POP_JUMP_IF_TRUE": "or", "POP_JUMP_IF_FALSE": "and"}

    def _short_circuit_at(self, instrs: list, i: int) -> Optional[Tuple[str, int, int]]:
        """
        Recognize a value-context `and`/`or` operator starting at ``instrs[i]``.

        3.9 spells it ``JUMP_IF_TRUE_OR_POP`` / ``JUMP_IF_FALSE_OR_POP``; 3.12+ spells it
        ``COPY 1; [TO_BOOL;] POP_JUMP_IF_TRUE/FALSE; [NOT_TAKEN;] POP_TOP``. Returns
        ``(word, target_offset, index_of_right_operand)`` or None.
        """
        ins = instrs[i]
        if ins.opname in ("JUMP_IF_TRUE_OR_POP", "JUMP_IF_FALSE_OR_POP"):
            return self._SHORT_CIRCUIT_WORDS[ins.opname], self._get_jump_target(ins), i + 1
        if ins.opname != "COPY" or ins.arg != 1:
            return None
        j = i + 1
        while j < len(instrs) and instrs[j].opname in ("TO_BOOL", "NOT_TAKEN", "CACHE"):
            j += 1
        if j >= len(instrs) or instrs[j].opname not in ("POP_JUMP_IF_TRUE", "POP_JUMP_IF_FALSE"):
            return None
        k = j + 1
        while k < len(instrs) and instrs[k].opname in ("NOT_TAKEN", "CACHE"):
            k += 1
        if k >= len(instrs) or instrs[k].opname != "POP_TOP":
            return None
        return self._SHORT_CIRCUIT_WORDS[instrs[j].opname], self._get_jump_target(instrs[j]), k + 1

    def _guarded_boolop_at(self, instrs: list, i: int) -> Optional[Tuple[str, str, int, int, int]]:
        """
        Recognize ``X and Y or Z`` / ``X or Y and Z`` starting at ``instrs[i]``: when the
        first operator decides, X is discarded and evaluation continues at Z, while Y ends
        in the second operator, which jumps past Z. Two encodings:

        * 3.9-3.13: the first operator is a plain ``POP_JUMP_IF_FALSE/TRUE`` to Z;
        * 3.14: it is a full ``COPY 1 ...`` operator whose target is the ``POP_TOP`` it
          shares with the second operator, just before Z.

        Returns ``(first_word, second_word, y_start, unit_index, z_index)``, where
        ``unit_index`` is where Y's operator starts, or None.
        """
        ins = instrs[i]
        copy_unit = self._short_circuit_at(instrs, i) if ins.opname == "COPY" else None
        if copy_unit is not None:
            first, target, y_start = copy_unit
            pop_idx = next((n for n in range(y_start, len(instrs)) if instrs[n].offset == target), None)
            if pop_idx is None or instrs[pop_idx].opname != "POP_TOP":
                return None
            z_idx = pop_idx + 1
        elif ins.opname in ("POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE"):
            target = self._get_jump_target(ins)
            z_idx = next((n for n in range(i + 1, len(instrs)) if instrs[n].offset == target), None)
            if z_idx is None:
                return None
            first = "and" if ins.opname == "POP_JUMP_IF_FALSE" else "or"
            y_start = i + 1
        else:
            return None
        for u in range(y_start, z_idx):
            found = self._short_circuit_at(instrs, u)
            if found is not None and found[2] == z_idx and found[0] != first:
                return first, found[0], y_start, u, z_idx
        return None

    def _branch_is_expression(self, seq: list, end_offset: int) -> bool:
        """
        True if *seq* (one branch of a conditional expression, ending where its value is
        consumed at *end_offset*) is a pure expression, value-context `and`/`or` included.
        """
        def lands_in_branch(ins, target):
            return isinstance(target, int) and ins.offset < target <= end_offset

        i = 0
        ok = bool(seq)
        while ok and i < len(seq):
            guarded = self._guarded_boolop_at(seq, i)
            found = self._short_circuit_at(seq, i)
            if guarded is not None:
                _, _, y_start, unit, z_idx = guarded
                inner = self._short_circuit_at(seq, unit)
                ok = (inner is not None and lands_in_branch(seq[unit], inner[1])
                      and self._branch_is_expression(seq[y_start:unit], end_offset)
                      and self._branch_is_expression(seq[z_idx:], end_offset))
                break
            if found is not None:
                # an operator needs its right operand before the branch ends
                ok = lands_in_branch(seq[i], found[1]) and found[2] < len(seq)
                i = found[2]
            else:
                ok = seq[i].opname in _TERNARY_PURE
                i += 1
        return ok

    def _uses_outer_stack(self, seq: list) -> bool:
        """
        True if evaluating *seq* would consume values pushed before it, i.e. the branch
        is the tail of a larger expression (`foo(1, a)` where `foo` and `1` were pushed
        before the test) rather than an expression of its own.
        """
        depth = 0
        for ins in seq:
            try:
                arg = ins.arg if ins.opcode >= dis.HAVE_ARGUMENT else None
                depth += dis.stack_effect(ins.opcode, arg, jump=False)
            except (ValueError, TypeError):
                continue
            if depth < 0:
                return True
        return False

    def _eval_ternary_branch(self, instrs: list) -> str:
        mini_stack: list = []
        idx = 0
        while idx < len(instrs):
            guarded = self._guarded_boolop_at(instrs, idx) if mini_stack else None
            if guarded is not None:
                # `(X and Y) or Z` / `(X or Y) and Z`, with Z running to the end.
                first, second, y_start, unit, z_idx = guarded
                left = mini_stack.pop()
                middle = self._eval_ternary_branch(instrs[y_start:unit])
                right = self._eval_ternary_branch(instrs[z_idx:])
                mini_stack.append(f"(({left} {first} {middle}) {second} {right})")
                idx = len(instrs)
                continue
            found = self._short_circuit_at(instrs, idx)
            if found is not None and mini_stack:
                # `left and/or right`: the right operand runs up to the jump's target
                # (or to the end of the branch, where the operator's value is used).
                word, target, resume = found
                end = next((n for n in range(resume, len(instrs)) if instrs[n].offset >= target), len(instrs))
                left = mini_stack.pop()
                right = self._eval_ternary_branch(instrs[resume:end])
                mini_stack.append(f"({left} {word} {right})")
                idx = end
                continue
            ins = instrs[idx]
            idx += 1
            op = ins.opname
            if op in ("CACHE", "RESUME", "NOT_TAKEN", "COPY_FREE_VARS",
                      "PRECALL", "PUSH_NULL", "TO_BOOL",
                      "RETURN_CONST", "RETURN_VALUE"):
                continue
            if op == "LOAD_FAST_BORROW_LOAD_FAST_BORROW":
                if isinstance(ins.argval, (tuple, list)) and len(ins.argval) >= 2:
                    mini_stack.append(str(ins.argval[0]))
                    mini_stack.append(str(ins.argval[1]))
                continue
            if op in ("LOAD_FAST", "LOAD_NAME", "LOAD_GLOBAL",
                      "LOAD_SMALL_INT", "LOAD_FAST_BORROW",
                      "LOAD_CONST_BORROW", "LOAD_DEREF",
                      "LOAD_GLOBAL_MODULE", "LOAD_CONST"):
                val = ins.argval
                if val is None and op == "LOAD_SMALL_INT":
                    val = ins.arg
                if op == "LOAD_GLOBAL_MODULE" and isinstance(val, (list, tuple)) and val:
                    val = val[0]
                if "CONST" in op or "SMALL_INT" in op:
                    mini_stack.append(self._format_val(val))
                else:
                    mini_stack.append(str(val))
            elif op in ("FORMAT_VALUE", "FORMAT_SIMPLE", "BUILD_STRING", "BUILD_CONST_KEY_MAP", "BUILD_MAP"):
                if op in ("FORMAT_VALUE", "FORMAT_SIMPLE"):
                    val = mini_stack.pop() if mini_stack else "{expr}"
                    mini_stack.append(f"{{{val}}}")
                elif op == "BUILD_STRING":
                    count = int(ins.arg) if ins.arg is not None else 0
                    parts = []
                    for _ in range(count):
                        if mini_stack:
                            p_val = mini_stack.pop()
                            p = self._format_fstring_part(str(p_val))
                            parts.insert(0, p)
                    mini_stack.append(f'f"{"".join(parts)}"')
                elif op in ("BUILD_MAP", "BUILD_CONST_KEY_MAP"):
                    count = int(ins.arg) if ins.arg is not None else 0
                    pop_cnt = (count * 2) if op == "BUILD_MAP" else (count + 1)
                    for _ in range(pop_cnt):
                        if mini_stack: mini_stack.pop()
                    mini_stack.append("{}") # Placeholder for dict
            elif op in ("GET_ATTR", "LOAD_ATTR", "LOAD_METHOD"):
                if mini_stack:
                    obj = mini_stack.pop()
                    obj_str = str(obj)
                    if obj_str.startswith("-"):
                        obj_str = self._wrap_negative_literal(obj_str)
                    mini_stack.append(f"{obj_str}.{ins.argval}")
            elif op in ("CALL", "CALL_FUNCTION", "CALL_METHOD"):
                num = int(ins.arg) if ins.arg is not None else 0
                args = []
                for _ in range(num):
                    args.insert(0, mini_stack.pop() if mini_stack else "?")
                func = mini_stack.pop() if mini_stack else "?"
                if " + NULL" in str(func) or "|NULL" in str(func):
                    func = str(func).split(" + ")[0].split("|")[0]
                mini_stack.append(f"{func}({', '.join(args)})")
            elif op == "COMPARE_OP":
                if len(mini_stack) >= 2:
                    right, left = mini_stack.pop(), mini_stack.pop()
                    op_sym = str(ins.argval)
                    m = re.search(r"\(([^)]+)\)", op_sym)
                    if m:
                        op_sym = m.group(1)
                    mini_stack.append(f"{left} {op_sym} {right}")
            elif op == "BINARY_OP":
                if len(mini_stack) >= 2:
                    right, left = mini_stack.pop(), mini_stack.pop()
                    op_map = {0: "+", 1: "&", 2: "//", 3: "<<", 4: "@", 5: "*",
                              6: "%", 7: "|", 8: "**", 9: ">>", 10: "-", 11: "/", 12: "^",
                              26: "[]"}
                    sym = op_map.get(int(ins.arg) if ins.arg is not None else -1, "?")
                    if sym == "[]":
                        mini_stack.append(f"{left}[{right}]")
                    else:
                        mini_stack.append(f"({left} {sym} {right})")
            elif op in ("IS_OP",):
                if len(mini_stack) >= 2:
                    right, left = mini_stack.pop(), mini_stack.pop()
                    sym = "is not" if bool(ins.arg) else "is"
                    mini_stack.append(f"{left} {sym} {right}")
            elif op in ("CONTAINS_OP",):
                if len(mini_stack) >= 2:
                    container, item = mini_stack.pop(), mini_stack.pop()
                    sym = "not in" if bool(ins.arg) else "in"
                    mini_stack.append(f"{item} {sym} {container}")
            elif op in _UNARY_SYMBOLS or (
                    op == "CALL_INTRINSIC_1" and "UNARY_POSITIVE" in str(ins.argrepr)):
                if mini_stack:
                    symbol = _UNARY_SYMBOLS.get(op, "+")
                    mini_stack.append(f"({symbol}{_unary_operand(mini_stack.pop())})")
            elif op == "BUILD_SLICE":
                # Python 3.9: BUILD_SLICE N pops stop,start (and optionally step)
                argc = ins.arg if ins.arg is not None else 2
                step = str(mini_stack.pop()) if (argc == 3 and mini_stack) else None
                stop = str(mini_stack.pop()) if mini_stack else 'None'
                start = str(mini_stack.pop()) if mini_stack else 'None'
                s = '' if start == 'None' else start
                e = '' if stop == 'None' else stop
                if step is None or step == 'None':
                    mini_stack.append(f'{s}:{e}')
                else:
                    mini_stack.append(f'{s}:{e}:{step}')
            elif op == "BINARY_SLICE":
                # Python 3.11+: pops stop, start, container
                if len(mini_stack) >= 3:
                    stop, start, container = mini_stack.pop(), mini_stack.pop(), mini_stack.pop()
                    s = "" if str(start) == "None" else str(start)
                    e = "" if str(stop) == "None" else str(stop)
                    mini_stack.append(f"{container}[{s}:{e}]")
            elif op == "BINARY_SUBSCR":
                if len(mini_stack) >= 2:
                    key, obj = mini_stack.pop(), mini_stack.pop()
                    # If key is a slice-notation string (from BUILD_SLICE), keep it as-is
                    if isinstance(key, str) and ':' in key and not key.startswith("'"):
                        mini_stack.append(f"{obj}[{key}]")
                    else:
                        mini_stack.append(f"{obj}[{key}]")
            elif op in (
                "BINARY_ADD", "BINARY_SUBTRACT", "BINARY_MULTIPLY",
                "BINARY_TRUE_DIVIDE", "BINARY_FLOOR_DIVIDE", "BINARY_MODULO",
                "BINARY_POWER", "BINARY_LSHIFT", "BINARY_RSHIFT",
                "BINARY_AND", "BINARY_OR", "BINARY_XOR", "BINARY_MATRIX_MULTIPLY",
            ):
                # Python 3.9 named binary opcodes
                _bin_map = {
                    "BINARY_ADD": "+", "BINARY_SUBTRACT": "-",
                    "BINARY_MULTIPLY": "*", "BINARY_TRUE_DIVIDE": "/",
                    "BINARY_FLOOR_DIVIDE": "//", "BINARY_MODULO": "%",
                    "BINARY_POWER": "**", "BINARY_LSHIFT": "<<",
                    "BINARY_RSHIFT": ">>", "BINARY_AND": "&",
                    "BINARY_OR": "|", "BINARY_XOR": "^",
                    "BINARY_MATRIX_MULTIPLY": "@",
                }
                if len(mini_stack) >= 2:
                    right, left = mini_stack.pop(), mini_stack.pop()
                    sym = _bin_map.get(op, "?")
                    l_s = str(left)
                    r_s = str(right)
                    # Add parens only when the left operand is a compound expression
                    # (has spaces and is not already a parenthesised atom or string literal)

                    def _needs_parens(s: str) -> bool:
                        if not s or s[0] in ("'", '"', "(", "[", "{"):
                            return False  # string literal or already wrapped
                        return " " in s and not s.startswith("(") and s.endswith(")")
                    if _needs_parens(l_s):
                        l_s = f"({l_s})"
                    mini_stack.append(f"{l_s} {sym} {r_s}")
        return str(mini_stack[-1]) if mini_stack else "?"

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
        self._inner_back_jumps = self._find_inner_back_jumps()
        self._dup_guard_exits = set()

        for jb_idx, jb in enumerate(self.instructions):
            if not self._is_backward_instruction(jb):
                continue
            # A handler that ends by jumping back to the code after its `try` (the
            # handler table sits at the end of the function) is not a loop.
            # A handler that continues the loop is not the loop's closing back-edge either.
            if self._is_handler_exit_back_jump(jb_idx):
                continue
            # A `continue`, or the end of an if/else branch threaded to the loop
            # head, is not the loop's own back-edge and defines no guard.
            if jb.offset in self._inner_back_jumps:
                continue
            body_start = self._get_jump_target(jb)

            # The loop guard: POP_JUMP_IF_FALSE immediately before body_start
            # whose target is beyond JUMP_BACKWARD (the loop-exit path).
            # We want the CLOSEST (highest offset) qualifying guard to correctly
            # associate nested inner loops with their own guard.
            # The while-loop guard: the conditional jump that exits the loop.
            # Python 3.12-: guard is BEFORE body_start (condition checked at top).
            # Python 3.14+: guard may be AFTER body_start (do-while style, condition
            #               checked at bottom, just before JUMP_BACKWARD).
            # Strategy: find the conditional jump that is CLOSEST to body_start
            # (either just before or anywhere before jb.offset) whose target
            # is BEYOND jb.offset (i.e. exits the loop).
            # Also: dis may leave argval unresolved on 3.14, so compute the
            # absolute target two ways and take the larger.
            # Trivial opcodes that can appear between a guard's fall-through
            # and the loop body_start without meaning the guard is not a real
            # while-guard (e.g. NOT_TAKEN hint, NOP, RESUME, etc.).

            # A `while True:` loop is headed by a NOP (3.12+): it has no
            # condition at its head, so no conditional jump after the head can
            # be its guard. Those are `if ...: break` statements.
            head_instr = next(
                (i for i in self.instructions if i.offset == body_start), None
            )
            head_is_nop = head_instr is not None and head_instr.opname == "NOP"
            head_is_for = head_instr is not None and head_instr.opname in _TRY_LOOP_HEADS
            if head_instr is not None and head_instr.opname == "FOR_ITER":
                # A loop headed by FOR_ITER is a `for` loop and has no `while` guard; a
                # conditional jump in its body is an `if`, even one that lands right after
                # the back-edge (`if c: return x` as the last statement).
                continue
            guard_offset = -1
            guard_dist = float('inf')   # distance from body_start
            for ins in self.instructions:
                if head_is_nop and ins.offset > body_start:
                    continue
                # A guard after the loop head is the head's own condition only
                # if nothing but condition-evaluating code precedes it. A plain
                # store in between (not a walrus) is a body statement, which
                # makes this conditional an `if` inside the body.
                if (not head_is_for and ins.offset > body_start
                        and self._has_body_store_between(body_start, ins.offset)):
                    continue
                if not ("POP_JUMP_IF_FALSE" in ins.opname
                        or "POP_JUMP_IF_TRUE" in ins.opname
                        or "JUMP_IF_FALSE" in ins.opname
                        or "JUMP_IF_TRUE" in ins.opname):
                    continue
                # Must be within the loop (not after jb)
                if ins.offset > jb.offset:
                    continue
                # Compute absolute target robustly
                t_argval = self._get_jump_target(ins)
                if t_argval >= 0:
                    t = t_argval
                else:
                    arg = ins.arg if ins.arg is not None else 0
                    # Fallback for unresolved target (e.g. 3.14+ relative jumps)
                    t = ins.offset + 2 + (arg * 2)

                # Target must exit the loop: land strictly beyond the
                # back-edge. A jump that lands ON the back-edge itself is an
                # `if` whose false branch skips to the loop head (e.g.
                # `while True: ...; if x: break`), not the loop's guard.
                if t <= jb.offset and not (
                        ins.offset > body_start and (
                            self._falls_to_outer_back_jump(ins, body_start)
                            or t < body_start)):  # 3.9/3.10: exit jumps straight to an outer head
                    continue
                # For guards positioned BEFORE body_start: the fall-through
                # (ins.offset+2) must reach body_start via only trivial
                # instructions (NOP, NOT_TAKEN, RESUME, CACHE).  If there are
                # real (store/load/call) instructions between the fall-through
                # and body_start, this is an outer 'if' guard, not a while-guard.
                if ins.offset < body_start:
                    fall_through = ins.offset + 2
                    only_trivial = True
                    for scan in self.instructions:
                        if fall_through <= scan.offset < body_start:
                            if scan.opname not in _WHILE_TRIVIAL_OPS:
                                only_trivial = False
                                break
                    if not only_trivial:
                        continue  # outer if-guard, skip it
                # Pick the guard closest to body_start (from either direction)
                dist = abs(ins.offset - body_start)
                if dist < guard_dist:
                    guard_dist = dist
                    guard_offset = ins.offset
            if guard_offset >= 0:
                self._while_header_targets[body_start] = guard_offset
                # 3.12/3.13 copy the loop condition to the bottom of the body.
                # Suppress exactly that copy's exit jumps, never body-level ifs.
                dup_jumps = self._dup_guard_jump_offsets(jb)
                dup_jumps.discard(guard_offset)
                self._while_body_offsets.update(dup_jumps)
                for ins in self.instructions:
                    if ins.offset in dup_jumps:
                        self._dup_guard_exits.add(self._get_jump_target(ins))
            else:
                # while-True pattern: no conventional guard found, but the
                # back-edge target is a known jump target (loop start).
                # Record sentinel -1 so _handle_instruction emits 'while True:'
                body_instr = next(
                    (ins for ins in self.instructions if ins.offset == body_start), None
                )
                # Do NOT classify FOR_ITER / GET_ITER / GET_ANEXT as while-True:
                # those are for-loop heads whose back-edge is the loop iterator
                # stepping, not an unconditional while-True loop body.
                if (body_instr and body_instr.is_jump_target
                        and body_instr.opname not in _TRY_LOOP_HEADS):
                    self._while_header_targets[body_start] = -1

    def _has_body_store_between(self, start: int, end: int) -> bool:
        """
        Return True if a plain assignment (a STORE_* that is not the store half of a
        walrus, which duplicates its value first) lies in the offset range [start, end).
        """
        prev = None
        for ins in self.instructions:
            if ins.offset < start:
                continue
            if ins.offset >= end:
                break
            if (ins.opname.startswith("STORE_")
                    and not (prev is not None and prev.opname in ("DUP_TOP", "COPY"))):
                return True
            prev = ins
        return False

    def _is_handler_exit_back_jump(self, idx: int) -> bool:
        """
        Return True if the backward jump at self.instructions[idx] leaves an
        exception handler (POP_EXCEPT shortly before it, inside a handler zone).
        """
        instrs = self.instructions
        jb = instrs[idx]
        target = self._get_jump_target(jb)
        has_push = any(i.opname == "PUSH_EXC_INFO" for i in instrs)  # absent before 3.11
        if has_push and not any(
                target <= i.offset < jb.offset and i.opname == "PUSH_EXC_INFO" for i in instrs):
            return False
        for back in range(1, 7):
            if idx - back < 0:
                break
            name = instrs[idx - back].opname
            if name == "POP_EXCEPT":
                return True
            if name in ("CALL", "BINARY_OP", "COMPARE_OP"):
                break
        return False

    def _find_inner_back_jumps(self) -> dict:
        """
        Classify backward jumps that are not the closing back-edge of their loop.

        A loop can have several backward jumps: one per `continue`, and one per
        if/else branch that ends the loop body (the compiler threads each of them
        straight to the loop head). Only the last one closes the loop. A backward
        jump J is *inner* when a later backward jump F closes a loop that contains
        J and J targets the same head as F, or (3.12/3.13 `while cond:`, where
        `continue` re-enters at the top condition but F re-enters after it) a
        point before F's target.

        Exception-handler exits are ignored: they are handled separately.

        Returns:
            dict mapping the offset of each inner jump to the offset of the
            back-edge F that closes its loop.
        """
        cands = [
            (i, ins) for i, ins in enumerate(self.instructions)
            if self._is_backward_instruction(ins) and not self._is_handler_exit_back_jump(i)
        ]
        inner = {}
        for _, j in cands:
            jt = self._get_jump_target(j)
            best = None
            for _, f in cands:
                if f.offset <= j.offset:
                    continue
                ft = self._get_jump_target(f)
                if ft > j.offset or jt > ft:
                    continue
                if jt < ft and not self._range_is_trivial(jt, ft) \
                        and not self._is_loop_top_condition(jt, ft, f.offset):
                    continue  # J re-enters an enclosing loop's head, not F's loop
                if best is None or f.offset < best.offset:
                    best = f
            if best is not None:
                inner[j.offset] = best.offset
        return inner

    def _falls_to_outer_back_jump(self, cond, body_start: int) -> bool:
        """
        True if the conditional jump `cond` falls through (past trivial ops) into a
        backward jump to a point before `body_start`: the loop's exit is a jump back
        to an enclosing loop's head (3.14 inner `while` as the last statement of an
        outer loop body).
        """
        instrs = self.instructions
        k = instrs.index(cond) + 1
        while k < len(instrs) and instrs[k].opname in _WHILE_TRIVIAL_OPS:
            k += 1
        if k >= len(instrs) or not self._is_backward_instruction(instrs[k]):
            return False
        return self._get_jump_target(instrs[k]) < body_start

    def _loop_end_after(self, body_start: int) -> int:
        """Offset just past the closing back-edge of the loop headed at `body_start`."""
        instrs = self.instructions
        last = None
        for k, ins in enumerate(instrs):
            if (self._is_backward_instruction(ins) and ins.offset not in self._inner_back_jumps
                    and self._get_jump_target(ins) == body_start):
                last = k
        if last is None:
            return instrs[-1].offset + 2
        return instrs[last + 1].offset if last + 1 < len(instrs) else instrs[last].offset + 2

    def _is_loop_head(self, offset: int) -> bool:
        """True if `offset` is a loop head: a FOR_ITER/iterator head, or a target reached by a non-handler back-jump."""
        head = next((i for i in self.instructions if i.offset == offset), None)
        if head is None:
            return False
        if head.opname in _TRY_LOOP_HEADS:
            return True
        for k, ins in enumerate(self.instructions):
            if (self._is_backward_instruction(ins) and self._get_jump_target(ins) == offset
                    and not self._is_handler_exit_back_jump(k)):
                return True
        return False

    def _has_exceptional_finally_copy(self, merge_off: int) -> bool:
        """
        True if the code at `merge_off` is a real `finally` body.

        The compiler emits a `finally` body twice: inline on the normal path, and
        again in a handler that re-raises (`PUSH_EXC_INFO; <body>; RERAISE`). Code
        that merely follows a `try`/`except` has no such second copy.
        """
        instrs = self.instructions
        entries = self._exception_entries()

        owned_by = None
        if entries is not None:
            # The `finally` handler protects the `try` body and its `except` handlers,
            # so its table entries overlap them. Identical code elsewhere in the loop
            # (another try's cleanup) does not. A body may be covered by several
            # ranges that share one handler (a `continue` splits it).
            body_ends = [e for e in entries if e.end == merge_off]
            regions = [(e.start, e.end) for e in body_ends]
            pei_offsets = sorted(i.offset for i in instrs if i.opname == "PUSH_EXC_INFO")
            for h in {e.target for e in body_ends}:
                nxt = next((o for o in pei_offsets if o > h), instrs[-1].offset + 2)
                regions.append((h, nxt))
            owned_by = {
                e.target for e in entries
                if any(e.start < r_end and e.end > r_start for r_start, r_end in regions)
            }

        for k, ins in enumerate(instrs):
            if ins.opname != "PUSH_EXC_INFO" or (owned_by is not None and ins.offset not in owned_by):
                continue
            copy = []
            for nxt in instrs[k + 1:]:
                if nxt.opname in ("RERAISE", "CHECK_EXC_MATCH", "WITH_EXCEPT_START"):
                    break
                copy.append(nxt)
            else:
                continue
            if not copy or instrs[k + 1 + len(copy)].opname != "RERAISE":
                continue
            # The inline copy has the same instructions as the handler copy. They are
            # compared in normalized form because the optimizer may have specialized
            # one copy and not the other (3.13+ borrowed and fused loads).
            merge_idx = next((n for n, i in enumerate(instrs) if i.offset >= merge_off), None)
            if merge_idx is not None and _match_finally_copy(instrs, merge_idx, _copy_signature(copy)) is not None:
                return True
        return False

    def _range_is_trivial(self, start: int, end: int) -> bool:
        """True if [start, end) holds only no-op style instructions."""
        return all(i.opname in _WHILE_TRIVIAL_OPS for i in self.instructions if start <= i.offset < end)

    def _is_loop_top_condition(self, start: int, end: int, loop_end: int) -> bool:
        """
        True if the instructions in [start, end) are nothing but a loop's top
        condition: no statements, ending in a conditional jump that leaves the loop
        (target beyond the closing back-edge at `loop_end`).
        """
        seq = [i for i in self.instructions if start <= i.offset < end and i.opname not in _WHILE_TRIVIAL_OPS]
        if not seq:
            return False
        last = seq[-1]
        if not last.opname.startswith("POP_JUMP_IF"):
            return False
        t = self._get_jump_target(last)
        if not (isinstance(t, int) and t > loop_end):
            return False
        for k, ins in enumerate(seq):
            name = ins.opname
            if name.startswith("POP_JUMP_IF"):
                tt = self._get_jump_target(ins)
                if not (isinstance(tt, int) and tt > loop_end):
                    return False
            elif name.startswith("STORE_"):
                if not (k > 0 and seq[k - 1].opname in ("COPY", "DUP_TOP")):
                    return False
            elif name in _STATEMENT_BOUNDARY_OPS or name.startswith(("JUMP", "DELETE_")):
                return False
        return True

    def _dup_guard_jump_offsets(self, jb: BytecodeInstruction) -> set:
        """
        Offsets of the exit jumps of a loop condition duplicated at the bottom of
        the loop body (3.12/3.13 layout), or an empty set when there is none.

        The copy sits directly before the closing back-edge `jb` and ends in a
        conditional jump that leaves the loop. Everything before the copy's first
        instruction is a body statement; body-level `if` jumps stay out of the set.
        """
        instrs = self.instructions
        idx = next((i for i, ins in enumerate(instrs) if ins is jb), -1)
        if idx < 0:
            return set()

        def is_cond_jump(ins):
            return ins.opname.startswith("POP_JUMP_IF")

        def exits_loop(ins):
            t = self._get_jump_target(ins)
            return isinstance(t, int) and t > jb.offset

        k = idx - 1
        while k >= 0 and instrs[k].opname in _WHILE_TRIVIAL_OPS:
            k -= 1
        if k < 0 or not (is_cond_jump(instrs[k]) and exits_loop(instrs[k])):
            return set()
        # `jb` itself leaving a nested loop (back to an enclosing head) is that
        # loop's exit path, not a duplicated condition.
        jb_target = self._get_jump_target(jb)
        for fi, f in enumerate(instrs):
            if (f.offset > jb.offset and self._is_backward_instruction(f)
                    and not self._is_handler_exit_back_jump(fi)):
                ft = self._get_jump_target(f)
                if jb_target < ft <= jb.offset:
                    return set()

        out = set()
        while k >= 0:
            ins = instrs[k]
            name = ins.opname
            if name in _WHILE_TRIVIAL_OPS:
                k -= 1
                continue
            if is_cond_jump(ins):
                if not exits_loop(ins):
                    break  # a body-level `if`
                out.add(ins.offset)
            elif name.startswith("STORE_"):
                if not (k > 0 and instrs[k - 1].opname in ("COPY", "DUP_TOP")):
                    break  # a plain assignment ends the previous statement
            elif name in _STATEMENT_BOUNDARY_OPS or name.startswith(("JUMP", "DELETE_")):
                break
            k -= 1
        return out

    def _find_jump_backward_end(self) -> int:
        """
        Find the offset immediately after the final backward-jump instruction in the instruction list.

        Returns:
            int: The offset one past the last backward-jump instruction, or -1 if no backward jump is present.
        """
        last_offset = -1
        for ins in self.instructions:
            if self._is_backward_instruction(ins):
                last_offset = ins.offset
        if last_offset >= 0:
            return last_offset + 2
        return -1

    def _prescan_try_structure(self) -> None:
        """
        Pre-scan the function's bytecode and classify exception-handling and with-block structure for later reconstruction.

        Populates the following attributes used by the instruction dispatcher:
            _try_nop_offsets (set[int]):
                Offsets of NOP instructions that mark try-block entries.
            _finally_merge_offsets (set[int]):
                Offsets that act as finally-merge labels where deferred `finally:` bodies should be emitted.
            _suppress_push_exc_offsets (set[int]):
                Offsets of `PUSH_EXC_INFO` instructions that should be suppressed (re-raise wrappers or with-exit handlers).
            _exc_handler_jump_offsets (set[int]):
                Offsets of backward jumps that represent exception-handler exits rather than loop back-edges.
            _with_exit_suppress_offsets (set[int]):
                Offsets of instructions that implement the normal-path `with` exit sequence and should be suppressed.
            _push_exc_to_finally_merge (dict[int, int]):
                Mapping from a real `PUSH_EXC_INFO` offset to its corresponding finally-merge offset.
            _finally_body_suppress (set[int]):
                Offsets of inlined finally-body instructions to suppress from normal dispatch.
            _deferred_finally_lines (dict[int, list[str]]):
                Pre-rendered lines for deferred finally bodies keyed by merge offset.
            _deferred_except_lines (dict[int, list[str]]):
                Pre-rendered lines for deferred except-handler sections keyed by merge offset.
            _handler_section_suppress (set[int]):
                Offsets of instructions that belong to pre-rendered handler sections and should be suppressed.
            _wrapper_body_suppress (set[int]):
                Offsets of re-raise/with-exit wrapper instructions to suppress.
            _nop_to_push_exc (dict[int, int]):
                Maps try-entry NOP offsets to the `PUSH_EXC_INFO` offset that handles their try body.
            _pending_finally_merge (Optional[int]):
                Internal placeholder used while building deferred-finally mappings.

        The routine uses available exception-table data when present and falls back to heuristics on older hosts to identify these regions.
        """
        self._for_iter_targets = {
            self._get_jump_target(fi) for fi in self.instructions if fi.opname == "FOR_ITER"
        }
        
        instrs = self.instructions
        n = len(instrs)

        # ── 0. Try to get exception table coverage from dis ─────────────
        # exception_entries available on Python 3.11+ (same versions that use
        # NOP / PUSH_EXC_INFO rather than SETUP_FINALLY).
        try:
            _exc_entries = dis.Bytecode(self.code_obj).exception_entries
            # Build a set of offsets that start a try-covered range.
            # Include ALL depths (depth=0 for module-level try, depth=1+ for nested).
            _try_covered_starts = {e.start for e in _exc_entries}
            # Build a set of ALL targets that are real except-entries (depth 1)
            # and whose handler starts with CHECK_EXC_MATCH → real except Exception:
            _except_entry_targets = set()
            _reraise_wrapper_targets = set()
            push_exc_offs = {ins.offset for ins in instrs if ins.opname == "PUSH_EXC_INFO"}
            for e in _exc_entries:
                if e.target in push_exc_offs:
                    # Peek at what follows the PUSH_EXC_INFO target
                    t_idx = next(
                        (i for i, ins in enumerate(instrs) if ins.offset == e.target),
                        None,
                    )
                    if t_idx is not None:
                        look = t_idx + 1
                        while look < n and instrs[look].opname in _TRY_SKIP_NOP:
                            look += 1
                        if look < n and instrs[look].opname in _TRY_LOAD_OPS:
                            # Check for CHECK_EXC_MATCH within a few steps to
                            # confirm this is a real typed except: handler.
                            look2 = look + 1
                            found_cem = False
                            for _ in range(5):
                                if look2 >= n:
                                    break
                                if instrs[look2].opname == "CHECK_EXC_MATCH":
                                    found_cem = True
                                    break
                                look2 += 1
                            if found_cem:
                                _except_entry_targets.add(e.target)
                            else:
                                _reraise_wrapper_targets.add(e.target)
                        elif look < n and instrs[look].opname == "POP_TOP":
                            # Bare except: POP_TOP discards the exception.
                            # This is a real except: handler, NOT a re-raise wrapper.
                            _except_entry_targets.add(e.target)
                        else:
                            _reraise_wrapper_targets.add(e.target)
        except (AttributeError, TypeError):
            _exc_entries = []
            _try_covered_starts = set()
            _except_entry_targets = set()
            _reraise_wrapper_targets = set()

        # ── 1. Classify PUSH_EXC_INFO offsets ───────────────────────────
        self._suppress_push_exc_offsets: set = set()
        for ins in instrs:
            if ins.opname != "PUSH_EXC_INFO":
                continue
            if ins.offset in _reraise_wrapper_targets:
                # Re-raise wrapper or with-exit handler — suppress silently
                self._suppress_push_exc_offsets.add(ins.offset)
            elif ins.offset not in _except_entry_targets:
                # Not in either set (no exception_entries available) — use heuristic:
                # peek at what follows; if it's a LOAD + no CHECK_EXC_MATCH, suppress.
                t_idx = next((i for i, ins2 in enumerate(instrs) if ins2.offset == ins.offset), None)
                if t_idx is not None:
                    look = t_idx + 1
                    while look < n and instrs[look].opname in _TRY_SKIP_NOP:
                        look += 1
                    if look < n:
                        # WITH_EXCEPT_START → with-exit handler
                        if instrs[look].opname == "WITH_EXCEPT_START":
                            self._suppress_push_exc_offsets.add(ins.offset)
                        # LOAD_GLOBAL + no CHECK_EXC_MATCH within 3 steps → re-raise wrapper
                        elif instrs[look].opname in _TRY_LOAD_OPS:
                            look2 = look + 1
                            found_cem = False
                            for _ in range(4):
                                if look2 >= n:
                                    break
                                if instrs[look2].opname == "CHECK_EXC_MATCH":
                                    found_cem = True
                                    break
                                look2 += 1
                            if not found_cem:
                                self._suppress_push_exc_offsets.add(ins.offset)

        # ── 2. Classify NOP instructions as try-entry vs. epilogue ──────
        self._try_nop_offsets: set = set()
        for ins in instrs:
            if ins.opname != "NOP" or ins.offset == 2:
                continue
            # The instruction right after this NOP (offset + 2) should be the
            # start of a try-covered range.
            next_off = ins.offset + 2
            if _try_covered_starts:
                if next_off in _try_covered_starts:
                    self._try_nop_offsets.add(ins.offset)
            else:
                # Fallback heuristic: NOP is a try-entry if the next instruction
                # is not LOAD_CONST None (the __exit__ epilogue).
                t_idx = next((i for i, ins2 in enumerate(instrs) if ins2.offset == next_off), None)
                if t_idx is not None:
                    next_ins = instrs[t_idx]
                    if not (next_ins.opname == "LOAD_CONST" and next_ins.argval is None):
                        if any(ins2.opname == "PUSH_EXC_INFO" and ins2.offset > ins.offset
                               for ins2 in instrs):
                            self._try_nop_offsets.add(ins.offset)

        # ── 3. Find finally-merge labels ─────────────────────────────────
        # A finally-merge label is an instruction offset O such that:
        #   - At least one JUMP_BACKWARD from inside an except-handler zone targets O
        #   - That JUMP_BACKWARD is immediately preceded by POP_EXCEPT (handler exit)
        #   - O is a is_jump_target instruction
        #   - O is NOT a PUSH_EXC_INFO offset
        #   - O is NOT a FOR_ITER instruction (loop head, not a merge point)
        # The code at O..next_try_nop is the inlined finally body.
        self._finally_merge_offsets: set = set()
        push_exc_offs = {ins.offset for ins in instrs if ins.opname == "PUSH_EXC_INFO"}
        # Build offset-to-index map for quick look-up
        off2idx = {ins.offset: i for i, ins in enumerate(instrs)}
        # Loop/iter opcodes that can never be finally-merge labels
        # Opcodes that appear in exception-handler cleanup just before the JB

        def _is_with_cleanup_jump(instrs: list, idx: int) -> bool:
            """Return True when the jump at instrs[idx] is a with-block natural exit (POP_EXCEPT -> POP_TOP -> JUMP), not a user 'continue'."""
            return idx >= 2 and instrs[idx - 1].opname == "POP_TOP" and instrs[idx - 2].opname == "POP_EXCEPT"

        from collections import Counter
        jb_targets: Counter = Counter()
        for jb in instrs:
            if not self._is_backward_instruction(jb):
                continue
            target = self._get_jump_target(jb)
            # The JB must be inside an exception handler zone
            has_pei = any(
                target <= ins.offset < jb.offset and ins.opname == "PUSH_EXC_INFO"
                for ins in instrs
            )
            if not has_pei:
                continue
            # The JB must be preceded by POP_EXCEPT or handler cleanup within 6 steps
            jb_idx = off2idx.get(jb.offset, -1)
            if self._handler_exit_before(jb_idx) and not _is_with_cleanup_jump(instrs, jb_idx):
                jb_targets[target] += 1

        for target_off, count in jb_targets.items():
            if count >= 1 and target_off not in push_exc_offs:
                t_ins = next((ins for ins in instrs if ins.offset == target_off), None)
                if t_ins and t_ins.is_jump_target and t_ins.opname not in _TRY_LOOP_HEADS:
                    self._finally_merge_offsets.add(target_off)
                    if not self._has_exceptional_finally_copy(target_off):
                        # Handlers return to ordinary code after the `try`: there is
                        # no `finally`, only the handlers to place before that code.
                        if not hasattr(self, "_except_only_merge_offsets"):
                            self._except_only_merge_offsets = set()
                        self._except_only_merge_offsets.add(target_off)

        # ── 4. Classify JUMP_BACKWARD as exc-handler exits ───────────────
        self._exc_handler_jump_offsets: set = set()
        for jb in instrs:
            if not self._is_backward_instruction(jb):
                continue
            target = self._get_jump_target(jb)
            # A JB is an exception handler exit if:
            # (a) It is inside a handler zone (PEI between target and JB)
            # (b) It is preceded by POP_EXCEPT or handler cleanup (same check as above)
            has_pei = any(
                target <= ins.offset < jb.offset and ins.opname in (
                    "PUSH_EXC_INFO", "CHECK_EXC_MATCH", "POP_EXCEPT",
                )
                for ins in instrs
            )
            if not has_pei:
                continue
            jb_idx = off2idx.get(jb.offset, -1)
            if self._handler_exit_before(jb_idx) and not _is_with_cleanup_jump(instrs, jb_idx):
                self._exc_handler_jump_offsets.add(jb.offset)
                if not hasattr(self, "_exc_handler_continue_offsets"):
                    self._exc_handler_continue_offsets = set()
                # A jump is a continue if its target is a loop head (FOR_ITER or while header)
                loop_heads = {ins.offset for ins in instrs if ins.opname == "FOR_ITER"}
                if hasattr(self, "_while_header_targets"):
                    loop_heads.update(self._while_header_targets.values())

                if target in loop_heads:
                    self._exc_handler_continue_offsets.add(jb.offset)

        # ── 6. Build deferred-finally mappings ───────────────────────────
        # For try/except/finally patterns, the inlined finally code sits
        # physically between the try body and the except handlers in the
        # bytecode.  The except handlers themselves sit AFTER the second try
        # body.  We pre-decompile both the handler section and the finally body,
        # suppress their instructions from normal dispatch, and emit them in the
        # correct source order (handlers → finally) when we encounter the
        # finally-merge label.
        self._push_exc_to_finally_merge: dict = {}   # push_exc_offset → merge_offset
        self._finally_body_suppress: set = set()     # inlined finally code to suppress
        self._deferred_finally_lines: dict = {}      # merge_offset → rendered finally lines
        self._deferred_except_lines: dict = {}       # merge_offset → rendered handler lines
        self._handler_section_suppress: set = set()  # except handler instructions to suppress
        self._wrapper_body_suppress: set = set()     # re-raise/with-exit wrapper instructions
        self._pending_finally_merge: Optional[int] = None

        try:
            _exc_entries = dis.Bytecode(self.code_obj).exception_entries  # type: ignore[name-defined]
        except Exception:
            _exc_entries = []

        real_push_exc = {
            ins.offset for ins in instrs
            if ins.opname == "PUSH_EXC_INFO"
            and ins.offset not in self._suppress_push_exc_offsets
        }

        # A `continue`/`break` in a try body is a jump the exception table leaves
        # out, so one try body can be covered by several entries that share a
        # handler. The try body ends where the last of them ends.
        _body_end_by_target: dict = {}
        for e in _exc_entries:
            if e.target in real_push_exc:
                _body_end_by_target[e.target] = max(_body_end_by_target.get(e.target, 0), e.end)

        # Map: first real PUSH_EXC_INFO → finally-merge offset
        for e in _exc_entries:
            if e.target in real_push_exc:
                merge = None
                body_end = _body_end_by_target[e.target]
                after_body = self._merge_after_nops(body_end)
                if body_end in self._finally_merge_offsets:
                    # Already a known finally-merge label.
                    merge = body_end
                elif e.end in self._finally_merge_offsets:
                    merge = e.end
                elif after_body is not None:
                    # Only the no-op that marks the end of the try body lies in between
                    # (a body ending in a loop leaves one before the inline finally).
                    merge = after_body
                else:
                    # Case 1: FOR_ITER loop wraps the try body.
                    # Use the FOR_ITER's jump target (loop-exit) as the merge.
                    # Only accept FOR_ITER — not conditional jumps inside outer
                    # except handlers, which would give false positives.
                    for ins in instrs:
                        if ins.offset >= e.start:
                            break
                        if ins.opname != "FOR_ITER":
                            continue
                        t = self._get_jump_target(ins)
                        if isinstance(t, int) and t > e.start:
                            if merge is None or t < merge:
                                merge = t
                if merge is not None:
                    target = e.target
                    if merge not in self._push_exc_to_finally_merge:
                        self._push_exc_to_finally_merge[merge] = []
                    if target not in self._push_exc_to_finally_merge[merge]:
                        self._push_exc_to_finally_merge[merge].append(target)
                        self._push_exc_to_finally_merge[merge].sort()
                    if merge not in self._finally_merge_offsets:
                        if not hasattr(self, "_except_only_merge_offsets"):
                            self._except_only_merge_offsets = set()
                        self._except_only_merge_offsets.add(merge)

        # ── Suppress full bodies of re-raise wrappers and with-exit handler
        for pei_off in sorted(self._suppress_push_exc_offsets):
            pei_idx = next((i for i, ins in enumerate(instrs) if ins.offset == pei_off), None)
            if pei_idx is None:
                continue
            # Find end: start of next PUSH_EXC_INFO (any kind)
            end_off = instrs[-1].offset + 2
            for ins in instrs:
                if ins.offset > pei_off and ins.opname == "PUSH_EXC_INFO":
                    end_off = ins.offset
                    break
            for ins in instrs:
                if pei_off <= ins.offset < end_off:
                    self._wrapper_body_suppress.add(ins.offset)

        # ── Find end of each handler section and pre-decompile it
        sorted_real_pei = sorted(real_push_exc)
        for merge_off, targets in self._push_exc_to_finally_merge.items():
            for push_off in targets:
                # Handler section starts at push_off, ends at status of either the
                # next real PUSH_EXC_INFO or the nearest suppress-wrapper PUSH_EXC_INFO
                handler_end = instrs[-1].offset + 2
                for ins in instrs:
                    if ins.offset > push_off and ins.opname == "PUSH_EXC_INFO":
                        handler_end = ins.offset
                        break

                # Collect handler instructions (not already suppressed as finally body)
                handler_instrs = [
                    ins for ins in instrs
                    if push_off <= ins.offset < handler_end
                    and ins.offset not in self._finally_body_suppress
                ]
                for ins in handler_instrs:
                    self._handler_section_suppress.add(ins.offset)
                    self._finally_body_suppress.discard(ins.offset)  # don't double-suppress

                # Pre-decompile the handler section
                handler_lines = self._render_handler_section(handler_instrs, merge_off)
                if merge_off not in self._deferred_except_lines:
                    self._deferred_except_lines[merge_off] = []
                self._deferred_except_lines[merge_off].extend(handler_lines)

        # ── Find and suppress finally body instructions
        for merge_off, targets in self._push_exc_to_finally_merge.items():
            if merge_off in getattr(self, "_except_only_merge_offsets", ()):
                continue
            # This try's finally handler is the first re-raise wrapper after its except
            # handlers (3.11+ lays them out in that order).
            owner = min((w for w in self._suppress_push_exc_offsets if w > max(targets)), default=None)
            matched = self._match_finally_wrapper(merge_off, owner)
            body_end = None if matched is None else matched[0]
            if matched is not None:
                # Every inline copy in this try body before the normal-path one sits at a
                # continue/break/return.
                try_start = min((e.start for e in _exc_entries if e.target in targets), default=merge_off)
                self._suppress_copies(self._find_finally_copies(matched[1], try_start, merge_off))
            if body_end is None:
                for ins in instrs:
                    if ins.offset > merge_off and ins.opname == "NOP":
                        if ins.offset in self._try_nop_offsets or ins.offset == 2:
                            body_end = ins.offset
                            break
            if body_end is None:
                for ins in instrs:
                    if ins.offset > merge_off and (
                        (ins.opname == "LOAD_CONST" and ins.argval is None)
                        or ins.opname in ("RETURN_CONST", "RETURN_VALUE")
                    ):
                        body_end = ins.offset
                        break
            if body_end is None:
                continue
            for ins in instrs:
                if merge_off <= ins.offset < body_end:
                    self._finally_body_suppress.add(ins.offset)
            sub_instrs = [ins for ins in instrs if merge_off <= ins.offset < body_end]
            lines = self._render_finally_body(sub_instrs) or ["pass"]
            self._deferred_finally_lines[merge_off] = lines

        # ── 7. Build nop_to_push_exc: maps each try-entry NOP to the correct
        #       PUSH_EXC_INFO that handles its try body, using the exception table.
        self._nop_to_push_exc: dict = {}  # nop_offset → push_exc_info_offset
        self._nop_to_finally_merge: dict = {}  # nop_offset → finally start, for try/finally without except
        try:
            _exc_entries_for_nop = dis.Bytecode(self.code_obj).exception_entries  # type: ignore[name-defined]
        except (AttributeError, IndexError, TypeError, ValueError):
            _exc_entries_for_nop = []
        for nop_off in list(self._try_nop_offsets) + ([2] if any(ins.offset == 2 and ins.opname == "NOP" for ins in instrs) else []):
            body_start = nop_off + 2  # first instruction of the try body
            for e in _exc_entries_for_nop:
                if e.start <= body_start < e.end and e.target in real_push_exc:
                    self._nop_to_push_exc[nop_off] = e.target
                    break

        # ── 7b. Handle try/finally without except ───────────────────────
        # When a try-entry NOP has no corresponding real PUSH_EXC_INFO in
        # _nop_to_push_exc (because the only handler is a suppressed re-raise
        # wrapper), the structure is try/finally without any except clause.
        # The finally body is inlined immediately after the try body.
        # Pattern: NOP → try body → finally body → RETURN_CONST → suppressed PEI
        # Detect by: NOP is not in _nop_to_push_exc, but there IS a suppressed
        # PUSH_EXC_INFO whose exception-table entry covers [nop+2, try_body_end).
        for nop_off in list(self._try_nop_offsets) + ([2] if any(ins.offset == 2 and ins.opname == "NOP" for ins in instrs) else []):
            if nop_off in self._nop_to_push_exc:
                continue  # already handled
            body_start = nop_off + 2
            # Find the suppressed PEI that handles this try body
            for e in _exc_entries_for_nop:
                if e.start <= body_start < e.end and e.target in self._suppress_push_exc_offsets:
                    # This NOP has only a finally (no except).
                    # Find the try body end = start of finally body = e.end
                    # A continue/break/return in the body splits the protected range, so
                    # the body ends where the last range aimed at this handler ends.
                    try_body_end = max(x.end for x in _exc_entries_for_nop
                                       if x.target == e.target and x.start >= body_start)
                    # Finally body: from try_body_end to first RETURN_CONST or
                    # next suppressed PUSH_EXC_INFO
                    finally_start = try_body_end
                    # The copy where the protected body ends is the normal-path one; any
                    # earlier copy belongs to a continue/break/return and is not source. A
                    # body that always raises or returns has no normal-path copy, and
                    # try-body statements that merely look like the finally body stay.
                    copies = self._find_finally_copies(self._handler_copy_instrs(e.target), body_start,
                                                       self._try_body_limit(e.target, _exc_entries_for_nop))
                    normal = [c for c in copies if c[2] and instrs[c[0]].offset >= try_body_end]
                    after_return = self._copy_precedes_final_return(normal[-1][1]) if normal else None
                    if after_return is not None:
                        # The try body ends in `return value`: every copy, the last one
                        # included, runs on the way out of a return. The return stays in
                        # the try body, which ends right after it (before the end of an
                        # enclosing loop), and the finally body is the handler's copy.
                        self._suppress_copies(copies)
                        finally_start = after_return
                        normal = []
                    else:
                        if normal:
                            finally_start = instrs[normal[-1][0]].offset
                        self._suppress_copies([c for c in copies if not normal or c is not normal[-1]])
                    finally_end = self._finally_copy_end(finally_start, owner=e.target) if normal else None
                    if finally_end is None and not normal:
                        # No inline copy: the code at the body's end is not the finally body.
                        finally_end = finally_start
                    if finally_end is None:
                        for ins in instrs:
                            if ins.offset >= finally_start:
                                if ins.opname in ("RETURN_CONST", "RETURN_VALUE"):
                                    finally_end = ins.offset
                                    break
                                if ins.opname == "PUSH_EXC_INFO":
                                    finally_end = ins.offset
                                    break
                    if finally_end is None or finally_end < finally_start:
                        break
                    # Suppress the finally body instructions
                    for ins in instrs:
                        if finally_start <= ins.offset < finally_end:
                            self._finally_body_suppress.add(ins.offset)
                    # Pre-render the finally body
                    sub_instrs = [ins for ins in instrs if finally_start <= ins.offset < finally_end]
                    if not sub_instrs:
                        # A try body that always raises or returns has no inline copy of the
                        # finally body; the copy in the exception handler is the only one.
                        sub_instrs = self._handler_copy_instrs(e.target)
                    lines = self._render_finally_body(sub_instrs) or ["pass"]
                    self._deferred_finally_lines[finally_start] = lines
                    # The try body ends where the finally body starts; _op_nop needs it
                    # to open and close the `try:` block (there is no handler to ask).
                    self._nop_to_finally_merge[nop_off] = finally_start
                    # Record this as a finally-merge point (even though there's no
                    # JUMP_BACKWARD — the inline finally body entry IS the merge label)
                    self._finally_merge_offsets.add(finally_start)
                    # The "push_exc_to_finally_merge" dict maps None here
                    # (no real PUSH_EXC_INFO), so no handler lines to pre-decompile.
                    self._deferred_except_lines[finally_start] = []
                    break
        # The normal-exit epilogue for a `with` block is:
        #   LOAD_CONST None; LOAD_CONST None; LOAD_CONST None; CALL 2; POP_TOP
        # Suppress the LOAD_CONSTs and the CALL to prevent ``None(None, None)``.
        self._with_exit_suppress_offsets: set = set()
        for i, ins in enumerate(instrs):
            if ins.opname != "BEFORE_WITH":
                continue
            for j in range(i + 1, min(i + _MAX_WITH_EXIT_SEARCH_WINDOW, n)):
                if instrs[j].opname == "LOAD_CONST" and instrs[j].argval is None and not (
                        instrs[j - 1].opname.startswith(_VALUE_OPS)):
                    # (after a value: the arguments of a user call such as f(None, None, None))
                    k = j + 1
                    while k < n and instrs[k].opname in _TRY_SKIP_NOP:
                        k += 1
                    if k < n and instrs[k].opname == "LOAD_CONST" and instrs[k].argval is None:
                        m = k + 1
                        while m < n and instrs[m].opname in _TRY_SKIP_NOP:
                            m += 1
                        if m < n and instrs[m].opname == "LOAD_CONST" and instrs[m].argval is None:
                            c = m + 1
                            while c < n and instrs[c].opname in _TRY_SKIP_NOP:
                                c += 1
                            if c < n and instrs[c].opname == "CALL":
                                for off in (instrs[j].offset, instrs[k].offset,
                                            instrs[m].offset, instrs[c].offset):
                                    self._with_exit_suppress_offsets.add(off)
                                break
                    break

    def _try_body_limit(self, handler: int, entries: list) -> int:
        """
        Exclusive bound for the finally copies that belong to the try protected by
        *handler*: everything up to the normal-path copy, which starts where the body's
        last protected range ends or where a jump from inside the body lands (past NOPs).
        Later code, such as the next try statement's copies, is beyond it.
        """
        instrs = self.instructions
        ranges = [e for e in entries if e.target == handler]
        ends = {e.end for e in ranges}
        ends |= {self._get_jump_target(x) for x in instrs
                 if "JUMP" in x.opname and any(e.start <= x.offset < e.end for e in ranges)}
        last = max((t for t in ends if isinstance(t, int) and t < handler), default=handler)
        k = next((i for i, x in enumerate(instrs) if x.offset >= last), len(instrs))
        while k < len(instrs) and instrs[k].opname in _FILLER_OPS:
            k += 1
        return min(handler, instrs[k].offset + 1) if k < len(instrs) else handler

    def _prescan_with_exits(self) -> None:
        """
        3.11+: find every inlined ``__exit__`` call of each ``with`` block and where the
        block's body ends.

        Each way out of a ``with`` body (falling off the end, ``return``, ``break``,
        ``continue``) is compiled as ``[SWAP...] LOAD_CONST None x3; CALL; POP_TOP``
        followed by the exit itself; the SWAPs move a return value past the exit
        function. Only the exit itself is source code, so all those calls are hidden
        (``_with_exit_suppress_offsets``). The body ends after the last of them, or,
        when that one carries a return value, at the handler, so the `return` stays in
        the block (``_with_body_end``, keyed by the first offset the handler protects).
        """
        self._with_body_end = {}
        self._with_exit_owner = {}
        instrs = self.instructions
        try:
            entries = list(dis.Bytecode(self.code_obj).exception_entries)
        except (AttributeError, IndexError, TypeError, ValueError):
            return
        by_offset = {ins.offset: k for k, ins in enumerate(instrs)}
        handlers = {e.target for e in entries
                    if e.target in by_offset and by_offset[e.target] + 1 < len(instrs)
                    and instrs[by_offset[e.target]].opname == "PUSH_EXC_INFO"
                    and instrs[by_offset[e.target] + 1].opname == "WITH_EXCEPT_START"}
        for handler in handlers:
            ranges = [e for e in entries if e.target == handler]
            if not ranges:
                continue
            body_start = min(e.start for e in ranges)
            # The body's normal exit follows its last protected range of ordinary code
            # (an exit before it is an early one: `continue` in `if c: continue`, with
            # more body after). Handler code, which 3.11+ lays out after all ordinary
            # code, can be protected too (an inner try's handlers): it does not count.
            cold = min((x.offset for x in instrs if x.opname == "PUSH_EXC_INFO" and x.offset > body_start),
                       default=handler)
            # Code in a try nested in the body is protected by that try's handler, whose
            # own code the with's handler protects: those ranges belong to the body too.
            body_ranges = [e for e in entries if e.target in _owned_handlers(handler, entries) and e.start < cold]
            limit = max((e.end for e in body_ranges), default=handler)
            # The normal exit starts right there (past NOPs), or where a jump from the
            # body lands (after an `if` that ends the body). Other exits are early ones,
            # or sit in handler code (`except E: continue` leaves the with too).
            starts = {limit} | {self._get_jump_target(x) for x in instrs if "JUMP" in x.opname
                                and any(e.start <= x.offset < e.end for e in body_ranges)}
            normal_at = {self._skip_nops(by_offset[t]) for t in starts if t in by_offset and t < cold}
            # An exit call belongs to this with when one of its protected ranges ends
            # right there (only NOPs in between); a later or nested with's exit calls
            # follow that with's own ranges instead.
            range_ends = {e.end for e in ranges}
            # ... or when a jump from inside its body lands there (the normal exit after
            # an `if` at the end of the body is reached by the `if`'s jump).
            range_ends |= {self._get_jump_target(x) for x in instrs
                           if "JUMP" in x.opname and any(e.start <= x.offset < e.end for e in ranges)}
            last = None
            k = by_offset.get(body_start, len(instrs))
            while k < len(instrs) and instrs[k].offset < handler:
                seq_end = self._with_exit_call_at(k)
                if seq_end is None or not self._follows_range_end(k, range_ends):
                    k += 1
                    continue
                self._with_exit_suppress_offsets.update(x.offset for x in instrs[k:seq_end])
                # In nested with blocks the outer body's range covers the inner exits
                # too: an exit belongs to the innermost block (the latest body start).
                exit_off = instrs[seq_end - 1].offset
                if self._with_exit_owner.get(exit_off, -1) < body_start:
                    self._with_exit_owner[exit_off] = body_start
                if k in normal_at and instrs[k].offset < cold:
                    last = (k, seq_end)
                k = seq_end
            if last is None:
                # The body never exits normally (it always raises, or every exit is an
                # early one): it ends with its last protected code.
                self._with_body_end[body_start] = limit
                continue
            carries_value = instrs[last[0]].opname == "SWAP"
            end = handler if carries_value or last[1] >= len(instrs) else instrs[last[1]].offset
            self._with_body_end[body_start] = end

    def _skip_nops(self, k: int) -> int:
        """The index of the first instruction at or after *k* that is not a NOP/CACHE."""
        while k < len(self.instructions) and self.instructions[k].opname in _FILLER_OPS:
            k += 1
        return k

    def _handler_exit_before(self, jb_idx: int) -> bool:
        """
        Whether the jump at *jb_idx* leaves an exception handler: POP_EXCEPT (or other
        handler clean-up) comes within a few instructions before it. Inlined
        ``__exit__`` calls of enclosing `with` blocks between them are skipped: an
        `except E: continue` inside a `with` in a loop exits the with on its way back.
        """
        instrs = self.instructions
        start = jb_idx
        moved = True
        while moved:
            moved = False
            for size in (5, 6):  # the exit call, with 3.11's PRECALL or without it
                if start >= size and start == self._with_exit_call_at(start - size):
                    start -= size
                    moved = True
                    break
        for back in range(1, 7):
            check_idx = start - back
            if check_idx < 0:
                return False
            if instrs[check_idx].opname in _TRY_HANDLER_EXIT_OPS:
                return True
            # Stop early if we cross a non-trivial boundary
            if instrs[check_idx].opname in ("CALL", "BINARY_OP", "COMPARE_OP"):
                return False
        return False

    def _follows_range_end(self, k: int, range_ends: set) -> bool:
        """True if one of *range_ends* (offsets) is at ``instrs[k]`` or just before it, past only NOPs."""
        instrs = self.instructions
        j = k
        while True:
            if instrs[j].offset in range_ends:
                return True
            if j == 0 or instrs[j - 1].opname not in _FILLER_OPS:
                return False
            j -= 1

    def _with_exit_call_at(self, k: int) -> Optional[int]:
        """
        Index just past an inlined ``__exit__(None, None, None)`` call starting at *k*, or None.

        The shape is ``[SWAP...] LOAD_CONST None x3; [PRECALL (3.11)] CALL 2|3; POP_TOP``; the SWAPs move
        a return value past the exit function. A user call such as ``f(None, None, None)``
        has the same tail, but its callee is loaded right before the arguments, while
        an exit call follows the end of a statement (or a jump).
        """
        instrs = self.instructions
        m = k
        while m < len(instrs) and instrs[m].opname == "SWAP":
            m += 1
        # 3.11 puts a PRECALL before the CALL.
        ops = [x.opname for x in instrs[m:m + 6]]
        length = 6 if ops[3:4] == ["PRECALL"] else 5
        seq = instrs[m:m + length]
        shape = tuple(x.opname for x in seq if x.opname != "PRECALL")
        if shape != ("LOAD_CONST", "LOAD_CONST", "LOAD_CONST", "CALL", "POP_TOP"):
            return None
        if any(x.argval is not None for x in seq[:3]) or seq[-2].arg not in (2, 3):
            return None
        if m == k and m > 0 and instrs[m - 1].opname.startswith(_VALUE_OPS):
            # The callee of an ordinary call precedes its arguments; when it was chosen
            # by a branch, `(f if c else g)(None, None, None)`, the arguments are a jump
            # target too, but the code falling into them still pushed a value. An exit
            # call is never fallen into from a value: only from the end of a statement.
            return None
        return m + length

    def _find_finally_copies(self, handler_body: list, lo: int, hi: int) -> list:
        """
        Locate the inline copies of a `finally` body that start in ``[lo, hi)``.

        CPython inlines the finally body on the normal exit and again before every
        `continue`, `break` and `return` in the try body. Each copy has the same
        instructions as the copy in the re-raise handler (*handler_body*), which is
        how they are recognized.

        A statement in the try body can compile to the same instructions (``cleanup()``
        in both the try body and the finally body). Copies carry the finally body's line
        numbers, so a match on another line is such a statement and is left alone; without
        line numbers, a match counts as a copy only when an early exit follows it.

        Returns:
            list: ``(start_index, end_index, is_copy)`` triples into ``self.instructions``, in
            order. ``is_copy`` is False for a match that is a statement of the try body.
        """
        template = _copy_signature(handler_body)
        if not template:
            return []
        instrs = self.instructions
        first = next((i for i, x in enumerate(instrs)
                      if any(x is y for y in handler_body) and _copy_signature([x])), None)
        body_line = None if first is None else self._line_at(first)
        found: list = []
        idx = 0
        while idx < len(instrs):
            if not lo <= instrs[idx].offset < hi:
                idx += 1
                continue
            end = _match_finally_copy(instrs, idx, template)
            if end is None:
                idx += 1
                continue
            # The first instruction can be a no-op of the exit's own line.
            line = self._line_at(next((k for k in range(idx, end) if _copy_signature([instrs[k]])), idx))
            if body_line is not None and line is not None:
                is_copy = line == body_line
            else:
                is_copy = self._precedes_early_exit(end)
            found.append((idx, end, is_copy))
            idx = end
        return found

    def _line_at(self, idx: int) -> Optional[int]:
        """Source line of ``self.instructions[idx]``: the line it, or the nearest instruction before it, starts."""
        instrs = self.instructions
        k = idx
        while k >= 0:
            if instrs[k].starts_line is not None:
                return instrs[k].starts_line
            k -= 1
        return None

    def _precedes_early_exit(self, idx: int) -> bool:
        """
        Whether ``self.instructions[idx]`` begins a `continue`, `break` or `return`: an
        unconditional jump, a return, or dropping a loop iterator (``POP_TOP``, after a
        swap when a value is returned), possibly after leaving a block.
        """
        instrs = self.instructions
        while idx < len(instrs) and instrs[idx].opname in ("NOP", "CACHE", "POP_BLOCK", "POP_EXCEPT"):
            idx += 1
        if idx >= len(instrs):
            return True
        op = instrs[idx].opname
        return op.startswith(("RETURN_", "JUMP")) or op in ("JUMP_ABSOLUTE", "POP_TOP", "ROT_TWO", "SWAP")

    def _copy_precedes_final_return(self, end_idx: int) -> Optional[int]:
        """
        Offset just past the `return` when the finally copy ending at index *end_idx* runs
        on the way out of a `return` of a value that is the try body's last statement,
        else None.

        Such a return keeps its own line: a returned name or expression is loaded before
        the copy, and a constant is loaded or returned right after it without starting a
        new line (3.12: ``NOP`` on the return's line, the copy, ``RETURN_CONST``; 3.14:
        the copy, ``LOAD_SMALL_INT``, ``RETURN_VALUE``). A `return` after the whole
        try statement starts a line of its own after the copy. Inside loops, the iterators
        are dropped (``SWAP 2; POP_TOP``) between the copy and the return. A bare
        `return None` is left alone, since the implicit return at the end of a function
        looks the same.
        """
        instrs = self.instructions
        k = end_idx
        while k < len(instrs) and instrs[k].opname in ("NOP", "CACHE", "SWAP", "POP_TOP"):
            k += 1
        if k >= len(instrs):
            return None
        first = instrs[k]
        ret: Optional[int] = None
        value: Optional[tuple] = None
        if first.opname in ("LOAD_CONST", "LOAD_SMALL_INT") and k + 1 < len(instrs) \
                and instrs[k + 1].opname == "RETURN_VALUE":
            ret, value = k + 1, (first.opname, first.argval)
        elif first.opname == "RETURN_CONST":
            ret, value = k, ("LOAD_CONST", first.argval)
        elif first.opname == "RETURN_VALUE":
            ret, value = k, ((instrs[k - 1].opname, instrs[k - 1].argval) if k > 0 else None)
        if ret is None or value == ("LOAD_CONST", None):
            return None
        if any(instrs[j].starts_line is not None for j in range(end_idx, k + 1)):
            # The return starts a line after the copy: it follows the try statement.
            return None
        return instrs[ret + 1].offset if ret + 1 < len(instrs) else instrs[ret].offset + 2

    def _suppress_copies(self, copies: list) -> None:
        """
        Suppress the finally copies among ``(start_index, end_index, is_copy)`` matches.

        These are the copies at early exits: only the exit itself is source code, since the
        finally body runs because the exit leaves the `try`. Matches that are statements of
        the try body stay.
        """
        for start, end, is_copy in copies:
            if not is_copy:
                continue
            for k in range(start, end):
                self._finally_body_suppress.add(self.instructions[k].offset)

    def _merge_after_nops(self, offset: int) -> Optional[int]:
        """
        The finally-merge label reached from *offset* by skipping nothing but NOPs, or None.
        """
        for ins in self.instructions:
            if ins.offset < offset:
                continue
            if ins.offset in self._finally_merge_offsets:
                return ins.offset
            if ins.opname != "NOP":
                return None
        return None

    def _handler_copy_instrs(self, wrapper_off: int) -> list:
        """
        Instructions of the `finally` body copy inside the re-raise handler at *wrapper_off*.

        Returns an empty list when the handler is not a `PUSH_EXC_INFO ... RERAISE 0` wrapper.
        """
        entries = self._exception_entries()
        if entries is None:
            return []
        entry = next((e for e in entries if e.start == wrapper_off and e.lasti), None)
        if entry is None:
            return []
        wrapper = [ins for ins in self.instructions if wrapper_off < ins.offset < entry.end]
        if not wrapper or wrapper[-1].opname != "RERAISE" or wrapper[-1].arg != 0:
            return []
        return wrapper[:-1]

    def _finally_copy_end(self, start_offset: int, owner: Optional[int] = None) -> Optional[int]:
        """Offset just past the inline `finally` copy at *start_offset*, or None (see `_match_finally_wrapper`)."""
        matched = self._match_finally_wrapper(start_offset, owner)
        return None if matched is None else matched[0]

    def _match_finally_wrapper(self, start_offset: int, owner: Optional[int] = None) -> Optional[Tuple[int, list]]:
        """
        Offset just past the inline copy of a `finally` body that begins at *start_offset*.

        CPython compiles a finally body more than once: inline on the normal exit path,
        and again in a handler that runs it and re-raises (`PUSH_EXC_INFO ... RERAISE 0`).
        The exception table bounds the handler copy exactly, and the inline copy is the
        same code, so the two have the same instructions. Matching them tells where the
        inline copy ends, which is where the code after the try statement begins. Scanning
        for the first `return` instead (the fallback) swallows every statement that
        follows the try into the finally body.

        Only the handler of the `try` that ends at *start_offset* is used: *owner* when
        the caller knows it, otherwise the handler whose protected range ends there.
        Comparing against every handler would let an earlier `try` whose finally body
        is empty, or a prefix of this one, cut this body short.

        Returns None when no handler copy matches, for instance when the body itself
        returns or loops, so the caller can fall back to scanning. Otherwise returns the
        end offset together with the instructions of the matching handler copy. The end
        may be *start_offset* itself: that is a body with no instructions, i.e. `pass`.
        """
        instrs = self.instructions
        start_idx = next((i for i, ins in enumerate(instrs) if ins.offset == start_offset), None)
        if start_idx is None:
            return None
        entries = self._exception_entries()
        if entries is None:
            return None
        suppressed = getattr(self, "_suppress_push_exc_offsets", set())
        if owner is not None:
            candidates = [owner]
        else:
            candidates = sorted({e.target for e in entries
                                 if e.target in suppressed and self._ends_just_before(e.end, start_idx)})
        for wrapper_off in candidates:
            entry = next((e for e in entries if e.start == wrapper_off and e.lasti), None)
            if entry is None:
                continue
            wrapper = [ins for ins in instrs if wrapper_off < ins.offset < entry.end]
            if not wrapper or wrapper[-1].opname != "RERAISE" or wrapper[-1].arg != 0:
                continue  # a with-exit or other handler, not a finally copy
            end_idx = _match_finally_copy(instrs, start_idx, _copy_signature(wrapper[:-1]))
            if end_idx is not None:
                end_offset = instrs[end_idx].offset if end_idx < len(instrs) else instrs[-1].offset + 2
                return end_offset, wrapper[:-1]
        return None

    def _ends_just_before(self, end_offset: int, idx: int) -> bool:
        """True if *end_offset* is ``instrs[idx]``'s offset, or only NOPs lie between them."""
        instrs = self.instructions
        j = idx
        while j >= 0:
            if instrs[j].offset == end_offset:
                return True
            if j == idx or instrs[j].opname in ("NOP", "CACHE"):
                j -= 1
                continue
            return False
        return False

    def _render_finally_body(self, sub_instrs: list) -> list:
        """
        Render the instructions of a ``finally`` body into source lines.

        Runs the full sub-decompiler over the slice, so calls, loops, nested code objects and
        every other construct render exactly as they would in ordinary code.

        Parameters:
            sub_instrs (list): The instructions of the finally body (one copy of it).

        Returns:
            list: Source lines, without indentation; empty for an empty body.
        """
        return [ln for ln in self._render_handler_section(sub_instrs, 0) if ln.strip()]

    def _emit_deferred_finally(self, merge_offset: int, header_indent: int = -1) -> None:
        """
        Emit any pre-rendered except handlers followed by the deferred `finally:` block for a given finally-merge label.

        This writes lines into the decompiler's reconstructed output (via _append_reconstructed) and temporarily adjusts self.indent_level while emitting. It first emits any stored except-handler lines for the merge label, then emits a `finally:` header and its stored body lines if present. If no deferred handlers or finally body exist for the given merge label, nothing is emitted for that section.

        Parameters:
            merge_offset (int): The merge-label offset used as the key into the precomputed
                `_deferred_except_lines` and `_deferred_finally_lines` mappings.
            header_indent (int): The indentation level at which to emit `except:` / `finally:`
                headers. If negative, the method prefers the last value in `self._except_header_indents`
                when available and otherwise uses the current `self.indent_level`.
        """
        # Determine the header indent: prefer the explicitly passed value;
        # fall back to _except_header_indents if set; last resort: current level.
        if header_indent < 0:
            header_indent = self._except_header_indents[-1] if self._except_header_indents \
                else self.indent_level

        # Emit pre-decompiled except handler lines first
        except_lines = getattr(self, "_deferred_except_lines", {}).get(merge_offset)
        if except_lines:
            save_indent = self.indent_level
            self.indent_level = header_indent
            for line in except_lines:
                self._append_reconstructed(line)
            self.indent_level = save_indent

        # Then emit the finally: block
        finally_lines = getattr(self, "_deferred_finally_lines", {}).get(merge_offset)
        if finally_lines:
            self.indent_level = header_indent
            self._append_reconstructed("finally:")
            self.indent_level += 1
            for line in finally_lines:
                self._append_reconstructed(line)
            self.indent_level -= 1

    def _render_handler_section(self, handler_instrs: list, merge_offset: int) -> list:
        """
        Render an exception-handler instruction sequence into decompiled source lines.

        Runs a lightweight sub-decompiler (same backend class) with indent_level=0 over handler_instrs so the caller can replay the returned lines at the desired indentation.

        Parameters:
            handler_instrs (list): A list of disassembled instructions that form the handler body.
            merge_offset (int): Merge-label offset associated with this handler (used by callers to correlate deferred emission).

        Returns:
            list: Decompiled source lines (each element is a string); may be empty.
        """
        if not handler_instrs:
            return []

        # Build a mini code object placeholder and run a sub-decompiler instance
        # with the same decompiler class.
        dec_class = _pick_decompiler_class(self)
        # Create a sub-instance that shares our code_obj but gets a fresh state
        sub = dec_class.__new__(dec_class)
        # Minimal __init__ — borrow from DecompilerGeneric
        DecompilerGeneric.__init__(sub, self.code_obj, indent_level=0, beautification_level=self.beautification_level, target_version=self.target_version)
        # Install the same prescan results so sentinel detection works
        sub._try_nop_offsets = set()
        sub._suppress_push_exc_offsets = set()
        sub._finally_merge_offsets = set()
        sub._exc_handler_jump_offsets = getattr(self, "_exc_handler_jump_offsets", set())
        sub._exc_handler_continue_offsets = getattr(self, "_exc_handler_continue_offsets", set())
        sub._exc_handler_continue_offsets = getattr(self, "_exc_handler_continue_offsets", set())
        sub._with_exit_suppress_offsets = set()
        sub._with_body_end = {}
        sub._with_exit_owner = {}
        sub._finally_body_suppress = set()
        sub._handler_section_suppress = set()
        sub._wrapper_body_suppress = set()
        sub._push_exc_to_finally_merge = {}
        sub._deferred_finally_lines = {}
        sub._deferred_except_lines = {}
        sub._pending_finally_merge = None
        sub._nop_to_push_exc = {}
        sub._nop_to_finally_merge = {}
        # Sync while loop headers for continue detection in sub-decompiler fallback logic
        sub._while_header_targets = getattr(self, "_while_header_targets", {})
        sub._while_body_offsets = set()
        sub._while_true_ends = set()
        sub._ternary_jumps = {}
        sub._ternary_suppress = set()
        sub._compound_cond_map = {}
        sub._compound_suppress = set()
        sub._except_header_indents = [0]
        sub._exc_bound_names = set()
        # Install only the handler instructions
        sub.instructions = handler_instrs
        sub.pc = 0

        # Run the handler instructions through the dispatcher
        while sub.pc < len(sub.instructions):
            instr = sub.instructions[sub.pc]
            # Close blocks whose end offset we have passed
            while sub.blocks and instr.offset >= sub.blocks[-1][0]:
                boff, btype = sub.blocks[-1]
                if boff == instr.offset and sub._is_backward_instruction(instr) and isinstance(instr.argval, int) and instr.argval < instr.offset:
                    break  # delay popping until after the jump instruction

                sub.blocks.pop()
                last_i = len(sub.reconstructed) - 1
                while last_i >= 0 and not sub.reconstructed[last_i].strip():
                    last_i -= 1
                if last_i >= 0 and sub.reconstructed[last_i].strip().endswith(":"):
                    sub._append_reconstructed("pass")
                sub.indent_level -= 1
            sub.pc += 1
            sub._handle_instruction(instr)
            if instr.offset in sub._handler_return_close:
                sub._handler_return_close.discard(instr.offset)
                sub._close_except_clause()

        return [line for line in sub.reconstructed if line or line == ""]

    # ------------------------------------------------------------------
    # Instruction dispatch
    # ------------------------------------------------------------------

    def _build_dispatch(self):
        """
        Initialize the opcode dispatch table used to route bytecode instructions to handler methods.

        Populates self._dispatch with a mapping from opcode name strings to the corresponding
        instance methods that implement their decompilation behavior (loads, stores, imports,
        calls, control-flow, exception handling, collection builders, stack manipulation, and
        no-op/suppressed opcodes). The table includes expanded call opcode routing, sequence
        unpack handling, slice/subscript operators, combined no-op entries, and specialized
        handlers for exception/with/try-related opcodes.
        """
        self._dispatch = {
            # Loads
            "LOAD_CONST": self._op_load, "LOAD_NAME": self._op_load, "LOAD_FAST": self._op_load,
            "LOAD_GLOBAL": self._op_load, "LOAD_SMALL_INT": self._op_load, "LOAD_FAST_BORROW": self._op_load,
            "LOAD_CONST_BORROW": self._op_load, "LOAD_DEREF": self._op_load,
            "LOAD_FAST_BORROW_LOAD_FAST_BORROW": self._op_load, "LOAD_GLOBAL_MODULE": self._op_load,
            "LOAD_CLOSURE": self._op_load, "LOAD_CLASSDEREF": self._op_load, "LOAD_COMMON_CONSTANT": self._op_load,

            # Stores
            "STORE_NAME": self._op_store, "STORE_FAST": self._op_store, "STORE_GLOBAL": self._op_store,
            "STORE_DEREF": self._op_store,  # closure variable assignment (issue_2, Python 3.9 closures)
            "STORE_ATTR": self._op_store_attr, "STORE_SUBSCR": self._op_store_subscr,

            # Imports
            "IMPORT_NAME": self._op_import_name, "IMPORT_FROM": self._op_import_from,

            # Subscripts
            "BINARY_SLICE": self._op_binary_slice,
            "BINARY_OP": self._op_binary,
            "BINARY_SUBSCR": self._op_binary_subscr,

            # Exceptions
            "RAISE_VARARGS": self._op_raise_varargs, "PUSH_EXC_INFO": self._op_push_exc_info,
            "CHECK_EXC_MATCH": self._op_check_exc_match, "POP_EXCEPT": self._op_cleanup,
            "RERAISE": self._op_cleanup,
            "SETUP_FINALLY": self._op_setup_finally, "SETUP_EXCEPT": self._op_setup_finally,
            "SETUP_WITH": self._op_setup_with, "BEFORE_WITH": self._op_before_with,
            "WITH_EXCEPT_START": self._op_with_except_start, "BEGIN_FINALLY": self._op_with_except_start,

            # Functions
            "MAKE_FUNCTION": self._op_make_function, "SET_FUNCTION_ATTRIBUTE": self._op_set_function_attribute,

            # Returns
            "RETURN_VALUE": self._op_return_value, "RETURN_CONST": self._op_return_const,

            # Functions
            "CALL": self._op_call, "CALL_FUNCTION": self._op_call,
            "CALL_FUNCTION_KW": self._op_call, "CALL_FUNCTION_EX": self._op_call,
            "CALL_METHOD": self._op_call, "CALL_KW": self._op_call,
            "KW_NAMES": self._op_kw_names,
            "PUSH_NULL": self._op_push_null,

            # Pops
            "POP_TOP": self._op_pop_top,

            # Load build class
            "LOAD_BUILD_CLASS": self._op_load_build_class,

            # Stack manip
            "ROT_TWO": self._op_stack_manip, "ROT_THREE": self._op_stack_manip, "ROT_FOUR": self._op_stack_manip,
            "COPY": self._op_stack_manip, "SWAP": self._op_stack_manip,
            "DUP_TOP": self._op_dup_top, "DUP_TOP_TWO": self._op_dup_top,

            # Strings
            "FORMAT_VALUE": self._op_fstring, "FORMAT_SIMPLE": self._op_fstring,
            "BUILD_STRING": self._op_build_string,

            # Jumps
            "JUMP_FORWARD": self._op_jump, "JUMP_BACKWARD": self._op_jump,
            "JUMP_ABSOLUTE": self._op_jump,
            "POP_JUMP_IF_FALSE": self._op_conditional_jump,
            "POP_JUMP_IF_TRUE": self._op_conditional_jump,
            "JUMP_IF_FALSE_OR_POP": self._op_jump_if_false_or_pop,
            "JUMP_IF_TRUE_OR_POP": self._op_jump_if_true_or_pop,
            "POP_JUMP_IF_NONE": self._op_conditional_jump,
            "POP_JUMP_IF_NOT_NONE": self._op_conditional_jump,
            "JUMP_IF_NOT_EXC_MATCH": self._op_jump_if_not_exc_match,

            # Unary operators
            "UNARY_NEGATIVE": self._op_unary, "UNARY_POSITIVE": self._op_unary,
            "UNARY_INVERT": self._op_unary, "UNARY_NOT": self._op_unary,
            "CALL_INTRINSIC_1": self._op_unary,   # 3.12+: unary plus is an intrinsic

            # Iteration
            "FOR_ITER": self._op_for_iter,
            "INLINED_COMPREHENSION": self._op_inlined_comprehension,

            # Collections
            "BUILD_TUPLE": self._op_build_collection, "BUILD_LIST": self._op_build_collection,
            "BUILD_SET": self._op_build_collection, "BUILD_MAP": self._op_build_map,
            "BUILD_SLICE": self._op_build_slice,
            "BUILD_CONST_KEY_MAP": self._op_build_const_key_map,
            "GET_ITER": self._op_no_op, "UNPACK_SEQUENCE": self._op_unpack_sequence,
            "UNPACK_EX": self._op_unpack_sequence,
            "LIST_APPEND": self._op_list_append,
            "LIST_EXTEND": self._op_list_extend, "DICT_MERGE": self._op_dict_merge,
            "DICT_UPDATE": self._op_dict_merge,
            "SET_ADD": self._op_set_add, "SET_UPDATE": self._op_set_update,
            "MAP_ADD": self._op_map_add,

            # Secondary loads
            "LOAD_ATTR": self._op_load_attr, "LOAD_METHOD": self._op_load_attr,
            "LOAD_SUPER_ATTR": self._op_load_super_attr,
            "LOAD_FROM_DICT_OR_GLOBALS": self._op_load_from_dict_or_globals,

            # Coroutines
            "YIELD_VALUE": self._op_yield_value,

            # Deletes
            "DELETE_NAME": self._op_delete, "DELETE_FAST": self._op_delete, "DELETE_GLOBAL": self._op_delete,
            "DELETE_DEREF": self._op_delete,

            # Error
            "LOAD_ASSERTION_ERROR": self._op_load_assertion_error,

            # Nops
            "RESUME": self._op_no_op, "PRECALL": self._op_no_op, "CACHE": self._op_no_op,
            "COPY_FREE_VARS": self._op_no_op, "NOT_TAKEN": self._op_no_op, "MAKE_CELL": self._op_no_op,
            "END_FOR": self._op_no_op, "POP_ITER": self._op_no_op, "YIELD_FROM": self._op_no_op,
            "NOP": self._op_nop,
        }

    def _handle_instruction(self, instr: BytecodeInstruction):  # noqa: C901
        """
        Process a single disassembled bytecode instruction, update the decompiler's internal state, and emit reconstructed source lines when appropriate.

        Parameters:
            instr (BytecodeInstruction): The instruction to handle; this method may mutate the decompiler's expression stack, block/indentation state, program counter, and reconstructed output buffer.
        """
        opname = instr.opname
        # Suppress then-branch instructions of detected ternary expressions;
        # the ternary is pushed as a whole expression at POP_JUMP_IF time.
        if instr.offset in getattr(self, "_ternary_suppress", ()):
            return
        # Suppress intermediate instructions belonging to a compound boolean
        # These are the non-controlling POP_JUMP_IF_* instructions; pop their
        # clause operand from the stack so the stack stays balanced.
        if instr.offset in getattr(self, "_compound_suppress", ()):
            if self.stack:
                self.stack.pop()
            return
        # When we reach a finally-merge label, emit the pre-decompiled except
        # handlers + finally body in source order, then continue normally.
        # This check MUST come before _finally_body_suppress since the merge-label
        # instruction is the first instruction of the suppressed finally body range.
        if instr.offset in getattr(self, "_finally_merge_offsets", ()) or instr.offset in getattr(self, "_except_only_merge_offsets", ()):
            if instr.offset in getattr(self, "_finally_merge_offsets", ()):
                self._finally_merge_offsets.discard(instr.offset)
            if instr.offset in getattr(self, "_except_only_merge_offsets", ()):
                self._except_only_merge_offsets.discard(instr.offset)
            self._pending_finally_merge = None
            # self.indent_level at this point is the correct header indent:
            # the block manager has just closed the try_body block, leaving us
            # at the same level as the enclosing try:/with: statement.
            self._emit_deferred_finally(instr.offset, self.indent_level)
            # Only return (skip instruction) if it was actually a finally merge
            # (which means this instruction is the start of a suppressed body).
            if instr.offset in getattr(self, "_finally_body_suppress", ()):
                return
        # Suppress BEFORE_WITH __exit__(None,None,None) epilogue instructions. This comes
        # after the merge-label check: a try inside a with merges right at the exit call.
        if instr.offset in getattr(self, "_with_exit_suppress_offsets", ()):
            return
        # Suppress inlined finally-body instructions (deferred; emitted at merge label)
        if instr.offset in getattr(self, "_finally_body_suppress", ()):
            return
        # Suppress pre-decompiled except handler instructions (deferred; emitted at merge label)
        if instr.offset in getattr(self, "_handler_section_suppress", ()):
            return
        # Suppress re-raise wrapper and with-exit handler bodies
        if instr.offset in getattr(self, "_wrapper_body_suppress", ()):
            return

        handler = self._dispatch.get(opname)
        if handler:
            handler(instr)
        else:
            self._op_unknown(instr)

    def _op_unknown(self, instr: BytecodeInstruction):
        opname = instr.opname
        if "BINARY" in opname and opname != "BINARY_SUBSCR":
            self._op_binary(instr)
        elif "INPLACE" in opname:
            self._op_inplace(instr)
        elif "CALL" in opname and opname not in ("CALL_INTRINSIC_1", "CALL_INTRINSIC_2"):
            self._op_call(instr)
        elif "COMPARE_OP" in opname:
            self._op_compare(instr)
        elif "CONTAINS_OP" in opname:
            self._op_contains(instr)
        elif "IS_OP" in opname:
            self._op_is(instr)
        elif "TO_BOOL" in opname:
            pass  # value already on stack
        elif "LOAD_FAST" in opname or "LOAD_GLOBAL" in opname:
            self._op_load_fallback(instr)
        elif self._is_backward_instruction(instr):
            self._op_jump(instr)
        elif self._is_compound_cjump(opname):
            self._op_conditional_jump(instr)

    def _op_load(self, instr: BytecodeInstruction):
        opname = instr.opname
        if _is_code(instr.argval):
            self.stack.append(("code", instr.argval))
        elif instr.offset == self._doc_load_offset:
            pass  # already emitted as the docstring
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
                
        # 3.11+ LOAD_GLOBAL with bit 0 set pushes NULL sentinel.
        # Python 3.9/3.10 do not use this bit for sentinels.
        if getattr(self, "target_version", sys.version_info) >= (3, 11) and ("LOAD_GLOBAL" in opname):
            arg = int(instr.arg) if instr.arg is not None else 0
            if (arg & 1):
                # Pushes [NULL, func]
                func = self.stack.pop()
                self.stack.append(StackNULL(is_method=False))
                self.stack.append(func)

        # ── stores ─────────────────────────────────────────────────────

    def _op_store(self, instr: BytecodeInstruction):
        opname = instr.opname
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
            if (cleanup and name == cleanup and str(val) == "None") or (name in bound and str(val) == "None"):
                return
            if name in (
                "__module__", "__qualname__", "__firstlineno__",
                "__classdictcell__", "__classcell__",
                "__static_attributes__", "__classdict__",
            ):
                return
            if isinstance(val, tuple) and len(val) >= 2 and val[0] == "import":
                _, imp_name, fromlist, level = val
                dots = '.' * int(level) if level and int(level) > 0 else ''
                if str(fromlist) in ("None", "()"):
                    self._append_reconstructed(f"import {dots}{imp_name}")
                else:
                    inames = str(fromlist).strip("()").replace("'", "").replace(" ", "")
                    mod_part = f" {imp_name}" if imp_name else ""
                    self._append_reconstructed(f"from {dots}{mod_part} import {inames}")
            elif isinstance(val, tuple) and len(val) >= 2 and val[0] in ("func", "class"):
                self._append_reconstructed(str(val[1]), indent_multiline=True)
                if self.indent_level == 0:
                    self.reconstructed.append("")
            elif name == "__doc__" and instr.offset in self._class_doc_store_offsets:
                self._emit_docstring(self._class_doc_store_offsets[instr.offset])
            elif val == name:
                pass  # suppress redundant x = x
            else:
                self._append_reconstructed(f"{name} = {val}", indent_multiline=False)

    def _op_store_attr(self, instr: BytecodeInstruction):
        opname = instr.opname
        if len(self.stack) >= 2:
            obj = self.stack.pop()
            val = self.stack.pop()
            self._append_reconstructed(f"{obj}.{instr.argval} = {val}")

        # STORE_SUBSCR (x[key] = val)

    def _op_store_subscr(self, instr: BytecodeInstruction):
        opname = instr.opname
        if len(self.stack) >= 3:
            key = self.stack.pop()
            container = self.stack.pop()
            val = self.stack.pop()
            self._append_reconstructed(f"{container}[{key}] = {val}")

        # ── imports ────────────────────────────────────────────────────

    def _op_import_name(self, instr: BytecodeInstruction):
        opname = instr.opname
        if len(self.stack) >= 2:
            fromlist = self.stack.pop()
            level = self.stack.pop()
            self.stack.append(("import", instr.argval, fromlist, level))

    def _op_import_from(self, instr: BytecodeInstruction):
        """
        Emit a `from <module> import <name>` statement when pairing an IMPORT_FROM with a prior IMPORT_NAME tuple on the stack.

        If the top-of-stack is the import sentinel tuple produced by IMPORT_NAME (("import", module, fromlist, level)), this emits a corresponding `from ... import <name>` line (respecting relative import `level`) and pushes a sentinel `("_from_import_done", name)` so a subsequent STORE_* does not re-emit the symbol. Otherwise, pushes the import name value from `instr.argval` onto the expression stack for later use.

        Parameters:
            instr (BytecodeInstruction): The IMPORT_FROM instruction being processed; `instr.argval` is the imported symbol name.
        """
        opname = instr.opname
        # TOS is the ("import", module, fromlist, level) tuple from IMPORT_NAME.
        # Emit `from module import name` immediately and leave the module tuple
        # on the stack (POP_TOP will clean it up after all IMPORT_FROM/STORE
        # pairs are done).
        if self.stack and isinstance(self.stack[-1], tuple) and self.stack[-1][0] == "import":
            imp_tuple = self.stack[-1]
            mod_name = str(imp_tuple[1])
            level = imp_tuple[3]
            dots = '.' * int(level) if level and int(level) > 0 else ''
            mod_part = f" {mod_name}" if mod_name else ""
            sym = str(instr.argval)
            self._append_reconstructed(f"from {dots}{mod_part} import {sym}")
            # Push a sentinel so STORE_NAME for this symbol is suppressed
            self.stack.append(("_from_import_done", sym))
        else:
            self.stack.append(instr.argval)

    def _op_unpack_sequence(self, instr: BytecodeInstruction):
        # UNPACK_SEQUENCE N / UNPACK_EX followed by its targets (STORE_* or nested
        # unpackings) is a tuple-unpacking assignment: (a, b), *c = expr.
        shape = _unpack_shape(instr)
        n = shape[0] if shape is not None else 0
        if n < 1:
            return
        pending: List[str] = []
        parsed = self._parse_store_target(self.instructions, self.pc - 1, pending)
        targets = None
        next_pc = self.pc
        if parsed is not None and not pending:
            targets, next_pc = [parsed[0]], parsed[2]

        if targets is not None and self.stack:
            expr = str(self.stack.pop())
            self._append_reconstructed(f"{targets[0]} = {expr}")
            self.pc = next_pc
        elif self.stack:
            # Fallback for complex un-matched structures:
            # discrete indexed extraction from the stack value
            val = self.stack.pop()
            val_str = str(val)
            # Push discrete accessors onto the stack for subsequent STORE_*
            # to consume.  Since STOREs are processed sequentially, we push
            # item[n-1], then item[n-2], ..., item[0] so that the stack
            # serves them up in 0..n-1 order.
            if instr.opname == "UNPACK_EX" and isinstance(instr.arg, int):
                before, after = instr.arg & 0xFF, instr.arg >> 8
                stop = f"-{after}" if after else ""
                accessors = ([f"{val_str}[{i}]" for i in range(before)]
                             + [f"list({val_str}[{before}:{stop}])"]
                             + [f"{val_str}[{i - after}]" for i in range(after)])
            else:
                accessors = [f"{val_str}[{i}]" for i in range(n)]
            self.stack.extend(reversed(accessors))

        # ── subscript / attr ───────────────────────────────────────────

    def _op_binary_subscr(self, instr: BytecodeInstruction):
        """
        Constructs a subscription expression from the top two stack entries and pushes the resulting string back onto the expression stack.

        Pops the subscript expression and the container expression (if available), formats them as "container[subscript]", and appends that string to self.stack. If there are fewer than two items on the stack, no change is made.

        Parameters:
            instr (BytecodeInstruction): The bytecode instruction being handled; provided for context but not otherwise inspected by this handler.
        """
        opname = instr.opname
        if len(self.stack) >= 2:
            sub = self.stack.pop()
            container = self.stack.pop()
            self.stack.append(f"{container}[{sub}]")

        # ── exceptions ─────────────────────────────────────────────────

    def _op_raise_varargs(self, instr: BytecodeInstruction):
        opname = instr.opname
        num = int(instr.arg) if instr.arg is not None else 0
        def _pop_exc_val():
            if not self.stack: return "Exception"
            v = self.stack.pop()
            # Sentinels skipped only on 3.11+
            if getattr(self, "target_version", sys.version_info) >= (3, 11):
                while self._is_null_sentinel(v) and self.stack:
                    v = self.stack.pop()
            return v

        if num == 2:
            cause = _pop_exc_val()
            exc = _pop_exc_val()
            self._append_reconstructed(f"raise {exc} from {cause}")
        elif num == 1:
            val = _pop_exc_val()
            self._append_reconstructed(f"raise {val}")
        else:
            self._append_reconstructed("raise")

        # try/except/finally structural blocks.
        # Modern CPython (3.11+) exception handling structure:
        #   offset 2:  NOP                         ← try body start marker
        #   ...        <try body instructions>
        #              RETURN_CONST None            ← normal exit (suppress)
        #   >> N:      PUSH_EXC_INFO               ← except-handler entry
        #              LOAD_NAME <ExcType>
        #              CHECK_EXC_MATCH
        #              POP_JUMP_IF_FALSE → reraise
        #              STORE_NAME e                 ← 'as e' binding
        #              <handler body>
        #              POP_EXCEPT
        #              LOAD_CONST None; STORE_NAME e; DELETE_NAME e  ← cleanup
        #              JUMP_BACKWARD / RETURN_CONST None
        #   >> reraise: RERAISE ...
        #   >> M:      PUSH_EXC_INFO               ← finally-handler entry
        #              <finally body>
        #              RERAISE 0

    def _op_push_exc_info(self, instr: BytecodeInstruction):
        opname = instr.opname
        # Silently suppress re-raise wrappers and with-exit handlers
        if instr.offset in getattr(self, "_suppress_push_exc_offsets", ()):
            return

        # Close try body block if tracked
        if self.blocks and self.blocks[-1][1] == "try_body":
            self.blocks.pop()
            self.indent_level -= 1

        # Record the indent at which except headers should be emitted
        indent = getattr(self, "_pei_indents", {}).get(instr.offset, self.indent_level)
        self._except_header_indents.append(indent)
        cleanup = self._handler_cleanup_target(instr.offset)
        if cleanup is not None:
            self._header_indent_by_cleanup[cleanup] = indent
        # Peek: is there a LOAD + CHECK_EXC_MATCH coming?
        look = self.pc
        while look < len(self.instructions) and self.instructions[look].opname in (
            "RESUME", "NOP", "CACHE", "NOT_TAKEN"
        ):
            look += 1
        if look < len(self.instructions) and self.instructions[look].opname in (
            "LOAD_NAME", "LOAD_GLOBAL", "LOAD_FAST", "LOAD_DEREF", "PUSH_NULL"
        ):
            self.stack.append("_exc_info")
            return  # defer header to CHECK_EXC_MATCH
        # Bare except (no type check)
        self.indent_level = indent
        self._append_reconstructed("except:")
        self.indent_level += 1

    def _op_check_exc_match(self, instr: BytecodeInstruction):
        opname = instr.opname
        exc_type = self.stack.pop() if self.stack else "Exception"
        if self.stack and str(self.stack[-1]) == "_exc_info":
            self.stack.pop()
        # Reset indent to the except-header level (handles multi-except chains
        # where the first handler incremented indent but the second check fires
        # without a new PUSH_EXC_INFO reset).
        # A later clause of the same try (`except A: ... except B:`) shares the first
        # clause's exception clean-up handler. When the previous clause already closed
        # (it ended in `return`, say), reopen at that try's header indent.
        header = self._header_indent_by_cleanup.get(self._handler_cleanup_target(instr.offset))
        if header is not None and (not self._except_header_indents or self._except_header_indents[-1] != header):
            self._except_header_indents.append(header)
        if self._except_header_indents:
            self.indent_level = self._except_header_indents[-1]
        # Peek ahead from the current position to find the optional
        # STORE_NAME / STORE_FAST that binds the 'as varname' in except.
        # The bytecode varies by version:
        #   3.12: POP_JUMP_IF_FALSE -> STORE_NAME e
        #   3.14: POP_JUMP_IF_FALSE -> POP_TOP -> STORE_NAME e
        #         (may include additional CACHE or other slots)
        # Strategy: skip forward through "harmless" single-cycle opcodes
        # (POP_JUMP_IF_*, POP_TOP, CACHE, NOP, COPY, RESUME) until we
        # either find a STORE or hit something that clearly belongs to
        # the handler body (a LOAD, BINARY, COMPARE, RETURN, RERAISE...).
        # The window is capped at 10 instructions to prevent runaway.
        # Scan forward from the current PC to find the optional
        # STORE_NAME / STORE_FAST that binds 'as varname' in except.
        # The binding zone varies by Python version:
        #   3.12 no-as:  POP_JUMP_IF_FALSE -> POP_TOP    -> LOAD_CONST  -> body
        #   3.12 as-e:   POP_JUMP_IF_FALSE -> STORE_NAME e -> body
        #   3.14 no-as:  POP_JUMP_IF_FALSE -> POP_TOP    -> LOAD_CONST  -> body
        #   3.14 as-e:   POP_JUMP_IF_FALSE -> POP_TOP    -> STORE_NAME e -> body
        # The correct discriminator is NOT "POP_TOP = no binding".
        # On 3.14, POP_TOP appears in BOTH cases (it pops the exc_type from
        # the CHECK_EXC_MATCH result). The real signal is whether STORE_NAME
        # appears before any LOAD_* or other body-start instruction.
        # Rules:
        #  1. Skip POP_JUMP_IF_* opcodes (the type-match conditional gate)
        #  2. Skip POP_TOP, CACHE, NOP, RESUME, COPY (neutral in all versions)
        #  3. If STORE_NAME / STORE_FAST found -> that IS the 'as e' binding
        #  4. Stop (no binding) at: any LOAD_*, RERAISE, RETURN_*, JUMP_*,
        #     or a jump-target boundary (entered a new block)
        #  5. Cap at 8 steps

        as_name = None
        look = self.pc
        # Step 1: skip conditional jumps
        while (look < len(self.instructions)
               and "POP_JUMP_IF" in self.instructions[look].opname):
            look += 1
        # Steps 2-5: scan binding zone
        for _ in range(8):
            if look >= len(self.instructions):
                break
            ins_l = self.instructions[look]
            op = ins_l.opname
            # Step 3: found the 'as e' binding
            # (STORE_DEREF when a closure captures the name, STORE_GLOBAL under `global`)
            if op in ("STORE_NAME", "STORE_FAST", "STORE_DEREF", "STORE_GLOBAL"):
                as_name = str(ins_l.argval)
                self._exc_as_store_offset = ins_l.offset
                break
            # Step 4: definitively in handler body — no binding
            if op in _IS_COMP_GEN_STOP or self._is_backward_jump(op):
                break
            # Step 4: new block boundary — no binding
            if ins_l.is_jump_target:
                break
            # Step 2: neutral opcode — skip past it
            if op in _IS_COMP_GEN_SKIP:
                look += 1
                continue
            # Unknown opcode — stop safely
            break
        if as_name is None:
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

    def _op_cleanup(self, instr: BytecodeInstruction):
        opname = instr.opname
        # POP_EXCEPT for try/except cleanup.
        # In newer Python (3.11+), the POP_EXCEPT is often immediately followed
        # by a JUMP_BACKWARD (continue) or JUMP_FORWARD (end of try/except).
        # To avoid emitting a premature 'pass' or popping indentation before
        # the 'continue' is rendered, we check if the next instruction is a
        # jump that will be handled by our special logic.
        if opname == "POP_EXCEPT" and self._except_header_indents:
            next_is_handler_jump = False
            if self.pc < len(self.instructions):
                nxt = self.instructions[self.pc]
                if nxt.offset in getattr(self, "_exc_handler_jump_offsets", ()):
                    next_is_handler_jump = True

            if not next_is_handler_jump and not self._defer_close_to_handler_return():
                self._close_except_clause()
        pass  # cleanup suppression (_exc_cleanup_name) stays active until DELETE_NAME fires

    def _close_except_clause(self) -> None:
        """End the innermost except clause: give an empty one a `pass`, and dedent."""
        if not self._except_header_indents:
            return
        last_idx = len(self.reconstructed) - 1
        while last_idx >= 0 and not self.reconstructed[last_idx].strip():
            last_idx -= 1
        if last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":"):
            self._append_reconstructed("pass")
        self.indent_level = self._except_header_indents.pop()

    def _handler_cleanup_target(self, offset: int) -> Optional[int]:
        """
        The clean-up handler (``COPY 3; POP_EXCEPT; RERAISE 1``) that protects the
        handler code at *offset* (3.11+); every clause of one try statement shares it.
        """
        entries = self._exception_entries() or []
        hit = [e for e in entries if e.start <= offset < e.end and e.lasti]
        return min(hit, key=lambda e: e.end - e.start).target if hit else None

    def _is_as_name_cleanup(self, k: int, bound: set) -> bool:
        """Whether ``LOAD_CONST None; STORE name; DELETE name`` for an ``as`` name starts at *k*."""
        trio = self.instructions[k:k + 3]
        if len(trio) < 3 or trio[0].opname != "LOAD_CONST" or trio[0].argval is not None:
            return False
        store, delete = trio[1], trio[2]
        return (store.opname.startswith("STORE_") and delete.opname.startswith("DELETE_")
                and str(store.argval) in bound and delete.argval == store.argval)

    def _handler_exit_return(self, start: int) -> Optional[Tuple[int, int]]:
        """
        Find the `return` that ends an except clause, from the instruction after its
        POP_EXCEPT (#165).

        A `return` inside an except clause leaves the handler first: POP_EXCEPT, the
        clean-up of an ``as`` name (``LOAD_CONST None; STORE name; DELETE name``), and
        then the return. A value computed before the exit was moved below the exception
        (``ROT_FOUR``/``SWAP 2``) and is returned directly; otherwise the returned
        expression is computed here, as straight-line code. Code that any jump reaches,
        such as the statements after the try statement, is not part of the clause.

        Returns:
            ``(resume_index, return_index)``: where decompiling continues (past the
            clean-up) and the index of the RETURN instruction; or None.
        """
        instrs = self.instructions
        bound = self._exc_bound_names
        k = start
        while k < len(instrs):
            op = instrs[k].opname
            if op in ("NOP", "CACHE", "POP_BLOCK"):
                k += 1
            elif self._is_as_name_cleanup(k, bound):
                k += 3
            else:
                break
        resume = k
        targets = {self._get_jump_target(x) for x in instrs if "JUMP" in x.opname or x.opname == "FOR_ITER"}
        placed = getattr(self, "_finally_body_suppress", set())
        while k < len(instrs):
            ins = instrs[k]
            if ins.offset in placed:
                k += 1  # the inline finally body on the way out, rendered by its finally:
                continue
            if ins.offset in targets or ins.is_jump_target:
                return None
            if ins.opname in ("RETURN_VALUE", "RETURN_CONST"):
                return resume, k
            if (ins.opname == "ROT_TWO" or (ins.opname == "SWAP" and ins.arg == 2)) \
                    and k + 1 < len(instrs) and instrs[k + 1].opname == "POP_TOP":
                k += 2  # returning from inside a loop drops its iterator
                continue
            if ("JUMP" in ins.opname or ins.opname.startswith(("STORE_", "DELETE_", "POP_", "RAISE", "RERAISE",
                                                                "SETUP_", "FOR_ITER", "YIELD", "SEND"))
                    or ins.opname in ("PUSH_EXC_INFO", "WITH_EXCEPT_START", "END_FOR")):
                return None
            k += 1
        return None

    def _defer_close_to_handler_return(self) -> bool:
        """
        At an except clause's POP_EXCEPT: when the clause ends in `return`, keep it open
        until that return has been emitted, so the return stays inside the clause
        instead of landing after the try statement (#165).
        """
        found = self._handler_exit_return(self.pc)
        if found is None:
            return False
        resume, ret = found
        instrs = self.instructions
        body = [x for x in instrs[resume:ret] if x.opname not in ("NOP", "CACHE")]
        returns_none = (instrs[ret].opname == "RETURN_CONST" and instrs[ret].argval is None and not body) or (
            instrs[ret].opname == "RETURN_VALUE" and len(body) == 1
            and body[0].opname == "LOAD_CONST" and body[0].argval is None)
        if returns_none:
            # A bare `return` and the implicit return at the end of the function look
            # the same here (3.12+ copies the latter into the handler). Only the
            # explicit one starts a line of its own at the POP_EXCEPT.
            first = max(self.pc - 1, 0)
            while first > 0 and instrs[first - 1].opname in ("POP_BLOCK", "ROT_FOUR", "SWAP", "NOP"):
                first -= 1  # the exit's own instructions before POP_EXCEPT (3.9: POP_BLOCK)
            if not any(instrs[j].starts_line is not None for j in range(first, ret + 1)):
                return False
            self._explicit_returns.add(instrs[ret].offset)
        self.pc = resume
        if self._except_header_indents and self.indent_level > self._except_header_indents[-1] + 1:
            # The return sits in a block inside the clause (`if c: return x`); the
            # clause goes on after that block and closes at its own POP_EXCEPT.
            return True
        self._handler_return_close.add(instrs[ret].offset)
        return True

    def _op_setup_finally(self, instr: BytecodeInstruction):
        opname = instr.opname
        # Legacy 3.9/3.10 try/except via SETUP_* opcodes
        self._append_reconstructed("try:")
        self.indent_level += 1
        jump_target = self._get_jump_target(instr)
        if isinstance(jump_target, int):
            min_t = None
            for j_ins in self.instructions:
                if j_ins.offset >= instr.offset:
                    break
                # Only use FOR_ITER as definite loop boundaries;
                # conditional jumps inside outer except bodies are not loop limits.
                if j_ins.opname not in ("FOR_ITER",):
                    continue
                t = self._get_jump_target(j_ins)
                if t is not None and isinstance(t, int) and t > instr.offset:
                    if min_t is None or t < min_t:
                        min_t = t
            if min_t is not None and min_t < jump_target:
                jump_target = min_t
        self.blocks.append((jump_target, "try_body"))

    def _op_setup_with(self, instr: BytecodeInstruction):
        opname = instr.opname
        ctx = self.stack.pop() if self.stack else "ctx"
        # Peek ahead for the STORE that binds the 'as' variable (3.9/3.10).
        # In 3.9 the instruction immediately after SETUP_WITH is always
        # STORE_FAST / STORE_NAME binding the __enter__() return value.
        as_var = None
        look = self.pc
        while look < len(self.instructions) and self.instructions[look].opname in _GENERIC_SKIP_OPS:
            look += 1
        if look < len(self.instructions) and self.instructions[look].opname in (
            "STORE_FAST", "STORE_NAME", "STORE_GLOBAL"
        ):
            as_var = str(self.instructions[look].argval)
            self.pc = look + 1   # consume the STORE so it isn't processed again
        if as_var:
            self._append_reconstructed(f"with {ctx} as {as_var}:")
        else:
            self._append_reconstructed(f"with {ctx}:")
        self.indent_level += 1
        jump_target = self._get_jump_target(instr)
        self.blocks.append((jump_target, "with"))
        # In Python 3.9 the normal with-exit sequence immediately preceding the
        # exception handler looks like:
        #   LOAD_CONST None; DUP_TOP; DUP_TOP; CALL_FUNCTION 3; POP_TOP; JUMP_FORWARD
        # Suppress these so they don't produce spurious 'except:' / func() lines.
        t_idx = next(
            (i for i, x in enumerate(self.instructions) if x.offset == jump_target), -1
        )
        if t_idx > 0:
            j = t_idx - 1
            # Walk backwards to collect the with-exit epilogue block.
            # In Python 3.9, the epilogue always starts at POP_BLOCK:
            #   POP_BLOCK; LOAD_CONST None; DUP_TOP; DUP_TOP;
            #   CALL_FUNCTION 3; POP_TOP; JUMP_FORWARD
            # We must NOT walk past POP_BLOCK because any POP_TOP
            # before it belongs to the user's with-body (e.g. k.write(a)).
            suppress_start = None
            while j >= 0:
                op = self.instructions[j].opname
                if op in ("LOAD_CONST", "DUP_TOP", "CALL_FUNCTION",
                          "POP_TOP", "JUMP_FORWARD", "JUMP_ABSOLUTE"):
                    suppress_start = self.instructions[j].offset
                    j -= 1
                elif op == "POP_BLOCK":
                    # POP_BLOCK is the epilogue boundary; include it then stop.
                    suppress_start = self.instructions[j].offset
                    break
                else:
                    break
            if suppress_start is not None:
                _fb = getattr(self, "_finally_body_suppress", set())
                for k in range(j, t_idx):  # j is POP_BLOCK index now
                    op = self.instructions[k].opname
                    if op not in ("LOAD_CONST", "DUP_TOP", "CALL_FUNCTION",
                                  "POP_TOP", "JUMP_FORWARD", "JUMP_ABSOLUTE", "POP_BLOCK"):
                        break
                    _fb.add(self.instructions[k].offset)
                self._finally_body_suppress = _fb
            self._suppress_with_exits_39(instr.offset, t_idx)

    def _suppress_with_exits_39(self, setup_offset: int, handler_idx: int) -> None:
        """
        3.9/3.10: hide the `__exit__` calls the compiler inlines into a `with` body, and
        the exception handler that follows it.

        Every way out of the body (`return`, `break`, `continue`, falling off the end)
        is ``POP_BLOCK; [ROT_TWO;] LOAD_CONST None; DUP_TOP; DUP_TOP; CALL_FUNCTION 3;
        POP_TOP`` followed by the exit itself. Only the exit is source code. The handler
        (``WITH_EXCEPT_START ... POP_EXCEPT; POP_TOP`` and a jump back into the code
        after the block) runs only on exceptions and has no source of its own.
        """
        instrs = self.instructions
        setup_idx = next((k for k, x in enumerate(instrs) if x.offset == setup_offset), handler_idx)
        hidden = self._finally_body_suppress
        exit_call = ("LOAD_CONST", "DUP_TOP", "DUP_TOP", "CALL_FUNCTION", "POP_TOP")
        for k in range(setup_idx + 1, handler_idx):
            if instrs[k].opname != "POP_BLOCK":
                continue
            m = k + 1
            if m < len(instrs) and instrs[m].opname == "ROT_TWO":
                m += 1
            seq = instrs[m:m + len(exit_call)]
            if (tuple(x.opname for x in seq) == exit_call and seq[0].argval is None
                    and seq[3].arg == 3):
                hidden.update(x.offset for x in instrs[k:m + len(exit_call)])
        if not 0 <= handler_idx < len(instrs) or instrs[handler_idx].opname != "WITH_EXCEPT_START":
            return
        end = next((n for n in range(handler_idx, len(instrs)) if instrs[n].opname == "POP_EXCEPT"), None)
        if end is None:
            return
        end += 1  # the POP_TOP that drops the exit function
        if end < len(instrs) and instrs[end].opname == "POP_TOP":
            end += 1
        if (end < len(instrs) and instrs[end].opname in ("JUMP_ABSOLUTE", "JUMP_FORWARD")
                and not instrs[end].is_jump_target):
            end += 1  # the handler's own jump back to the code after the block
        hidden.update(x.offset for x in instrs[handler_idx:end])

    def _op_before_with(self, instr: BytecodeInstruction):
        opname = instr.opname
        # BEFORE_WITH — Python 3.11+ context-manager entry opcode.
        # At this point TOS is the context manager object (result of CALL).
        # BEFORE_WITH calls __enter__(), pushes the __exit__ callable, and
        # leaves the __enter__ return value on top of the stack.
        # The STORE_FAST / STORE_NAME that immediately follows binds the
        # 'as' variable.
        ctx = self.stack.pop() if self.stack else "ctx"
        # Peek ahead for the STORE that binds the 'as' variable name.
        as_var = None
        look = self.pc
        while look < len(self.instructions) and self.instructions[look].opname in _GENERIC_SKIP_OPS:
            look += 1
        if look < len(self.instructions) and self.instructions[look].opname in (
            "STORE_FAST", "STORE_NAME", "STORE_GLOBAL"
        ):
            as_var = str(self.instructions[look].argval)
            # Advance pc past the STORE so it is not processed again
            self.pc = look + 1
        if as_var:
            self._append_reconstructed(f"with {ctx} as {as_var}:")
        else:
            self._append_reconstructed(f"with {ctx}:")
        self.indent_level += 1
        # Find the PUSH_EXC_INFO that guards this with-block's exit handler
        # and record it as the block boundary so we can de-indent properly.
        # The with-handler PUSH_EXC_INFO is the one with the highest offset
        # (the __exit__ call handler, at the very end of the function).
        with_exc_offsets = [
            ins.offset for ins in self.instructions
            if ins.opname == "PUSH_EXC_INFO"
            and ins.offset > instr.offset
            # The with-exit PUSH_EXC_INFO is NOT in _finally_push_offsets
            # (it handles the __exit__ call, not user finally: code).
        ]
        body_end = self._with_body_end.get(self.instructions[self.pc].offset) if self.pc < len(self.instructions) else None
        if body_end is None and self.pc > 0:
            body_end = self._with_body_end.get(self.instructions[self.pc - 1].offset)
        if body_end is not None:
            self.blocks.append((body_end, "with"))
        elif with_exc_offsets:
            # Use the last (highest-offset) PUSH_EXC_INFO — that is the one
            # that handles the __exit__ on exceptional exit from the with body.
            self.blocks.append((max(with_exc_offsets), "with"))
        # Also record the indent level so finally: de-indents correctly
        self._except_header_indents.append(self.indent_level - 1)

    def _op_with_except_start(self, instr: BytecodeInstruction):
        opname = instr.opname

        # ── functions ──────────────────────────────────────────────────

    def _op_make_function(self, instr: BytecodeInstruction):
        opname = instr.opname
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
        closure = str(self.stack.pop()) if (flags & 0x08) and self.stack else None
        annots = str(self.stack.pop()) if (flags & 0x04) and self.stack else None
        kw_defs = str(self.stack.pop()) if (flags & 0x02) and self.stack else None
        defaults = self.stack.pop() if (flags & 0x01) and self.stack else None

        if code_obj_val is not None and isinstance(code_obj_val, tuple) and code_obj_val[0] == "code":
            inner_code = code_obj_val[1]
            positional = list(inner_code.co_varnames[: inner_code.co_argcount])

            # CO_VARARGS (0x04): function accepts *args — stored in
            # co_varnames[co_argcount] (after all positional params).
            _CO_VARARGS = 0x04
            _CO_VARKEYWORDS = 0x08
            has_varargs = bool(inner_code.co_flags & _CO_VARARGS)
            has_varkw = bool(inner_code.co_flags & _CO_VARKEYWORDS)
            # Index of *args and **kwargs names in co_varnames:
            varargs_idx = inner_code.co_argcount + inner_code.co_kwonlyargcount
            varkw_idx = inner_code.co_argcount + inner_code.co_kwonlyargcount + int(has_varargs)
            varargs_name = (
                "*" + inner_code.co_varnames[varargs_idx]
                if has_varargs and varargs_idx < len(inner_code.co_varnames)
                else None
            )
            varkw_name = (
                "**" + inner_code.co_varnames[varkw_idx]
                if has_varkw and varkw_idx < len(inner_code.co_varnames)
                else None
            )

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

            # Extract keyword-only parameters
            kwonly_params = []
            if inner_code.co_kwonlyargcount > 0:
                kwonly_start = inner_code.co_argcount
                kwonly_end = inner_code.co_argcount + inner_code.co_kwonlyargcount
                kwonly_names = list(inner_code.co_varnames[kwonly_start:kwonly_end])

                # Attach defaults if kw_defs is available
                if kw_defs:
                    # kw_defs is usually a string repr of a dict like "{'x': 1, 'y': 'hi'}"
                    # We can use regex to safely extract the value for each parameter name.
                    for i, name in enumerate(kwonly_names):
                        # Pattern for finding the value of a key in a dict repr
                        m_val = re.search(rf"'{name}': ([^,}}]+)", kw_defs)
                        if m_val:
                            val = m_val.group(1).strip()
                            kwonly_names[i] = f"{name}={val}"
                kwonly_params = kwonly_names

            # Combine positional, keyword-only, *args, and **kwargs
            params = positional.copy()

            # Position-only arguments marker (/)
            # inner_code.co_posonlyargcount exists in Python 3.8+
            posonly_count = getattr(inner_code, "co_posonlyargcount", 0)
            if posonly_count > 0:
                params.insert(posonly_count, "/")

            if varargs_name:
                params.append(varargs_name)
            if kwonly_params:
                # If there's no *args but we have kw-only, insert bare '*' separator
                if not varargs_name:
                    params.append("*")
                params.extend(kwonly_params)
            if varkw_name:
                params.append(varkw_name)

            dec_class = _pick_decompiler_class(self)
            dec = dec_class(inner_code, indent_level=1, beautification_level=self.beautification_level, target_version=self.target_version)
            body = dec.decompile()
            sig = f"def {inner_code.co_name}({', '.join(params)}):"
            
            if inner_code.co_name == '<lambda>':
                rendered = _render_func_tuple(f"{sig}\n{body}", [])
                if not rendered.startswith("("):
                    rendered = f"({rendered})"
                self.stack.append(rendered)
            else:
                self.stack.append(("func", f"{sig}\n{body}"))
        else:
            self.stack.append("make_function(?)")

        # ── SET_FUNCTION_ATTRIBUTE (Python 3.14+) ─────────────────────

    def _op_set_function_attribute(self, instr: BytecodeInstruction):
        opname = instr.opname
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

    def _op_return_value(self, instr: BytecodeInstruction):
        opname = instr.opname
        if self.stack:
            val = self.stack.pop()
            val_str = str(val)
            if val_str == "None" and self._module_level_return_none():
                return
            # Use the new predicate for smarter suppression
            is_compiler_gen = val_str == "None" and self.is_compiler_generated_return(self.pc - 1)

            if is_compiler_gen:
                pass
            elif "__class__" in val_str or "__classdict__" in val_str:
                pass
            else:
                self._append_reconstructed(f"return {val}")
                # A return exits the function entirely. If we are within a try/except,
                # any remaining instructions are either dead code or jump targets for
                # other control flow. Because RETURN_VALUE bypasses the normal POP_BLOCK
                # sequence, we need to artificially clear out try/except blocks to
                # restore indentation for any following peer functions/blocks.
                # Actually, the Decompiler runs linearly. Returning doesn't shift
                # the decompiler's physical PC backward, but any code immediately AFTER
                # a return is typically an except/finally block or unreachable. 
                # We should drop all pending try_body / exc_cleanup blocks that belong
                # to the current return path. Wait, _close_blocks uses the block target.
                # There is no need, the blocks will be closed by _close_blocks at their
                # targets, or they will be skipped if unreachable.
                # Actually, let's just leave it, maybe the bug is in _render_finally_body or try_ret block handling.
    def _op_return_const(self, instr: BytecodeInstruction):
        opname = instr.opname
        # RETURN_CONST None has two meanings:
        #   - Inside a 'while True:' (NOP-driven, unconditional) block: it's `break`
        #   - Everywhere else: compiler-generated exit sentinel — suppress if effectively last
        if instr.argval is None:
            # The exit path of a suppressed bottom-of-loop condition copy: the loop
            # simply ends here, there is nothing to emit.
            if instr.offset in self._dup_guard_exits and not (getattr(self.code_obj, "co_flags", 0) & 0x1):
                return
            if self._module_level_return_none():
                return
            in_while_true = any(
                b[1] == "while" and b[0] in getattr(self, "_while_true_ends", set())
                for b in self.blocks
            )
            if in_while_true:
                self._append_reconstructed("break")
            elif not self.is_compiler_generated_return(self.pc - 1):
                self._append_reconstructed("return None")
            return

        val = instr.argval
        val_str = str(val)
        self._append_reconstructed("return " + _const_repr(val))

        # ── POP_TOP ────────────────────────────────────────────────────

    def _op_pop_top(self, instr: BytecodeInstruction):
        opname = instr.opname
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

    def _op_binary(self, instr: BytecodeInstruction):
        opname = instr.opname
        if len(self.stack) >= 2:
            right = self.stack.pop()
            left = self.stack.pop()

            # Python 3.11+ BINARY_OP handles all binary ops via arg
            if opname == "BINARY_OP":
                nb_ops = [
                    "+", "&", "//", "<<", "@", "*", "%", "|", "**", ">>", "-", "/", "^",
                    "+=", "&=", "//=", "<<=", "@=", "*=", "%=", "|=", "**=", ">>=", "-=", "/=", "^=", "[]"
                ]
                op = nb_ops[instr.arg] if instr.arg is not None and instr.arg < len(nb_ops) else "?"
                if op == "[]":
                    self.stack.append(f"{left}[{right}]")
                    return
            else:
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

    def _op_binary_slice(self, instr: BytecodeInstruction):
        # Python 3.11+ BINARY_SLICE pops: stop, start, container
        if len(self.stack) >= 3:
            stop = self.stack.pop()
            start = self.stack.pop()
            container = self.stack.pop()

            if str(start) == "None":
                start = ""
            if str(stop) == "None":
                stop = ""

            self.stack.append(f"{container}[{start}:{stop}]")

        # INPLACE_* → augmented assignment

    def _op_inplace(self, instr: BytecodeInstruction):
        opname = instr.opname
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

    def _normalize_val(self, val: Any) -> str:
        """Convert decompiler-internal tuples ('func', ...) into their source string."""
        if isinstance(val, tuple) and len(val) >= 2 and val[0] in ("func", "class"):
            return str(val[1])
        return str(val)

    def _op_kw_names(self, instr: BytecodeInstruction):
        """Consume a keyword-names tuple (Python 3.11+ KW_NAMES opcode)."""
        self._kw_names = instr.argval

    def _op_push_null(self, instr: BytecodeInstruction):
        self.stack.append(StackNULL(is_method=False))

        # ── calls ──────────────────────────────────────────────────────

    def _op_call(self, instr: BytecodeInstruction):
        """
        Reconstruct a function or method call from the bytecode instruction and push the resulting expression or special placeholder onto the decompiler stack.

        Handles positional and keyword argument collection for CALL/CALL_KW/CALL_FUNCTION_* variants, reconstructs call argument lists, and consumes the callable and arguments from self.stack. Recognizes and transforms several high-level patterns instead of emitting a plain call:
        - Decorator application: when a freshly-made function object is immediately called by a CALL following MAKE_FUNCTION, it packages a decorated function tuple ("func", "<decorated def...>") and pushes it back on the stack.
        - Immediate anonymous-function invocation: converts called anonymous function tuples into an inline expression via _render_func_tuple and pushes the rendered result.
        - Class construction via __build_class__: detects the class-builder pattern and pushes a ("class", "<class ...>") tuple with reconstructed class header and body.
        - super(): when called with no arguments emits "super()".
        - Normal calls: pushes a string "callable(arg1, arg2, kw=val, ...)" when no special pattern applies.

        Side effects:
        - Pops argument and callable values from self.stack and pushes the reconstructed call or special tuple/string back onto self.stack.
        - May modify intermediate argument formatting (e.g., strip NULL sentinels).

        Parameters:
            instr (BytecodeInstruction): The CALL-family bytecode instruction being processed; its opname and arg determine the number and kind of arguments to consume.
        """
        opname = instr.opname
        num_args = int(instr.arg) if instr.arg is not None else 0
        final_args: List[str] = []
        raw_args: List[Any] = []

        # CALL_KW (3.13/3.14) or CALL_FUNCTION_KW (3.9/3.10) pops names tuple first
        if opname in ("CALL_KW", "CALL_FUNCTION_KW"):
            if self.stack:
                self._kw_names = self.stack.pop()

        # CALL_FUNCTION_EX: *args [and **kwargs]
        if opname == "CALL_FUNCTION_EX":
            flags = num_args
            kwargs_dict = None
            if flags & 1:
                item = self.stack.pop() if self.stack else "{}"
                while self._is_null_sentinel(item) and self.stack:
                    item = self.stack.pop()
                kwargs_dict = item
            
            item = self.stack.pop() if self.stack else "()"
            while self._is_null_sentinel(item) and self.stack:
                item = self.stack.pop()
            args_tuple = item
            final_args = [f"*{self._normalize_val(args_tuple)}"]
            if kwargs_dict:
                final_args.append(f"**{self._normalize_val(kwargs_dict)}")
            raw_args = [args_tuple]
        else:
            final_args = []
            final_args_raw = []
            if self.stack:
                # Handle KW_NAMES: if preceding KW_NAMES exist, CALL pops (num_args) positional
                # and then the keyword names tuple was already popped by _op_kw_names
                # and stored in self._kw_names.
                kw_names = getattr(self, "_kw_names", ())
                if isinstance(kw_names, str) and kw_names.startswith("("):
                    try:
                        import ast
                        kw_names = ast.literal_eval(kw_names)
                    except:
                        pass
                
                if kw_names and len(self.stack) >= num_args:
                    n_kw = len(kw_names)
                    n_pos = num_args - n_kw
                    # Pop keyword values first (they are on top of positional args)
                    kw_vals = []
                    collected_kw = 0
                    while collected_kw < n_kw and self.stack:
                        item = self.stack.pop()
                        if self._is_null_sentinel(item): continue
                        kw_vals.insert(0, item)
                        collected_kw += 1

                    # Pop positional args
                    pos_args = []
                    collected_pos = 0
                    while collected_pos < n_pos and self.stack:
                        item = self.stack.pop()
                        if self._is_null_sentinel(item): continue
                        pos_args.insert(0, item)
                        collected_pos += 1
                    
                    final_args = [self._normalize_val(v) for v in pos_args]
                    final_args_raw = pos_args
                    for name, val in zip(kw_names, kw_vals):
                        v_str = self._normalize_val(val)
                        final_args.append(f"{name}={v_str}")
                        final_args_raw.append(val)
                    self._kw_names = () # clear
                    # Special cases need the raw args
                    raw_args = final_args_raw
                else:
                    raw_args = []
                    collected = 0
                    while collected < num_args and self.stack:
                        item = self.stack.pop()
                        if self._is_null_sentinel(item):
                            # Skip leaked sentinels from previous loads
                            continue
                        raw_args.insert(0, item)
                        collected += 1
                    final_args = [self._normalize_val(v) for v in raw_args]
                    raw_args = raw_args

        # Standard functional name is the first pop (or Pattern 2 sentinel).
        func_val = self.stack.pop() if self.stack else "func"

        # Handle NULL sentinels (e.g. from PUSH_NULL in Python 3.11+).
        # Pattern 1 (3.11-3.12): Sentinel is BELOW functional name: [obj/NULL, func, args]
        # Pattern 2 (3.14+):      Sentinel is ABOVE functional name: [func, NULL, args]
        is_method_call = False
        def _get_sentinel(v):
            if v is None: return None
            if getattr(v, '__class__', None).__name__ == 'StackNULL': return v
            if str(v) == "__SENTINEL_NULL__": return StackNULL(is_method=False)
            return None

        sentinel = _get_sentinel(func_val)
        if sentinel:
            # Pattern 2: Popped sentinel as callable. Real callable is on stack.
            func_val = self.stack.pop() if self.stack else "func"
            if sentinel.is_method and self.stack:
                obj = self.stack.pop()
                func_val = f"{self._wrap_negative_literal(obj)}.{func_val}"
                is_method_call = True
        elif self.stack:
            # Pattern 1: Popped name as callable. Sentinel might be on stack.
            sentinel = _get_sentinel(self.stack[-1])
            if sentinel:
                self.stack.pop() # pop sentinel
                if sentinel.is_method and self.stack:
                    obj = self.stack.pop()
                    func_val = f"{self._wrap_negative_literal(obj)}.{func_val}"
                    is_method_call = True
            elif opname in ("CALL", "CALL_KW"):
                # Missing NULL sentinel in bound calls (genexprs/lambdas).
                if isinstance(func_val, (str, tuple)) and not self._is_null_sentinel(func_val):
                    first_arg = func_val
                    func_val = self.stack.pop()
                    raw_args.insert(0, first_arg)
                    final_args.insert(0, self._normalize_val(first_arg))

        orig_func_val = func_val
        func = self._normalize_val(func_val)

        # ── Decorator pattern detection ───────────────────────────────
        #   func_val = decorator, raw_args = [('func', body)]

        # Pattern A (3.11+ / Generic fallback)
        if isinstance(orig_func_val, tuple) and orig_func_val[0] in ("func", "class"):
            body_text = str(orig_func_val[1])
            first_line = body_text.strip().split("\n")[0] if body_text.strip() else ""
            if orig_func_val[0] == "class" or not _is_anonymous_func_body(first_line):
                if self.stack:
                    decorator_expr = self._normalize_val(self.stack.pop())
                    deco_line = f"@{decorator_expr}"
                    self.stack.append((orig_func_val[0], f"{deco_line}\n{body_text}"))
                    return
            if orig_func_val[0] == "func":
                rendered = _render_func_tuple(body_text, final_args)
                self.stack.append(rendered)
                return

        # Pattern B (3.9 specialisation incorporated here)
        if (len(raw_args) == 1
                and isinstance(raw_args[0], tuple)
                and raw_args[0][0] in ("func", "class")):
            body_val = raw_args[0]
            body_text = str(body_val[1])
            first_line = body_text.strip().split("\n")[0] if body_text.strip() else ""
            if body_val[0] == "class" or not _is_anonymous_func_body(first_line):
                # Detected as decorator application: @decorator def name... or @decorator class Name...
                deco_line = f"@{func}"
                self.stack.append((body_val[0], f"{deco_line}\n{body_text}"))
                return

        # ── Class builder detection ──────────────────────────────────
        # Pattern A (3.11+): [__build_class__, ('func', body), 'Name', bases...]
        # Pattern B (3.9):   [__build_class__, ('func', body), 'Name', bases...]
        # (Both look similar once positional args are popped)
        if func == "__build_class__" and len(raw_args) >= 2:
            body_val = raw_args[0]
            if isinstance(body_val, tuple) and body_val[0] == "func":
                body_text = str(body_val[1])
            else:
                body_text = str(body_val)

            cls_name = str(self._normalize_val(raw_args[1])).strip("'\"")
            bases = [self._normalize_val(b) for b in raw_args[2:]]
            bases_str = f"({', '.join(bases)})" if bases else ""
            lines = body_text.split("\n")
            real_body = "\n".join(lines[1:])
            if real_body.strip():
                self.stack.append(("class", f"class {cls_name}{bases_str}:\n{real_body}"))
            else:
                # Only the preamble (__module__, __qualname__, ...): an empty body (#167).
                self.stack.append(("class", f"class {cls_name}{bases_str}:\n    pass"))
            return

        if func == "super" and not final_args:
            self.stack.append("super()")
        else:
            self.stack.append(f"{func}({', '.join(final_args)})")

    def _op_load_build_class(self, instr: BytecodeInstruction):
        opname = instr.opname
        self.stack.append("__build_class__")

        # ── comparisons ────────────────────────────────────────────────

    def _op_compare(self, instr: BytecodeInstruction):
        opname = instr.opname
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

    def _op_contains(self, instr: BytecodeInstruction):
        opname = instr.opname
        if len(self.stack) >= 2:
            container = self.stack.pop()
            item = self.stack.pop()
            op = "not in" if bool(instr.arg) else "in"
            self.stack.append(f"{item} {op} {container}")

    def _op_is(self, instr: BytecodeInstruction):
        opname = instr.opname
        if len(self.stack) >= 2:
            right = self.stack.pop()
            left = self.stack.pop()
            op = "is not" if bool(instr.arg) else "is"
            self.stack.append(f"{left} {op} {right}")

    def _loop_return_tail(self) -> Optional[int]:
        """
        Index of the RETURN_VALUE when the code ahead is the tail of `return x` from loops.

        Returning a value from inside `for` loops swaps it above each live iterator and
        drops the iterator: ``SWAP 2; POP_TOP`` (``ROT_TWO; POP_TOP`` before 3.11), once per
        enclosing loop, then ``RETURN_VALUE``. The caller has just handled the first swap.
        """
        instrs = self.instructions
        k = self.pc
        while True:
            while k < len(instrs) and instrs[k].opname in _GENERIC_SKIP_OPS:
                k += 1
            if k >= len(instrs) or instrs[k].opname != "POP_TOP":
                return None
            k += 1
            while k < len(instrs) and instrs[k].opname in _GENERIC_SKIP_OPS:
                k += 1
            if k < len(instrs) and instrs[k].opname == "RETURN_VALUE":
                return k
            is_swap = k < len(instrs) and (
                instrs[k].opname == "ROT_TWO" or (instrs[k].opname == "SWAP" and instrs[k].arg == 2))
            if not is_swap:
                return None
            k += 1

    def _op_stack_manip(self, instr: BytecodeInstruction):
        opname = instr.opname
        nxt = next((x for x in self.instructions[self.pc:] if x.opname not in ("NOP", "CACHE", "POP_BLOCK")), None)
        if nxt is not None and nxt.opname == "POP_EXCEPT" and (
                opname == "ROT_FOUR" or (opname == "SWAP" and instr.arg == 2)):
            # `return value` inside an except clause moves the value below the handled
            # exception, which is not on the modelled stack: the value stays on top.
            return
        is_swap_two = opname == "ROT_TWO" or (opname == "SWAP" and instr.arg == 2)
        if is_swap_two:
            return_at = self._loop_return_tail()
            if return_at is not None:
                # The iterators are not tracked on the stack; leave the value for RETURN_VALUE.
                self.pc = return_at
                return
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
        elif opname == "SWAP":
            n = int(instr.arg) if instr.arg is not None else 0
            if n > 1 and len(self.stack) >= n:
                # SWAP n: swap TOS with the n-th element (TOS is 1st)
                # CPython 3.11 implementation: swap stack[-(1)] with stack[-(n)]
                self.stack[-1], self.stack[-n] = self.stack[-n], self.stack[-1]
        elif opname == "COPY":
            n = int(instr.arg) if instr.arg is not None else 0
            if n >= 1 and len(self.stack) >= n:
                # COPY n: push a copy of the n-th element to TOS
                self.stack.append(self.stack[-n])

    def _op_dup_top(self, instr: BytecodeInstruction):
        opname = instr.opname
        if opname == "DUP_TOP" and self.stack:
            self.stack.append(self.stack[-1])
        elif opname == "DUP_TOP_TWO" and len(self.stack) >= 2:
            self.stack.extend([self.stack[-2], self.stack[-1]])

        # ── f-strings ──────────────────────────────────────────────────

    def _op_fstring(self, instr: BytecodeInstruction):
        # arg bitmask: 0x01=str(), 0x02=repr(), 0x03=ascii(), 0x04=format_spec
        arg = int(instr.arg) if instr.arg is not None else 0
        conversion = ""
        if (arg & 0x03) == 1: conversion = "!s"
        elif (arg & 0x03) == 2: conversion = "!r"
        elif (arg & 0x03) == 3: conversion = "!a"
        
        format_spec = ""
        if (arg & 0x04):
            # The format spec follows the value on the stack
            spec = self.stack.pop() if self.stack else ""
            spec_str = str(spec).strip("'\"")
            format_spec = f":{spec_str}"
            
        val = self.stack.pop() if self.stack else "{expr}"
        
        self.stack.append(f"{{{val}{conversion}{format_spec}}}")

    def _op_build_string(self, instr: BytecodeInstruction):
        count = int(instr.arg) if instr.arg is not None else 0
        parts = []
        for _ in range(count):
            if self.stack:
                p_val = self.stack.pop()
                # Skip sentinels that might have leaked to the stack
                if (p_val is None or str(p_val) == "__SENTINEL_NULL__" or 
                    getattr(p_val, '__class__', None).__name__ == 'StackNULL'):
                    continue
                p = self._format_fstring_part(str(p_val))
                parts.insert(0, p)
        res = "".join(parts)
        self.stack.append(f"f\"{res}\"")
        # ── jumps / control flow ───────────────────────────────────────

    def _op_jump(self, instr: BytecodeInstruction):
        """
        Handle jump opcodes and reconstruct high-level control flow constructs (break, continue, else, while),
        updating the decompiler's block stack, indentation, and reconstructed output.
        
        Forward jumps:
        - If the jump lands at or beyond the end of an enclosing `while`/`for` block, emit `break`.
        - If the forward jump is not an exception-handler jump and matches the end of a previously opened `if`
          header, close any inner `if`/`else` branches, emit a `pass` when required for empty headers,
          decrease indentation for the matched `if`, and emit an `else:` block with adjusted indentation.
          Does nothing for exception-handler jump offsets.
        
        Backward jumps (loop back-edges):
        - If the jump is an exception-handler back-jump, do not treat it as a loop back-edge (may emit
          `continue` when it represents an explicit continue inside a handler).
        - If a prescan already recorded a `while` header for this loop, leave it unchanged.
        - Otherwise, suppress duplicated condition bytecode produced by certain CPython layouts,
          drain any extra expressions pushed by the duplicated condition from the expression stack,
          and retroactively rewrite the most recently emitted `if` header into a `while` header.
        
        Parameters:
            instr (BytecodeInstruction): The disassembled jump instruction to handle.
        
        """
        opname = instr.opname
        jump_target = self._get_jump_target(instr)

        # Detect `break`: forward jump that lands at the exact end of a while/for loop.
        if instr.opname in ("JUMP_FORWARD", "JUMP_ABSOLUTE") and isinstance(jump_target, int) and jump_target > instr.offset:
            matched_while = False
            matched_b_type = None
            for b_off, b_type in reversed(self.blocks):
                if b_type in ("while", "for") and jump_target >= b_off:
                    self._append_reconstructed("break")
                    matched_while = True
                    matched_b_type = b_type
                    break
                if b_type in ("while", "for"):
                    break  # only check innermost loop

            if matched_while:
                # Python 3.9: a break from a for-loop emits:
                #   POP_TOP (pops the iterator) + JUMP_ABSOLUTE(past_end) + JUMP_ABSOLUTE(for_head)
                # The last JUMP_ABSOLUTE is dead/unreachable but will be processed next.
                # Suppress it so the backward-jump handler doesn't retroactively rewrite
                # the preceding `if` block header to `while`.
                if matched_b_type == "for" and self.pc < len(self.instructions):
                    nxt = self.instructions[self.pc]
                    if (not nxt.is_jump_target
                            and "JUMP" in nxt.opname
                            and isinstance(self._get_jump_target(nxt), int)
                            and self._get_jump_target(nxt) <= nxt.offset):
                        _fb = getattr(self, "_finally_body_suppress", set())
                        _fb.add(nxt.offset)
                        self._finally_body_suppress = _fb
                return

            # Non-break forward jump -> reconstruct as else-branch only when a
            # prior if-block whose target matches jump_target is actually open.
            # (Exception-handler jumps are always excluded.)
            if instr.offset not in getattr(self, "_exc_handler_jump_offsets", ()):
                next_offset = -1
                if self.pc < len(self.instructions):
                    next_offset = self.instructions[self.pc].offset

                matched_bi = None
                for bi in range(len(self.blocks)-1, -1, -1):
                    if self.blocks[bi][1] == "if" and self.blocks[bi][0] == next_offset:
                        matched_bi = bi
                        break

                if matched_bi is not None:
                    # Force close any blocks nested INSIDE (above) the matched if block.
                    # Only pop interior 'if' blocks — stop at any outer structural block
                    # (else, while, for, try_body, etc.) which we must NOT collapse.
                    while len(self.blocks) - 1 > matched_bi:
                        _, cb_type = self.blocks[-1]
                        # Stop if we hit a block that is not an inner if/else branch
                        if cb_type not in ("if", "else"):
                            break
                        self.blocks.pop()
                        if cb_type not in _NO_PASS_TYPES:
                            last_idx = len(self.reconstructed) - 1
                            while last_idx >= 0 and not self.reconstructed[last_idx].strip():
                                last_idx -= 1
                            if last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":"):
                                self._append_reconstructed("pass")
                        if cb_type not in _NO_INDENT_TYPES:
                            self.indent_level -= 1

                    if matched_bi != len(self.blocks) - 1:
                        return

                    last_idx = len(self.reconstructed) - 1
                    while last_idx >= 0 and not self.reconstructed[last_idx].strip():
                        last_idx -= 1
                    if last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":"):
                        self._append_reconstructed("pass")

                    self.indent_level -= 1
                    self._append_reconstructed("else:")
                    self.indent_level += 1
                    self.blocks.pop(matched_bi)  # pop the matched 'if' block
                    self.blocks.append((jump_target, "else"))
            return
        # detect while loop.
        # 3.11+ CPython compiles `while cond: body` as:
        #   A:  <condition>; POP_JUMP_IF_FALSE(end)  ← condition check #1
        #   B:  <body>
        #   C:  <condition>; POP_JUMP_IF_FALSE(end-2) ← condition check #2 (dup)
        #   D:  JUMP_BACKWARD(B)
        #   end-2: RETURN_CONST None  (loop-exhausted path — suppress)
        #   end:   RETURN_CONST None  (skipped path — suppress)
        # When we see JUMP_BACKWARD(B) we:
        #   1. Find the start of the duplicated condition block (first
        #      instruction at or after B whose offset is ≥ the last body
        #      instruction + 2). Anything from there through JUMP_BACKWARD
        #      is duplicate — register those offsets for suppression.
        #   2. Retroactively rewrite the 'if' header → 'while'.
        #   3. Drain spurious stack items pushed by the dup condition.
        if self._is_backward_instruction(instr):
            body_start = jump_target

            # JUMP_BACKWARD instructions that exit except/finally
            # handlers (they jump to a finally-merge label) must NOT be
            # treated as loop back-edges.  Skip them silently.
            # Exception: if they are a 3.9-style explicit 'continue' inside
            # a handler body, emit 'continue' before returning.
            if instr.offset in getattr(self, "_exc_handler_jump_offsets", ()):
                if instr.offset in getattr(self, "_exc_handler_continue_offsets", ()):
                    self._append_reconstructed("continue")
                return

            # A `continue`, or the end of an if/else branch threaded to the loop head.
            if instr.offset in self._inner_back_jumps:
                self._emit_inner_back_jump(instr, self._inner_back_jumps[instr.offset])
                return

            # If the prescan successfully identified the loop guard
            # (guard_offset in _while_header_targets), it already arranged for
            # the POP_JUMP_IF_FALSE handler to open a "while" block instead of
            # an "if" block.  Nothing more to do — just return.
            if body_start in self._while_header_targets:
                return  # prescan handled it

            # Fallback for Python 3.14+ where the prescan could not identify
            # the guard (e.g. guard offset >= body_start in a do-while layout,
            # or argval unresolved in a way the broadened search still misses).
            # Retroactively rewrite the most recent "if" header → "while".
            # Also suppress dup-condition instructions that already executed:
            # find where the dup region starts (first instruction after the
            # last STORE in the body) and drain extra stack items.
            dup_start = instr.offset
            for ins in self.instructions:
                if body_start <= ins.offset < instr.offset:
                    if ins.opname in (
                        "STORE_NAME", "STORE_FAST", "STORE_GLOBAL",
                        "STORE_ATTR", "STORE_SUBSCR",
                    ):
                        dup_start = ins.offset + 2

            dup_depth = 0
            for ins in self.instructions:
                if dup_start <= ins.offset < instr.offset:
                    if ins.opname in (
                        "LOAD_CONST", "LOAD_NAME", "LOAD_FAST",
                        "LOAD_GLOBAL", "LOAD_DEREF", "LOAD_SMALL_INT",
                    ):
                        dup_depth += 1
                    elif ins.opname in ("COMPARE_OP", "BINARY_OP"):
                        dup_depth -= 1
            for _ in range(max(0, dup_depth)):
                if self.stack:
                    self.stack.pop()

            # Find the most recently emitted "if" header and rewrite it.
            head_instr = next((i for i in self.instructions if i.offset == body_start), None)
            for bi in range(len(self.blocks) - 1, -1, -1):
                boff, btype = self.blocks[bi]
                if btype == "if" and boff > instr.offset and head_instr is not None and head_instr.opname == "FOR_ITER":
                    # An `if` that is still open at a jump back to a `for` head is a body
                    # statement ending in `continue` (`if c: return x` as the last
                    # statement compiles to `if not c: continue`), not a `while` guard.
                    self._append_reconstructed("continue")
                    return
                if btype == "if" and boff >= instr.offset:
                    for idx in range(len(self.reconstructed) - 1, -1, -1):
                        if self.reconstructed[idx].lstrip().startswith("if "):
                            self.reconstructed[idx] = self.reconstructed[idx].replace(
                                "if ", "while ", 1
                            )
                            self.blocks[bi] = boff, "while"
                            self._while_true_ends.discard(boff)
                            break
                    break

            return  # never treat JUMP_BACKWARD as else-opener

        # ── no-ops ─────────────────────────────────────────────────────

    def _op_no_op(self, instr: BytecodeInstruction):
        pass

    def _op_nop(self, instr: BytecodeInstruction):
        opname = instr.opname
        # NOP in CPython 3.11+ serves several roles:
        #   1. try-block entry marker — any NOP whose offset is in
        #      _try_nop_offsets (which includes offset 2 for the outermost
        #      try, plus any inner NOPs detected by _prescan_try_structure).
        #   2. while-True loop header — when the NOP is the body_start of a
        #      while-True loop identified by _prescan_while_loops (sentinel -1).
        # Regular while-loops (while cond: body) do NOT trigger here; they
        # are detected retroactively by the JUMP_BACKWARD handler.
        is_try_nop = (
            instr.offset == 2 or
            instr.offset in getattr(self, "_try_nop_offsets", ())
        )
        if is_try_nop:
            if self._has_exception_handler():
                # Find the PUSH_EXC_INFO that handles THIS try body, using
                # the exception-table-derived map from prescan.  Fall back to
                # scanning for the nearest PUSH_EXC_INFO if the map is empty.
                nop_map = getattr(self, "_nop_to_push_exc", {})
                next_pei = nop_map.get(instr.offset, -1)
                # try/finally with no except: the handler is only a re-raise wrapper,
                # so the prescan recorded where the finally body starts instead.
                finally_only = getattr(self, "_nop_to_finally_merge", {}).get(instr.offset)
                if next_pei < 0 and finally_only is None:
                    for ins in self.instructions:
                        if ins.opname == "PUSH_EXC_INFO" and ins.offset > instr.offset:
                            if ins.offset not in getattr(self, "_suppress_push_exc_offsets", ()):
                                next_pei = ins.offset
                                break
                # Only emit try: if not already inside a try block
                already_in_try = any(b[1] in ("try_body",) for b in self.blocks)
                if not already_in_try or instr.is_jump_target or instr.offset != 2:
                    self._append_reconstructed("try:")
                    if next_pei > 0:
                        self._pei_indents[next_pei] = self.indent_level
                    self.indent_level += 1
                    if next_pei > 0:
                        # Use the finally-merge label as block end when present
                        pef_map = getattr(self, "_push_exc_to_finally_merge", {})
                        merge = None
                        for m_off, targets in pef_map.items():
                            if next_pei in targets:
                                merge = m_off
                                break
                        if merge is not None and merge > instr.offset:
                            block_end = merge
                        else:
                            block_end = next_pei
                            for j_ins in self.instructions:
                                if j_ins.offset >= instr.offset:
                                    break
                                # Only FOR_ITER is a definite loop boundary;
                                # conditional jumps inside except handlers are not.
                                if j_ins.opname not in ("FOR_ITER",):
                                    continue
                                t = self._get_jump_target(j_ins)
                                if isinstance(t, int) and t > instr.offset and t < block_end:
                                    block_end = t
                        self.blocks.append((block_end, "try_body"))
                    elif finally_only is not None:
                        self.blocks.append((finally_only, "try_body"))
                return

        # while True: — NOP is the body_start of a while-True loop.
        # This covers:
        #   (a) any offset where _prescan_while_loops set the sentinel guard -1,
        #       e.g. an inner "while True:" loop at a NOP inside a function body.
        #   (b) Python 3.12: the NOP at body_start-2 (is_jt=False) precedes the
        #       actual back-edge target (body_start, is_jt=True).  The prescan
        #       stores body_start (the JUMP_BACKWARD target), so we also match when
        #       instr.offset + 2 is in _while_true_body_starts.
        #   (c) offset == 2: the entire function body is an unconditional loop
        #       (old fallback path, kept for older Python version compatibility).
        _while_header_targets = getattr(self, "_while_header_targets", {})
        _while_true_body_starts = {
            bs for bs, go in _while_header_targets.items() if go == -1
        }
        # Determine if this NOP is the while-True header.
        # Case A: this NOP's offset IS the prescan body_start (common for older/current patterns).
        # Case B: this NOP's offset+2 IS the prescan body_start (Python 3.12 where the NOP
        #         precedes the actual JUMP_BACKWARD target by 2 bytes, but has is_jt=False).
        nop_is_while_true = instr.offset in _while_true_body_starts
        nop_body_start = instr.offset  # actual back-edge target for loop_end search
        if not nop_is_while_true and (instr.offset + 2) in _while_true_body_starts:
            nop_is_while_true = True
            nop_body_start = instr.offset + 2

        if nop_is_while_true:
            self._append_reconstructed("while True:")
            self.indent_level += 1
            # Find the loop end: offset just after the last backward jump that
            # targets nop_body_start (the actual back-edge target).
            loop_end = nop_body_start + 2
            for ins in self.instructions:
                if (self._is_backward_instruction(ins)
                        and self._get_jump_target(ins) == nop_body_start):
                    loop_end = max(loop_end, ins.offset + 2)
            self.blocks.append((loop_end, "while"))
            _wte = getattr(self, "_while_true_ends", set())
            _wte.add(loop_end)
            self._while_true_ends = _wte
            return

        # Old offset-2 fallback: function body IS a while-True loop but was not
        # caught by _prescan_while_loops (e.g., no is_jump_target on the NOP).
        if instr.offset == 2:
            if self._has_jump_backward() and not self._loop_cond_before_body(instr.offset + 2):
                self._append_reconstructed("while True:")
                self.indent_level += 1
                end_offset = self._find_jump_backward_end()
                if end_offset > 0:
                    self.blocks.append((end_offset, "while"))
                    self._while_true_ends.add(end_offset)

    def _emit_inner_back_jump(self, instr: BytecodeInstruction, loop_end: int) -> None:
        """
        Emit the source for a backward jump that does not close its loop.

        * It ends an `if` body that already has statements: the code after it up to
          the loop's back-edge is the `else` branch, so open `else:`.
        * It is the whole body of an `if`: that is a `continue`.
        * It sits anywhere else with loop-body code after it: `continue`.
        * Nothing but the back-edge follows: the jump is redundant, emit nothing.

        Parameters:
            instr: the inner backward jump.
            loop_end: offset of the back-edge that closes the loop.
        """
        nxt_off = self.instructions[self.pc].offset if self.pc < len(self.instructions) else -1
        k = self.pc
        while k < len(self.instructions) and self.instructions[k].opname in _WHILE_TRIVIAL_OPS:
            k += 1
        next_real = self.instructions[k].offset if k < len(self.instructions) else -1
        has_more = 0 <= next_real < loop_end

        last_idx = len(self.reconstructed) - 1
        while last_idx >= 0 and not self.reconstructed[last_idx].strip():
            last_idx -= 1
        body_empty = last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":")

        ends_if = bool(self.blocks) and self.blocks[-1][1] == "if" and self.blocks[-1][0] == nxt_off
        if ends_if and body_empty:
            self._append_reconstructed("continue")
        elif ends_if and has_more:
            self.blocks.pop()
            self.indent_level -= 1
            self._append_reconstructed("else:")
            self.indent_level += 1
            self.blocks.append((loop_end, "else"))
        elif not ends_if and has_more:
            self._append_reconstructed("continue")

    def _lowered_tail_if(self, instr: BytecodeInstruction, jump_target: int):
        """
        Recognize a tail `if` that 3.11+ lowered to `POP_JUMP_IF_TRUE body; JUMP_BACKWARD head; body`.

        A conditional jump cannot go backward there, so `if cond: body` at the end
        of a loop body is compiled as "jump over the back-jump when cond is true".

        Returns:
            (index of the back-jump, offset of the loop's closing back-edge), or None.
        """
        if instr.opname not in ("POP_JUMP_IF_TRUE", "POP_JUMP_IF_NOT_NONE"):
            return None
        k = self.pc
        while k < len(self.instructions) and self.instructions[k].opname in _WHILE_TRIVIAL_OPS:
            k += 1
        if k >= len(self.instructions):
            return None
        back = self.instructions[k]
        loop_end = self._inner_back_jumps.get(back.offset)
        if loop_end is None or jump_target != back.offset + 2 or back.offset + 2 >= loop_end:
            return None
        return k, loop_end

    def _closing_back_edge_for(self, instr: BytecodeInstruction, target: int):
        """
        For a conditional jump that goes backward to a loop head (3.9/3.10 thread a
        tail `if` straight to the head), return the offset of the unconditional
        back-edge that closes that loop later in the code, or None.
        """
        best = None
        for i, ins in enumerate(self.instructions):
            if ins.offset <= instr.offset or not self._is_backward_instruction(ins):
                continue
            if ins.opname.startswith("POP_JUMP") or self._get_jump_target(ins) != target:
                continue
            if self._is_handler_exit_back_jump(i):
                continue
            if best is None or ins.offset < best:
                best = ins.offset
        return best

    @staticmethod
    def _negate_condition(cond: str) -> str:
        """
        Return source text for the logical negation of a condition expression.

        Only a leading `not` is stripped; comparisons are never rewritten to their
        "opposite" operator, because that is not semantics-preserving in general.
        """
        try:
            node = ast.parse(cond.strip(), mode="eval").body
        except SyntaxError:
            return f"not ({cond})"
        if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
            return cond.strip()[len("not"):].strip()
        if isinstance(node, ast.Compare) and len(node.ops) == 1:
            swapped = {ast.Is: ast.IsNot, ast.IsNot: ast.Is, ast.In: ast.NotIn, ast.NotIn: ast.In}.get(type(node.ops[0]))
            if swapped is not None:  # exact complements by language definition
                node.ops[0] = swapped()
                return ast.unparse(node)
        if isinstance(node, (ast.Name, ast.Attribute, ast.Call, ast.Subscript, ast.Constant)):
            return f"not {cond.strip()}"
        return f"not ({cond.strip()})"

    def _loop_exit_break_index(self, instr: BytecodeInstruction, jump_target: int):
        """
        Recognize a conditional jump that leaves the innermost `while` loop, i.e.
        a conditional `break` (3.14 compiles `if cond: break` and
        `if cond: ... else: break` at the end of a loop body this way).

        The jump qualifies when its target is at or beyond the end of the
        innermost `while` loop and that loop ends in a backward jump.

        Returns:
            None if this jump is not a loop exit. Otherwise the index of the
            loop's back-edge instruction when the jump is immediately followed by
            it (so the caller can consume it), or -1 when more code follows.
        """
        loop_end = None
        for end, btype in reversed(self.blocks):
            if btype in ("while", "for"):
                if btype == "while":
                    loop_end = end
                break
        if loop_end is None or not isinstance(jump_target, int) or jump_target < loop_end:
            return None
        last = next((i for i in self.instructions if i.offset == loop_end - 2), None)
        if last is None or not self._is_backward_instruction(last):
            return None
        idx = self.pc
        while idx < len(self.instructions) and self.instructions[idx].opname in _WHILE_TRIVIAL_OPS:
            idx += 1
        if idx < len(self.instructions) and self.instructions[idx] is last:
            return idx
        return -1

    def _op_conditional_jump(self, instr: BytecodeInstruction):
        """
        Handle a conditional jump instruction, emitting reconstructed `if`, `while`, or ternary code and updating decompiler state.

        This method detects compiler patterns and mutates the decompiler's stack, reconstructed output, indentation, block stack, and program counter as appropriate:
        - If the jump was pre-identified as a ternary, evaluates both branches and either emits an augmented-assignment form or pushes a ternary expression onto the stack; advances the PC past the else-branch instructions that were speculatively evaluated.
        - Suppresses duplicate condition values produced for `while` guards and exception-match checks.
        - Rewrites `POP_JUMP_IF_NONE` / `POP_JUMP_IF_NOT_NONE` style tests into `is None` / `is not None` predicates when necessary.
        - Emits combined `and`/`or` compound conditions when a compound condition was precomputed by prescan.
        - Emits `while` headers for backward-jump guards detected by prescan.
        - Emits `if` headers for forward conditional jumps, adjusts indentation, and records block end offsets for later closing; recognizes certain immediate jump targets (returns, breaks, raises) to influence block handling.

        Parameters:
            instr (BytecodeInstruction): The conditional jump instruction to handle; its `opname`, `offset`, and argument fields are used to determine emitted source and control-flow updates.
        """
        opname = instr.opname
        # ── Ternary expression detection ──────────────────────────────
        # If _prescan_ternaries identified this jump as a ternary, evaluate
        # both branches speculatively and push the ternary expression onto
        # the stack instead of opening a control-flow block.

        if instr.offset in getattr(self, "_ternary_jumps", {}):
            store_name, then_instrs, else_instrs, is_true, aug_op = \
                self._ternary_jumps[instr.offset]
            cond_expr = str(self.stack.pop()) if self.stack else "?"
            # For POP_JUMP_IF_NONE / POP_JUMP_IF_NOT_NONE the raw condition
            # on the stack is the *value* being tested (not a bool expression).
            # Rewrite it to the appropriate 'is None' / 'is not None' form
            # so the emitted ternary reads correctly.
            if "IF_NOT_NONE" in opname and "is " not in cond_expr:
                cond_expr = f"{cond_expr} is None"
            elif "IF_NONE" in opname and "NOT" not in opname and "is " not in cond_expr:
                cond_expr = f"{cond_expr} is not None"
            then_expr = self._eval_ternary_branch(then_instrs)
            else_expr = self._eval_ternary_branch(else_instrs)

            ternary = f"{then_expr} if {'not ' if is_true else ''}{cond_expr} else {else_expr}"

            if aug_op:
                # Augmented ternary: the target variable was loaded onto the stack
                # before the jump. Pop it now so we can emit the assignment statement.
                target_var = self.stack.pop() if self.stack else store_name
                self._append_reconstructed(f"{target_var} {aug_op} {ternary}")
            else:
                # Regular ternary expr: push to stack for a following STORE to pick up.
                self.stack.append(ternary)
            # Skip past the else-branch instructions up to (but not including)
            # the else-STORE: they have already been evaluated speculatively.
            # The else-STORE fires normally and emits the assignment.
            jump_target = self._get_jump_target(instr)
            while self.pc < len(self.instructions):
                if self.instructions[self.pc].offset >= jump_target:
                    break
                self.pc += 1
            return

        # Suppress dup-condition instructions pre-identified by pre-scan.
        if instr.offset in self._while_body_offsets:
            if self.stack:
                self.stack.pop()
            return
        if self.stack:
            cond = self.stack.pop()
            # Suppress the POP_JUMP after CHECK_EXC_MATCH.
            if str(cond) in ("_exc_match", "_exc_info"):
                return
            compound_cond_map = getattr(self, "_compound_cond_map", {})
            compound_cond = compound_cond_map.get(instr.offset)
            compound_precomputed = False
            if compound_cond is not None:
                cond = compound_cond
                compound_precomputed = True
            else:
                if "IF_NONE" in opname and "NOT" not in opname:
                    # Fires on None; body runs on NOT None
                    cond = f"{cond} is not None"
                elif "IF_NOT_NONE" in opname:
                    # Fires on NOT None; body runs on None
                    cond = f"{cond} is None"

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
                    if self._falls_to_outer_back_jump(instr, bs) and jump_target > instr.offset:
                        # Inverted guard: the jump enters the body and the
                        # fall-through leaves the loop via a back-jump to an
                        # enclosing loop's head.
                        loop_end = self._loop_end_after(bs)
                        text = cond if (is_true or compound_precomputed) else f"not {cond}"
                        self._append_reconstructed(f"while {text}:")
                        self.indent_level += 1
                        self.blocks.append((loop_end, "while"))
                        return
                    if jump_target < bs and jump_target < instr.offset:
                        # Exit is a conditional jump back to an enclosing loop head.
                        text = f"not {cond}" if is_true and not compound_precomputed else cond
                        self._append_reconstructed(f"while {text}:")
                        self.indent_level += 1
                        self.blocks.append((self._loop_end_after(bs), "while"))
                        return
                    if compound_precomputed:
                        self._append_reconstructed(f"while {cond}:")
                    elif is_true:
                        self._append_reconstructed(f"while not {cond}:")
                    else:
                        self._append_reconstructed(f"while {cond}:")
                    self.indent_level += 1
                    self.blocks.append((jump_target, "while"))
                    return

            # and/or chain: same target as current if-block
            if self.blocks and self.blocks[-1][1] == "if" and self.blocks[-1][0] == jump_target:
                p_line = self.reconstructed[-1].strip() if self.reconstructed else ""
                if p_line.startswith("if "):
                    prev_line = self.reconstructed.pop()
                    prev_cond = p_line[3:].rstrip(":")
                    self.indent_level -= 1
                    if compound_precomputed:
                        self._append_reconstructed(f"if {prev_cond} and {cond}:")
                    elif is_true:
                        self._append_reconstructed(f"if {prev_cond} or not {cond}:")
                    else:
                        self._append_reconstructed(f"if {prev_cond} and {cond}:")
                    self.indent_level += 1
                    return

            # Conditional `break`: this jump leaves the enclosing `while` loop.
            if not compound_precomputed:
                back_edge_idx = self._loop_exit_break_index(instr, jump_target)
                if back_edge_idx is not None:
                    body_cond = f"not {cond}" if is_true else cond
                    self._append_reconstructed(f"if {self._negate_condition(body_cond)}:")
                    self.indent_level += 1
                    self._append_reconstructed("break")
                    self.indent_level -= 1
                    if back_edge_idx >= 0:
                        self.pc = back_edge_idx + 1
                    return

            # Tail `if` lowered to "jump over a back-jump": `if cond: <rest of body>`.
            if not compound_precomputed:
                lowered = self._lowered_tail_if(instr, jump_target)
                if lowered is not None:
                    back_idx, loop_end = lowered
                    body_cond = f"not {cond}" if is_true else cond
                    self._append_reconstructed(f"if {self._negate_condition(body_cond)}:")
                    self.indent_level += 1
                    self.blocks.append((loop_end, "if"))
                    self.pc = back_idx + 1
                    return

            # A conditional jump back to the loop head (3.9/3.10): the `if` body is
            # the rest of the loop body, up to the loop's closing back-edge.
            if_block_end = jump_target
            if isinstance(jump_target, int) and jump_target < instr.offset:
                closing = self._closing_back_edge_for(instr, jump_target)
                if closing is not None:
                    if_block_end = closing

            if compound_precomputed:
                self._append_reconstructed(f"if {cond}:")
            elif is_true:
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
                if prev.opname in ("RETURN_VALUE", "RETURN_CONST", "RAISE_VARARGS") and self.stack:
                    # The compiler duplicated the rest of the statement into both arms
                    # (`print(a if f else b)` at the end of a function on 3.12+). Values
                    # pushed before the test (the callee, earlier arguments) are used up
                    # by the first arm, and the second arm needs them again.
                    self._else_stack_snapshots[jump_target] = list(self.stack)
                if prev.opname in (
                    "RETURN_VALUE", "RETURN_CONST", "RAISE_VARARGS",
                    "BREAK_LOOP", "JUMP_FORWARD",
                ):
                    self.blocks.append((if_block_end, "if"))
                    return

            self.blocks.append((if_block_end, "if"))

    def _prescan_value_boolops(self) -> None:
        """
        Find `and`/`or` used as a value (`x = a or b`, `return p and q`, call arguments).

        Their jumps keep the tested operand as the result instead of branching, so
        they look different from an `if` condition:
          * 3.9/3.10: `JUMP_IF_FALSE_OR_POP` / `JUMP_IF_TRUE_OR_POP` to the join.
          * 3.12+: `COPY 1; [TO_BOOL]; POP_JUMP_IF_FALSE/TRUE join; [NOT_TAKEN]; POP_TOP`.
        Mixed chains (`a and b or c`) on 3.9/3.10 also use plain `POP_JUMP_IF_*` to the
        start of the next alternative; those are recorded as groups that the next
        `*_OR_POP` closes. A chained comparison (`a < b < c`) uses the same opcodes but
        jumps to a `SWAP/ROT_TWO; POP_TOP` cleanup and is left alone.
        """
        self._boolop_or_pop = {}
        self._boolop_copy = {}
        self._boolop_groups = {}
        self._boolop_pending = []
        instrs = self.instructions
        by_offset = {ins.offset: k for k, ins in enumerate(instrs)}
        skip = ("TO_BOOL", "NOT_TAKEN", "CACHE")

        def is_chain_cleanup(target):
            k = by_offset.get(target)
            if k is None:
                return False
            if instrs[k].opname == "ROT_TWO":
                return True
            return instrs[k].opname == "SWAP" and k + 1 < len(instrs) and instrs[k + 1].opname == "POP_TOP"

        def unthread_return(after, target):
            # CPython 3.9 threads a jump to `RETURN_VALUE` onto a later identical return,
            # skipping the right operand's own return: the operator really joins at the
            # first return its right operand falls into.
            k = by_offset.get(target)
            if k is None or instrs[k].opname != "RETURN_VALUE":
                return target
            first = next((x.offset for x in instrs if after < x.offset < target and x.opname == "RETURN_VALUE"), None)
            return target if first is None else first

        for k, ins in enumerate(instrs):
            if ins.opname in ("JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP"):
                target = self._get_jump_target(ins)
                if isinstance(target, int):
                    target = unthread_return(ins.offset, target)
                if isinstance(target, int) and target > ins.offset and not is_chain_cleanup(target):
                    op = "and" if ins.opname == "JUMP_IF_FALSE_OR_POP" else "or"
                    self._boolop_or_pop[ins.offset] = (op, target)
            elif ins.opname == "COPY" and ins.arg == 1:
                j = k + 1
                while j < len(instrs) and instrs[j].opname in skip:
                    j += 1
                if j >= len(instrs) or instrs[j].opname not in ("POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE"):
                    continue
                m = j + 1
                while m < len(instrs) and instrs[m].opname in skip:
                    m += 1
                if m >= len(instrs) or instrs[m].opname != "POP_TOP":
                    continue
                target = self._get_jump_target(instrs[j])
                if not isinstance(target, int) or target <= ins.offset or is_chain_cleanup(target):
                    continue
                op = "and" if instrs[j].opname == "POP_JUMP_IF_FALSE" else "or"
                resume = instrs[m + 1].offset if m + 1 < len(instrs) else instrs[m].offset + 2
                self._boolop_copy[ins.offset] = (op, target, resume)

        # 3.9/3.10 mixed chains: a plain POP_JUMP whose target lies inside the expression
        # that a later *_OR_POP closes.
        for ins in instrs:
            if ins.opname not in ("POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE"):
                continue
            target = self._get_jump_target(ins)
            if not isinstance(target, int) or target <= ins.offset:
                continue
            for q_off, (_, join) in self._boolop_or_pop.items():
                if ins.offset < q_off < target < join:
                    # Code that cannot fall through to the target (a return in between)
                    # makes the jump an `if` statement, not the first operator of an
                    # expression. Checked only here, for a jump that could join a chain.
                    if any(ins.offset < x.offset < target
                           and x.opname in ("RETURN_VALUE", "RETURN_CONST", "RAISE_VARARGS")
                           for x in instrs):
                        break
                    self._boolop_groups[ins.offset] = ("and" if ins.opname == "POP_JUMP_IF_FALSE" else "or", target)
                    break

    def _boolop_step(self, instr: BytecodeInstruction) -> bool:
        """
        Advance value-context `and`/`or` reconstruction for `instr`.

        Closes operators whose join point has been reached, then consumes the
        operator's jump instructions. Returns True if `instr` was fully handled here.
        """
        if instr.offset in getattr(self, "_ternary_suppress", ()) or instr.offset in getattr(self, "_ternary_jumps", ()):
            return False  # a conditional expression (or part of a branch), evaluated as a whole
        pending = self._boolop_pending
        if pending:
            # Reaching a join: the right operand is on top of the stack. A COPY/jump/POP_TOP
            # sequence is consumed as one unit, so a join that lands on its POP_TOP (3.14
            # shares that POP_TOP between two operators) closes before the unit is read.
            copy_spec = self._boolop_copy.get(instr.offset)
            limit = copy_spec[2] if copy_spec is not None else instr.offset + 1
            pending[:] = [e for e in pending if not (e[0].startswith("g") and instr.offset <= e[2] < limit)]
            while pending and instr.offset <= pending[-1][2] < limit and not pending[-1][0].startswith("g"):
                kind, left, _ = pending.pop()
                if not self.stack:
                    break
                self.stack.append(_BoolExpr.join(kind, left, self.stack.pop()))

        spec = self._boolop_or_pop.get(instr.offset)
        if spec is not None and self.stack:
            self._boolop_open(spec[0], spec[1], instr.offset)
            return True

        spec = self._boolop_copy.get(instr.offset)
        if spec is not None and self.stack:
            self._boolop_open(spec[0], spec[1], instr.offset)
            while self.pc < len(self.instructions) and self.instructions[self.pc].offset < spec[2]:
                self.pc += 1
            return True

        spec = self._boolop_groups.get(instr.offset)
        if spec is not None and self.stack:
            pending.append(["g" + spec[0], self.stack.pop(), spec[1]])
            return True
        return False

    def _boolop_open(self, op: str, join: int, offset: int) -> None:
        """Record `<TOS> op ...`, first folding any open mixed-chain groups into the left operand."""
        pending = self._boolop_pending
        left = self.stack.pop()
        while pending and pending[-1][0].startswith("g") and pending[-1][2] > offset:
            kind, group_left, _ = pending.pop()
            left = _BoolExpr.join(kind[1:], group_left, left)
        pending.append([op, left, join])

    def _op_unary(self, instr: BytecodeInstruction):
        """
        UNARY_NEGATIVE / UNARY_POSITIVE / UNARY_INVERT / UNARY_NOT: apply the operator to TOS.

        The result is parenthesised so that a later `**`, attribute access or
        comparison cannot rebind it (`(-x) ** 2` is not `-x ** 2`).
        """
        if instr.opname == "CALL_INTRINSIC_1":
            # Only unary plus changes the expression; the other intrinsics are
            # bookkeeping that never reaches the source.
            if "UNARY_POSITIVE" not in str(instr.argrepr):
                return
            symbol = "+"
        else:
            symbol = {
                "UNARY_NEGATIVE": "-", "UNARY_POSITIVE": "+",
                "UNARY_INVERT": "~", "UNARY_NOT": "not ",
            }[instr.opname]
        if not self.stack:
            return
        self.stack.append(f"({symbol}{_unary_operand(self.stack.pop())})")

    def _op_jump_if_not_exc_match(self, instr: BytecodeInstruction):
        # 3.9 / 3.10: JUMP_IF_NOT_EXC_MATCH(target)
        # Pops the exception type to match against and the exception instance.
        if len(self.stack) >= 2:
            exc_type = self.stack.pop()
            exc_instance = self.stack.pop()

            # Heuristic for `except Exception as e`:
            # Modern Python (3.11+) often has a DUP_TOP, STORE_* sequence
            # following the match. We scan ahead for a STORE instruction
            # targets the same offset as the jump.
            exc_var = ""
            peek_pc = self.pc
            # Skip trivial ops
            while peek_pc < len(self.instructions) and self.instructions[peek_pc].opname in ("COPY", "PUSH_NULL", "RESUME", "CACHE"):
                peek_pc += 1

            if peek_pc < len(self.instructions):
                next_instr = self.instructions[peek_pc]
                if "STORE" in next_instr.opname:
                    exc_var = f" as {next_instr.argval}"
                    # Skip the store instruction if we consume it here
                    # self.pc = peek_pc + 1

            # Reconstruct "except {exc_type}:" header
            if self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup") and self.blocks[-1][0] == instr.offset:
                self.blocks.pop()
                self.indent_level -= 1
            if self._except_header_indents:
                self.indent_level = self._except_header_indents[-1]
            self._append_reconstructed(f"except {exc_type}{exc_var}:")
            self.indent_level += 1
            self.stack.append("_exc_match")

    def _op_jump_if_true_or_pop(self, instr: BytecodeInstruction):
        """
        JUMP_IF_TRUE_OR_POP: Peek TOS; if true jump to target (leaving value on stack),
        otherwise pop it and fall through.
        """
        if not self.stack:
            return
        # Peek the condition without popping it initially
        cond = self.stack[-1]
        jump_target = self._get_jump_target(instr)

        # Check for ternary expression pattern (reuse existing logic)
        if instr.offset in getattr(self, "_ternary_jumps", {}):
            # Delegate to _op_conditional_jump for ternary handling
            # Temporarily restore the dispatch to call the shared logic
            self._op_conditional_jump(instr)
            return

        # Semantics: if condition is true, KEEP it on stack and jump;
        # if false, POP it and continue (loading the next term).
        # We simulate the taken-branch state by default for the stack,
        # but if this is part of a complex boolean chain, the pre-scan
        # handles the reconstruction.

    def _op_jump_if_false_or_pop(self, instr: BytecodeInstruction):
        """
        JUMP_IF_FALSE_OR_POP: Peek TOS; if false jump to target (leaving value on stack),
        otherwise pop it and fall through.
        """
        if not self.stack:
            return
        # Peek the condition without popping it initially
        cond = self.stack[-1]
        jump_target = self._get_jump_target(instr)

        # Check for ternary expression pattern (reuse existing logic)
        if instr.offset in getattr(self, "_ternary_jumps", {}):
            # Delegate to _op_conditional_jump for ternary handling
            self._op_conditional_jump(instr)
            return

        # Semantics: if condition is false, KEEP it on stack and jump;
        # if true, POP it and continue.

    # ── for loop ───────────────────────────────────────────────────

    def _op_for_iter(self, instr: BytecodeInstruction):
        opname = instr.opname
        if self.stack:
            iterator = self.stack.pop()
            var_name = "_item"
            peek_pc = self.pc
            # Peek ahead for STORE_* or UNPACK_SEQUENCE to get var name(s).
            # Skip no-op / hint instructions that may appear between FOR_ITER
            # and the STORE in some Python versions (e.g. NOT_TAKEN on 3.14).

            if self.pc < len(self.instructions):
                peek_pc = self.pc
                while (peek_pc < len(self.instructions)
                       and self.instructions[peek_pc].opname in _GENERIC_SKIP_OPS):
                    peek_pc += 1
                if peek_pc < len(self.instructions):
                    next_instr = self.instructions[peek_pc]
                    if next_instr.opname in ("STORE_NAME", "STORE_FAST", 
                                             "STORE_FAST_STORE_FAST", "STORE_FAST_LOAD_FAST"):
                        if "STORE_FAST_STORE_FAST" in next_instr.opname:
                            var_name = ", ".join([str(v) for v in next_instr.argval])
                            self.pc = peek_pc + 1
                        else:
                            var_name = str(next_instr.argval)
                    elif next_instr.opname in ("UNPACK_SEQUENCE", "UNPACK_EX"):
                        pending: List[str] = []
                        parsed = self._parse_store_target(self.instructions, peek_pc, pending)
                        if parsed is not None and not pending:
                            var_name = parsed[0]
                            self.pc = parsed[2]  # skip the targets we parsed
            self._append_reconstructed(f"for {var_name} in {iterator}:")
            self.indent_level += 1
            jump_target = self._get_jump_target(instr)
            self.blocks.append((jump_target, "for"))
            if self.pc <= peek_pc:
                # The target's STORE is still ahead and consumes the item. When the
                # targets were already read (tuple unpacking), nothing does, and a left-
                # over item would surface as a stray statement at 3.13+'s END_FOR; POP_TOP.
                self.stack.append(var_name)

        # ── collection builders ────────────────────────────────────────

    def _op_build_collection(self, instr: BytecodeInstruction):
        opname = instr.opname
        items = []
        num = int(instr.arg) if instr.arg is not None else 0
        for _ in range(num):
            if self.stack:
                items.insert(0, str(self.stack.pop()))

        if opname == "BUILD_TUPLE":
            self.stack.append(f"({', '.join(items)})")
        elif opname == "BUILD_LIST":
            self.stack.append("[" + ", ".join(items) + "]")
        elif opname == "BUILD_SET":
            if not items:
                self.stack.append("set()")
            else:
                self.stack.append("{" + ", ".join(items) + "}")

        # BUILD_MAP

    def _op_build_map(self, instr: BytecodeInstruction):
        opname = instr.opname
        num = int(instr.arg) if instr.arg is not None else 0
        pairs = []
        for _ in range(num):
            val = str(self.stack.pop()) if self.stack else "?"
            key = str(self.stack.pop()) if self.stack else "?"
            pairs.insert(0, f"{key}: {val}")
        self.stack.append("{" + ", ".join(pairs) + "}")

    def _op_build_slice(self, instr: BytecodeInstruction):
        argc = instr.arg if instr.arg is not None else 2
        step = str(self.stack.pop()) if argc == 3 else ""
        stop = str(self.stack.pop())
        start = str(self.stack.pop())
        if start == "None":
            start = ""
        if stop == "None":
            stop = ""
        if step == "None":
            step = ""

        slice_str = f"{start}:{stop}"
        if argc == 3:
            slice_str += f":{step}"
        self.stack.append(slice_str)

    def _op_build_const_key_map(self, instr: BytecodeInstruction):
        keys_val = self.stack.pop() if self.stack else "()"
        count = int(instr.arg) if instr.arg is not None else 0
        
        # keys_val is e.g. "('a', 'b')" or "(0, 1)"
        try:
            import ast
            keys = ast.literal_eval(keys_val)
        except:
            keys = [k.strip("'\"") for k in str(keys_val).strip("() ").split(",") if k.strip()]
            
        values = []
        for _ in range(count):
            if self.stack:
                values.insert(0, self.stack.pop())
                
        items = []
        for i, k in enumerate(keys):
            v = values[i] if i < len(values) else "None"
            k_repr = repr(k)
            items.append(f"{k_repr}: {v}")
        self.stack.append(f"{{{', '.join(items)}}}")

    def _op_list_extend(self, instr: BytecodeInstruction):
        opname = instr.opname
        if len(self.stack) >= 2:
            it = str(self.stack.pop())
            lst = str(self.stack.pop())
            if lst == "[]":
                self.stack.append(f"[*{it}]" if it.startswith("(") else f"list({it})")
            else:
                self.stack.append(f"[*{lst}, *{it}]")

    def _collection_mutator(self, instr: BytecodeInstruction, kind: str):
        if kind == "map":
            val = str(self.stack.pop()) if self.stack else "None"
            key = str(self.stack.pop()) if self.stack else "None"
        else:
            val = str(self.stack.pop()) if self.stack else "None"
            key = None

        i = int(instr.arg) if instr.arg is not None else 1
        in_loop = any(b[1] in ("for", "while") for b in self.blocks)

        if len(self.stack) >= i:
            raw_target = self.stack[-i]
            target = str(raw_target)
            already_accum = isinstance(raw_target, _CollectionAccumSentinel)

            if not in_loop:
                if kind == "list":
                    if target == "[]":
                        self.stack[-i] = f"[{val}]"
                        return
                    elif target.startswith("[") and target.endswith("]"):
                        inner = target[1:-1]
                        self.stack[-i] = f"[{inner + (', ' if inner else '') + val}]"
                        return
                elif kind == "set":
                    if target in ("set()", "{}"):
                        self.stack[-i] = f"{{{val}}}"
                        return
                    elif target.startswith("{") and target.endswith("}"):
                        inner = target[1:-1]
                        self.stack[-i] = f"{{{inner + (', ' if inner else '') + val}}}"
                        return
                elif kind == "map":
                    if target == "{}":
                        self.stack[-i] = f"{{{key}: {val}}}"
                        return
                    elif target.startswith("{") and target.endswith("}"):
                        inner = target[1:-1]
                        self.stack[-i] = f"{{{inner + (', ' if inner else '') + key}: {val}}}"
                        return

            is_comp = self.code_obj.co_name in ("<listcomp>", "<setcomp>", "<dictcomp>", "<genexpr>")
            if not is_comp:
                if not already_accum:
                    # target is the *user*'s iterable variable, not our accumulator.
                    # Check whether it was '_item' (the decompiler-generated loop placeholder):
                    # if so, initialise with an empty literal; otherwise seed from the iterable.
                    if kind == "list":
                        init_val = "[]" if target == "_item" else target
                    elif kind == "set":
                        init_val = "set()" if target == "_item" else target
                    else:
                        init_val = "{}" if target == "_item" else target
                    fresh_name = self._fresh_accum_name()
                    sentinel = _CollectionAccumSentinel(fresh_name)
                    self._append_reconstructed(f"{sentinel} = {init_val}")
                    self.stack[-i] = sentinel
                    target = str(sentinel)

                if kind == "list":
                    self._append_reconstructed(f"{target}.append({val})")
                elif kind == "set":
                    self._append_reconstructed(f"{target}.add({val})")
                else:
                    self._append_reconstructed(f"{target}[{key}] = {val}")
                return

        if kind == "map":
            self._append_reconstructed(f"yield {key}: {val}")
        else:
            self._append_reconstructed(f"yield {val}")

    def _op_list_append(self, instr: BytecodeInstruction):
        self._collection_mutator(instr, "list")

    def _op_set_add(self, instr: BytecodeInstruction):
        self._collection_mutator(instr, "set")

    def _op_set_update(self, instr: BytecodeInstruction):
        # Python 3.9+ frozenset literals generate: BUILD_SET 0, LOAD_CONST frozenset, SET_UPDATE 1
        if self.stack:
            val = str(self.stack.pop())
            # We want to represent the updated set. The base is TOS.
            if self.stack:
                base = str(self.stack.pop())
                # If base is an empty set (set()), and val is frozenset({a, b}),
                # we render the inner literal without frozenset wrap for the argument.
                # E.g. val is "frozenset({'A', 'B'})" -> "{'A', 'B'}"
                # Or if val is just a set constructor.
                if val.startswith("frozenset({") and val.endswith("})"):
                    inner = val[10:-1]  # removes frozenset( and )
                    self.stack.append(inner)
                elif val.startswith("frozenset([") and val.endswith("])"):
                    inner = "{" + val[11:-2] + "}"
                    self.stack.append(inner)
                else:
                    self.stack.append(val)
            else:
                self.stack.append(val)

    def _op_map_add(self, instr: BytecodeInstruction):
        self._collection_mutator(instr, "map")

    def _op_dict_merge(self, instr: BytecodeInstruction):
        opname = instr.opname
        if len(self.stack) >= 2:
            src = str(self.stack.pop())
            base = str(self.stack.pop())
            self.stack.append(f"{{**{base}, **{src}}}")

        # ── secondary LOAD paths (dead-code guard — already handled above
        #    for the primary names; this catches any variant not in the top
        #    LOAD_* list, e.g. new fused opcodes in future versions) ──────

    def _op_load_fallback(self, instr: BytecodeInstruction):
        opname = instr.opname
        if isinstance(instr.argval, (tuple, list)):
            for n in instr.argval:
                self.stack.append(str(n))
        else:
            self.stack.append(str(instr.argval))

    def _op_load_attr(self, instr: BytecodeInstruction):
        if not self.stack: return
        obj = self.stack.pop()
        
        # 3.12+ LOAD_ATTR arg is a bitmask. Bit 0 (0x01) means push NULL/self sentinel.
        # This is used for method calls: stack becomes [obj, NULL, func]
        arg = int(instr.arg) if instr.arg is not None else 0
        if (arg & 1) and self.target_version >= (3, 12):
            self.stack.append(obj)
            self.stack.append(StackNULL(is_method=True))
            self.stack.append(str(instr.argval))
        else:
            self.stack.append(f"{self._wrap_negative_literal(obj)}.{instr.argval}")

    def _op_load_super_attr(self, instr: BytecodeInstruction):
        # LOAD_SUPER_ATTR pops 3: [global_super, class, self/class]
        for _ in range(3):
            if self.stack:
                self.stack.pop()
        
        name = str(instr.argval)
        if " + " in name:
            name = name.split(" + ")[0]
        
        if instr.arg is not None and (instr.arg & 1):
             # Method call: push sentinel then the method name
             self.stack.append(StackNULL(is_method=False))
             self.stack.append(f"super().{name}")
        else:
             self.stack.append(f"super().{name}")

    def _op_load_from_dict_or_globals(self, instr: BytecodeInstruction):
        opname = instr.opname
        self.stack.append(str(instr.argval))

    def _op_yield_value(self, instr: BytecodeInstruction):
        opname = instr.opname
        val = self.stack.pop() if self.stack else "None"
        self._append_reconstructed(f"yield {val}")

    def _op_delete(self, instr: BytecodeInstruction):
        opname = instr.opname
        # Suppress except-cleanup `del e` for any except-bound name.
        bound = getattr(self, "_exc_bound_names", set())
        if str(instr.argval) in bound:
            if str(instr.argval) == getattr(self, "_exc_cleanup_name", None):
                self._exc_cleanup_name = None
            return
        self._append_reconstructed(f"del {instr.argval}")

        # ── assert ─────────────────────────────────────────────────────

    def _op_load_assertion_error(self, instr: BytecodeInstruction):
        opname = instr.opname
        self.stack.append("AssertionError")


# ---------------------------------------------------------------------------
# Helper: pick the correct decompiler subclass for recursive calls
# ---------------------------------------------------------------------------

def _pick_decompiler_class(instance):
    # Imported lazily: the version backends import this module (they subclass DecompilerGeneric).
    from .py311 import Decompiler311Plus
    from .py314 import Decompiler314
    from .py39 import Decompiler39
    if isinstance(instance, Decompiler39):
        return Decompiler39
    if isinstance(instance, Decompiler314):
        return Decompiler314
    if isinstance(instance, Decompiler311Plus):
        return Decompiler311Plus
    return DecompilerGeneric
