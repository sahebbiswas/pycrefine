"""Text-level post-processing of decompiled source (imports, parentheses, elif flattening)."""
import ast
import io
import re
import tokenize
from typing import Callable, FrozenSet, List, NamedTuple, Optional, Set, Tuple


def _line_is_in_triple_quoted_string(lines: List[str], line_idx: int) -> bool:
    """
    Return True when *line_idx* (0-based) falls inside an open triple-quoted
    string literal in the source formed by ``lines[:line_idx]``.

    We use ``tokenize.generate_tokens`` so that triple-quote character sequences
    that happen to appear inside ordinary single-line strings or inside comments
    are correctly ignored — the naive substring-scan approach would miscount
    those and flip the open/closed state erroneously.

    The algorithm:
    1. Join ``lines[:line_idx]`` into a fake source block and run the tokenizer.
    2. For every STRING token, check whether its ``string_prefix`` is a
       triple-quote opener (``\"\"\"`` / ``'''``).
    3. A triple-quoted STRING whose *end row* equals ``line_idx + 1`` (i.e. the
       token closes on the very line we are testing) still means that line is
       *inside* the literal.  A token that started before ``line_idx + 1`` but
       whose end row is *after* ``line_idx + 1`` means the string overruns the
       current line, so the current line is definitely inside it.
    4. If the tokenizer raises (e.g. because the source up to ``lines[:line_idx]``
       ends in the middle of an unterminated triple-quoted string) we catch the
       ``tokenize.TokenError`` and return True — an unterminated string means we
       are currently inside one.
    """
    if line_idx == 0:
        return False

    source_fragment = "\n".join(lines[:line_idx]) + "\n"
    reader = io.StringIO(source_fragment).readline
    _TRIPLE_PREFIXES = ('"""', "'''", 'r"""', "r'''", 'b"""', "b'''",
                        'rb"""', "rb'''", 'br"""', "br'''",
                        'f"""', "f'''", 'rf"""', "rf'''", 'fr"""', "fr'''",
                        'u"""', "u'''",
                        'R"""', "R'''", 'B"""', "B'''",
                        'RB"""', "RB'''", 'BR"""', "BR'''",
                        'F"""', "F'''", 'RF"""', "RF'''", 'FR"""', "FR'''")
    try:
        for tok_type, tok_str, tok_start, tok_end, _ in tokenize.generate_tokens(reader):
            if tok_type == tokenize.STRING:
                # tok_str is the full token text, e.g. '"""hello\nworld"""'
                is_triple = any(tok_str.startswith(p) for p in _TRIPLE_PREFIXES)
                if is_triple:
                    # tok_start/tok_end are (row, col), 1-based rows
                    start_row, _ = tok_start
                    end_row, _ = tok_end
                    # The token spans lines [start_row .. end_row] (1-based).
                    # line_idx is 0-based, so the line we are checking is
                    # row (line_idx + 1) in 1-based terms.  If that row falls
                    # within [start_row, end_row] the line is inside the literal.
                    target_row = line_idx + 1
                    if start_row <= target_row <= end_row:
                        return True
    except tokenize.TokenError:
        # An unclosed triple-quoted string causes TokenError; treat as in-triple.
        return True
    return False


def _block_opener_keyword(lines: List[str], tail_idx: int, base_indent_len: int) -> Optional[str]:
    """Return the leading keyword of the block header whose *closing* line is at
    ``tail_idx`` (indent == ``base_indent_len``, content ends with ``:``).

    For a single-line header (``if cond:``) the check is immediate.  For a
    multi-line header (``if (\n    cond\n):``) the function counts unmatched
    closing brackets going backward until the running total reaches zero; that
    line is the opener.  Returns ``None`` when the opener cannot be found.
    """
    _KW = "if ", "elif ", "for ", "while ", "with ", "try:", "except", "finally:"
    tail_stripped = lines[tail_idx].strip()
    for kw in _KW:
        if tail_stripped.startswith(kw):
            return kw.rstrip(" :")
    # Multi-line: count unmatched closing brackets walking backward.
    unmatched = 0
    for p in range(tail_idx, -1, -1):
        ps = lines[p].strip()
        if not ps:
            continue
        pi = len(lines[p]) - len(lines[p].lstrip())
        unmatched += ps.count(')') - ps.count('(')
        unmatched += ps.count(']') - ps.count('[')
        unmatched += ps.count('}') - ps.count('{')
        if pi == base_indent_len and unmatched <= 0:
            for kw in _KW:
                if ps.startswith(kw):
                    return kw.rstrip(" :")
            return None
    return None


