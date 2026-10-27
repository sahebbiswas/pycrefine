"""Text-level post-processing of decompiled source (imports, parentheses, elif flattening)."""
import ast
import io
import re
import tokenize
from typing import List, Optional, Set


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


_MULTILINE_STRING_MARK = "__pycrefine_multiline_string_{}__"


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


def _mask_multiline_strings(source: str):
    """
    Replace each multi-line string that is a statement of its own (a docstring) with a
    one-line placeholder.

    The clean-ups below are line- and regex-based; inside such a string they would
    change its value (spacing after commas, collapsed blank lines, sorted imports).
    Strings inside expressions are left to them. Returns the masked source and the
    literals in order.
    """
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError, SyntaxError):
        return source, []
    lines = source.split("\n")
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line) + 1)
    pieces, literals, pos = [], [], 0
    boundary = (tokenize.NEWLINE, tokenize.NL, tokenize.INDENT, tokenize.DEDENT, tokenize.ENCODING)
    significant = [t for t in tokens if t.type not in (tokenize.COMMENT,)]
    for n, tok in enumerate(significant):
        if tok.type != tokenize.STRING or tok.start[0] == tok.end[0]:
            continue
        before = significant[n - 1].type if n else tokenize.NEWLINE
        after = significant[n + 1].type if n + 1 < len(significant) else tokenize.NEWLINE
        if before not in boundary or after not in (tokenize.NEWLINE, tokenize.ENDMARKER):
            continue  # part of an expression, which the clean-ups may rewrite
        start = offsets[tok.start[0] - 1] + tok.start[1]
        end = offsets[tok.end[0] - 1] + tok.end[1]
        pieces.append(source[pos:start])
        pieces.append(f'"{_MULTILINE_STRING_MARK.format(len(literals))}"')
        literals.append(source[start:end])
        pos = end
    pieces.append(source[pos:])
    return "".join(pieces), literals


def _unmask_multiline_strings(source: str, literals: List[str]) -> str:
    """Put back the literals that :func:`_mask_multiline_strings` replaced."""
    for n, literal in enumerate(literals):
        source = source.replace(f'"{_MULTILINE_STRING_MARK.format(n)}"', literal, 1)
    return source


def post_process_source(source: str, beautification_level: str = 'core') -> str:
    """Clean up decompiled output to be more Pythonic."""
    source, literals = _mask_multiline_strings(source)
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

    def fix_print_parens_and_strings(match):
        prefix = match.group(1)
        inner = match.group(2)
        
        # Convert triple-quoted strings inside a print() to single-line double-quoted
        # strings with literal \n escapes. Use repr() to safely re-quote the value
        # so embedded quotes never produce invalid syntax.
        def repl_str(m):
            raw_content = m.group(1)          # exactly what was between the triple quotes
            # repr() of a str always produces a syntactically valid Python literal.
            return repr(raw_content)  # e.g. "'hello\\nworld'" or '"it\'s"'

        inner = re.sub(r"'''([\s\S]*?)'''", repl_str, inner)
        inner = re.sub(r'"""([\s\S]*?)"""', repl_str, inner)

        # strip extra grouping parentheses if present, but only when the inner
        # expression is NOT a tuple (top-level comma would change semantics).
        m = re.match(r'^\((.*)\)$', inner, flags=re.DOTALL)
        if m:
            inside = m.group(1)
            # Balance check: the parens must form a matched pair.
            depth = 0
            balanced = True
            for char in inside:
                if char == '(': depth += 1
                elif char == ')': depth -= 1
                if depth < 0:
                    balanced = False
                    break
            if balanced and depth == 0:
                try:
                    # Robust check using AST to see if 'inside' is a tuple.
                    # We use mode='eval' because 'inside' should be a single expression
                    # (possibly a tuple expression).
                    node = ast.parse(inside, mode='eval')
                    # If it's an ast.Tuple, then unwrapping the outer parentheses
                    # would turn one tuple argument into multiple arguments.
                    if not isinstance(node.body, ast.Tuple):
                        inner = inside
                except (SyntaxError, ValueError):
                    # Fallback to the manual comma scan if AST parsing fails.
                    # (e.g., for specialized syntax like '*args' which mode='eval' might reject)
                    # Use a scan that skips content inside delimiters.
                    top_level_comma = False
                    d = 0
                    for char in inside:
                        if char in '([{': d += 1
                        elif char in ')]}': d -= 1
                        elif char == ',' and d == 0:
                            top_level_comma = True
                            break
                    if not top_level_comma:
                        inner = inside
        
        return f"{prefix}{inner})"

    if beautification_level in ('core', 'aggressive'):
        text = re.sub(r'^([ \t]*print\s*\()([\s\S]*?)\)$', fix_print_parens_and_strings, text, flags=re.MULTILINE)

    text = re.sub(r'\n{3,}', '\n\n', text)

    # ── Final cleanup: suppress raw decompiler-tuple leakage ─────────────
    # If any ('func', ...) or ('class', ...) tuples slipped through to an
    # assignment RHS or statement position, replace the ENTIRE statement
    # (to end-of-line) with a comment + None so the output stays valid Python.
    # Matches either:
    # 1. string representation created by `_render_func_tuple`: "lambda params: ..."
    # 2. raw "def <lambda>(params):\n..." block
    text = re.sub(
        r"([ \t]*)([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*\('(func|class)',\s*.*?(?:def\s+<lambda>|lambda\s).*?(?:\n\1\)|'\)|\"\)|\"\"\"\)|'''\))\)?",
        lambda m: (
            f"{m.group(1)}# <{'genexpr/lambda' if m.group(3) == 'func' else 'class'}"
            f" \u2014 not reconstructable>\n{m.group(1)}{m.group(2)} = None"
        ),
        text,
        flags=re.MULTILINE | re.DOTALL
    )
    
    # Also clean up un-wrapped cases that might be floating
    text = re.sub(
        r"([ \t]*)([A-Za-z_][A-Za-z0-9_.]*)\s*=\s*\('(func|class)',[^\n]*",
        lambda m: (
            f"{m.group(1)}# <{'genexpr/lambda' if m.group(3) == 'func' else 'class'}"
            f" \u2014 not reconstructable>\n{m.group(1)}{m.group(2)} = None"
        ),
        text,
    )
    
    # Catch stray `def <lambda>` blocks anywhere
    text = re.sub(
        r"([ \t]*).*(?:def\s+<lambda>)[\s\S]*?(?=\n[ \t]*\S|\Z)",
        lambda m: (
            f"{m.group(1)}# <genexpr/lambda \u2014 not reconstructable>\n{m.group(1)}pass"
        ),
        text,
        flags=re.MULTILINE
    )

    if beautification_level in ('core', 'aggressive'):
        text = flatten_elif(text)

    if beautification_level == 'aggressive':
        # Stub for aggressive beautification - to be implemented in future
        pass

    return _unmask_multiline_strings(text.strip('\r\n') + '\n', literals)
