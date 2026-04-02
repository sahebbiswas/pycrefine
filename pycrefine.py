#!python3
# pycrefine.py
import argparse
import dis
import importlib.util
import io
import marshal
import re
import struct
import sys
import traceback
import types
from typing import List, Optional, Any, Dict, Union, Tuple
from dataclasses import dataclass

# --- Magic Numbers & Search Windows ---
_MAX_WITH_EXIT_SEARCH_WINDOW = 200
_MAX_TERNARY_SEARCH_WINDOW = 20
_MAX_TERNARY_SHORT_SEARCH_WINDOW = 12
_MAX_TERNARY_MINI_SEARCH_WINDOW = 8

# --- Constant Instruction Sets ---
_NO_PASS_TYPES = frozenset({"exc_cleanup", "finally_wrapper"})
_NO_INDENT_TYPES = frozenset({"finally_wrapper"})

_TERNARY_STORES = frozenset(("STORE_FAST", "STORE_NAME", "STORE_GLOBAL", "STORE_DEREF"))
_TERNARY_SKIP = frozenset(("CACHE", "RESUME", "NOT_TAKEN", "COPY_FREE_VARS"))
_TERNARY_TERM = frozenset(("RETURN_CONST", "RETURN_VALUE"))
_TERNARY_PURE = frozenset((
    "LOAD_FAST", "LOAD_NAME", "LOAD_GLOBAL", "LOAD_CONST", "LOAD_DEREF",
    "LOAD_SMALL_INT", "LOAD_GLOBAL_MODULE", "LOAD_ATTR", "LOAD_METHOD",
    "GET_ATTR", "LOAD_FAST_BORROW", "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
    "LOAD_CONST_BORROW", "CALL", "CALL_FUNCTION", "CALL_METHOD",
    "COMPARE_OP", "BINARY_OP", "IS_OP", "CONTAINS_OP",
    "UNARY_NOT", "UNARY_NEGATIVE", "UNARY_POSITIVE", "UNARY_INVERT",
    "BUILD_TUPLE", "BUILD_LIST", "BUILD_SET", "BUILD_MAP",
    "PRECALL", "PUSH_NULL", "FORMAT_VALUE", "FORMAT_SIMPLE", "BUILD_STRING",
    "BINARY_SUBSCR", "TO_BOOL",
    # Python 3.9 named binary opcodes (3.11+ uses BINARY_OP instead):
    "BINARY_ADD", "BINARY_SUBTRACT", "BINARY_MULTIPLY", "BINARY_TRUE_DIVIDE",
    "BINARY_FLOOR_DIVIDE", "BINARY_MODULO", "BINARY_POWER", "BINARY_LSHIFT",
    "BINARY_RSHIFT", "BINARY_AND", "BINARY_OR", "BINARY_XOR",
    "BINARY_MATRIX_MULTIPLY",
)) | _TERNARY_SKIP | _TERNARY_TERM

_COMPOUND_SKIP = frozenset(("CACHE", "RESUME", "NOT_TAKEN", "COPY_FREE_VARS", "PRECALL", "PUSH_NULL"))
_COMPOUND_EXPR_OPS = frozenset((
    "LOAD_FAST", "LOAD_NAME", "LOAD_GLOBAL", "LOAD_CONST", "LOAD_DEREF",
    "LOAD_SMALL_INT", "LOAD_FAST_BORROW", "LOAD_CONST_BORROW", "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
    "LOAD_GLOBAL_MODULE", "LOAD_ATTR", "LOAD_METHOD", "GET_ATTR",
    "CALL", "CALL_FUNCTION", "CALL_METHOD", "COMPARE_OP", "BINARY_OP", "IS_OP", "CONTAINS_OP",
    "UNARY_NOT", "UNARY_NEGATIVE", "UNARY_POSITIVE", "UNARY_INVERT",
    "BUILD_TUPLE", "BUILD_LIST", "BUILD_SET", "BUILD_MAP",
    "TO_BOOL", "FORMAT_VALUE", "FORMAT_SIMPLE", "BUILD_STRING", "BINARY_SUBSCR",
    # Python 3.9 binary opcodes:
    "BINARY_ADD", "BINARY_SUBTRACT", "BINARY_MULTIPLY", "BINARY_TRUE_DIVIDE",
    "BINARY_FLOOR_DIVIDE", "BINARY_MODULO", "BINARY_POWER", "BINARY_LSHIFT",
    "BINARY_RSHIFT", "BINARY_AND", "BINARY_OR", "BINARY_XOR",
    "BINARY_MATRIX_MULTIPLY"
)) | _COMPOUND_SKIP

_WHILE_TRIVIAL_OPS = frozenset({"NOP", "RESUME", "NOT_TAKEN", "CACHE", "COPY_FREE_VARS"})
_TRY_SKIP_NOP = frozenset({"RESUME", "CACHE", "NOT_TAKEN"})
_TRY_LOAD_OPS = frozenset({"LOAD_NAME", "LOAD_GLOBAL", "LOAD_FAST", "LOAD_DEREF", "LOAD_SMALL_INT", "LOAD_GLOBAL_MODULE"})
_TRY_LOOP_HEADS = frozenset({"FOR_ITER", "GET_ITER", "GET_ANEXT", "GET_AWAITABLE"})
_TRY_HANDLER_EXIT_OPS = frozenset({"POP_EXCEPT", "DELETE_FAST", "DELETE_NAME", "DELETE_GLOBAL", "STORE_FAST", "STORE_NAME"})

_IS_COMP_GEN_SKIP = frozenset({"POP_TOP", "CACHE", "NOP", "RESUME", "COPY", "NOT_TAKEN"})
_IS_COMP_GEN_STOP = frozenset({"LOAD_CONST", "LOAD_NAME", "LOAD_FAST", "LOAD_GLOBAL", "LOAD_DEREF", "LOAD_SMALL_INT", "LOAD_ATTR", "PUSH_NULL", "RERAISE", "RAISE_VARARGS", "RETURN_CONST", "RETURN_VALUE", "JUMP_FORWARD"})

_GENERIC_SKIP_OPS = frozenset({"RESUME", "CACHE", "NOP", "NOT_TAKEN", "COPY_FREE_VARS", "EXTENDED_ARG"})

# --- Augmented Assignment Opcodes ---
_AUG_ASSIGN_MAP = {
    13: "+=",  14: "&=",  15: "//=", 16: "<<=", 17: "@=",
    18: "*=",  19: "%=",  20: "|=",  21: "**=", 22: ">>=",
    23: "-=",  24: "/=",  25: "^=",
}
_INPLACE_ASSIGN_MAP = {
    "INPLACE_ADD": "+=", "INPLACE_SUBTRACT": "-=",
    "INPLACE_MULTIPLY": "*=", "INPLACE_TRUE_DIVIDE": "/=",
    "INPLACE_FLOOR_DIVIDE": "//=", "INPLACE_MODULO": "%=",
    "INPLACE_POWER": "**=", "INPLACE_LSHIFT": "<<=",
    "INPLACE_RSHIFT": ">>=", "INPLACE_AND": "&=",
    "INPLACE_OR": "|=", "INPLACE_XOR": "^=",
    "INPLACE_MATRIX_MULTIPLY": "@=",
}


def post_process_source(source: str) -> str:
    """Clean up decompiled output to be more Pythonic."""
    lines = source.split('\n')
    out_lines = []
    
    current_imports = []
    current_froms = {}
    current_indent = None
    
    def flush_imports():
        nonlocal current_imports, current_froms, current_indent
        if current_imports:
            unique_mods = []
            for m in current_imports:
                if m not in unique_mods:
                    unique_mods.append(m)
            for m in unique_mods:
                out_lines.append(f"{current_indent}import {m}")
            current_imports.clear()
            
        for mod, syms in current_froms.items():
            unique_syms = []
            for s in syms:
                if s not in unique_syms:
                    unique_syms.append(s)
            out_lines.append(f"{current_indent}from {mod} import {', '.join(unique_syms)}")
        current_froms.clear()
        current_indent = None

    # Parens-stripping regexes.
    # The inner-expression pattern deliberately excludes lines that contain
    # 'for', 'if', ':=', or 'lambda' because those keywords indicate that the
    # outer parens are *required* (genexpr, conditional expr, walrus, lambda)
    # rather than redundant grouping added by the decompiler.
    _NO_KW = r'(?![^()]*\b(?:for|lambda)\b)'  # negative lookahead: no for/lambda inside
    assignment_parens_re = re.compile(
        r'^(\s*[A-Za-z_][A-Za-z0-9_.]*\s*(?:\+|-|\*|/|//|%|&|\||\^|<<|>>)?=\s*)'
        r'\(' + _NO_KW + r'([^,()]+)\)$'
    )
    return_parens_re = re.compile(
        r'^(\s*return\s+)\(' + _NO_KW + r'([^,()]+)\)$'
    )
    if_parens_re = re.compile(
        r'^(\s*(?:if|elif)\s+)\(' + _NO_KW + r'([^,()]+)\):$'
    )
    while_parens_re = re.compile(
        r'^(\s*while\s+)\(' + _NO_KW + r'([^,()]+)\):$'
    )
    
    for line in lines:
        imp_m = re.match(r'^([ \t]*)import\s+(.+)$', line)
        from_m = re.match(r'^([ \t]*)from\s+([A-Za-z0-9_.]+)\s+import\s+(.+)$', line)
        
        handled = False
        if imp_m:
            indent, mods = imp_m.groups()
            mods_list = [m.strip() for m in mods.split(',')]
            if indent == current_indent and not current_froms:
                current_imports.extend(mods_list)
                handled = True
            elif current_indent is None:
                current_indent = indent
                current_imports.extend(mods_list)
                handled = True
                
        elif from_m:
            indent, mod, syms = from_m.groups()
            sym_list = [s.strip() for s in syms.split(',')]
            if indent == current_indent and not current_imports:
                current_froms.setdefault(mod, []).extend(sym_list)
                handled = True
            elif current_indent is None:
                current_indent = indent
                current_froms.setdefault(mod, []).extend(sym_list)
                handled = True
                
        if not handled:
            flush_imports()
            if imp_m:
                indent, mods = imp_m.groups()
                current_indent = indent
                current_imports.extend([m.strip() for m in mods.split(',')])
            elif from_m:
                indent, mod, syms = from_m.groups()
                current_indent = indent
                current_froms.setdefault(mod, []).extend([s.strip() for s in syms.split(',')])
            else:
                # Cleanup doubled-up assignments from INPLACE binary ops
                # e.g., 'var = (var += expr)' -> 'var += expr'
                m_double = re.match(
                    r'^(\s*)([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*\(\2\s*(\+|-|\*|/|//|%|&|\||\^|<<|>>|\*\*|@)=\s*(.*)\)$',
                    line
                )
                if m_double:
                    indent, var_name, op_sym, rhs = m_double.groups()
                    line = f"{indent}{var_name} {op_sym}= {rhs}"
                else:
                    line = assignment_parens_re.sub(r'\1\2', line)
                    line = return_parens_re.sub(r'\1\2', line)
                    line = if_parens_re.sub(r'\1\2:', line)
                    line = while_parens_re.sub(r'\1\2:', line)
                
                out_lines.append(line)
                
    flush_imports()
    
    text = '\n'.join(out_lines)
    text = re.sub(r'\n{3,}', '\n\n', text)

    # ── Final cleanup: suppress raw decompiler-tuple leakage ─────────────
    # If any ('func', ...) or ('class', ...) tuples slipped through to an
    # assignment RHS or statement position, replace the ENTIRE statement
    # (to end-of-line) with a comment + None so the output stays valid Python.
    text = re.sub(
        r"([ \t]*)([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*\('(func|class)',[^\n]*",
        lambda m: (
            f"{m.group(1)}# <{'genexpr/lambda' if m.group(3) == 'func' else 'class'}"
            f" \u2014 not reconstructable>\n{m.group(1)}{m.group(2)} = None"
        ),
        text,
    )
    return text.strip('\r\n') + '\n'

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
    argrepr: str = ""