def _collect_multiline_header(lines: List[str], start_idx: int, header_indent_len: int):
    """Return ``(end_idx, condition)`` for the ``if``/``elif`` header starting
    at ``start_idx``.

    *condition* is the text after the leading keyword (``if ``/``elif ``) up to
    and **including** the trailing ``:`` — ready to be emitted as
    ``f"{indent}elif {condition}"``.

    For a single-line header this is a fast no-scan return.  For a multi-line
    header (e.g. ``if (\n    very_long_cond\n):``) the continuation lines are
    joined with a single space, producing a valid single-line ``elif`` that is
    semantically identical.
    """
    first_stripped = lines[start_idx].lstrip()
    kw_len = 3 if first_stripped.startswith("if ") else 5  # "if " vs "elif "
    if lines[start_idx].rstrip().endswith(':'):
        return start_idx, first_stripped[kw_len:].rstrip()
    parts = [first_stripped[kw_len:].rstrip()]
    for hj in range(start_idx + 1, len(lines)):
        hs = lines[hj].strip()
        if not hs:
            continue
        hi = len(lines[hj]) - len(lines[hj].lstrip())
        parts.append(hs)
        if hi == header_indent_len and hs.endswith(':'):
            return hj, " ".join(parts)
    return start_idx, first_stripped[kw_len:].rstrip()


def collapse_chained_comparisons(e1: str, e2: str, b_level: str) -> Optional[str]:
    """
    Attempt to collapse implicit chained conditions (e.g. `a < b` and `b < c`)
    into explicit python shorthand: `a < b < c`.
    """
    if b_level not in ('core', 'aggressive'):
        return None
    ops = [' <= ', ' >= ', ' < ', ' > ', ' == ', ' != ', ' in ', ' not in ', ' is ', ' is not ']
    e2_left = None
    e2_remainder = None
    depth = 0
    for idx, char in enumerate(e2):
        if char == '(':
            depth += 1
        elif char == ')':
            depth -= 1
        elif depth == 0:
            for op in ops:
                if e2.startswith(op, idx):
                    e2_left = e2[:idx]
                    e2_remainder = e2[idx:]
                    break
            if e2_left is not None:
                break
    if not e2_left:
        return None
    if e1.endswith(e2_left):
        e1_prefix = e1[:-len(e2_left)]
        if any(e1_prefix.endswith(op) for op in ops):
            return e1_prefix + e2_left + e2_remainder
    return None


