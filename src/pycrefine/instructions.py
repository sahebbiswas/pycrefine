"""Disassembled-instruction record and small rendering helpers."""
import ast
import math
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple, Type


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


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------


def _const_sort_key(value: Any) -> Tuple[Any, ...]:
    """
    Sort key giving code-object constants one fixed order, whatever mix of types
    a set holds: None, bool, numbers, str, bytes, complex, tuples, frozensets.

    Each key starts with a type rank, so values of different types never get
    compared with each other; numbers sort by value (NaN last) and strings and
    bytes naturally, which keeps small sets of the same type in the order a
    person would write them.
    """
    rank: int = 8
    payload: Any = repr(value)
    if value is None:
        rank, payload = 0, 0
    elif isinstance(value, bool):
        rank, payload = 1, int(value)
    elif isinstance(value, (int, float)):
        is_nan = isinstance(value, float) and math.isnan(value)
        # Equal numbers can still render differently (1 and 1.0 inside tuples), so the
        # repr breaks the tie and keeps the order independent of set iteration order.
        rank, payload = 2, ((1, repr(value)) if is_nan else (0, value, repr(value)))
    elif isinstance(value, str):
        rank, payload = 3, value
    elif isinstance(value, bytes):
        rank, payload = 4, value
    elif isinstance(value, complex):
        rank, payload = 5, repr(value)
    elif isinstance(value, tuple):
        rank, payload = 6, tuple(_const_sort_key(v) for v in value)
    elif isinstance(value, (frozenset, set)):
        rank, payload = 7, tuple(sorted(_const_sort_key(v) for v in value))
    return (rank, payload)


def _const_repr(value: Any) -> str:
    """
    ``repr()`` of a code-object constant with a deterministic spelling.

    A set or frozenset iterates in hash order, which changes with
    PYTHONHASHSEED (and, for None or other objects hashed by address, from run to
    run), so its plain ``repr()`` makes the decompiler's output unstable. Sets are
    rendered with their elements sorted (see _const_sort_key), including sets
    nested inside tuples. Every other constant is spelled exactly as ``repr()``.
    """
    if isinstance(value, (frozenset, set)):
        items = ", ".join(_const_repr(v) for v in sorted(value, key=_const_sort_key))
        if isinstance(value, frozenset):
            return f"frozenset({{{items}}})" if items else "frozenset()"
        return f"{{{items}}}" if items else "set()"
    if isinstance(value, tuple):
        items = ", ".join(_const_repr(v) for v in value)
        return f"({items},)" if len(value) == 1 else f"({items})"
    return repr(value)


# ---------------------------------------------------------------------------
# Finally-body copies
# ---------------------------------------------------------------------------

# Instructions that carry no meaning for comparing two compilations of the same code.
_COPY_SKIP_OPS = frozenset({"CACHE", "NOP", "NOT_TAKEN", "RESUME"})

# Superinstructions and variants (3.13+) that the optimizer applies to one copy of a
# finally body and not necessarily to another, spelled as the plain instructions
# they stand for.
_COPY_EXPANSIONS: Dict[str, Tuple[str, ...]] = {
    "LOAD_FAST_BORROW": ("LOAD_FAST",),
    "LOAD_FAST_CHECK": ("LOAD_FAST",),
    "LOAD_FAST_LOAD_FAST": ("LOAD_FAST", "LOAD_FAST"),
    "LOAD_FAST_BORROW_LOAD_FAST_BORROW": ("LOAD_FAST", "LOAD_FAST"),
    "STORE_FAST_LOAD_FAST": ("STORE_FAST", "LOAD_FAST"),
    "STORE_FAST_STORE_FAST": ("STORE_FAST", "STORE_FAST"),
}


def _copy_signature(instrs: List[BytecodeInstruction]) -> List[Tuple[str, Any]]:
    """
    Reduce *instrs* to ``(opname, operand)`` pairs that are equal for two compilations
    of the same source: optimizer variants are spelled as their plain forms, no-ops are
    dropped, and jump operands (absolute offsets that differ between copies) are blanked.
    """
    signature: List[Tuple[str, Any]] = []
    for ins in instrs:
        op = ins.opname
        if op in _COPY_SKIP_OPS:
            continue
        expansion = _COPY_EXPANSIONS.get(op)
        if expansion is not None:
            names = ins.argval if isinstance(ins.argval, (tuple, list)) else (ins.argval,)
            signature.extend(zip(expansion, names))
        elif op == "LOAD_SMALL_INT":
            signature.append(("LOAD_CONST", ins.arg))
        elif "JUMP" in op or op in ("FOR_ITER", "SEND"):
            signature.append((op, None))
        else:
            signature.append((op, ins.argval))
    return signature