def _render_func_tuple(body_text: str, args: List[str]) -> str:
    """
    Convert a raw ('func', body_text) decompiler tuple into a clean Python
    expression.  Called when a code object created by MAKE_FUNCTION is
    immediately called (genexpr, lambda, comprehension helper, etc.) instead
    of being stored under a name.

    Three cases handled:
    1. Generator expression — ``def <genexpr>(.0): for x in .0: yield expr``
       → rendered as ``(expr for x in iterable [if cond])``
    2. Lambda — ``def <lambda>(params): return expr``
       → rendered as ``lambda params: expr``
    3. Anything else (unknown inner function) → ``<func>(args)`` placeholder.
    """
    lines = [line.strip() for line in body_text.strip().splitlines() if line.strip()]
    if not lines:
        return f"<func>({', '.join(args)})"

    # ── Case 1: comprehensions (genexpr / listcomp / setcomp / dictcomp) ───────
    _comp_names = ("<genexpr>", "<listcomp>", "<setcomp>", "<dictcomp>")
    if any(name in lines[0] for name in _comp_names):
        # Choose the correct bracket style for each comprehension type.
        if "<listcomp>" in lines[0]:
            wrapper_open, wrapper_close = "[", "]"
        elif "<setcomp>" in lines[0] or "<dictcomp>" in lines[0]:
            wrapper_open, wrapper_close = "{", "}"
        else:  # genexpr
            wrapper_open, wrapper_close = "(", ")"

        for_clause = None
        if_clause  = None
        yield_expr = None

        for line in lines[1:]:
            # Skip structural wrappers that appear in some versions:
            # 3.9 wraps the genexpr body in 'while True:' before the for loop.
            if line in ("while True:", "while True"):
                continue
            if line.startswith("for "):
                fc = line.rstrip(":")
                # Replace the implicit .0 parameter with the actual iterable arg
                if args:
                    # Strip a trailing "()" suffix that may have been added by a
                    # preceding CALL-0 misfire (GET_ITER treated as a no-arg call).
                    # Use removesuffix rather than rstrip so we only strip exactly
                    # one trailing "()" and never mangle expressions like range(10).
                    actual_iter = str(args[0])
                    if actual_iter.endswith("()"):
                        actual_iter = actual_iter[:-2]
                    fc = fc.replace(".0", actual_iter)
                for_clause = fc
            elif line.startswith("if "):
                if_clause = line.rstrip(":")
            elif line.startswith("yield "):
                yield_expr = line[6:].strip()

        if for_clause and yield_expr is not None:
            result = wrapper_open + yield_expr + " " + for_clause
            if if_clause:
                result += " " + if_clause
            result += wrapper_close
            return result

    # ── Case 2: lambda ───────────────────────────────────────────────────
    if "<lambda>" in lines[0]:
        # Extract params from 'def <lambda>(params):'
        m = re.match(r"def\s+<lambda>\s*\(([^)]*)\):", lines[0])
        params = m.group(1).strip() if m else ""
        # Find return expression
        ret_expr = None
        for line in lines[1:]:
            if line.startswith("return "):
                ret_expr = line[7:].strip()
                break
        if ret_expr is not None:
            return f"lambda {params}: {ret_expr}" if params else f"lambda: {ret_expr}"

    # ── Case 3: fallback — extract function name if recognisable ─────────
    # e.g. 'def _find_something(...)' used as a first-class callback
    m = re.match(r"def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", lines[0])
    if m and "<" not in m.group(1):
        return m.group(1)

    return f"<func>({', '.join(args)})"