def flatten_elif(source: str) -> str:
    lines = source.split('\n')
    changed = False

    out_lines = []
    i = 0
    while i < len(lines):
        line = lines[i]
        stripped = line.lstrip()
        if stripped == "else:":
            base_indent_len = len(line) - len(stripped)
            base_indent = line[:base_indent_len]

            # Guard 1: skip if inside a triple-quoted string.
            # Use tokenize to inspect the real token stream for lines[:i] so
            # that triple-quote sequences that appear inside normal single-line
            # strings or comments do not flip the state incorrectly.
            in_triple = _line_is_in_triple_quoted_string(lines, i)
            if in_triple:
                out_lines.append(line)
                i += 1
                continue

            # Guard 2: verify the controlling header above this `else:` is an
            # `if` or `elif` at exactly base_indent_len (not `for`, `try`, etc.).
            # Multi-line headers (e.g. `if (\n    cond\n):`) are handled by
            # _block_opener_keyword which walks back with bracket-depth counting.
            parent_ok = False
            for p in range(i - 1, -1, -1):
                prev_line = lines[p]
                prev_stripped = prev_line.strip()
                if not prev_stripped or prev_stripped.startswith('#'):
                    continue
                prev_indent_len = len(prev_line) - len(prev_line.lstrip())
                if prev_indent_len <= base_indent_len:
                    if prev_indent_len == base_indent_len:
                        if prev_stripped.startswith("if ") or prev_stripped.startswith("elif "):
                            parent_ok = True
                        elif prev_stripped.endswith(':'):
                            # Closing line of a multi-line header — find the opener.
                            kw = _block_opener_keyword(lines, p, base_indent_len)
                            parent_ok = kw in ("if", "elif")
                    break
                # Deeper indent — keep scanning upward.
            if not parent_ok:
                out_lines.append(line)
                i += 1
                continue

            j = i + 1
            while j < len(lines) and not lines[j].strip():
                j += 1

            if j < len(lines) and lines[j].lstrip().startswith("if "):
                if_line = lines[j]
                if_indent_len = len(if_line) - len(if_line.lstrip())
                if if_indent_len == base_indent_len + 4:
                    # Collect the full (possibly multi-line) header so that
                    # conditions like `if (\n    cond\n):` are not truncated.
                    header_end, condition = _collect_multiline_header(
                        lines, j, if_indent_len
                    )
                    is_only_if = True
                    k = header_end + 1
                    while k < len(lines):
                        next_line = lines[k]
                        next_stripped = next_line.strip()
                        if not next_stripped:
                            k += 1
                            continue

                        next_indent_len = len(next_line) - len(next_line.lstrip())
                        if next_indent_len <= base_indent_len:
                            break

                        if next_indent_len == if_indent_len:
                            if not (next_stripped.startswith("elif ") or next_stripped.startswith("else:")):
                                is_only_if = False
                                break
                        elif next_indent_len < if_indent_len:
                            is_only_if = False
                            break

                        k += 1

                    if is_only_if:
                        out_lines.append(f"{base_indent}elif {condition}")

                        for idx in range(i + 1, j):
                            out_lines.append(lines[idx])

                        for idx in range(header_end + 1, k):
                            if lines[idx].strip():
                                if lines[idx].startswith(base_indent + "    "):
                                    out_lines.append(lines[idx][:base_indent_len] + lines[idx][base_indent_len + 4:])
                                else:
                                    out_lines.append(lines[idx])
                            else:
                                out_lines.append(lines[idx])

                        i = k
                        changed = True
                        continue
        out_lines.append(line)
        i += 1

    res = '\n'.join(out_lines)
    if changed:
        return flatten_elif(res)
    return res


_MULTILINE_STRING_MARK = "__pycrefine_multiline_string_"


