from __future__ import annotations

import os
import py_compile
import tempfile
import sys
import unittest
from pathlib import Path
from typing import Any, List, Tuple, Union

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE.parent))

from pycrefine import (
    BytecodeInstruction,
    _block_opener_keyword,
    _collect_multiline_header,
    _line_is_in_triple_quoted_string,
    flatten_elif,
    get_decompiler,
    post_process_source,
    Decompiler39,
)

def _compile(src: str) -> str:
    """Compile *src* to a temp .pyc and return its path.  Caller must delete."""
    with tempfile.NamedTemporaryFile(
        suffix=".py", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(src)
        py_path = f.name
    pyc_path = py_path + "c"
    py_compile.compile(py_path, cfile=pyc_path, doraise=True)
    os.unlink(py_path)
    return pyc_path

def decompile(src: str) -> str:
    """Compile *src* and decompile it; cleans up the .pyc automatically."""
    pyc = _compile(src)
    try:
        return get_decompiler(pyc).decompile()
    finally:
        if os.path.exists(pyc):
            os.unlink(pyc)

def assert_contains(output: str, *fragments: str) -> None:
    """Assert that every fragment appears somewhere in *output*."""
    for frag in fragments:
        assert frag in output, (
            f"Expected fragment {frag!r} not found in decompiled output:\n{output}"
        )

def assert_not_contains(output: str, *fragments: str) -> None:
    """Assert that none of the fragments appear in *output*."""
    for frag in fragments:
        assert frag not in output, (
            f"Unexpected fragment {frag!r} found in decompiled output:\n{output}"
        )

def _run39_full_impl(instructions):
    """Shared helper to run Decompiler39 with all prescans on a synthetic instruction list."""
    code = compile("pass", "<test>", "exec")
    dec = Decompiler39(code)
    dec.instructions = list(instructions)

    _JUMP_OPS = {
        "FOR_ITER", "JUMP_FORWARD", "JUMP_ABSOLUTE",
        "POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE",
        "JUMP_IF_FALSE_OR_POP", "JUMP_IF_TRUE_OR_POP",
        "SETUP_FINALLY", "SETUP_WITH", "JUMP_IF_NOT_EXC_MATCH",
    }
    targets = {
        ins.argval for ins in dec.instructions
        if ins.opname in _JUMP_OPS and isinstance(ins.argval, int)
    }
    dec.instructions = [
        BytecodeInstruction(
            ins.opcode, ins.opname, ins.arg, ins.argval,
            ins.offset, ins.starts_line,
            ins.offset in targets,
        )
        for ins in dec.instructions
    ]

    dec.pc = 0
    dec.blocks = []
    dec._while_header_targets = {}
    dec._while_body_offsets = set()
    dec._while_true_ends = set()
    dec._prescan_while_loops()
    dec._prescan_try_structure()
    dec._prescan_ternaries()
    dec._prescan_compound_conds()

    dec.has_doc = False
    if dec.code_obj.co_consts and isinstance(dec.code_obj.co_consts[0], str):
        first_meaningful = None
        for ins in dec.instructions:
            if ins.opname not in ("RESUME", "NOP", "CACHE", "NOT_TAKEN"):
                first_meaningful = ins
                break
        is_docstring = False
        if first_meaningful and first_meaningful.opname == "LOAD_CONST" and first_meaningful.arg == 0:
            idx = dec.instructions.index(first_meaningful)
            if idx + 1 < len(dec.instructions):
                next_op = dec.instructions[idx + 1].opname
                if next_op in ("POP_TOP", "STORE_NAME"):
                    is_docstring = True
        if is_docstring:
            doc = dec.code_obj.co_consts[0]
            if doc:
                dec._append_reconstructed('"""', indent_multiline=True)
                dec._append_reconstructed(doc.strip(), indent_multiline=True)
                dec._append_reconstructed('"""', indent_multiline=True)
                dec.has_doc = True
                dec.reconstructed.append("")

    dec.pc = 0
    while dec.pc < len(dec.instructions):
        instr = dec.instructions[dec.pc]
        dec._close_blocks(instr.offset)
        dec.pc += 1
        dec._handle_instruction(instr)
    dec._close_blocks(0x7fffffff)

    if dec.reconstructed:
        last_idx = len(dec.reconstructed) - 1
        while last_idx >= 0 and not dec.reconstructed[last_idx].strip():
            last_idx -= 1
        
        if last_idx >= 0 and dec.reconstructed[last_idx].strip() == "return None":
            line = dec.reconstructed[last_idx]
            if not (line.startswith(" ") or line.startswith("\t")):
                has_others = False
                for i in range(last_idx):
                     strip_line = dec.reconstructed[i].strip()
                     if strip_line and not (
                         strip_line.startswith('"""') or strip_line.startswith("'''")
                     ):
                         has_others = True
                         break
                
                if has_others or getattr(dec, "has_doc", False):
                    dec.reconstructed.pop(last_idx)
                else:
                    indent = len(line) - len(line.lstrip())
                    dec.reconstructed[last_idx] = line[:indent] + "pass"

    raw_source = "\n".join(str(s) for s in dec.reconstructed).rstrip()
    return post_process_source(raw_source)


# ---------------------------------------------------------------------------
# Regression tests: tokenize-based triple-quote guard
# ---------------------------------------------------------------------------

class TestLineIsInTripleQuotedString(unittest.TestCase):
    """Regression tests for _line_is_in_triple_quoted_string.

    The function must use tokenize (not naive substring search) so that
    triple-quote characters inside ordinary single-quoted strings or inside
    comments are correctly ignored.
    """

    def test_plain_line_not_in_triple(self):
        """A bare else: after plain assignment is not inside a triple-quoted string."""
        lines = ["x = 1", "else:"]
        self.assertFalse(_line_is_in_triple_quoted_string(lines, 1))

    def test_open_triple_detected_via_token_error(self):
        """Lines[:i] ending mid-triple-string raises TokenError -> returns True."""
        lines = ['x = """', "hello", "else:"]
        self.assertTrue(_line_is_in_triple_quoted_string(lines, 2))

    def test_closed_triple_before_target_is_not_in_triple(self):
        """A triple-quoted string that is closed before line i must not flag line i."""
        lines = ['x = """close"""', "else:"]
        self.assertFalse(_line_is_in_triple_quoted_string(lines, 1))

    def test_triple_chars_in_single_quoted_string_ignored(self):
        """Triple-quote chars embedded in a normal single-quoted string must be
        ignored — the old substring-scan approach would mis-count these."""
        lines = ["s = '\"\"\"'", "else:"]
        self.assertFalse(_line_is_in_triple_quoted_string(lines, 1))

    def test_triple_chars_in_comment_ignored(self):
        """Triple-quote chars in a comment must not flip the in-string state."""
        lines = ['# hello """ world', "else:"]
        self.assertFalse(_line_is_in_triple_quoted_string(lines, 1))

    def test_triple_chars_in_single_quoted_using_single_delim(self):
        """Triple single-quote chars inside a double-quoted string are ignored."""
        lines = ["s = \"'''\"", "else:"]
        self.assertFalse(_line_is_in_triple_quoted_string(lines, 1))

    def test_multiline_open_triple_spans_target_line(self):
        """A multiline triple-quoted string that started before line i and has not
        yet closed means line i is inside the literal."""
        lines = ['x = """first', "second", "else:"]
        self.assertTrue(_line_is_in_triple_quoted_string(lines, 2))

    def test_triple_closed_on_line_before_target(self):
        """Triple closed on the line just before i -> target line is outside."""
        lines = ['x = """', 'closed"""', "else:"]
        self.assertFalse(_line_is_in_triple_quoted_string(lines, 2))

    def test_line_idx_zero_always_false(self):
        """line_idx=0 is an optimised early return — there are no preceding lines."""
        self.assertFalse(_line_is_in_triple_quoted_string(["else:"], 0))

    def test_f_string_triple_prefix_detected(self):
        """f-string triple-quote prefixes (f\"\"\") must also be recognised."""
        lines = ['x = f"""', "interpolation", "else:"]
        self.assertTrue(_line_is_in_triple_quoted_string(lines, 2))

    def test_raw_triple_prefix_detected(self):
        """r\"\"\" and similar raw-string triple-quote prefixes must be recognised."""
        lines = ['pat = r"""', "pattern", "else:"]
        self.assertTrue(_line_is_in_triple_quoted_string(lines, 2))


# ---------------------------------------------------------------------------
# Regression tests: flatten_elif must not transform else: inside triple strings
# ---------------------------------------------------------------------------

class TestFlattenElifTripleQuoteGuard(unittest.TestCase):
    """Regression tests ensuring flatten_elif honours the tokenize-based guard.

    The core bug: the old substring scan would miscount triple-quote occurrences
    that appeared inside single-line strings/comments, causing flatten_elif to
    incorrectly transform `else:` lines that were actually inside a triple-quoted
    string body.
    """

    def test_else_inside_multiline_triple_not_flattened(self):
        """An else: that appears as literal text inside a triple-quoted string
        must pass through unchanged — it is not Python syntax at that point."""
        src = 'x = """\nelse:\n    pass\n"""\n'
        out = flatten_elif(src)
        self.assertNotIn("elif", out)
        # The triple-quoted string content must be preserved verbatim.
        self.assertIn('"""', out)

    def test_else_inside_triple_single_quote_not_flattened(self):
        """Same check for ''' triple-quoted strings."""
        src = "x = '''\nelse:\n    pass\n'''\n"
        out = flatten_elif(src)
        self.assertNotIn("elif", out)

    def test_triple_chars_in_single_string_dont_suppress_flatten(self):
        """If a line contains '\"\"\"' inside a normal single-quoted string,
        the *following* else:/if block must still be flattened correctly
        (the false-positive suppression must not be triggered)."""
        src = (
            "s = '\"\"\"'\n"      # single-quoted string containing triple-quote chars
            "if a:\n"
            "    pass\n"
            "else:\n"
            "    if b:\n"
            "        pass\n"
        )
        out = flatten_elif(src)
        self.assertIn("elif b:", out)

    def test_triple_chars_in_comment_dont_suppress_flatten(self):
        """Triple-quote chars inside a comment must not prevent flattening."""
        src = (
            '# has """ in it\n'
            "if a:\n"
            "    pass\n"
            "else:\n"
            "    if b:\n"
            "        pass\n"
        )
        out = flatten_elif(src)
        self.assertIn("elif b:", out)

    def test_normal_if_else_if_still_flattened(self):
        """Baseline: a plain if/else/if pattern must still be flattened."""
        src = "if a:\n    pass\nelse:\n    if b:\n        pass\n"
        out = flatten_elif(src)
        self.assertIn("elif b:", out)
        self.assertNotIn("else:", out)


# ---------------------------------------------------------------------------
# Regression tests: _block_opener_keyword
# ---------------------------------------------------------------------------

class TestBlockOpenerKeyword(unittest.TestCase):
    """Pins _block_opener_keyword for both single-line and multi-line headers."""

    def test_single_line_if(self):
        lines = ["if cond:"]
        self.assertEqual(_block_opener_keyword(lines, 0, 0), "if")

    def test_single_line_elif(self):
        lines = ["elif cond:"]
        self.assertEqual(_block_opener_keyword(lines, 0, 0), "elif")

    def test_single_line_for(self):
        lines = ["for x in y:"]
        self.assertEqual(_block_opener_keyword(lines, 0, 0), "for")

    def test_multiline_if_at_indent_0(self):
        """if (\n    cond\n): — tail is '):', opener is 'if'."""
        lines = ["if (", "    cond", "):"]
        self.assertEqual(_block_opener_keyword(lines, 2, 0), "if")

    def test_multiline_elif_at_indent_0(self):
        lines = ["elif (", "    cond", "):"]
        self.assertEqual(_block_opener_keyword(lines, 2, 0), "elif")

    def test_multiline_if_indented(self):
        """Same but header is indented 4 spaces."""
        lines = ["    if (", "        a and b", "    ):"]
        self.assertEqual(_block_opener_keyword(lines, 2, 4), "if")

    def test_non_if_keyword_returns_correct(self):
        """A while multi-line header should return 'while', not None."""
        lines = ["while (", "    cond", "):"]
        self.assertEqual(_block_opener_keyword(lines, 2, 0), "while")

    def test_unrecognised_tail_returns_none(self):
        """A closing line with no recognisable opener returns None."""
        lines = ["):"]
        self.assertIsNone(_block_opener_keyword(lines, 0, 0))


# ---------------------------------------------------------------------------
# Regression tests: _collect_multiline_header
# ---------------------------------------------------------------------------

class TestCollectMultilineHeader(unittest.TestCase):
    """Pins _collect_multiline_header for both single-line and multi-line headers."""

    def test_single_line_if(self):
        lines = ["    if cond:"]
        end, cond = _collect_multiline_header(lines, 0, 4)
        self.assertEqual(end, 0)
        self.assertEqual(cond, "cond:")

    def test_single_line_elif(self):
        lines = ["    elif cond:"]
        end, cond = _collect_multiline_header(lines, 0, 4)
        self.assertEqual(end, 0)
        self.assertEqual(cond, "cond:")

    def test_multiline_two_continuation_lines(self):
        lines = ["    if (", "        cond", "    ):"]
        end, cond = _collect_multiline_header(lines, 0, 4)
        self.assertEqual(end, 2)
        self.assertEqual(cond, "( cond ):")

    def test_multiline_multiple_continuation_lines(self):
        lines = ["if (", "    a", "    and b", "):"]
        end, cond = _collect_multiline_header(lines, 0, 0)
        self.assertEqual(end, 3)
        self.assertEqual(cond, "( a and b ):")


# ---------------------------------------------------------------------------
# Regression tests: flatten_elif with multi-line headers
# ---------------------------------------------------------------------------

class TestFlattenElifMultilineHeaders(unittest.TestCase):
    """Regression tests for the fixed multi-line if/elif header handling.

    Before the fix:
    * Guard 2 stopped at `):` and never found `if`/`elif` -> transform skipped.
    * Condition extraction sliced only the first line -> broken `elif (`.
    """

    def test_multiline_parent_if_is_recognised(self):
        """Guard 2 must identify `if (\n    a\n):` as an if-parent and allow
        the transform (was silently skipped before the fix)."""
        src = "if (\n    a\n):\n    pass\nelse:\n    if b:\n        pass\n"
        out = flatten_elif(src)
        self.assertIn("elif b:", out)

    def test_multiline_nested_if_condition_fully_captured(self):
        """The full multi-line nested `if (\n    b\n):` condition must be
        joined and emitted correctly (was truncated to `elif (` before the fix)."""
        src = "if a:\n    pass\nelse:\n    if (\n        b\n    ):\n        pass\n"
        out = flatten_elif(src)
        self.assertIn("elif", out)
        # Condition must include the actual variable, not just an open paren.
        self.assertIn("b", out)
        # Output must be syntactically complete (condition ends with ':')
        import ast
        try:
            ast.parse(out)
        except SyntaxError as exc:
            self.fail(f"flatten_elif produced invalid Python: {exc}\n{out}")

    def test_both_headers_multiline(self):
        """Both parent and nested headers span multiple lines."""
        src = "if (\n    a\n):\n    pass\nelse:\n    if (\n        b\n    ):\n        pass\n"
        out = flatten_elif(src)
        self.assertIn("elif", out)
        import ast
        try:
            ast.parse(out)
        except SyntaxError as exc:
            self.fail(f"flatten_elif produced invalid Python: {exc}\n{out}")

    def test_single_line_baseline_still_works(self):
        """Ensure the original single-line fast path is unaffected."""
        src = "if a:\n    pass\nelse:\n    if b:\n        pass\n"
        out = flatten_elif(src)
        self.assertIn("elif b:", out)
        self.assertNotIn("else:", out)


if __name__ == "__main__":
    unittest.main()
