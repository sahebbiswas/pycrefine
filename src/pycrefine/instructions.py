"""Disassembled-instruction record and small rendering helpers."""
import re
from dataclasses import dataclass
from typing import Any, List, Optional


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
    _comp_names = "<genexpr>", "<listcomp>", "<setcomp>", "<dictcomp>"
    if any(name in lines[0] for name in _comp_names):
        # Choose the correct bracket style for each comprehension type.
        if "<listcomp>" in lines[0]:
            wrapper_open, wrapper_close = "[", "]"
        elif "<setcomp>" in lines[0] or "<dictcomp>" in lines[0]:
            wrapper_open, wrapper_close = "{", "}"
        else:  # genexpr
            wrapper_open, wrapper_close = "(", ")"

        clauses: List[str] = []   # for/if clauses in source order
        yield_expr = None

        for line in lines[1:]:
            # Skip structural wrappers that appear in some versions:
            # 3.9 wraps the genexpr body in 'while True:' before the for loop.
            if line in ("while True:", "while True"):
                continue
            if line.startswith("for "):
                fc = line.rstrip(":")
                # Only the outermost for-clause iterates the implicit .0
                # parameter; replace it with the actual iterable arg.
                if args and not clauses:
                    # Strip a trailing "()" suffix that may have been added by a
                    # preceding CALL-0 misfire (GET_ITER treated as a no-arg call).
                    # Strip exactly one trailing "()" so expressions like
                    # range(10) are never mangled.
                    fc = fc.replace(".0", str(args[0]))
                clauses.append(fc)
            elif line.startswith("if "):
                clauses.append(line.rstrip(":"))
            elif clauses and clauses[-1].startswith("for "):
                # Nested unpacking is decompiled as `for _item in it:` followed
                # by `(a, b), c = _item`; fold it back into the for-target.
                m_unpack = re.match(r"^(.+?) = (\w+)$", line)
                m_for = re.match(r"^for (\w+) in (.+)$", clauses[-1])
                if m_unpack and m_for and m_unpack.group(2) == m_for.group(1):
                    clauses[-1] = f"for {m_unpack.group(1)} in {m_for.group(2)}"
                elif line.startswith("yield "):
                    yield_expr = line[6:].strip()
            elif line.startswith("yield "):
                yield_expr = line[6:].strip()

        if clauses and clauses[0].startswith("for ") and yield_expr is not None:
            return wrapper_open + yield_expr + " " + " ".join(clauses) + wrapper_close

    # ── Case 2: lambda ───────────────────────────────────────────────────
    if "<lambda>" in lines[0]:
        # Extract params from 'def <lambda>(params):'
        m = re.match(r"^[ \t]*def\s+<lambda>\s*\(([^)]*)\):", lines[0])
        params = m.group(1).strip() if m else ""
        # Find return expression reliably without truncating multiline strings
        m_ret = re.search(r"^[ \t]*return\s+(.*)", body_text, flags=re.MULTILINE | re.DOTALL)
        if m_ret:
            ret_expr = m_ret.group(1)
            # if ret_expr was wrapped in parentheses by earlier processing, we can optionally strip outer parens
            # but usually it's fine. Wait, `body_text.strip()` or `ret_expr.rstrip()` is needed.
            ret_expr = ret_expr.rstrip()
            expr = f"lambda {params}: {ret_expr}" if params else f"lambda: {ret_expr}"
            if args:
                return f"({expr})({', '.join(str(a) for a in args)})"
            return expr

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