def _string_continuation_rows(text: str) -> Set[int]:
    """
    0-based rows of *text* that continue a multi-line string begun on an earlier row.

    Those rows are part of the string's value (a docstring's later lines, a triple-quoted
    literal), so nothing may be added to their start. Returns an empty set when *text* does
    not tokenize.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return set()
    rows: Set[int] = set()
    fstring_starts: List[int] = []
    for tok in tokens:
        name = tokenize.tok_name.get(tok.type, "")
        if name == "FSTRING_START":  # 3.12+ tokenizes an f-string in pieces
            fstring_starts.append(tok.start[0])
        elif name == "FSTRING_END" and fstring_starts:
            rows.update(range(fstring_starts.pop(), tok.end[0]))
        elif tok.type == tokenize.STRING and tok.end[0] > tok.start[0]:
            rows.update(range(tok.start[0], tok.end[0]))
    return rows


class _Literals(List[str]):
    """The literals taken out of a source, and the placeholder prefix that cannot occur in it."""

    def __init__(self, source: str = "") -> None:
        super().__init__()
        self.prefix = _MULTILINE_STRING_MARK
        while self.prefix in source:
            self.prefix += "x"

    def mark(self, index: int) -> str:
        return f'"{self.prefix}{index}__"'


def _string_spans(tokens: list) -> list:
    """(first token index, last token index) of each multi-line string literal, f-strings (3.12+ tokenizes them in pieces) included."""
    spans, fstarts = [], []
    for n, tok in enumerate(tokens):
        name = tokenize.tok_name.get(tok.type, "")
        if name == "FSTRING_START":
            fstarts.append(n)
        elif name == "FSTRING_END" and fstarts:
            first = fstarts.pop()
            if tokens[first].start[0] != tok.end[0] and not fstarts:
                spans.append((first, n))
        elif tok.type == tokenize.STRING and tok.start[0] != tok.end[0] and not fstarts:
            spans.append((n, n))
    return spans


def _in_print_call(tokens: list, index: int) -> bool:
    """Whether the token at ``index`` lies inside the parentheses of a ``print(`` call."""
    stack: List[bool] = []
    for n in range(index):
        tok = tokens[n]
        if tok.type == tokenize.OP and tok.string in "([{":
            stack.append(tok.string == "(" and n > 0 and tokens[n - 1].type == tokenize.NAME
                         and tokens[n - 1].string == "print")
        elif tok.type == tokenize.OP and tok.string in ")]}" and stack:
            stack.pop()
    return any(stack)


def _mask_multiline_strings(source: str, keep_print_arguments: bool = False, literals: Optional[_Literals] = None):
    """
    Replace each multi-line string with a one-line placeholder.

    The clean-ups are line- and regex-based; inside such a string they would change its value (spacing
    after commas, collapsed blank lines, merged imports). The strings inside a ``print(...)`` call are
    kept with ``keep_print_arguments``: the `print` tidying reads them. Returns the masked source and
    the literals in order (``literals``, if given, is extended and returned).
    """
    literals = _Literals(source) if literals is None else literals
    try:
        tokens = [t for t in tokenize.generate_tokens(io.StringIO(source).readline)
                  if t.type != tokenize.COMMENT]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source, literals
    lines = source.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    pieces, pos = [], 0
    for first, last in _string_spans(tokens):
        if keep_print_arguments and _in_print_call(tokens, first):
            continue
        start = offsets[tokens[first].start[0] - 1] + tokens[first].start[1]
        end = offsets[tokens[last].end[0] - 1] + tokens[last].end[1]
        pieces.append(source[pos:start])
        pieces.append(literals.mark(len(literals)))
        literals.append(source[start:end])
        pos = end
    pieces.append(source[pos:])
    return "".join(pieces), literals


def _unmask_multiline_strings(source: str, literals: "_Literals") -> str:
    """Put back the literals that :func:`_mask_multiline_strings` replaced, in one pass so restored text is never rescanned."""
    if not literals:
        return source
    pattern = re.compile('"' + re.escape(literals.prefix) + r'(\d+)__"')
    return pattern.sub(lambda m: literals[int(m.group(1))], source)


def _dict_lines(source: str, node: ast.Dict, indent: str) -> str:
    """*node* written one entry per line, its entries indented one level below *indent* (nested displays too)."""
    inner = indent + "    "
    rows = []
    for key, value in zip(node.keys, node.values):
        value_text = _display_text(source, value, inner)
        rows.append(f"{inner}**{value_text}," if key is None else f"{inner}{_display_text(source, key, inner)}: {value_text},")
    return "{\n" + "\n".join(rows) + f"\n{indent}}}"


def _display_text(source: str, node: ast.expr, indent: str) -> str:
    """
    The text of *node*: a dict display with several entries is split over lines, one nested in a
    one-line list, tuple or call is split in place, anything else is kept. A walrus or yield keeps its
    parentheses, which a dict entry needs.
    """
    if isinstance(node, ast.Dict) and len(node.keys) > 1 and node.lineno == node.end_lineno:
        return _dict_lines(source, node, indent)
    text = ast.get_source_segment(source, node) or ""
    if node.lineno == node.end_lineno:
        nested = _outer_one_line_dicts(node)
        if nested:
            raw = source.split("\n")[node.lineno - 1].encode("utf-8")[node.col_offset:node.end_col_offset]
            for inner in sorted(nested, key=lambda n: n.col_offset, reverse=True):
                lo, hi = inner.col_offset - node.col_offset, inner.end_col_offset - node.col_offset
                raw = raw[:lo] + _dict_lines(source, inner, indent).encode("utf-8") + raw[hi:]
            text = raw.decode("utf-8")
    if isinstance(node, (ast.NamedExpr, ast.Yield, ast.YieldFrom)):
        text = f"({text})"
    return text


def _outer_one_line_dicts(tree: ast.AST) -> List[ast.Dict]:
    """The dict displays of several entries that stand on one line and are not inside another such display."""
    found: List[ast.Dict] = []

    def visit(node: ast.AST) -> None:
        if isinstance(node, ast.JoinedStr):
            return                   # (the positions inside an f-string are not reliable before 3.12)
        if isinstance(node, ast.Dict) and len(node.keys) > 1 and node.lineno == node.end_lineno:
            found.append(node)
            return
        for child in ast.iter_child_nodes(node):
            visit(child)

    visit(tree)
    return found


def split_dict_displays(source: str) -> str:
    """
    Write each dict display of several entries that stands on one line with one entry per line.

    The source is parsed; if it does not parse, or no display qualifies, it is returned as it is. Only the
    text of the display changes, and the result is checked to parse to the same tree.
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError, RecursionError):
        return source
    try:
        nodes = _outer_one_line_dicts(tree)
    except RecursionError:
        return source
    if not nodes:
        return source
    lines = source.split("\n")
    for node in sorted(nodes, key=lambda n: (n.lineno, n.col_offset), reverse=True):
        raw = lines[node.lineno - 1].encode("utf-8")
        indent = re.match(r"[ \t]*", lines[node.lineno - 1]).group(0)
        text = _dict_lines(source, node, indent).encode("utf-8")
        lines[node.lineno - 1] = (raw[:node.col_offset] + text + raw[node.end_col_offset:]).decode("utf-8")
    result = "\n".join(lines)
    try:
        if ast.dump(ast.parse(result)) != ast.dump(tree):
            return source
    except (SyntaxError, ValueError, RecursionError):
        return source
    return result