def _match_finally_copy(instrs: List[BytecodeInstruction], start: int, template: List[Tuple[str, Any]]) -> Optional[int]:
    """
    Index just past the run of instructions that begins at ``instrs[start]`` and has the
    signature *template*, or None when the code there is something else.
    """
    done = 0
    idx = start
    while done < len(template):
        if idx >= len(instrs):
            return None
        piece = _copy_signature([instrs[idx]])
        if piece != template[done:done + len(piece)]:
            return None
        done += len(piece)
        idx += 1
    return idx


# ---------------------------------------------------------------------------
# Comprehension bodies
# ---------------------------------------------------------------------------

_COMPLEMENT_OPS: Dict[Type[ast.cmpop], Type[ast.cmpop]] = {
    ast.Is: ast.IsNot,
    ast.IsNot: ast.Is,
    ast.In: ast.NotIn,
    ast.NotIn: ast.In,
}


def _negate_node(node: ast.expr) -> ast.expr:
    """Return the logical negation of *node*, folding away a leading ``not``."""
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return node.operand
    if isinstance(node, ast.Compare) and len(node.ops) == 1:
        complement = _COMPLEMENT_OPS.get(type(node.ops[0]))
        if complement is not None:  # exact complements by language definition
            return ast.Compare(left=node.left, ops=[complement()], comparators=node.comparators)
    if isinstance(node, ast.BoolOp):
        # De Morgan. Only the truthiness of a filter matters, so this is exact here.
        flipped: ast.boolop = ast.Or() if isinstance(node.op, ast.And) else ast.And()
        return ast.BoolOp(op=flipped, values=[_negate_node(v) for v in node.values])
    return ast.UnaryOp(op=ast.Not(), operand=node)


def _negate_filter(cond: str) -> str:
    """Source text for ``not cond``, used when a comprehension filter is spelled ``if cond: continue``."""
    try:
        node = ast.parse(cond.strip(), mode="eval").body
    except SyntaxError:
        return f"not ({cond.strip()})"
    return ast.unparse(_negate_node(node))


@dataclass
class _BodyLine:
    """One line of a decompiled comprehension body with the lines nested under it."""
    text: str
    children: List["_BodyLine"] = field(default_factory=list)


def _parse_body_lines(body_text: str) -> List[_BodyLine]:
    """Arrange the lines of *body_text* into a tree by indentation."""
    roots: List[_BodyLine] = []
    stack: List[Tuple[int, _BodyLine]] = []
    for raw in body_text.splitlines():
        text = raw.strip()
        if not text:
            continue
        indent = len(raw) - len(raw.lstrip())
        node = _BodyLine(text)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        (stack[-1][1].children if stack else roots).append(node)
        stack.append((indent, node))
    return roots


def _only_continues(block: List[_BodyLine]) -> bool:
    """True when *block* does nothing except ``continue`` (``pass`` is ignored)."""
    return [n.text for n in block if n.text != "pass"] == ["continue"]