def _is_anonymous_func_body(first_line: str) -> bool:
    """
    Return True when *first_line* is the signature line of an anonymous code
    object — a generator expression, lambda, list/set/dict comprehension, or
    the synthetic ``<module>`` frame — rather than a named function.

    Used by the decorator-detection logic in CALL / CALL_FUNCTION handlers to
    distinguish ``@decorator def real_func(...): ...`` (should be emitted with
    a ``@`` line) from ``sum(x**2 for x in ...)`` (should NOT gain a decorator).
    """
    _ANON_TOKENS = (
        "<genexpr>", "<lambda>", "<listcomp>",
        "<setcomp>", "<dictcomp>", "<module>",
    )
    return any(tok in first_line for tok in _ANON_TOKENS)

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
        for instr in dis.get_instructions(self.code_obj):
            self.instructions.append(BytecodeInstruction(
                opcode=instr.opcode,
                opname=instr.opname,
                arg=instr.arg,
                argval=instr.argval,
                offset=instr.offset,
                starts_line=instr.starts_line,
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
        # Search ahead from current PC.
        for i in range(self.pc, len(self.instructions)):
            instr = self.instructions[i]
            op = instr.opname
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
        # Save current state for lookahead
        old_pc = self.pc
        self.pc = instr_index + 1
        effectively_last = self.is_effectively_last()
        self.pc = old_pc

        if not effectively_last:
            return False

        # If we're inside any user-facing control flow block, it's likely 
        # an explicit return in the source.
        user_blocks = {"if", "else", "for", "while", "try_body", "except", "finally_body", "with", "with_body"}
        blocks = getattr(self, "blocks", [])
        for _, btype in blocks:
            if btype in user_blocks:
                return False

        # Robust check for Python 3.11+: if the return is within a protected 
        # range in the exception table, it's explicit. Compiler-generated 
        # returns for with/try blocks are always outside the protected range.
        try:
            for e in getattr(dis.Bytecode(self.code_obj), "exception_entries", []):
                if e.start <= instr.offset < e.end:
                    return False
        except (AttributeError, TypeError, ValueError, IndexError):
            pass

        return True

# ---------------------------------------------------------------------------
# Generic decompiler (3.10–3.13 primary path)
# ---------------------------------------------------------------------------

class DecompilerGeneric(DecompilerBase):
    def __init_subclass__(cls, **kw):
        super().__init_subclass__(**kw)

    def __init__(self, code_obj: types.CodeType, indent_level: int = 0):
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
            _except_header_indent (Optional[int]): Indentation level to use when emitting except headers (None when none).
            _except_end_offset (int): End offset for an exception region used to suppress intervening jumps.
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
        super().__init__(code_obj, indent_level)
        self.stack: List[Union[str, Tuple[Any, ...]]] = []
        self.has_doc = False
        # Tracks offsets of while-loop body starts so we can suppress the
        # duplicated condition check emitted at the bottom of 3.11+ while loops.
        self._while_body_offsets: set = set()
        # Exception-handler bookkeeping
        self._exc_as_store_offset: int = -1      # offset of 'as e' STORE to skip
        self._exc_cleanup_name: Optional[str] = None   # name to suppress in cleanup
        self._except_header_indent: Optional[int] = None           # indent level for except headers
        self._except_end_offset: int = -1             # end of exception zone (suppress JUMP_FWD)
        self._exc_bound_names: set = set()             # all names ever bound in except-as
        # The following sets are populated by _prescan_try_structure() which runs in decompile()
        self._try_nop_offsets: set = set()
        self._suppress_push_exc_offsets: set = set()
        self._finally_merge_offsets: set = set()
        self._exc_handler_jump_offsets: set = set()
        self._with_exit_suppress_offsets: set = set()
        self._finally_body_suppress: set = set()
        self._push_exc_to_finally_merge: dict = {}
        self._deferred_finally_lines: dict = {}
        self._deferred_except_lines: dict = {}
        self._handler_section_suppress: set = set()
        self._wrapper_body_suppress: set = set()
        self._pending_finally_merge: Optional[int] = None
        self._nop_to_push_exc: dict = {}
        self._build_dispatch()

    # ------------------------------------------------------------------
    # Main loop
    # ------------------------------------------------------------------

    def decompile(self) -> str:
        """
        Decompile the stored code object into a human-readable Python source string.
        
        Performs bytecode disassembly, structural prescans (loops, ternaries, compound conditions, try/except/finally), reconstructs source lines (including a module docstring when present), closes open blocks, suppresses redundant module-level trailing `return None` when appropriate, and runs post-processing to normalize imports, parentheses, and spacing.
        
        Returns:
            The reconstructed Python source as a single string, post-processed and ending with a single trailing newline.
        """
        start_indent = self.indent_level
        self._disassemble()
        self.pc = 0
        self.blocks = []
        self._while_header_targets = {}
        self._prescan_while_loops()
        self._prescan_try_structure()
        self._prescan_ternaries()
        self._prescan_compound_conds()

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
                if ins.opname not in ("RESUME", "NOP", "CACHE", "NOT_TAKEN"):
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
            self.pc += 1

            # Close any blocks whose end offset we have passed
            self._close_blocks(instr.offset)
            self._handle_instruction(instr)

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
                    has_others = False
                    for i in range(last_idx):
                         strip_line = self.reconstructed[i].strip()
                         if strip_line and not (
                             strip_line.startswith('"""') or strip_line.startswith("'''")
                         ):
                             has_others = True
                             break
                    
                    if has_others or getattr(self, "has_doc", False):
                        self.reconstructed.pop(last_idx)
                    else:
                        indent = len(line) - len(line.lstrip())
                        self.reconstructed[last_idx] = line[:indent] + "pass"

        raw_source = "\n".join(str(s) for s in self.reconstructed).rstrip()
        return post_process_source(raw_source)

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
            if block_type not in _NO_PASS_TYPES:
                last_idx = len(self.reconstructed) - 1
                while last_idx >= 0 and not self.reconstructed[last_idx].strip():
                    last_idx -= 1
                if last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":"):
                    self._append_reconstructed("pass")
            if block_type not in _NO_INDENT_TYPES:
                self.indent_level -= 1

    # ------------------------------------------------------------------
    # Output helpers
    # ------------------------------------------------------------------

    def _append_reconstructed(self, line: str, indent_multiline: bool = False):
        """
        Append a reconstructed source line (or multi-line block) to the decompiler's output buffer with proper indentation and spacing.
        
        This method mutates self.reconstructed by:
        - inserting a blank line before top-level/major block headers (`def`, `class`, `if`, `for`, `while`, `try:`) when the previous line is not already blank;
        - splitting multi-line input on `\n` and emitting each line, either indenting every line when `indent_multiline` is true or only indenting the first line when false;
        - prefixing emitted lines with the current indentation (four spaces per indent level) and preserving empty lines.
        
        Parameters:
            line (str): Source text to append; may contain embedded newlines. An empty string is ignored.
            indent_multiline (bool): If true, apply indentation to every line produced by splitting `line`; if false, only the first line is indented.
        """
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

    def _find_jump_backward_target(self) -> int:
        """Return the jump target of the first backward jump, or -1."""
        for ins in self.instructions:
            if self._is_backward_instruction(ins):
                return self._get_jump_target(ins)
        return -1


    def _prescan_ternaries(self) -> None:
        """
        Identify bytecode sequences that encode ternary assignments and record them for later reconstruction.
        
        Populates two attributes used by the decompiler:
        - _ternary_jumps: maps the offset of a conditional POP_JUMP_IF_* instruction to a tuple
          (store_name, then_instrs, else_instrs, is_true_jump, aug_op) where `store_name` is the 
          target variable name, `then_instrs` and `else_instrs` are lists of instructions 
          forming the true/false branch expressions, `is_true_jump` is True when the jump 
          was taken for the truthy branch, and `aug_op` is an optional augmented-assignment 
          operator string (e.g. "+=") when the ternary is duplicated into an augmented path.
        - _ternary_suppress: a set of instruction offsets that should be skipped during normal
          instruction processing because they are part of a recognized ternary pattern.
        """

        self._ternary_jumps: dict = {}
        self._ternary_suppress: set = set()

        offset_to_idx = {ins.offset: i for i, ins in enumerate(self.instructions)}

        for idx, ins in enumerate(self.instructions):
            if "POP_JUMP_IF_FALSE" not in ins.opname and "POP_JUMP_IF_TRUE" not in ins.opname:
                continue
            jump_target = self._get_jump_target(ins)
            t_idx = offset_to_idx.get(jump_target)
            if t_idx is None or t_idx <= idx:
                continue  # backward jump

            # ── Collect then-branch instructions ─────────────────────────
            then_raw = self.instructions[idx + 1 : t_idx]
            then_sig = [x for x in then_raw if x.opname not in _TERNARY_SKIP]

            if not then_sig:
                continue

            # Detect Pattern B: then-branch contains a forward jump that skips
            # the else-expression and lands directly at the STORE instruction.
            # On 3.12 this is JUMP_FORWARD; on 3.14 it may be a different opcode
            # (e.g. JUMP_FORWARD still, but with a different encoding).  Match any
            # opcode whose name contains "FORWARD" or "JUMP" (but not backward/IF).
            _FWD_JUMP = lambda op: (
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
                    if not all(x.opname in _TERNARY_PURE for x in actual_then_expr):
                        continue
                    if any(x.opname == "POP_TOP" or "POP_JUMP_IF" in x.opname
                           for x in actual_then_expr):
                        continue
                    else_raw    = self.instructions[t_idx : st_idx]
                    else_instrs = [x for x in else_raw if x.opname not in _TERNARY_SKIP]
                    if any(x.opname == "POP_TOP" or "POP_JUMP_IF" in x.opname
                           for x in else_instrs):
                        continue
                    if not all(x.opname in _TERNARY_PURE for x in else_instrs):
                        continue
                    store_name = str(store_instr.argval)
                    is_true    = "IF_TRUE" in ins.opname
                    
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
                            actual_then_expr.pop()
                            else_instrs.pop()

                    self._ternary_jumps[ins.offset] = (
                        store_name, actual_then_expr, else_instrs, is_true, aug_op
                    )
                    for x in then_raw:
                        self._ternary_suppress.add(x.offset)
                    for x in else_instrs:
                        self._ternary_suppress.add(x.offset)
                    continue

                # ── B1: no then-STORE before the jump ────────────────────
                if not all(x.opname in _TERNARY_PURE for x in before_jf):
                    continue
                if any(x.opname == "POP_TOP" or "POP_JUMP_IF" in x.opname
                       for x in before_jf):
                    continue

                store_target = self._get_jump_target(jf)
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

                if st_idx is None or self.instructions[st_idx].opname not in _TERNARY_STORES:
                    st_idx = None
                    for fi in range(t_idx, min(t_idx + _MAX_TERNARY_SEARCH_WINDOW, len(self.instructions))):
                        if self.instructions[fi].opname in _TERNARY_STORES:
                            st_idx = fi
                            break
                if st_idx is None:
                    continue

                else_end = st_idx - 1 if is_inplace else st_idx
                else_raw    = self.instructions[t_idx : else_end]
                else_instrs = [x for x in else_raw if x.opname not in _TERNARY_SKIP]
                if any(x.opname == "POP_TOP" or "POP_JUMP_IF" in x.opname
                       for x in else_instrs):
                    continue
                if not all(x.opname in _TERNARY_PURE for x in else_instrs):
                    continue

                if st_idx >= len(self.instructions):
                    continue
                store_instr = self.instructions[st_idx]
                if store_instr.opname not in _TERNARY_STORES:
                    continue

                store_name = str(store_instr.argval)
                is_true    = "IF_TRUE" in ins.opname

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
                        before_jf.pop()
                        else_instrs.pop()

                self._ternary_jumps[ins.offset] = (
                    store_name, before_jf, else_instrs, is_true, aug_op
                )
                for x in then_raw:
                    self._ternary_suppress.add(x.offset)
                for x in else_instrs:
                    self._ternary_suppress.add(x.offset)
                continue  # done with this POP_JUMP_IF

            # ── Pattern A: no JUMP_FORWARD ────────────────────────────────
            store_idxs = [i for i, x in enumerate(then_sig) if x.opname in _TERNARY_STORES]
            if len(store_idxs) != 1:
                continue
            store_pos  = store_idxs[0]
            then_store = then_sig[store_pos]
            before_store = then_sig[:store_pos]

            if any(x.opname == "POP_TOP" for x in before_store):
                continue
            if any("POP_JUMP_IF" in x.opname for x in before_store):
                continue
            if not all(x.opname in _TERNARY_PURE for x in before_store):
                continue

            # Find the first STORE in the else-branch
            else_store = None
            for i in range(t_idx, min(t_idx + _MAX_TERNARY_SHORT_SEARCH_WINDOW, len(self.instructions))):
                xi = self.instructions[i]
                if xi.opname in _TERNARY_STORES:
                    else_store = xi
                    break
                if xi.opname not in _TERNARY_PURE:
                    break

            if else_store is None or else_store.argval != then_store.argval:
                continue

            es_idx = offset_to_idx[else_store.offset]
            else_raw = self.instructions[t_idx : es_idx]
            else_instrs = [x for x in else_raw if x.opname not in _TERNARY_SKIP]
            if any(x.opname == "POP_TOP" for x in else_instrs):
                continue
            if any("POP_JUMP_IF" in x.opname for x in else_instrs):
                continue

            store_name = str(then_store.argval)
            is_true    = "IF_TRUE" in ins.opname

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
                    before_store.pop()
                    else_instrs.pop()

            self._ternary_jumps[ins.offset] = (
                store_name, before_store, else_instrs, is_true, aug_op
            )
            for x in then_raw:
                self._ternary_suppress.add(x.offset)
            for x in else_instrs:
                self._ternary_suppress.add(x.offset)

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
        self._compound_suppress: set = set() # instruction offsets to skip

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


            valid = False
            while len(group) >= 2:
                last_jump_idx = group[-1][0]
                body_target = -1
                for k in range(last_jump_idx + 1, n):
                    if instrs[k].opname not in _COMPOUND_SKIP:
                        body_target = instrs[k].offset
                        break
                end_target = self._get_jump_target(group[-1][1])
                jump_starts = {g[3] for g in group}
                
                valid = True
                for (_, jinstr, _, _) in group:
                    t = self._get_jump_target(jinstr)
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

            for _, jinstr, expr_instrs, _ in group:
                raw_expr = self._eval_cond_expr(expr_instrs)
                t = self._get_jump_target(jinstr)
                op = jinstr.opname
                is_success_type = ("IF_TRUE" in op)
                
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
                    comb = f"{comb} {c} {parts[k][0]}"
                    h_or = h_or or (c == "or")
                return comb, parts[e_idx-1][1], h_or

            def assemble(start_idx, end_idx, context_exit):
                """Assemble parts[start_idx:end_idx]. Returns (expr, next_conn_is_or, has_top_level_or)."""
                if start_idx + 1 == end_idx:
                    return parts[start_idx][0], parts[start_idx][1], False
                
                subs: list = [] # list of (expr, unit_is_or, has_or)
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


    def _eval_ternary_branch(self, instrs: list) -> str:
        """
        Reconstructs a Python expression string from a sequence of pure-expression bytecode instructions.
        
        Parameters:
        	instrs (list): Disassembled instruction objects representing the then- or else-branch of a ternary expression; should contain only expression-building opcodes.
        
        Returns:
        	expr (str): The reconstructed expression as source text, or "?" if an expression could not be determined.
        """
        mini_stack: list = []
        for ins in instrs:
            op = ins.opname
            if op in ("CACHE", "RESUME", "NOT_TAKEN", "COPY_FREE_VARS",
                      "PRECALL", "PUSH_NULL", "TO_BOOL",
                      "RETURN_CONST", "RETURN_VALUE"):
                continue
            if op == "LOAD_FAST_BORROW_LOAD_FAST_BORROW":
                # Pushes two values: argval is a tuple (name1, name2)
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
            elif op in ("GET_ATTR", "LOAD_ATTR", "LOAD_METHOD"):
                if mini_stack:
                    obj = mini_stack.pop()
                    mini_stack.append(f"{obj}.{ins.argval}")
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
                    op_map = {0:"+", 1:"&", 2:"//", 3:"<<", 4:"@", 5:"*",
                              6:"%", 7:"|", 8:"**", 9:">>", 10:"-", 11:"/", 12:"^",
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
            elif op == "UNARY_NOT":
                if mini_stack:
                    mini_stack.append(f"not {mini_stack.pop()}")
            elif op in ("UNARY_NEGATIVE",):
                if mini_stack:
                    mini_stack.append(f"-{mini_stack.pop()}")
            elif op == "BINARY_SUBSCR":
                if len(mini_stack) >= 2:
                    key, obj = mini_stack.pop(), mini_stack.pop()
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
                        return " " in s and not (s.startswith("(") and s.endswith(")"))
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

        for jb in self.instructions:
            if not self._is_backward_instruction(jb):
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


            guard_offset = -1
            guard_dist = float('inf')   # distance from body_start
            for ins in self.instructions:
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
                
                # Target must exit the loop (land at or beyond jb)
                if t < jb.offset:
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
            # and whose handler starts with CHECK_EXC_MATCH → real except:
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
            preceded_by_handler_exit = False
            for back in range(1, 7):
                check_idx = jb_idx - back
                if check_idx < 0:
                    break
                if instrs[check_idx].opname in _TRY_HANDLER_EXIT_OPS:
                    preceded_by_handler_exit = True
                    break
                # Stop early if we cross a non-trivial boundary
                if instrs[check_idx].opname in ("CALL", "BINARY_OP", "COMPARE_OP"):
                    break
            if preceded_by_handler_exit:
                jb_targets[target] += 1

        for target_off, count in jb_targets.items():
            if count >= 1 and target_off not in push_exc_offs:
                t_ins = next((ins for ins in instrs if ins.offset == target_off), None)
                if t_ins and t_ins.is_jump_target and t_ins.opname not in _TRY_LOOP_HEADS:
                    self._finally_merge_offsets.add(target_off)

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
            preceded_by_handler_exit = False
            for back in range(1, 7):
                check_idx = jb_idx - back
                if check_idx < 0:
                    break
                if instrs[check_idx].opname in _TRY_HANDLER_EXIT_OPS:
                    preceded_by_handler_exit = True
                    break
                if instrs[check_idx].opname in ("CALL", "BINARY_OP", "COMPARE_OP"):
                    break
            if preceded_by_handler_exit:
                self._exc_handler_jump_offsets.add(jb.offset)

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
        self._handler_section_suppress: set = set() # except handler instructions to suppress
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

        # Map: first real PUSH_EXC_INFO → finally-merge offset
        for e in _exc_entries:
            if e.target in real_push_exc and e.end in self._finally_merge_offsets:
                merge = e.end
                target = e.target
                if merge not in self._push_exc_to_finally_merge.values():
                    self._push_exc_to_finally_merge[target] = merge

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
        for push_off, merge_off in self._push_exc_to_finally_merge.items():
            # Handler section starts at push_off, ends at start of either the
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
            self._deferred_except_lines[merge_off] = handler_lines

        # ── Find and suppress finally body instructions
        for push_off, merge_off in self._push_exc_to_finally_merge.items():
            body_end = None
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
            lines = self._render_finally_body(sub_instrs)
            self._deferred_finally_lines[merge_off] = lines

        # ── 7. Build nop_to_push_exc: maps each try-entry NOP to the correct
        #       PUSH_EXC_INFO that handles its try body, using the exception table.
        self._nop_to_push_exc: dict = {}  # nop_offset → push_exc_info_offset
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
                    try_body_end = e.end
                    # Finally body: from try_body_end to first RETURN_CONST or
                    # next suppressed PUSH_EXC_INFO
                    finally_start = try_body_end
                    finally_end = None
                    for ins in instrs:
                        if ins.offset >= finally_start:
                            if ins.opname in ("RETURN_CONST", "RETURN_VALUE"):
                                finally_end = ins.offset
                                break
                            if ins.opname == "PUSH_EXC_INFO":
                                finally_end = ins.offset
                                break
                    if finally_end is None or finally_end <= finally_start:
                        break
                    # Suppress the finally body instructions
                    for ins in instrs:
                        if finally_start <= ins.offset < finally_end:
                            self._finally_body_suppress.add(ins.offset)
                    # Pre-render the finally body
                    sub_instrs = [ins for ins in instrs if finally_start <= ins.offset < finally_end]
                    lines = self._render_finally_body(sub_instrs)
                    self._deferred_finally_lines[finally_start] = lines
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
                if instrs[j].opname == "LOAD_CONST" and instrs[j].argval is None:
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

    def _render_finally_body(self, sub_instrs: list) -> list:
        """
        Render a sequence of bytecode instructions into a list of Python source lines suitable for a deferred `finally` body.
        
        Parameters:
            sub_instrs (list): Sequence of BytecodeInstruction objects (a contiguous slice of disassembled instructions) representing the finally-body to render.
        
        Returns:
            list: Ordered lines of Python source (strings) representing the rendered body. Unknown or complex operations are emitted as commented disassembly lines.
        """
        # Simple stack-based mini-eval to convert the instruction sequence
        # (which can now include stores and complex ops) to source lines.
        stack: list = []
        lines: list = []

        # Standard decompiler skip ops
        _SKIP = {"RESUME", "CACHE", "NOP", "NOT_TAKEN"}

        for ins in sub_instrs:
            op = ins.opname
            if op == "POP_TOP":
                if stack:
                    stmt = str(stack.pop())
                    # Only emit as a line if it's not a boring constant or sentinel
                    if stmt not in ("None", "_exc_info", "_exc_match", "True", "False"):
                        lines.append(stmt)
                continue

            if op in _IS_COMP_GEN_SKIP:
                continue

            # --- Loading ---
            if op in ("LOAD_CONST", "LOAD_NAME", "LOAD_FAST", "LOAD_GLOBAL",
                      "LOAD_SMALL_INT", "LOAD_GLOBAL_MODULE", "LOAD_DEREF"):
                if isinstance(ins.argval, types.CodeType):
                    stack.append(("code", ins.argval))
                else:
                    val = ins.argval
                    if op == "LOAD_GLOBAL_MODULE" and isinstance(val, (list, tuple)) and val:
                        val = val[0]
                    # Use repr for constants so strings keep their quotes
                    if op in ("LOAD_CONST", "LOAD_SMALL_INT"):
                        stack.append(repr(val))
                    else:
                        s = str(val)
                        if " + NULL" in s:
                            s = s.split(" + ")[0]
                        if "|" in s:
                            s = s.split("|")[0]
                        stack.append(s)
                continue

            if op in ("LOAD_ATTR", "LOAD_METHOD", "GET_ATTR"):
                obj = stack.pop() if stack else "obj"
                name = str(ins.argval).split(" + ")[0].split("|")[0]
                stack.append(f"{obj}.{name}")
                continue

            # --- Storing / Deleting ---
            if op in ("STORE_NAME", "STORE_FAST", "STORE_GLOBAL", "STORE_DEREF"):
                val = stack.pop() if stack else "None"
                target = str(ins.argval)
                lines.append(f"{target} = {val}")
                continue

            if op in ("DELETE_NAME", "DELETE_FAST", "DELETE_GLOBAL"):
                target = str(ins.argval)
                lines.append(f"del {target}")
                continue

            # --- Control flow (simple) ---
            if op in ("RETURN_VALUE", "RETURN_CONST"):
                if op == "RETURN_CONST":
                    val = repr(ins.argval)
                else:
                    val = stack.pop() if stack else "None"
                lines.append(f"return {val}")
                continue

            # --- Expression Building ---
            if op == "CALL":
                num = int(ins.arg or 0)
                args = []
                for _ in range(num):
                    args.insert(0, str(stack.pop()) if stack else "?")
                func = stack.pop() if stack else "?"
                # Clean func name from NULL/self markers
                s_func = str(func).split(" + ")[0].split("|")[0]
                stack.append(f"{s_func}({', '.join(args)})")
                continue

            if op == "BINARY_OP":
                rhs = stack.pop() if stack else "?"
                lhs = stack.pop() if stack else "?"
                op_sym = str(ins.argrepr) if ins.argrepr else "+"
                stack.append(f"({lhs} {op_sym} {rhs})")
                continue

            if op == "BUILD_LIST":
                num = int(ins.arg or 0)
                items = [str(stack.pop()) if stack else "?" for _ in range(num)]
                stack.append(f"[{', '.join(reversed(items))}]")
                continue

            if op == "BUILD_MAP":
                num = int(ins.arg or 0)
                pairs = []
                for _ in range(num):
                    v = stack.pop() if stack else "?"
                    k = stack.pop() if stack else "?"
                    pairs.insert(0, f"{k}: {v}")
                stack.append(f"{{ {', '.join(pairs)} }}")
                continue

            # --- Fallback ---
            # For truly complex or unknown ops, emit disassembly as a comment
            lines.append(f"# {ins.opname} {ins.argrepr or ''}")

        # Flush remaining stack items as statements
        for item in stack:
            s = str(item)
            if s not in ("None", "_exc_info", "_exc_match", "True", "False"):
                lines.append(s)

        return lines

    def _emit_deferred_finally(self, merge_offset: int, header_indent: int = -1) -> None:
        """
        Emit any pre-rendered except handlers followed by the deferred `finally:` block for a given finally-merge label.
        
        This writes lines into the decompiler's reconstructed output (via _append_reconstructed) and temporarily adjusts self.indent_level while emitting. It first emits any stored except-handler lines for the merge label, then emits a `finally:` header and its stored body lines if present. If no deferred handlers or finally body exist for the given merge label, nothing is emitted for that section.
        
        Parameters:
            merge_offset (int): The merge-label offset used as the key into the precomputed
                `_deferred_except_lines` and `_deferred_finally_lines` mappings.
            header_indent (int): The indentation level at which to emit `except:` / `finally:`
                headers. If negative, the method prefers `self._except_header_indent` when
                set (>= 0) and otherwise uses the current `self.indent_level`.
        """
        # Determine the header indent: prefer the explicitly passed value;
        # fall back to _except_header_indent if set; last resort: current level.
        if header_indent < 0:
            header_indent = self._except_header_indent if self._except_header_indent is not None \
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
        DecompilerGeneric.__init__(sub, self.code_obj, indent_level=0)
        # Install the same prescan results so sentinel detection works
        sub._try_nop_offsets = set()
        sub._suppress_push_exc_offsets = set()
        sub._finally_merge_offsets = set()
        sub._exc_handler_jump_offsets = getattr(self, "_exc_handler_jump_offsets", set())
        sub._with_exit_suppress_offsets = set()
        sub._finally_body_suppress = set()
        sub._handler_section_suppress = set()
        sub._wrapper_body_suppress = set()
        sub._push_exc_to_finally_merge = {}
        sub._deferred_finally_lines = {}
        sub._deferred_except_lines = {}
        sub._pending_finally_merge = None
        sub._nop_to_push_exc = {}
        sub._while_header_targets = {}
        sub._while_body_offsets = set()
        sub._while_true_ends = set()
        sub._ternary_jumps = {}
        sub._ternary_suppress = set()
        sub._compound_cond_map = {}
        sub._compound_suppress = set()
        sub._except_header_indent = 0
        sub._exc_bound_names = set()
        # Install only the handler instructions
        sub.instructions = handler_instrs
        sub.pc = 0

        # Run the handler instructions through the dispatcher
        while sub.pc < len(sub.instructions):
            instr = sub.instructions[sub.pc]
            # Close blocks whose end offset we have passed
            while sub.blocks and instr.offset >= sub.blocks[-1][0]:
                boff, btype = sub.blocks.pop()
                last_i = len(sub.reconstructed) - 1
                while last_i >= 0 and not sub.reconstructed[last_i].strip():
                    last_i -= 1
                if last_i >= 0 and sub.reconstructed[last_i].strip().endswith(":"):
                    sub._append_reconstructed("pass")
                sub.indent_level -= 1
            sub.pc += 1
            sub._handle_instruction(instr)

        return [line for line in sub.reconstructed if line or line == ""]

    # ------------------------------------------------------------------
    # Instruction dispatch
    # ------------------------------------------------------------------

    def _build_dispatch(self):
        self._dispatch = {
            # Loads
            "LOAD_CONST": self._op_load, "LOAD_NAME": self._op_load, "LOAD_FAST": self._op_load,
            "LOAD_GLOBAL": self._op_load, "LOAD_SMALL_INT": self._op_load, "LOAD_FAST_BORROW": self._op_load,
            "LOAD_CONST_BORROW": self._op_load, "LOAD_DEREF": self._op_load, 
            "LOAD_FAST_BORROW_LOAD_FAST_BORROW": self._op_load, "LOAD_GLOBAL_MODULE": self._op_load,
            
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
            "RERAISE": self._op_cleanup, "COPY": self._op_cleanup,
            "SETUP_FINALLY": self._op_setup_finally, "SETUP_EXCEPT": self._op_setup_finally,
            "SETUP_WITH": self._op_setup_with, "BEFORE_WITH": self._op_before_with,
            "WITH_EXCEPT_START": self._op_with_except_start, "BEGIN_FINALLY": self._op_with_except_start,
            
            # Functions
            "MAKE_FUNCTION": self._op_make_function, "SET_FUNCTION_ATTRIBUTE": self._op_set_function_attribute,
            
            # Returns
            "RETURN_VALUE": self._op_return_value, "RETURN_CONST": self._op_return_const,
            
            # Pops
            "POP_TOP": self._op_pop_top,
            
            # Load build class
            "LOAD_BUILD_CLASS": self._op_load_build_class,
            
            # Stack manip
            "ROT_TWO": self._op_stack_manip, "ROT_THREE": self._op_stack_manip, "ROT_FOUR": self._op_stack_manip,
            "DUP_TOP": self._op_dup_top, "DUP_TOP_TWO": self._op_dup_top,
            
            # Strings
            "FORMAT_VALUE": self._op_fstring, "FORMAT_SIMPLE": self._op_fstring,
            "BUILD_STRING": self._op_build_string,
            
            # Jumps
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
            
            # Iteration
            "FOR_ITER": self._op_for_iter,
            
            # Collections
            "BUILD_TUPLE": self._op_build_collection, "BUILD_LIST": self._op_build_collection,
            "BUILD_SET": self._op_build_collection, "BUILD_MAP": self._op_build_map,
            "BUILD_SLICE": self._op_build_slice,
            "BUILD_CONST_KEY_MAP": self._op_build_const_key_map,
            "GET_ITER": self._op_no_op, "UNPACK_SEQUENCE": self._op_no_op,
            "LIST_EXTEND": self._op_list_extend, "DICT_MERGE": self._op_dict_merge,
            "DICT_UPDATE": self._op_dict_merge,
            
            # Secondary loads
            "LOAD_ATTR": self._op_load_attr, "LOAD_METHOD": self._op_load_attr,
            "LOAD_SUPER_ATTR": self._op_load_super_attr,
            "LOAD_FROM_DICT_OR_GLOBALS": self._op_load_from_dict_or_globals,
            
            # Coroutines
            "YIELD_VALUE": self._op_yield_value,
            
            # Deletes
            "DELETE_NAME": self._op_delete, "DELETE_FAST": self._op_delete, "DELETE_GLOBAL": self._op_delete,
            
            # Error
            "LOAD_ASSERTION_ERROR": self._op_load_assertion_error,
            
            # Nops
            "PUSH_NULL": self._op_no_op, "RESUME": self._op_no_op, "PRECALL": self._op_no_op, "CACHE": self._op_no_op,
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
        # Suppress BEFORE_WITH __exit__(None,None,None) epilogue instructions
        if instr.offset in getattr(self, "_with_exit_suppress_offsets", ()):
            return
        # When we reach a finally-merge label, emit the pre-decompiled except
        # handlers + finally body in source order, then continue normally.
        # This check MUST come before _finally_body_suppress since the merge-label
        # instruction is the first instruction of the suppressed finally body range.
        if instr.offset in getattr(self, "_finally_merge_offsets", ()):
            self._finally_merge_offsets.discard(instr.offset)
            self._pending_finally_merge = None
            # self.indent_level at this point is the correct header indent:
            # the block manager has just closed the try_body block, leaving us
            # at the same level as the enclosing try:/with: statement.
            self._emit_deferred_finally(instr.offset, self.indent_level)
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
        opname = instr.opname
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

    def _op_binary_subscr(self, instr: BytecodeInstruction):
        opname = instr.opname
        if len(self.stack) >= 2:
            sub = self.stack.pop()
            container = self.stack.pop()
            self.stack.append(f"{container}[{sub}]")

        # ── exceptions ─────────────────────────────────────────────────

    def _op_raise_varargs(self, instr: BytecodeInstruction):
        opname = instr.opname
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
        self._except_header_indent = self.indent_level
        # Peek: is there a LOAD + CHECK_EXC_MATCH coming?
        look = self.pc
        while look < len(self.instructions) and self.instructions[look].opname in (
            "RESUME", "NOP", "CACHE", "NOT_TAKEN"
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


    def _op_check_exc_match(self, instr: BytecodeInstruction):
        opname = instr.opname
        exc_type = self.stack.pop() if self.stack else "Exception"
        if self.stack and str(self.stack[-1]) == "_exc_info":
            self.stack.pop()
        # Reset indent to the except-header level (handles multi-except chains
        # where the first handler incremented indent but the second check fires
        # without a new PUSH_EXC_INFO reset).
        if self._except_header_indent is not None:
            self.indent_level = self._except_header_indent
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
            if op in ("STORE_NAME", "STORE_FAST"):
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
        # POP_EXCEPT for try/except cleanup
        if opname == "POP_EXCEPT" and self._except_header_indent is not None:
            last_idx = len(self.reconstructed) - 1
            while last_idx >= 0 and not self.reconstructed[last_idx].strip():
                last_idx -= 1
            if last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":"):
                self._append_reconstructed("pass")
            self.indent_level = self._except_header_indent
            self._except_header_indent = None
        pass  # cleanup suppression (_exc_cleanup_name) stays active until DELETE_NAME fires


    def _op_setup_finally(self, instr: BytecodeInstruction):
        opname = instr.opname
        # Legacy 3.9/3.10 try/except via SETUP_* opcodes
        self._append_reconstructed("try:")
        self.indent_level += 1
        jump_target = self._get_jump_target(instr)
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
        if with_exc_offsets:
            # Use the last (highest-offset) PUSH_EXC_INFO — that is the one
            # that handles the __exit__ on exceptional exit from the with body.
            self.blocks.append((max(with_exc_offsets), "with"))
        # Also record the _except_header_indent so finally: de-indents correctly
        self._except_header_indent = self.indent_level - 1


    def _op_with_except_start(self, instr: BytecodeInstruction):
        opname = instr.opname
        pass

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
        closure  = str(self.stack.pop()) if (flags & 0x08) and self.stack else None
        annots   = str(self.stack.pop()) if (flags & 0x04) and self.stack else None
        kw_defs  = str(self.stack.pop()) if (flags & 0x02) and self.stack else None
        defaults = self.stack.pop()      if (flags & 0x01) and self.stack else None

        if code_obj_val is not None and isinstance(code_obj_val, tuple) and code_obj_val[0] == "code":
            inner_code = code_obj_val[1]
            positional = list(inner_code.co_varnames[: inner_code.co_argcount])

            # CO_VARARGS (0x04): function accepts *args — stored in
            # co_varnames[co_argcount] (after all positional params).
            _CO_VARARGS = 0x04
            _CO_VARKEYWORDS = 0x08
            has_varargs = bool(inner_code.co_flags & _CO_VARARGS)
            has_varkw   = bool(inner_code.co_flags & _CO_VARKEYWORDS)
            # Index of *args and **kwargs names in co_varnames:
            varargs_idx  = inner_code.co_argcount + inner_code.co_kwonlyargcount
            varkw_idx    = inner_code.co_argcount + inner_code.co_kwonlyargcount + int(has_varargs)
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
            dec = dec_class(inner_code, indent_level=1)
            body = dec.decompile()
            sig = f"def {inner_code.co_name}({', '.join(params)}):"
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
            # Use the new predicate for smarter suppression
            is_compiler_gen = (val_str == "None" and self.is_compiler_generated_return(self.pc - 1))
            
            if is_compiler_gen:
                pass
            elif "__class__" in val_str or "__classdict__" in val_str:
                pass
            else:
                self._append_reconstructed(f"return {val}")
        return


    def _op_return_const(self, instr: BytecodeInstruction):
        opname = instr.opname
        # RETURN_CONST None has two meanings:
        #   - Inside a 'while True:' (NOP-driven, unconditional) block: it's `break`
        #   - Everywhere else: compiler-generated exit sentinel — suppress if effectively last
        if instr.argval is None:
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
        self._append_reconstructed("return " + repr(val))

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

        # ── calls ──────────────────────────────────────────────────────

    def _op_call(self, instr: BytecodeInstruction):
        opname = instr.opname
        num_args = int(instr.arg) if instr.arg is not None else 0

        # keyword argument handling
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

        # ── Decorator pattern detection ───────────────────────────────
        # When MAKE_FUNCTION is immediately followed by CALL, the pattern is
        # either:
        # (a) a decorator application: @decorator def name(...): body
        #     Stack: [..., decorator_expr, ('func', 'def name(...):\n body')]
        # (b) a genexpr/lambda called immediately (already handled below)
        # Distinguish: decorator bodies have a plain function name (no angle
        # brackets like <genexpr>, <lambda>, <listcomp> etc.).
        if isinstance(func_val, tuple) and func_val[0] == "func":
            body_text = str(func_val[1])
            # Is this a named function (not a genexpr/lambda/comprehension)?
            first_line = body_text.strip().split("\n")[0] if body_text.strip() else ""
            if not _is_anonymous_func_body(first_line) and self.stack:
                decorator_expr = str(self.stack.pop())
                # Strip NULL sentinel if present
                if " + NULL" in decorator_expr or "|NULL" in decorator_expr:
                    decorator_expr = decorator_expr.split(" + ")[0].split("|")[0]
                # Emit as a decorated function definition
                deco_line = f"@{decorator_expr}"
                self.stack.append(("func", f"{deco_line}\n{body_text}"))
                return
            # Anonymous function or no decorator — genexpr/lambda handling
            rendered = _render_func_tuple(body_text, final_args)
            self.stack.append(rendered)
            return

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


    def _op_stack_manip(self, instr: BytecodeInstruction):
        opname = instr.opname
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


    def _op_dup_top(self, instr: BytecodeInstruction):
        opname = instr.opname
        if opname == "DUP_TOP" and self.stack:
            self.stack.append(self.stack[-1])
        elif opname == "DUP_TOP_TWO" and len(self.stack) >= 2:
            self.stack.extend([self.stack[-2], self.stack[-1]])

        # ── f-strings ──────────────────────────────────────────────────

    def _op_fstring(self, instr: BytecodeInstruction):
        opname = instr.opname
        if self.stack:
            val = self.stack.pop()
            self.stack.append(f"{{{val}}}")


    def _op_build_string(self, instr: BytecodeInstruction):
        opname = instr.opname
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

    def _op_jump(self, instr: BytecodeInstruction):
        opname = instr.opname
        jump_target = self._get_jump_target(instr)

        # Detect `break`: forward jump that lands at the exact end of a while loop.
        if instr.opname in ("JUMP_FORWARD", "JUMP_ABSOLUTE") and isinstance(jump_target, int) and jump_target > instr.offset:
            matched_while = False
            for b_off, b_type in reversed(self.blocks):
                if b_type == "while" and jump_target == b_off:
                    self._append_reconstructed("break")
                    matched_while = True
                    break
                if b_type == "while":
                    break  # only check innermost loop
            
            if matched_while:
                return
                
            # Non-break forward jump -> reconstruct as else-branch only when a
            # prior if-block whose target matches jump_target is actually open.
            # (Exception-handler jumps are always excluded.)
            if instr.offset not in getattr(self, "_exc_handler_jump_offsets", ()):
                # Find the most recent "if" block in self.blocks.
                prior_if_target = None
                for b_off, b_type in reversed(self.blocks):
                    if b_type == "if":
                        prior_if_target = b_off
                        break
                
                next_offset = -1
                if self.pc < len(self.instructions):
                    next_offset = self.instructions[self.pc].offset
                    
                # Emit else: when the next instruction is the target of the if block
                if prior_if_target is not None and prior_if_target == next_offset:
                    self.indent_level -= 1
                    self._append_reconstructed("else:")
                    self.indent_level += 1
                    for bi in range(len(self.blocks)-1, -1, -1):
                        if self.blocks[bi][1] == "if":
                            self.blocks.pop(bi)
                            break
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
            if instr.offset in getattr(self, "_exc_handler_jump_offsets", ()):
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
            for bi in range(len(self.blocks) - 1, -1, -1):
                boff, btype = self.blocks[bi]
                if btype == "if" and boff >= instr.offset:
                    for idx in range(len(self.reconstructed) - 1, -1, -1):
                        if self.reconstructed[idx].lstrip().startswith("if "):
                            self.reconstructed[idx] = self.reconstructed[idx].replace(
                                "if ", "while ", 1
                            )
                            self.blocks[bi] = (boff, "while")
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
                if next_pei < 0:
                    for ins in self.instructions:
                        if ins.opname == "PUSH_EXC_INFO" and ins.offset > instr.offset:
                            if ins.offset not in getattr(self, "_suppress_push_exc_offsets", ()):
                                next_pei = ins.offset
                                break
                # Only emit try: if not already inside a try block
                already_in_try = any(b[1] in ("try_body",) for b in self.blocks)
                if not already_in_try or instr.is_jump_target or instr.offset != 2:
                    self._append_reconstructed("try:")
                    self.indent_level += 1
                    if next_pei > 0:
                        # Use the finally-merge label as block end when present
                        pef_map = getattr(self, "_push_exc_to_finally_merge", {})
                        merge = pef_map.get(next_pei)
                        block_end = merge if (merge is not None and merge > instr.offset) else next_pei
                        self.blocks.append((block_end, "try_body"))
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


    def _op_conditional_jump(self, instr: BytecodeInstruction):
        opname = instr.opname
        # ── Ternary expression detection ──────────────────────────────
        # If _prescan_ternaries identified this jump as a ternary, evaluate
        # both branches speculatively and push the ternary expression onto
        # the stack instead of opening a control-flow block.

        if instr.offset in getattr(self, "_ternary_jumps", {}):
            store_name, then_instrs, else_instrs, is_true, aug_op = \
                self._ternary_jumps[instr.offset]
            cond_expr = str(self.stack.pop()) if self.stack else "?"
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
                if prev.opname in (
                    "RETURN_VALUE", "RETURN_CONST", "RAISE_VARARGS",
                    "BREAK_LOOP", "JUMP_FORWARD",
                ):
                    self.blocks.append((jump_target, "if"))
                    return

            self.blocks.append((jump_target, "if"))
    def _op_jump_if_not_exc_match(self, instr: BytecodeInstruction):
        # 3.9 / 3.10: JUMP_IF_NOT_EXC_MATCH(target)
        # Pops the exception type to match against and the exception instance.
        if len(self.stack) >= 2:
            exc_type = self.stack.pop()
            exc_instance = self.stack.pop()
            # Reconstruct "except {exc_type}:" header
            # Mirror Decompiler39._op_jump_if_not_exc_match behavior
            if self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup") and self.blocks[-1][0] == instr.offset:
                self.blocks.pop()
                self.indent_level -= 1
            if self._except_header_indent is not None:
                self.indent_level = self._except_header_indent
            self._append_reconstructed(f"except {exc_type}:")
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

        # For OR short-circuit: `a or b` compiles to:
        #   LOAD a
        #   JUMP_IF_TRUE_OR_POP target  # if a is true, skip loading b
        #   LOAD b
        #   target: ...
        # Semantics: if condition is true, KEEP it on stack and jump;
        # if false, POP it and continue (loading the next term).
        # We can't reconstruct the full 'or' chain here without pre-scan,
        # so just handle the stack manipulation correctly.

        # For now, treat as a short-circuit boolean that doesn't consume TOS on taken branch
        # The actual reconstruction of 'or' chains is handled by pre-scan in real code
        # Here we just ensure correct stack behavior: on branch taken, keep value; else pop

        # Since we can't know at runtime which path is taken, we model the "fall-through" case
        # (the most common reconstruction path) where the value is popped
        self.stack.pop()
        # Note: if the jump is taken in actual execution, the value would remain on stack

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

        # For AND short-circuit: `a and b` compiles to:
        #   LOAD a
        #   JUMP_IF_FALSE_OR_POP target  # if a is false, skip loading b
        #   LOAD b
        #   target: ...
        # Semantics: if condition is false, KEEP it on stack and jump;
        # if true, POP it and continue (loading the next term).

        # Model the fall-through case where value is popped
        self.stack.pop()
        # Note: if the jump is taken in actual execution, the value would remain on stack


    # ── for loop ───────────────────────────────────────────────────

    def _op_for_iter(self, instr: BytecodeInstruction):
        opname = instr.opname
        if self.stack:
            iterator = self.stack.pop()
            var_name = "_item"
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
                    if next_instr.opname in ("STORE_NAME", "STORE_FAST"):
                        var_name = str(next_instr.argval)
                    elif next_instr.opname == "UNPACK_SEQUENCE":
                        count = int(next_instr.arg) if next_instr.arg else 2
                        names = []
                        look = peek_pc + 1
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
        # Python 3.6+: BUILD_CONST_KEY_MAP(count)
        # Top of stack is a tuple of keys. Below that are 'count' values.
        count = instr.arg
        keys = self.stack.pop()
        
        values = []
        for _ in range(count):
            if self.stack:
                values.append(self.stack.pop())
            else:
                # Stack underflow: append None as placeholder
                values.append(None)
                if self.debug:
                    print(f"Warning: stack underflow in BUILD_CONST_KEY_MAP at offset {instr.offset}")
        values.reverse()
        
        # keys can be a tuple object (if loaded via LOAD_CONST) 
        # or a string representation (if reconstructed).
        if isinstance(keys, str) and keys.startswith("(") and keys.endswith(")"):
            # Very basic string-tuple parsing for reconstructed keys
            k_list = [k.strip().strip("'\"") for k in keys[1:-1].split(",") if k.strip()]
        elif isinstance(keys, tuple):
            k_list = list(keys)
        else:
            k_list = [f"key{i}" for i in range(count)]
            
        items = []
        for k, v in zip(k_list, values):
            k_repr = repr(k) if not isinstance(k, str) else f"'{k}'"
            items.append(f"{k_repr}: {v}")
        self.stack.append("{" + ", ".join(items) + "}")


    def _op_list_extend(self, instr: BytecodeInstruction):
        opname = instr.opname
        if len(self.stack) >= 2:
            it = str(self.stack.pop())
            lst = str(self.stack.pop())
            if lst == "[]":
                self.stack.append(f"[*{it}]" if it.startswith("(") else f"list({it})")
            else:
                self.stack.append(f"[*{lst}, *{it}]")


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
        opname = instr.opname
        obj = self.stack.pop() if self.stack else "obj"
        name = str(instr.argval)
        if " + " in name:
            name = name.split(" + ")[0]
        s_obj = str(obj).strip("'\"") if str(obj) in ("self", "cls") else str(obj)
        self.stack.append(f"{s_obj}.{name}")


    def _op_load_super_attr(self, instr: BytecodeInstruction):
        opname = instr.opname
        name = str(instr.argval)
        if " + " in name:
            name = name.split(" + ")[0]
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
    # Verified against CPython 3.9.13 opcode.h and dis output from real 3.9 bytecode.
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
    48: "RERAISE",              # real 3.9: RERAISE=48 (no-arg!), not 119
    49: "WITH_EXCEPT_START",   # real 3.9: WITH_EXCEPT_START=49, not 80
    50: "GET_AITER",
    51: "GET_ANEXT",
    52: "BEFORE_ASYNC_WITH",
    54: "END_ASYNC_FOR",
    # 55-89: confirmed from co_code bytes of real 3.9.13 bytecode
    55: "INPLACE_ADD",       # confirmed: augmented_assign co_code[8]=55
    56: "INPLACE_SUBTRACT",
    57: "INPLACE_MULTIPLY",
    59: "INPLACE_MODULO",
    60: "STORE_SUBSCR",      # confirmed: subscript_write co_code[14]=60
    61: "DELETE_SUBSCR",
    62: "BINARY_LSHIFT",     # confirmed: binary_ops co_code[32]=62
    63: "BINARY_RSHIFT",     # confirmed: binary_ops co_code[40]=63
    64: "BINARY_AND",        # confirmed: binary_ops co_code[8]=64
    65: "BINARY_XOR",        # confirmed: binary_ops co_code[24]=65
    66: "BINARY_OR",         # confirmed: binary_ops co_code[16]=66
    67: "INPLACE_POWER",
    68: "GET_ITER",          # confirmed: for_loop co_code[6]=68
    69: "GET_YIELD_FROM_ITER",
    70: "PRINT_EXPR",
    71: "LOAD_BUILD_CLASS",
    72: "YIELD_FROM",
    73: "GET_AWAITABLE",
    74: "LOAD_ASSERTION_ERROR",
    75: "INPLACE_LSHIFT",
    76: "INPLACE_RSHIFT",
    77: "INPLACE_AND",
    78: "INPLACE_XOR",
    79: "INPLACE_OR",
    82: "LIST_TO_TUPLE",     # real 3.9
    83: "RETURN_VALUE",
    84: "IMPORT_STAR",
    85: "SETUP_ANNOTATIONS",
    86: "YIELD_VALUE",
    87: "POP_BLOCK",
    89: "POP_EXCEPT",
    # ── arg opcodes (>= 90) ──────────────────────────────────────────────
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
    121: "JUMP_IF_NOT_EXC_MATCH",  # real 3.9: 121 (not 120!)
    122: "SETUP_FINALLY",          # real 3.9: 122 (not 121!)
    124: "LOAD_FAST",              # real 3.9: 124 (not 122!)
    125: "STORE_FAST",
    126: "DELETE_FAST",
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
    160: "LOAD_METHOD",
    161: "CALL_METHOD",
    162: "LIST_EXTEND",
    163: "SET_UPDATE",
    164: "DICT_MERGE",
    165: "DICT_UPDATE",
}


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
        
        Calls the superclass prescan then records non-exception SETUP_FINALLY targets in self._finally_targets and merges or creates self._finally_body_suppress to include instruction offsets that belong to the exception-path copy of a finally body. The method detects exception-handler entries (typed or bare), with-exit handlers, and tiny cleanup-only handlers (e.g., `LOAD_CONST None` followed by delete + `RERAISE`) and treats those as exception handlers rather than user `finally:` blocks; any SETUP_FINALLY target not classified as an exception handler is treated as a user `finally` and its exception-path copy is added to self._finally_body_suppress.
        
        Side effects:
        - Ensures self._finally_targets is a set of offsets that are finalizer entry targets.
        - Ensures self._finally_body_suppress exists and is extended with offsets to skip when emitting the exception-path copy of finally bodies.
        """
        super()._prescan_try_structure()
        # NOTE: do NOT reset self._finally_body_suppress here — super() may have
        # populated it via the generic deferred-finally mechanism for PUSH_EXC_INFO
        # patterns (even though 3.9 uses SETUP_FINALLY, the super call is harmless
        # and we must preserve whatever it set).
        self._finally_targets = set()
        # Merge with any suppress set from super(), don't overwrite it.
        if not hasattr(self, "_finally_body_suppress"):
            self._finally_body_suppress = set()

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
                    if op in ("DELETE_FAST", "DELETE_NAME", "DELETE_GLOBAL", "RERAISE"):
                        is_cleanup = True
                        break
                    elif op not in ("LOAD_CONST", "STORE_FAST", "STORE_NAME",
                                    "STORE_GLOBAL", "POP_BLOCK", "NOP"):
                        break
                if is_cleanup:
                    is_except = True   # treat as cleanup, not a user finally

            if not is_except:
                self._finally_targets.add(target)
                # Suppress the exception-path copy of the finally body.
                # Find the end of it (up to and including the RERAISE).
                end_reraise = None
                level = 0
                for j in range(t_idx, len(self.instructions)):
                    jx = self.instructions[j]
                    if jx.opname in ("SETUP_FINALLY", "SETUP_WITH", "SETUP_ASYNC_WITH"):
                        level += 1
                    elif jx.opname == "POP_BLOCK":
                        level -= 1
                    elif jx.opname == "RERAISE" and level <= 0:
                        end_reraise = jx.offset
                        break
                if end_reraise is not None:
                    for j in range(t_idx, len(self.instructions)):
                        self._finally_body_suppress.add(self.instructions[j].offset)
                        if self.instructions[j].offset == end_reraise:
                            break

    # clean instruction dispatch for 3.9-specific opcodes
    def _handle_instruction(self, instr: BytecodeInstruction):
        # Suppress exception-path finally bodies
        """
        Handle a single disassembled bytecode instruction and update the decompiler's reconstruction state.
        
        This method dispatches on the instruction's opname and performs the appropriate reconstruction actions for that opcode: it may push or pop expression fragments on the internal stack, append emitted source lines to self.reconstructed, open or close control-flow blocks, adjust self.indent_level, modify self.blocks and various suppression/scan tables, and advance or adjust self.pc. For opcodes not specialized here the handler delegates to the superclass implementation.
        
        Parameters:
            instr (BytecodeInstruction): The disassembled instruction to process (uses fields such as opname, arg, argval, offset, and is_jump_target).
        """
        # Suppress then-branch instructions of detected ternary expressions
        if instr.offset in getattr(self, "_ternary_suppress", ()):
            return
        # Suppress intermediate instructions belonging to a compound boolean
        if instr.offset in getattr(self, "_compound_suppress", ()):
            if self.stack:
                self.stack.pop()
            return
        if getattr(self, "_finally_body_suppress", set()) and instr.offset in self._finally_body_suppress:
            return
        
        opname = instr.opname

        # Scoped suppression: clear except-zone state when we exit the handler scope.
        if self._except_end_offset >= 0 and instr.offset >= self._except_end_offset:
            self._except_header_indent = None
            self._except_end_offset = -1

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
        _while_header_targets = getattr(self, "_while_header_targets", {})
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
            _wte = getattr(self, "_while_true_ends", set())
            _wte.add(loop_end)
            self._while_true_ends = _wte

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
                        self.stack.append(cond) # Put it back and let super() or generic handle it
            
            if (jump_target <= instr.offset  # backward target → inside while-True
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
            super()._handle_instruction(instr)
            return

        if opname in _bin39:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                self.stack.append(f"({left} {_bin39[opname]} {right})")

        elif opname in _inplace39:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left  = self.stack.pop()
                op    = _inplace39[opname]
                # Peek ahead: if next instruction is a STORE back to the same
                # name, emit `left op= right` directly and consume the STORE.
                next_pc = self.pc
                while (next_pc < len(self.instructions)
                       and self.instructions[next_pc].opname in (
                           "CACHE", "RESUME", "NOP", "NOT_TAKEN"
                       )):
                    next_pc += 1
                if (next_pc < len(self.instructions)
                        and self.instructions[next_pc].opname in (
                            "STORE_NAME", "STORE_FAST", "STORE_GLOBAL",
                        )):
                    store_name = str(self.instructions[next_pc].argval)
                    left_name  = str(left).split(".")[-1]
                    if store_name == left_name or str(left) == store_name or str(left).endswith("." + store_name):
                        self._append_reconstructed(f"{left} {op} {right}")
                        self.pc = next_pc + 1  # consume the STORE
                        return
                # Fallback: push as expression for STORE to handle
                self.stack.append(f"({left} {op} {right})")

        elif opname == "COMPARE_OP":
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                op = str(instr.argval)
                # No outer parens: tests expect 'if x > 0:' not 'if (x > 0):'
                self.stack.append(f"{left} {op} {right}")

        # CALL_FUNCTION: positional-only call (3.9)
        elif opname == "CALL_FUNCTION":
            num = int(instr.arg) if instr.arg is not None else 0
            # Pop args WITHOUT converting to str — preserve ('func',…) and
            # ('class',…) tuples so __build_class__ detection works correctly.
            raw_args = []
            for _ in range(num):
                if self.stack:
                    raw_args.insert(0, self.stack.pop())
            func_val = self.stack.pop() if self.stack else "func"

            # ── ('func', body) called directly: genexpr / lambda ─────────
            # Same logic as the generic CALL handler: when MAKE_FUNCTION pushes
            # a code object that is immediately called (generator expression,
            # lambda) the tuple is the function, not the function name.
            if isinstance(func_val, tuple) and func_val[0] == "func":
                str_args = [str(a) for a in raw_args]
                rendered = _render_func_tuple(str(func_val[1]), str_args)
                self.stack.append(rendered)
                return

            func = str(func_val)

            # Class builder: LOAD_BUILD_CLASS pushes '__build_class__', then
            # MAKE_FUNCTION pushes ('func', body_text), then LOAD_CONST 'ClassName',
            # then optional base-class LOADs, then CALL_FUNCTION N.
            if func == "__build_class__" and len(raw_args) >= 2:
                # raw_args[0] is the ('func', body_text) tuple from MAKE_FUNCTION
                # raw_args[1] is the class name string
                # raw_args[2:] are base classes
                body_val = raw_args[0]
                if isinstance(body_val, tuple) and body_val[0] == "func":
                    body_text = str(body_val[1])
                else:
                    body_text = str(body_val)
                cls_name = str(raw_args[1]).strip("'\"")
                bases = [str(b) for b in raw_args[2:]]
                bases_str = f"({', '.join(bases)})" if bases else ""
                lines = body_text.split("\n")
                if len(lines) > 1:
                    real_body = "\n".join(lines[1:])
                    self.stack.append(("class", f"class {cls_name}{bases_str}:\n{real_body}"))
                else:
                    self.stack.append(("class", f"class {cls_name}{bases_str}: pass"))
            else:
                # ── Decorator pattern: func(('func', body)) ───────────────
                # When a decorator is applied on 3.9, the pattern is:
                #   LOAD decorator_expr
                #   MAKE_FUNCTION -> pushes ('func', body)
                #   CALL_FUNCTION 1 -> func_val=decorator, raw_args=[('func', body)]
                # Detect: exactly one arg that is a ('func', body) tuple with a
                # named (non-anonymous) function body.
                if (len(raw_args) == 1
                        and isinstance(raw_args[0], tuple)
                        and raw_args[0][0] == "func"):
                    body_text = str(raw_args[0][1])
                    first_line = body_text.strip().split("\n")[0] if body_text.strip() else ""
                    if not _is_anonymous_func_body(first_line):
                        deco_line = f"@{func}"
                        self.stack.append(("func", f"{deco_line}\n{body_text}"))
                        return

                # Regular function call — convert args to strings now
                str_args = []
                for v in raw_args:
                    if isinstance(v, tuple) and len(v) >= 2 and v[0] in ("func", "class"):
                        str_args.append(str(v[1]))
                    else:
                        str_args.append(str(v))
                self.stack.append(f"{func}({', '.join(str_args)})")

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

        # POP_BLOCK: marks the clean exit from a try body in Python 3.9.
        # Close the try_body block and record the indent level for except headers.
        elif opname == "POP_BLOCK":
            is_finally_pop = False
            finally_target = None
            if self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup"):
                # Peek ahead: if the next meaningful instruction is JUMP_ABSOLUTE
                # targeting the enclosing while-end, this POP_BLOCK is the exit from
                # a try: block that contains a bare `break` statement.  Emit `break`
                # now (at the current try-body indent) BEFORE closing the block, then
                # suppress the redundant dead-code instructions that follow (compiler
                # generates an extra POP_BLOCK + JUMP_ABSOLUTE backward that is
                # unreachable).
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
                                _fb = getattr(self, "_finally_body_suppress", set())
                                _fb.add(self.instructions[sup].offset)
                                sup += 1
                                while (sup < len(self.instructions)
                                       and self.instructions[sup].opname in ("RESUME", "NOP", "CACHE")):
                                    sup += 1
                                if (sup < len(self.instructions)
                                        and self.instructions[sup].opname == "JUMP_ABSOLUTE"):
                                    _fb.add(self.instructions[sup].offset)
                                self._finally_body_suppress = _fb

                finally_target = self.blocks[-1][0]
                if getattr(self, "_finally_targets", set()) and finally_target in self._finally_targets:
                    is_finally_pop = True
                _closing_exc_cleanup = (self.blocks[-1][1] == "exc_cleanup")
                self.blocks.pop()
                # exc_cleanup blocks do not change indent (SETUP_FINALLY for exc_cleanup
                # tracks the boundary but does not increment indent_level).
                if not _closing_exc_cleanup:
                    self.indent_level -= 1

            # Second case: POP_BLOCK at the end of all except-handlers may find a
            # finally_wrapper block on top.  This POP_BLOCK signals the merge point
            # at which the finally body should be emitted.
            elif self.blocks and self.blocks[-1][1] == "finally_wrapper":
                finally_target = self.blocks[-1][0]
                is_finally_pop = True
                self.blocks.pop()
                # finally_wrapper never incremented indent, nothing to decrement here.

            # Record the indent level where except/finally headers should appear.
            # Only set when not already inside an except handler.
            if self._except_header_indent is None:
                self._except_header_indent = self.indent_level
                # Peek ahead for JUMP_FORWARD at end of try block to establish scope.
                look = self.pc
                while (look < len(self.instructions) and
                       self.instructions[look].opname in ("RESUME", "NOP", "CACHE")):
                    look += 1
                if look < len(self.instructions) and self.instructions[look].opname == "JUMP_FORWARD":
                    self._except_end_offset = self._get_jump_target(self.instructions[look])

            if is_finally_pop:
                # Determine the correct indent for finally: header.
                # For finally_wrapper blocks, we captured the indent at SETUP_FINALLY time
                # which is the right structural level regardless of what other blocks
                # have been pushed/popped since then.
                saved_indent = getattr(self, "_finally_wrapper_indent", {}).get(finally_target, -1)
                if saved_indent >= 0:
                    self.indent_level = saved_indent
                elif self._except_header_indent is not None:
                    self.indent_level = self._except_header_indent
                self._append_reconstructed("finally:")
                self.indent_level += 1
                self.blocks.append((finally_target, "finally_body"))
                self._except_header_indent = None

        # JUMP_ABSOLUTE: in 3.9 this is used both as:
        #   (a) a loop back-edge (target <= current offset) — treat as JUMP_BACKWARD
        #   (b) a forward jump at end of except/if body — treat as JUMP_FORWARD
        #   (c) break: forward jump to while-end from inside a try: block
        elif opname == "JUMP_ABSOLUTE":
            jump_target = self._get_jump_target(instr)
            if jump_target <= instr.offset:
                # Backward JUMP_ABSOLUTE: delegate to the backward-jump handler
                # in super(). _is_backward_instruction returns True for this case.
                super()._handle_instruction(instr)
            else:
                # Check for break: forward jump whose target matches the enclosing
                # while-loop end.  This must be tested BEFORE the _except_header_indent
                # suppression, because break inside try: still sets _except_header_indent
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
                if self._except_header_indent is not None:
                    pass  # skip else-detection inside try/except context
                else:
                    fwd_instr = BytecodeInstruction(
                        instr.opcode, "JUMP_FORWARD",
                        instr.arg, instr.argval,
                        instr.offset, instr.starts_line, instr.is_jump_target,
                    )
                    super()._handle_instruction(fwd_instr)

        # JUMP_FORWARD: in try/except context (after POP_BLOCK) suppress the
        # if/else detection logic in the parent handler — it would create spurious
        # else blocks around the exception handler body.
        elif opname == "JUMP_FORWARD":
            if self._except_header_indent is not None:
                pass  # suppress else-detection inside try/except context
            else:
                super()._handle_instruction(instr)

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
                exc_type = str(self.instructions[look].argval)
                look += 1
                # Follow any LOAD_ATTR chain for dotted exception names like socket.error
                while (look < len(self.instructions)
                       and self.instructions[look].opname == "LOAD_ATTR"):
                    exc_type = f"{exc_type}.{self.instructions[look].argval}"
                    look += 1
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
                                    "STORE_NAME", "STORE_FAST"
                                )):
                            look += 1  # skip this POP_TOP
                            break      # next iteration: handle STORE_NAME below
                        look += 1  # skip POP_TOP with no binding after it
                    # Now check for 'as e' STORE_NAME
                    if (look < len(self.instructions)
                            and self.instructions[look].opname in (
                                "STORE_NAME", "STORE_FAST"
                            )):
                        as_name = str(self.instructions[look].argval)
                        self._exc_as_store_offset = self.instructions[look].offset
                        self._exc_cleanup_name = as_name
                        if as_name:
                            self._exc_bound_names.add(as_name)
                        look += 1

                    # Close any still-open try_body block
                    if self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup") and self.blocks[-1][0] == instr.offset:
                        self.blocks.pop()
                        self.indent_level -= 1

                    # Reset indent to except-header level
                    if self._except_header_indent is not None:
                        self.indent_level = self._except_header_indent
                    else:
                        self._except_header_indent = self.indent_level

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
            if self._except_header_indent is not None or (
                self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup") and self.blocks[-1][0] == instr.offset
            ):
                if self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup") and self.blocks[-1][0] == instr.offset:
                    self.blocks.pop()
                    self.indent_level -= 1
                if self._except_header_indent is not None:
                    self.indent_level = self._except_header_indent
                else:
                    self._except_header_indent = self.indent_level
                self._append_reconstructed("except:")
                self.indent_level += 1
                self.stack.append("_exc_match")
                return
            # Not an exception-match pattern — real DUP_TOP: duplicate TOS
            if self.stack:
                self.stack.append(self.stack[-1])

        # JUMP_IF_NOT_EXC_MATCH: 3.9/3.10 typed-except gate.
        # This fires when DUP_TOP already consumed the LOAD+JUMP_IF_NOT_EXC_MATCH
        # sequence and advanced pc past it (pc = look after the gate).
        # If it arrives here, it means the DUP_TOP pattern didn't fire
        # (shouldn't happen in well-formed 3.9 bytecode, but handle gracefully).
        elif opname == "JUMP_IF_NOT_EXC_MATCH":
            exc_type = str(self.stack.pop()) if self.stack else "Exception"
            if self.stack:
                self.stack.pop()  # exc_instance
            if self.blocks and self.blocks[-1][1] in ("try_body", "exc_cleanup") and self.blocks[-1][0] == instr.offset:
                self.blocks.pop()
                self.indent_level -= 1
            if self._except_header_indent is not None:
                self.indent_level = self._except_header_indent
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
            if self._except_header_indent is not None:
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
                    elif jump_target in getattr(self, "_finally_targets", set()):
                        is_nested_handler = True

                if is_nested_handler:
                    # Real nested try: inside an except — emit try: normally.
                    # Clear _except_header_indent so the inner handler
                    # sets it fresh at the right (inner) indent level.
                    self._except_header_indent = None
                    super()._handle_instruction(instr)
                else:
                    # 'as e' cleanup guard — silent, track boundary without try:.
                    self.blocks.append((jump_target, "exc_cleanup"))
            elif jump_target in getattr(self, "_finally_targets", set()):
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
                    # finally: at the right level even if _except_header_indent
                    # has been cleared by then.
                    if not hasattr(self, "_finally_wrapper_indent"):
                        self._finally_wrapper_indent = {}
                    self._finally_wrapper_indent[jump_target] = self.indent_level
                    self.blocks.append((jump_target, "finally_wrapper"))
                else:
                    # Plain try/finally (no except) — emit try: normally.
                    # POP_BLOCK will see try_body with a finally_targets offset
                    # and emit finally:.
                    super()._handle_instruction(instr)
            else:
                super()._handle_instruction(instr)

        # POP_TOP at a handler jump-target: Python 3.9 bare except starts with
        # three consecutive POP_TOPs (exc_type, exc_value, traceback) instead of
        # DUP_TOP.  Detect when POP_TOP fires at the handler entry point.
        elif opname == "POP_TOP" and instr.is_jump_target and self._except_header_indent is not None:
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
            if self._except_header_indent is not None:
                self.indent_level = self._except_header_indent
            self._append_reconstructed("except:")
            self.indent_level += 1
            self.stack.append("_exc_match")

        # RERAISE (opcode 48 in 3.9, no-arg): re-raises the current exception.
        # In handlers this appears after a failed JUMP_IF_NOT_EXC_MATCH as the
        # fall-through re-raise.  Nothing to emit — the decompiler has already
        # reached this via the jump target path, so just skip silently.
        elif opname == "POP_EXCEPT":
            if self._except_header_indent is not None:
                last_idx = len(self.reconstructed) - 1
                while last_idx >= 0 and not self.reconstructed[last_idx].strip():
                    last_idx -= 1
                if last_idx >= 0 and self.reconstructed[last_idx].strip().endswith(":"):
                    self._append_reconstructed("pass")

                self.indent_level = self._except_header_indent
                self._except_header_indent = None
            if self.stack and self.stack[-1] == "_exc_match":
                self.stack.pop()
        elif opname == "RERAISE":
            pass  # already handled by the JUMP target structure; no source emission

        else:
            super()._handle_instruction(instr)



# ---------------------------------------------------------------------------
# Python 3.11+ specialisation  (3.11, 3.12, 3.13)
# ---------------------------------------------------------------------------

class Decompiler311Plus(DecompilerGeneric):

    def _handle_instruction(self, instr: BytecodeInstruction):
        # Suppress then-branch instructions of detected ternary expressions
        if instr.offset in getattr(self, "_ternary_suppress", ()):
            return
        # Suppress intermediate instructions belonging to a compound boolean
        if instr.offset in getattr(self, "_compound_suppress", ()):
            if self.stack:
                self.stack.pop()
            return

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
                           self.instructions[next_pc].opname in ("CACHE", "RESUME", "NOT_TAKEN")):
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
                    # Unknown BINARY_OP index — use ? as a safe placeholder
                    self.stack.append(f"({left} ? {right})")
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
# Python 3.14 stub
# ---------------------------------------------------------------------------

class Decompiler314(Decompiler311Plus):
    """
    Python 3.14+ specific decompiler logic.
    Handles the new LOAD_SPECIAL opcodes and structure for with statements.
    """
    def _handle_instruction(self, instr: BytecodeInstruction):
        """
        Dispatch a single disassembled instruction to the decompiler's reconstruction logic, including Python 3.14-specific handling for with-statement preambles and normal-path cleanup suppression.
        
        This method:
        - Recognizes and skips LOAD_SPECIAL opcodes used for with/async-with scaffolding unless part of a detected call sequence.
        - Detects a Python 3.14 with-preamble pattern (COPY 1 followed by a LOAD_SPECIAL/CALL sequence), emits a corresponding "with <ctx>" header, increments indentation, and pushes a with-body block; it may consume following STORE_/POP_TOP instructions and advance the program counter.
        - Records and suppresses normal-path __exit__ cleanup sequences by populating an internal _with_exit_suppress set so those instructions are ignored later.
        - Suppresses a common three-None CALL/POP_TOP cleanup pattern and removes any internal "_with_info" sentinel left on the stack.
        - Ensures any internal with-sentinel (`("_with_info", ...)`) is removed from the expression stack before emitting a return, while preserving the runtime return value.
        - Delegates all non-specialized or remaining instruction handling to the superclass implementation.
        
        Parameters:
            instr (BytecodeInstruction): The instruction to process; its opname, opcode, arg, argval, and offset are used to match patterns and drive stack/block changes.
        
        Side effects:
            May mutate self.pc, self.stack, self.blocks, self.indent_level, self.reconstructed, and may create or update self._with_exit_suppress. Delegates to super()._handle_instruction for further emission when appropriate.
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
        
        # Suppress normal-path __exit__ body calls (identified by range scan)
        if getattr(self, "_with_exit_suppress", set()) and instr.offset in self._with_exit_suppress:
            return

        # Handle Python 3.14 LOAD_SPECIAL (opcode 95)
        if instr.opcode == 95:
            # LOAD_SPECIAL is used in with/async-with preambles and cleanup.
            # We skip it primarily, unless it's part of a detected call.
            return

        # Python 3.14 with preamble starting at COPY 1
        if opname == "COPY" and instr.arg == 1:
            if self.pc + 4 < len(self.instructions):
                seq = self.instructions[self.pc : self.pc + 5]
                # Pattern: LOAD_SPECIAL 1 (__exit__), SWAP 2, SWAP 3, LOAD_SPECIAL 0 (__enter__), CALL 0
                if (seq[0].opcode == 95 and seq[0].arg == 1 and
                    seq[1].opname == "SWAP" and seq[1].arg == 2 and
                    seq[2].opname == "SWAP" and seq[2].arg == 3 and
                    seq[3].opcode == 95 and seq[3].arg == 0 and
                    seq[4].opname == "CALL" and seq[4].arg == 0):
                    
                    ctx_mgr = str(self.stack.pop()) if self.stack else "unknown_ctx"
                    # Push internal sentinel representing EX and CM that stay on stack
                    self.stack.append(("_with_info", ctx_mgr))

                    self.pc += 5
                    next_instr = self.instructions[self.pc] if self.pc < len(self.instructions) else None
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
                        curr_offset = self.instructions[self.pc].offset if self.pc < len(self.instructions) else getattr(instr, "offset", 0)
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
                                normal_ends = [e.end for e in entries if e.target == handler_target and e.end < handler_target]
                                if normal_ends:
                                    exit_body_start = max(normal_ends)
                    except (AttributeError, IndexError, TypeError, ValueError):
                        pass
                        
                    if try_end is not None:
                        self.blocks.append((try_end, "with_body"))
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
            # Check if this is the start of a 3x None sequence
            if self.pc + 3 < len(self.instructions):
                seq = self.instructions[self.pc : self.pc + 4]
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

        super()._handle_instruction(instr)


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
            # 3.4+ uses 15-bit digits stored in 2 bytes
            n_digits = abs(size)
            res = 0
            for i in range(n_digits):
                digit = struct.unpack("<H", self._read(2))[0]
                res += digit * (2**(15 * i))
            return -res if size < 0 else res
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

    # version-aware CodeType constructor
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

        # Python 3.11+ includes exceptiontable after lnotab in the marshal stream
        # For earlier versions, this field doesn't exist in the stream, so we default to b""
        # We need to peek at the Python version that created the .pyc to know if it's there
        # For now, we'll try to read it and default to b"" if it's not present or parsing fails
        exceptiontable = b""
        # The marshal format for 3.11+ code objects includes exceptiontable as the next field
        # We can detect this by checking if there's more data to read
        # However, since we're building a version-agnostic loader, we'll conditionally read it
        # based on whether we're running on 3.11+ (vi check below will handle the CodeType construction)
        try:
            # Attempt to read exceptiontable if present (3.11+ marshal format)
            # This is speculative - if the stream doesn't have it, we'll catch the exception
            if hasattr(types.CodeType, '__code__'):
                # Check if the host Python version expects exceptiontable
                test_code = (lambda: None).__code__
                if hasattr(test_code, 'co_exceptiontable'):
                    # Running on 3.11+, try to read exceptiontable from stream
                    exceptiontable = self.load()
                    if isinstance(exceptiontable, str):
                        exceptiontable = bytes(exceptiontable, 'latin1')
                    elif not isinstance(exceptiontable, bytes):
                        exceptiontable = bytes(exceptiontable) if exceptiontable else b""
        except:
            exceptiontable = b""

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

        code     = bytes(code, 'latin1') if isinstance(code, str) else bytes(code)
        consts   = to_tuple(consts)
        names    = to_tuple_strings(names)
        varnames = to_tuple_strings(varnames)
        freevars = to_tuple_strings(freevars)
        cellvars = to_tuple_strings(cellvars)
        lnotab   = bytes(lnotab, 'latin1') if isinstance(lnotab, str) else bytes(lnotab)
        filename = filename.decode("utf-8", "replace") if isinstance(filename, bytes) else str(filename)
        name     = name.decode("utf-8", "replace") if isinstance(name, bytes) else str(name)

        vi = sys.version_info

        # branch on the *host* Python's CodeType signature
        if vi >= (3, 11):
            # 3.11+: argcount, posonlyargcount, kwonlyargcount, nlocals,
            #        stacksize, flags, codestring, constants, names,
            #        varnames, filename, name, qualname, firstlineno,
            #        linetable, exceptiontable, freevars, cellvars
            
            # Map lnotab to linetable (with appropriate padding if needed)
            # 3.11+ expects an exceptiontable as well.
            return types.CodeType(
                argcount, posonlyargcount, kwonlyargcount, nlocals,
                stacksize, flags, code, consts, names, varnames,
                filename, name, name,       # qualname
                firstlineno, lnotab, exceptiontable,   # linetable, exceptiontable
                freevars, cellvars
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

# updated magic-number version ranges
# Python version   magic (& 0xFFFF) range  (approximate — patch releases vary
#                                           by a few units but stay in band)
# 3.9              3410 – 3429
# 3.10             3430 – 3449
# 3.11             3450 – 3494
# 3.12             3495 – 3530
# 3.13             3531 – 3559
# 3.14             3560+

def _get_python_version_from_magic(version_id: int) -> Optional[str]:
    """Return a Python version string (e.g., '3.12') corresponding to the given magic number."""
    if 3310 <= version_id <= 3349: return "3.4"
    if 3350 <= version_id <= 3378: return "3.5"
    if 3379 <= version_id <= 3393: return "3.6"
    if 3394 <= version_id <= 3412: return "3.7"
    if 3413 <= version_id <= 3413: return "3.8"
    if 3420 <= version_id <= 3429: return "3.9"
    if 3430 <= version_id <= 3449: return "3.10"
    if 3450 <= version_id <= 3494: return "3.11"
    if 3495 <= version_id <= 3530: return "3.12"
    if 3531 <= version_id <= 3559: return "3.13"
    if version_id >= 3560: return "3.14+"
    return None

def get_decompiler(filepath: str) -> DecompilerBase:
    with open(filepath, "rb") as f:
        all_data = f.read()

    if len(all_data) < 16:
        raise ValueError(f"Invalid .pyc file '{filepath}': too short (found {len(all_data)} bytes)")

    magic = struct.unpack("<I", all_data[0:4])[0]
    version_id = magic & 0xFFFF

    host_magic = int.from_bytes(importlib.util.MAGIC_NUMBER, "little")
    host_version_id = host_magic & 0xFFFF

    if magic != host_magic and not (3410 <= version_id <= 5000):
        # We don't recognize the magic or it's very old/corrupt
        input_ver = _get_python_version_from_magic(version_id)
        host_ver = _get_python_version_from_magic(host_magic & 0xFFFF)
        
        msg = f"Invalid or unsupported Python magic number: 0x{magic:08x} (version id: {version_id})."
        if input_ver:
            msg += f"\n- Input file appears to be from Python {input_ver}."
        else:
            msg += "\n- Input file version is unrecognized or may be corrupt."
            
        if host_ver:
            msg += f"\n- Current interpreter is Python {host_ver} (magic: 0x{host_magic:08x})."
        
        raise ValueError(msg)

    host_magic = int.from_bytes(importlib.util.MAGIC_NUMBER, "little")

    code_obj = None
    if magic == host_magic:
        for offset in (16, 12, 8, 4):
            try:
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
        input_ver = _get_python_version_from_magic(version_id)
        msg = f"Could not find valid marshal code object in .pyc file '{filepath}'."
        if input_ver:
            msg += f" (Inferred version: Python {input_ver})"
        raise ValueError(msg)

    # corrected dispatch table
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
    parser = argparse.ArgumentParser(
        description="A Python .pyc decompiler that reconstructs readable source code from compiled bytecode."
    )
    parser.add_argument("input", help="The .pyc file to decompile")
    parser.add_argument(
        "-o", "--output",
        help="Optional filename to save the decompiled output to. If not supplied, prints to screen."
    )
    
    args = parser.parse_args()
    
    try:
        decompiler = get_decompiler(args.input)
        output_text = decompiler.decompile()
        
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as f:
                    f.write(output_text)
                print(f"Decompiled output saved to {args.output}", file=sys.stderr)
            except OSError as e:
                print(f"Error: Failed to save decompiled output to {args.output}: {e}", file=sys.stderr)
                sys.exit(1)
        else:
            if output_text and output_text.strip():
                print(output_text)
            else:
                print(f"Warning: Decompiler returned no source code for {args.input}", file=sys.stderr)
                sys.exit(1)
            
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()