_AUGMENTED_OPS = r'(?:\+|-|\*|/|//|%|&|\||\^|<<|>>)'
# The inner-expression pattern deliberately excludes lines that contain 'for', 'lambda' or ':=' because
# those indicate that the outer parens are *required* (genexpr, lambda, walrus) rather than redundant
# grouping added by the decompiler.
_NO_KW = r'(?![^()]*(?:\b(?:for|lambda)\b|:=))'
_ASSIGNMENT_PARENS_RE = re.compile(
    r'^(\s*[A-Za-z_][A-Za-z0-9_.]*\s*' + _AUGMENTED_OPS + r'?=\s*)\(' + _NO_KW + r'([^,()]+)\)$')
# (a `yield` needs its parentheses after `return`, `if`, `elif` and `while`; after `=` it does not)
_RETURN_PARENS_RE = re.compile(r'^(\s*return\s+)\((?!\s*yield\b)' + _NO_KW + r'([^,()]+)\)$')
_IF_PARENS_RE = re.compile(r'^(\s*(?:if|elif)\s+)\((?!\s*yield\b)' + _NO_KW + r'([^,()]+)\):$')
_WHILE_PARENS_RE = re.compile(r'^(\s*while\s+)\((?!\s*yield\b)' + _NO_KW + r'([^,()]+)\):$')
_DOUBLE_ASSIGNMENT_RE = re.compile(
    r'^(\s*)([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*\(\2\s*(\+|-|\*|/|//|%|&|\||\^|<<|>>|\*\*|@)=\s*(.*)\)$')