class _ComprehensionCollector:
    """
    Reads the loop body of a decompiled ``<genexpr>`` / ``<listcomp>`` / ... code
    object and gathers its ``for``/``if`` clauses and the yielded element.

    Besides the plain ``if cond: yield x`` shape, CPython 3.13+ (and some
    conditions on 3.12) compile a filter as a jump back to the loop head, which
    the decompiler writes as ``if cond: continue``. Such a line *rejects* the
    item, so the clause it stands for is the negation of ``cond``. A trailing
    ``else: continue`` is the same rejection for the other branch, so the ``if``
    keeps its own polarity.
    """

    def __init__(self, iterable: Optional[str]) -> None:
        self.iterable = iterable
        self.clauses: List[str] = []
        self.element: Optional[str] = None

    def collect(self, block: List[_BodyLine]) -> None:
        i = 0
        while i < len(block):
            node = block[i]
            i += 1
            following = block[i] if i < len(block) else None
            else_block: Optional[_BodyLine] = None
            if following is not None and following.text == "else:":
                else_block = following
                i += 1
            self._visit(node, else_block)

    def render(self, wrapper: Tuple[str, str]) -> Optional[str]:
        """The comprehension text, or None when no ``for`` clause and element were found."""
        if self.clauses and self.clauses[0].startswith("for ") and self.element is not None:
            return wrapper[0] + self.element + " " + " ".join(self.clauses) + wrapper[1]
        return None

    def _collect_else(self, else_block: Optional[_BodyLine]) -> None:
        """Look inside an ``else:`` block unless it only rejects the item."""
        if else_block is not None and not _only_continues(else_block.children):
            self.collect(else_block.children)

    def _visit(self, node: _BodyLine, else_block: Optional[_BodyLine]) -> None:
        if self._visit_loop(node) or self._visit_filter(node, else_block) or self._visit_leaf(node):
            return
        # Unrecognised compound statement (elif, try, ...): keep looking inside it.
        self.collect(node.children)
        self._collect_else(else_block)

    def _visit_loop(self, node: _BodyLine) -> bool:
        text = node.text
        if text in ("while True:", "while True"):
            # 3.9 wraps the whole genexpr body in 'while True:'.
            self.collect(node.children)
            return True
        if not text.startswith("for "):
            return False
        clause = text.rstrip(":")
        # Only the outermost for-clause iterates the implicit .0 parameter.
        if self.iterable is not None and not self.clauses:
            clause = clause.replace(".0", self.iterable)
        self.clauses.append(clause)
        self.collect(node.children)
        return True

    def _visit_filter(self, node: _BodyLine, else_block: Optional[_BodyLine]) -> bool:
        text = node.text
        # A real `while` cannot occur inside a comprehension: the decompiler writes
        # a filter whose false branch jumps back to the loop head that way.
        if text.startswith("while ") and text not in ("while True:", "while True"):
            cond = text[len("while "):].rstrip(":")
        elif text.startswith("if "):
            cond = text[len("if "):].rstrip(":")
        else:
            return False
        if else_block is None and _only_continues(node.children):
            self.clauses.append(f"if {_negate_filter(cond)}")
            return True
        self.clauses.append(f"if {cond}")
        self.collect(node.children)
        self._collect_else(else_block)
        return True

    def _visit_leaf(self, node: _BodyLine) -> bool:
        text = node.text
        if self.clauses and self.clauses[-1].startswith("for "):
            # Nested unpacking is decompiled as `for _item in it:` followed by
            # `(a, b), c = _item`; fold it back into the for-target.
            m_unpack = re.match(r"^(.+?) = (\w+)$", text)
            m_for = re.match(r"^for (\w+) in (.+)$", self.clauses[-1])
            if m_unpack and m_for and m_unpack.group(2) == m_for.group(1):
                self.clauses[-1] = f"for {m_unpack.group(1)} in {m_for.group(2)}"
                return True
        if text.startswith("yield "):
            self.element = text[len("yield "):].strip()
            return True
        return False


def _render_comprehension(body_text: str, args: List[str], wrapper: Tuple[str, str]) -> Optional[str]:
    """Render a decompiled comprehension body as ``(elt for x in it if cond)``; None if it has no loop and element."""
    roots = _parse_body_lines(body_text)
    if not roots:
        return None
    collector = _ComprehensionCollector(str(args[0]) if args else None)
    collector.collect(roots[0].children or roots[1:])
    return collector.render(wrapper)


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
            wrapper = ("[", "]")
        elif "<setcomp>" in lines[0] or "<dictcomp>" in lines[0]:
            wrapper = ("{", "}")
        else:  # genexpr
            wrapper = ("(", ")")
        rendered = _render_comprehension(body_text, args, wrapper)
        if rendered is not None:
            return rendered

    # ── Case 2: lambda ───────────────────────────────────────────────────
    if "<lambda>" in lines[0]:
        # Extract params from 'def <lambda>(params):'
        # (a default value can run over several lines, so the parameters end at the first `):` that
        # is followed by the body)
        m = re.match(r"^[ \t]*def\s+<lambda>\s*\((.*?)\):[ \t]*\n[ \t]*return\b", body_text.lstrip("\n"), flags=re.DOTALL)
        if m is None:
            m = re.match(r"^[ \t]*def\s+<lambda>\s*\((.*)\):\s*$", lines[0])
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
