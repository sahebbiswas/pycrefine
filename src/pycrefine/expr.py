"""Expression helpers and stack sentinels used while rebuilding source text."""
import ast
from typing import Any, List, Optional


def _unary_operand(operand: Any) -> str:
    """
    Return `operand` as source text that a unary operator can be applied to as a whole.

    Names, calls, subscripts and other atoms are used as they are; anything else (a
    conditional expression, a binary operation, ...) is parenthesised so that
    `-(a if flag else b)` does not turn into `-a if flag else b`.
    """
    text = str(operand)
    try:
        node = ast.parse(text, mode="eval").body
    except SyntaxError:
        return f"({text})"
    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
        return text
    if text.startswith("(") and text.endswith(")"):
        depth = 0
        for pos, ch in enumerate(text):
            depth += (ch == "(") - (ch == ")")
            if depth == 0:
                if pos == len(text) - 1:
                    return text   # already one parenthesised group
                break
    if isinstance(node, (ast.List, ast.Set, ast.Dict, ast.ListComp, ast.SetComp,
                         ast.DictComp, ast.GeneratorExp, ast.Tuple)) and text[:1] in "([{":
        return text
    if isinstance(node, ast.Constant) and not (isinstance(node.value, (int, float, complex))
                                               and text.startswith("-")):
        return text
    return f"({text})"


class _BoolExpr(str):
    """A parenthesised `and`/`or` expression that remembers its operator and operands, so chains flatten."""

    op: str
    parts: List[Any]

    @classmethod
    def join(cls, op: str, left: Any, right: Any) -> "_BoolExpr":  # type: ignore[override]
        """Build `left op right`, merging operands that already use the same operator."""
        def operands(side: Any) -> List[Any]:
            return list(side.parts) if isinstance(side, _BoolExpr) and side.op == op else [side]

        def wrap(side: Any) -> str:
            text = str(side)
            if isinstance(side, _BoolExpr):
                return text
            try:
                node = ast.parse(text, mode="eval").body
            except SyntaxError:
                return f"({text})"
            if isinstance(node, (ast.IfExp, ast.Lambda, ast.NamedExpr, ast.Yield, ast.YieldFrom, ast.Await)):
                return f"({text})"
            return text

        parts = operands(left) + operands(right)
        obj = cls("(" + f" {op} ".join(wrap(p) for p in parts) + ")")
        obj.op = op
        obj.parts = parts
        return obj


# ---------------------------------------------------------------------------
class StackNULL:
    """Internal sentinel pushed onto the stack to represent Python 3.11+ NULL sentinels in method calls."""
    def __init__(self, is_method: bool = False) -> None:
        self.is_method = is_method
    def __repr__(self) -> str: return "__SENTINEL_NULL__"
    def __str__(self) -> str: return "__SENTINEL_NULL__"


class _CollectionAccumSentinel:
    """Internal sentinel placed on the stack to mark a decompiler-managed collection accumulator.

    Using a typed sentinel (rather than the raw string ``"_res"``) prevents false
    matches when user code happens to have a variable literally named ``_res``.
    ``str()`` of the sentinel returns the accumulator name that will appear in the
    reconstructed source, so downstream ``f"{target}.append(...)"`` calls still
    produce readable output.
    """
    def __init__(self, name: str = "_res") -> None:
        self._name = name

    def __str__(self) -> str: return self._name
    def __repr__(self) -> str: return self._name


class _UnpackAccessor(str):
    """The value one target of a fallback unpacking will receive, as source text.

    When an unpacking cannot be written as a single target, the decompiler leaves one such
    value per target on the stack for the stores that follow. Being a ``str`` keeps it
    usable wherever stack text is expected; the type lets a store that fuses two targets
    (3.13+'s ``STORE_FAST_STORE_FAST``) tell these per-target values from a single tuple
    expression that holds all of them.
    """


class _MergedDisplay(str):
    """A dict display built by ``{k: v, **d}`` (``DICT_UPDATE``), as source text.

    Passing such a display on with ``**`` is not the same as writing its entries as keyword
    arguments: the display silently lets a later entry replace an earlier one, while a call
    with a repeated keyword raises ``TypeError``. The type lets the merge that follows keep
    the display as ``**{...}`` rather than spread its entries into the call.
    """


class _FStringField(str):
    """One replacement field of an f-string (``{x!r:>5}``), as the source text of the whole f-string.

    A field is built by ``FORMAT_VALUE`` (3.9 to 3.12) or ``FORMAT_SIMPLE`` / ``FORMAT_WITH_SPEC``
    (3.13 and later) before anything knows whether ``BUILD_STRING`` will join it with other
    pieces. When nothing does (``f"{x!r}"``, a lone field), the field is the whole string, so its
    text is the complete f-string and any consumer gets valid source. ``BUILD_STRING`` and a
    nested format spec read :attr:`piece`, the bare ``{...}`` text, instead.
    """

    piece: str

    def __new__(cls, text: str, piece: str) -> "_FStringField":
        self = super().__new__(cls, text)
        self.piece = piece
        return self


class _FString(str):
    """An f-string built by ``BUILD_STRING``, as source text, remembering the text between its quotes in :attr:`body`."""

    body: str

    def __new__(cls, text: str, body: str) -> "_FString":
        self = super().__new__(cls, text)
        self.body = body
        return self


class _ConvertedValue(str):
    """The value a 3.13+ ``CONVERT_VALUE`` converts, waiting for the ``FORMAT_*`` that writes the field.

    The text is the value itself; :attr:`conversion` is ``!s``, ``!r`` or ``!a``.
    """

    conversion: str

    def __new__(cls, text: str, conversion: str) -> "_ConvertedValue":
        self = super().__new__(cls, text)
        self.conversion = conversion
        return self


class UnknownExpression(str):
    """
    An expression the decompiler could not determine, standing in for it on the stack.

    It is a ``str`` so the code that builds source text keeps working, and it renders as
    ``?`` (or the given *text*), but it is its own type: code that needs to know whether a
    value is real asks :func:`is_unknown` instead of comparing with a magic string. It
    carries *reason* and the instruction (*opcode*, *offset*) it came from.
    """

    reason: str
    opcode: Optional[str]
    offset: Optional[int]

    def __new__(cls, reason: str, opcode: Optional[str] = None, offset: Optional[int] = None,
                text: str = "?") -> "UnknownExpression":
        self = super().__new__(cls, text)
        self.reason = reason
        self.opcode = opcode
        self.offset = offset
        return self

    def __repr__(self) -> str:
        where = f" at {self.opcode}" + (f" (offset {self.offset})" if self.offset is not None else "") if self.opcode else ""
        return f"UnknownExpression({self.reason!r}{where})"


def is_unknown(value: Any) -> bool:
    """True if *value* is a placeholder for an expression that could not be determined."""
    return isinstance(value, UnknownExpression)


class _ChainedValue(str):
    """
    A value that DUP_TOP / COPY left on the stack twice, so that several stores take it: a chained
    assignment ``a = b = f()`` computes ``f()`` once. ``row`` is the index of the output row the
    first store wrote, which the next store takes over as another target of the same statement.
    """
    row: Optional[int] = None
    alias: Optional[str] = None   # a temporary that already holds the value, for stores that cannot join