_IMPORT_RE = re.compile(r'^([ \t]*)import\s+(.+)$')
_FROM_IMPORT_RE = re.compile(r'^([ \t]*)from\s+([A-Za-z0-9_.]+)\s+import\s+(.+)$')


def _bound_name(entry: str) -> str:
    """The name an import entry binds: its alias, else the first component of a dotted module."""
    parts = entry.split()
    return parts[-1] if len(parts) > 1 and parts[-2] == 'as' else parts[0].split('.')[0]


def _unique(entries: List[str]) -> List[str]:
    """``entries`` without repeats; a repeat is kept when another entry bound its name in between."""
    kept: List[str] = []
    for entry in entries:
        if entry in kept:
            later = kept[kept.index(entry) + 1:]
            if not any(_bound_name(other) == _bound_name(entry) for other in later):
                continue
            kept.remove(entry)
        kept.append(entry)
    return kept


def _is_mergeable_import(match: "Optional[re.Match]", names_group: int) -> bool:
    """Whether a matched import line is a plain list of names: not parenthesised, continued, commented, joined to another statement or a wildcard."""
    return bool(match) and not re.search(r'[()\\#;*]', match.group(names_group))


def merge_imports(text: str) -> str:
    """Merge consecutive imports at one indent: one `import` line per run, and one `from` line per run of one module."""
    out_lines: List[str] = []
    imports: List[str] = []
    from_module: Optional[str] = None
    from_names: List[str] = []
    indent: Optional[str] = None

    def flush() -> None:
        nonlocal indent, from_module
        if imports:
            out_lines.extend(f"{indent}import {module}" for module in _unique(imports))
        if from_module is not None:
            out_lines.append(f"{indent}from {from_module} import {', '.join(_unique(from_names))}")
        imports.clear()
        from_names.clear()
        indent = from_module = None

    for line in text.split('\n'):
        imp_m = _IMPORT_RE.match(line)
        from_m = _FROM_IMPORT_RE.match(line)
        if not (_is_mergeable_import(imp_m, 2) or _is_mergeable_import(from_m, 3)):
            flush()
            out_lines.append(line)
            continue
        match = imp_m or from_m
        # a run is one kind of import at one indent (and, for `from`, one module); anything else starts a new run
        if indent is not None and match.group(1) != indent:
            flush()
        if (imp_m and from_module is not None) or (from_m and (imports or from_module != from_m.group(2))):
            flush()
        indent = match.group(1)
        if imp_m:
            imports.extend(m.strip() for m in imp_m.group(2).split(','))
        else:
            from_module = from_m.group(2)
            from_names.extend(m.strip() for m in from_m.group(3).split(','))
    flush()
    return '\n'.join(out_lines)


def strip_redundant_parens(text: str) -> str:
    """Drop grouping parentheses around a whole right-hand side, `return` value or `if`/`while` test."""
    out_lines = []
    for line in text.split('\n'):
        # a doubled-up assignment from an INPLACE op: `var = (var += expr)` -> `var += expr`
        m_double = _DOUBLE_ASSIGNMENT_RE.match(line)
        if m_double:
            indent, var_name, op_sym, rhs = m_double.groups()
            line = f"{indent}{var_name} {op_sym}= {rhs}"
        else:
            line = _ASSIGNMENT_PARENS_RE.sub(r'\1\2', line)
            line = _RETURN_PARENS_RE.sub(r'\1\2', line)
            line = _IF_PARENS_RE.sub(r'\1\2:', line)
            line = _WHILE_PARENS_RE.sub(r'\1\2:', line)
        out_lines.append(line)
    return '\n'.join(out_lines)


