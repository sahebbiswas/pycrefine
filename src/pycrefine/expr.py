"""Expression helpers and stack sentinels used while rebuilding source text."""
import ast


def _unary_operand(operand) -> str:
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
    parts: list

    @classmethod
    def join(cls, op: str, left, right) -> "_BoolExpr":
        """Build `left op right`, merging operands that already use the same operator."""
        def operands(side):
            return list(side.parts) if isinstance(side, _BoolExpr) and side.op == op else [side]

        def wrap(side):
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
    def __init__(self, is_method=False):
        self.is_method = is_method
    def __repr__(self): return "__SENTINEL_NULL__"
    def __str__(self): return "__SENTINEL_NULL__"


class _CollectionAccumSentinel:
    """Internal sentinel placed on the stack to mark a decompiler-managed collection accumulator.

    Using a typed sentinel (rather than the raw string ``"_res"``) prevents false
    matches when user code happens to have a variable literally named ``_res``.
    ``str()`` of the sentinel returns the accumulator name that will appear in the
    reconstructed source, so downstream ``f"{target}.append(...)"`` calls still
    produce readable output.
    """
    def __init__(self, name: str = "_res"):
        self._name = name

    def __str__(self): return self._name
    def __repr__(self): return self._name
