"""Post-processing must not change what the program means, and must be stable (#121).

``post_process_source`` rewrites the emitted text with regular expressions. This guard
decompiles every corpus entry that applies to the running Python and checks, wherever the
raw output parses, that the rewritten text parses to the same tree and that rewriting it a
second time changes nothing.
"""
import ast
import importlib.util
import os
import py_compile
import sys
import tempfile
import unittest
import warnings
from pathlib import Path

from pycrefine import get_decompiler
from pycrefine import postprocess
from pycrefine.decompiler import generic

_ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("check_differential_guard", _ROOT / "quality" / "check_differential.py")
cd = importlib.util.module_from_spec(_spec)
sys.modules["check_differential_guard"] = cd
_spec.loader.exec_module(cd)

# Raw output and its post-processed form, per decompiled unit, filled by the wrapper below.
_SEEN = []
_ORIGINAL = generic.post_process_source


def _recording(source, beautification_level="core"):
    result = _ORIGINAL(source, beautification_level=beautification_level)
    _SEEN.append((source, result))
    return result


class _Imports(ast.NodeTransformer):
    """Spell imports one name per statement and drop repeats within an import run.

    Merging imports is a deliberate rewrite (``import a`` twice becomes once, two
    ``from m import`` lines become one, even with another import in between); it must not
    count as a change of meaning. A run ends at any other statement, so an import repeated
    after one stays.
    """

    def generic_visit(self, node):
        super().generic_visit(node)
        for field in ("body", "orelse", "finalbody"):
            stmts = getattr(node, field, None)
            if not isinstance(stmts, list):
                continue
            flat = []
            for stmt in stmts:
                if isinstance(stmt, ast.Import):
                    flat.extend(ast.Import(names=[alias]) for alias in stmt.names)
                elif isinstance(stmt, ast.ImportFrom):
                    flat.extend(ast.ImportFrom(module=stmt.module, names=[alias], level=stmt.level)
                                for alias in stmt.names)
                else:
                    flat.append(stmt)
            folded = []
            run = set()  # dumps of the imports seen in the current uninterrupted run
            for stmt in flat:
                if isinstance(stmt, (ast.Import, ast.ImportFrom)):
                    key = ast.dump(stmt)
                    if key in run:
                        continue
                    run.add(key)
                else:
                    run.clear()
                folded.append(stmt)
            setattr(node, field, folded)
        return node


def _tree(text):
    """The tree of *text* with import spelling normalised, as a string."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")  # decompiled text may hold odd escapes (SyntaxWarning, or DeprecationWarning before 3.12)
        return ast.dump(_Imports().visit(ast.parse(_in_context(text))))


def _in_context(text):
    """*text* wrapped so a unit that starts indented parses.

    A function body is recorded before the ``def`` around it exists, so it starts at the
    indentation it will have inside the function. One ``if 1:`` per level restores that.
    """
    first = next((line for line in text.splitlines() if line.strip()), "")
    depth = (len(first) - len(first.lstrip(" "))) // 4
    if depth == 0:
        return text
    head = "".join("    " * level + "if 1:\n" for level in range(depth))
    return head + text


def _version():
    return sys.version_info[:2]


def _entries():
    entries = cd.load_corpus(_ROOT / "quality" / "corpus")
    return [e for e in cd.select_entries(entries, [], None, _version()) if e.source_path() is not None]


def _compile_at(source, optimize):
    """Compile *source* to a temp .pyc at *optimize* (the entry's declared level); caller deletes it."""
    with tempfile.TemporaryDirectory() as tmp:
        py_path = os.path.join(tmp, "unit.py")
        with open(py_path, "w", encoding="utf-8") as handle:
            handle.write(source)
        fd, pyc = tempfile.mkstemp(suffix=".pyc")
        os.close(fd)
        py_compile.compile(py_path, cfile=pyc, doraise=True, optimize=optimize)
    return pyc


def _raw_and_final(source, optimize=0):
    """Every (raw, post-processed) pair recorded while decompiling *source*, innermost first.

    Nested functions and classes are decompiled before the unit around them, so the last
    pair is the module and the earlier ones are the bodies within it.

    Nothing is caught: every corpus entry decompiles on every supported Python today, so an
    exception here is a real failure (a decompiler crash, or a bug in this test) and must not
    let the guard pass by skipping the entry.
    """
    pyc = _compile_at(source, optimize)
    _SEEN.clear()
    generic.post_process_source = _recording  # generic.py imported the name, so patch it there
    try:
        get_decompiler(pyc).decompile()
    finally:
        generic.post_process_source = _ORIGINAL
        os.unlink(pyc)
    return list(_SEEN)


class TestPostProcessingPreservesTheProgram(unittest.TestCase):

    def test_corpus_output_keeps_its_ast_and_is_stable(self):
        checked = nested = 0
        for entry in _entries():
            source = entry.source_path().read_text(encoding="utf-8")
            try:
                compile(source, entry.id, "exec", optimize=entry.optimize)
            except SyntaxError:  # the source itself needs a newer Python
                continue
            pairs = _raw_and_final(source, entry.optimize)
            for index, (raw, final) in enumerate(pairs):
                try:
                    raw_tree = _tree(raw)
                except SyntaxError:
                    continue  # nothing to preserve: the decompiler's own output is not Python
                checked += 1
                nested += index < len(pairs) - 1
                with self.subTest(entry=entry.id, unit=index):
                    try:
                        final_tree = _tree(final)
                    except SyntaxError as exc:
                        self.fail(f"post-processing made the output unparsable: {exc}")
                    self.assertEqual(final_tree, raw_tree, "post-processing changed the AST")
                    self.assertEqual(postprocess.post_process_source(final), final, "post-processing is not idempotent")
        self.assertGreater(checked, 20, "the guard checked too few units to mean anything")
        self.assertGreater(nested, 10, "the guard checked too few nested function bodies to mean anything")


class TestKnownRewrites(unittest.TestCase):
    """Rewrites the layer is meant to make, and shapes it must leave alone."""

    def assertPreserved(self, source):
        final = postprocess.post_process_source(source)
        self.assertEqual(_tree(final), _tree(source), final)
        self.assertEqual(postprocess.post_process_source(final), final)
        return final

    def test_redundant_parentheses_go_but_required_ones_stay(self):
        self.assertEqual(self.assertPreserved("def f(a):\n    return (a + 1)\n"), "def f(a):\n    return a + 1\n")
        self.assertPreserved("def f(a):\n    return (a if a else 0)\n")
        self.assertPreserved("def f(a):\n    x = (lambda: a)\n    return x\n")
        self.assertPreserved("def f(a):\n    return (x for x in a)\n")

    def test_imports_merge_only_in_a_run_at_one_indent(self):
        merged = self.assertPreserved("import os\nimport os\nimport sys\n")
        self.assertEqual(merged, "import os\nimport sys\n")
        self.assertPreserved("import os\nif x:\n    import os\n")
        self.assertEqual(self.assertPreserved("import os\nimport sys\nimport os\n"), "import os\nimport sys\n")
        self.assertPreserved("import os\nx = 1\nimport os\n")

    def test_elif_is_flattened_without_touching_strings(self):
        flat = self.assertPreserved("if a:\n    pass\nelse:\n    if b:\n        pass\n")
        self.assertIn("elif b:", flat)
        self.assertPreserved('x = """\nelse:\n    if b:\n"""\n')


if __name__ == "__main__":
    unittest.main()