def _operator_tokens(text: str) -> Optional[List[str]]:
    """The operator tokens of ``text`` (so brackets inside strings are not counted), or None when it does not tokenize."""
    try:
        return [t.string for t in tokenize.generate_tokens(io.StringIO(text).readline) if t.type == tokenize.OP]
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return None


def _parens_balanced(text: str) -> bool:
    ops = _operator_tokens(text)
    if ops is None:
        return False
    depth = 0
    for op in ops:
        depth += (op == '(') - (op == ')')
        if depth < 0:
            return False
    return depth == 0


def _has_top_level_comma(text: str) -> bool:
    depth = 0
    for op in _operator_tokens(text) or []:
        if op in '([{':
            depth += 1
        elif op in ')]}':
            depth -= 1
        elif op == ',' and depth == 0:
            return True
    return False


def _unwrap_print_argument(inner: str) -> str:
    """`(x)` -> `x` for a print argument, unless `x` is a tuple (a top-level comma would split the argument)."""
    m = re.match(r'^\((.*)\)$', inner, flags=re.DOTALL)
    if not m:
        return inner
    inside = m.group(1)
    if not _parens_balanced(inside):
        return inner
    try:
        node = ast.parse(inside, mode='eval')
        return inner if isinstance(node.body, ast.Tuple) else inside
    except (SyntaxError, ValueError):
        # e.g. `*args`, which mode='eval' rejects: fall back to a scan for a top-level comma
        # (a `yield` argument needs its parentheses)
        return inner if re.match(r'\s*yield\b', inside) or _has_top_level_comma(inside) else inside


def _one_line_triple_quoted(text: str) -> str:
    """``text`` with each unprefixed triple-quoted string token written as a one-line literal of the same value.

    A prefixed string (f\"\"\"...\"\"\", r, b) is left as it is: repr() would write the prefix in front of a quoted
    copy of its text, which is not the same string. Tokens, not a pattern, find the strings, so quotes inside a
    single-quoted string are never taken for an opener.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(text).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return text
    offsets = [0]
    for line in text.split("\n"):
        offsets.append(offsets[-1] + len(line) + 1)
    pieces, pos = [], 0
    for tok in tokens:
        if tok.type != tokenize.STRING or tok.string[:3] not in ('"""', "\'\'\'"):
            continue
        try:
            literal = repr(ast.literal_eval(tok.string))   # the value, escapes included, not the source text
        except (SyntaxError, ValueError):
            continue
        start = offsets[tok.start[0] - 1] + tok.start[1]
        end = offsets[tok.end[0] - 1] + tok.end[1]
        pieces.extend((text[pos:start], literal))
        pos = end
    pieces.append(text[pos:])
    return "".join(pieces)


def _tidy_print_call(match: "re.Match") -> str:
    inner = _one_line_triple_quoted(match.group(2))
    return f"{match.group(1)}{_unwrap_print_argument(inner)})"


def tidy_print_calls(text: str) -> str:
    """Write a triple-quoted string in a `print(...)` on one line and drop redundant grouping parentheses."""
    return re.sub(r'^([ \t]*print\s*\()([\s\S]*?)\)$', _tidy_print_call, text, flags=re.MULTILINE)


def collapse_blank_lines(text: str) -> str:
    """Never more than one blank line in a row."""
    return re.sub(r'\n{3,}', '\n\n', text)


def _leak_comment(kind: str) -> str:
    return 'genexpr/lambda' if kind == 'func' else 'class'


def _leak_replacement(match: "re.Match") -> str:
    return (f"{match.group(1)}# <{_leak_comment(match.group(3))} — not reconstructable>\n"
            f"{match.group(1)}{match.group(2)} = None")


def replace_leaked_tuples(text: str) -> str:
    """Replace raw decompiler tuples (`('func', ...)`, `def <lambda>`) that reached the output with a comment.

    The whole statement is replaced so the output stays valid Python. The first pattern matches the
    string form made by `_render_func_tuple` (``lambda params: ...``) or a raw ``def <lambda>(params):``
    block; the second an un-wrapped tuple floating on one line; the third a stray ``def <lambda>`` block.
    """
    text = re.sub(
        r"([ \t]*)([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*\('(func|class)',\s*.*?(?:def\s+<lambda>|lambda\s).*?"
        r"(?:\n\1\)|'\)|\"\)|\"\"\"\)|'''\))\)?",
        _leak_replacement, text, flags=re.MULTILINE | re.DOTALL)
    text = re.sub(r"([ \t]*)([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*\('(func|class)',[^\n]*", _leak_replacement, text)
    # the header line and the lines indented below it
    return re.sub(
        r"^([ \t]*)[^'\"\n]*(?:def\s+<lambda>).*(?:\n\1[ \t]+\S.*|\n[ \t]*(?=(?:\n[ \t]*)*\n\1[ \t]+\S))*",
        lambda m: f"{m.group(1)}# <genexpr/lambda — not reconstructable>\n{m.group(1)}pass",
        text, flags=re.MULTILINE)


class PostProcessTransform(NamedTuple):
    """One named step of the output clean-up.

    ``levels`` are the beautification levels that run it. ``preserves_ast`` says whether, for text that
    parses, the result parses to the same tree; tests/test_postprocess_guard.py checks that claim.
    """
    name: str
    apply: Callable[[str], str]
    levels: FrozenSet[str]
    preserves_ast: bool


_EVERY_LEVEL = frozenset(('none', 'core', 'aggressive'))
_CORE_UP = frozenset(('core', 'aggressive'))

# Run first, with only the docstrings masked: they read the triple-quoted strings inside a `print(...)`.
STRING_TRANSFORMS: Tuple[PostProcessTransform, ...] = (
    PostProcessTransform("tidy_print_calls", tidy_print_calls, _CORE_UP, True),
)

# Run next, in this order, with every multi-line string literal masked out so none of them can change its value.
TEXT_TRANSFORMS: Tuple[PostProcessTransform, ...] = (
    # a repeated import is dropped, so the tree is not always identical
    PostProcessTransform("merge_imports", merge_imports, _EVERY_LEVEL, False),
    PostProcessTransform("strip_redundant_parens", strip_redundant_parens, _EVERY_LEVEL, True),
    PostProcessTransform("collapse_blank_lines", collapse_blank_lines, _EVERY_LEVEL, True),
    # a leaked tuple becomes `name = None`
    PostProcessTransform("replace_leaked_tuples", replace_leaked_tuples, _EVERY_LEVEL, False),
    PostProcessTransform("flatten_elif", flatten_elif, _CORE_UP, True),
)

# Run last, on the finished text, after the string literals are put back.
FINAL_TRANSFORMS: Tuple[PostProcessTransform, ...] = (
    PostProcessTransform("split_dict_displays", split_dict_displays, frozenset(('aggressive',)), True),
)


def _run(transforms: Tuple[PostProcessTransform, ...], text: str, level: str) -> str:
    for transform in transforms:
        if level in transform.levels:
            text = transform.apply(text)
    return text


def post_process_source(source: str, beautification_level: str = 'core') -> str:
    """Clean up decompiled output to be more Pythonic (see STRING_TRANSFORMS, TEXT_TRANSFORMS and FINAL_TRANSFORMS)."""
    text, literals = _mask_multiline_strings(source, keep_print_arguments=True)
    text = _run(STRING_TRANSFORMS, text, beautification_level)
    text, literals = _mask_multiline_strings(text, literals=literals)
    text = _run(TEXT_TRANSFORMS, text, beautification_level)
    result = _unmask_multiline_strings(text.strip('\r\n') + '\n', literals)
    return _run(FINAL_TRANSFORMS, result, beautification_level)
