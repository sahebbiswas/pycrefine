"""Guards for the decompiler's state objects (issues #37 and #83)."""

import ast
import collections
import dataclasses
import os
import re
import sys
import unittest
from pathlib import Path

from pycrefine import get_decompiler
from pycrefine.decompiler.generic import DecompilerGeneric
from pycrefine.decompiler.state import (
    FIELD_OWNER,
    DocState,
    EmissionState,
    PrescanState,
)

GROUPS = {"prescan": PrescanState, "emit": EmissionState, "doc": DocState}
SHARED_WITH_HANDLER_SECTION = (
    "exc_handler_jump_offsets",
    "exc_handler_continue_offsets",
    "while_header_targets",
    "while_body_offsets",
    "dup_guard_exits",
)


def _fields(cls):
    return {f.name for f in dataclasses.fields(cls)}


class TestFieldOwnership(unittest.TestCase):
    def test_every_field_has_exactly_one_owner(self):
        for group, cls in GROUPS.items():
            for name in _fields(cls):
                with self.subTest(group=group, field=name):
                    self.assertEqual(FIELD_OWNER.get(name), group)

    def test_owner_table_names_only_real_fields(self):
        for name, group in FIELD_OWNER.items():
            with self.subTest(field=name):
                self.assertIn(name, _fields(GROUPS[group]))

    def test_field_names_are_unique_across_groups(self):
        seen = set()
        for cls in GROUPS.values():
            names = _fields(cls)
            self.assertFalse(seen & names)
            seen |= names


class TestHandlerSectionTransfer(unittest.TestCase):
    def test_prescan_shares_exactly_the_listed_fields(self):
        parent = PrescanState()
        parent.while_body_offsets.add(7)
        sub = parent.for_handler_section()
        for name in _fields(PrescanState):
            same = getattr(sub, name) is getattr(parent, name)
            with self.subTest(field=name):
                self.assertEqual(same, name in SHARED_WITH_HANDLER_SECTION)

    def test_emission_state_starts_fresh_with_base_indent(self):
        parent = EmissionState()
        parent.except_header_indents.append(4)
        sub = parent.for_handler_section()
        self.assertEqual(sub.except_header_indents, [0])
        self.assertEqual(parent.except_header_indents[-1], 4)


# A while loop in an except clause, inside a for loop: the clause is decompiled as a section by
# a sub-decompiler, which has to know where the loop's header is.
_WHILE_IN_HANDLER = """\
def f(items, q):
    for x in items:
        try:
            q.get(x)
        except ValueError:
            while q.n > 0:
                q.n -= 1
"""


@unittest.skipIf(sys.version_info < (3, 11), "handler sections are only built from exception tables (3.11+)")
class TestHandlerSectionCaller(unittest.TestCase):
    """The decompiler hands the while headers to a handler section (not just the state class).

    The rendered text for this shape is still imperfect on some versions (#105, #233), so the
    test follows what the caller passes on rather than pinning that text.
    """

    def _decompile(self):
        """Decompile the fixture, recording each (parent, section) prescan pair. Returns (source, calls)."""
        from .test_helpers import _compile

        path = _compile(_WHILE_IN_HANDLER)
        original = PrescanState.for_handler_section
        calls = []

        def spy(parent):
            sub = original(parent)
            calls.append((parent, sub))
            return sub

        PrescanState.for_handler_section = spy
        try:
            return get_decompiler(path).decompile(), calls
        finally:
            PrescanState.for_handler_section = original
            os.unlink(path)

    def test_section_is_built_with_the_parents_while_headers(self):
        _, calls = self._decompile()
        self.assertTrue(calls, "the fixture no longer reaches a handler section")
        self.assertTrue(any(parent.while_header_targets for parent, _ in calls),
                        "the fixture has no while loop for the section to need")
        for parent, sub in calls:
            self.assertIs(sub.while_header_targets, parent.while_header_targets)


_MUTATORS = {"add", "append", "discard", "pop", "update", "clear", "extend", "remove", "insert", "setdefault", "sort"}
_STATE_ATTRS = ("prescan", "emit", "doc")


def _state_field_of(node):
    """The field of ``self.<prescan|emit|doc>.<field>`` that *node* names (through subscripts), or None."""
    while isinstance(node, ast.Subscript):
        node = node.value
    if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Attribute)
            and node.value.attr in _STATE_ATTRS):
        return node.attr
    return None


def _writers_by_field():
    """Field -> names of the functions in the decompiler package that assign, mutate or delete it."""
    writers = collections.defaultdict(set)
    for path in (Path(__file__).resolve().parent.parent / "src" / "pycrefine" / "decompiler").glob("*.py"):
        for fn in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
            if not isinstance(fn, ast.FunctionDef):
                continue
            for node in ast.walk(fn):
                targets = []
                if isinstance(node, ast.Assign):
                    targets = node.targets
                elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
                    targets = [node.target]
                elif isinstance(node, ast.Delete):
                    targets = node.targets
                elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr in _MUTATORS:
                    targets = [node.func.value]
                for target in targets:
                    field = _state_field_of(target)
                    if field:
                        writers[field].add(fn.name)
    return writers


class TestStateTableMatchesCode(unittest.TestCase):
    """The "Written by" column of docs/architecture.md names functions that really write the field."""

    def test_listed_writers_write_the_field(self):
        doc = (Path(__file__).resolve().parent.parent / "docs" / "architecture.md").read_text(encoding="utf-8")
        writers = _writers_by_field()
        checked = 0
        for match in re.finditer(r"^\| `(\w+)` \| .* \| ((?:`\w+`(?:, )?)+) \|$", doc, flags=re.M):
            field, cell = match.groups()
            listed = set(re.findall(r"`(\w+)`", cell))
            checked += 1
            with self.subTest(field=field):
                self.assertTrue(writers.get(field), f"nothing in the package writes {field}")
                self.assertEqual(sorted(listed - writers[field]), [],
                                 f"architecture.md lists functions that do not write {field}")
        self.assertGreater(checked, 40, "the state tables were not found")


class TestNoAdHocAttributes(unittest.TestCase):
    def test_fresh_decompiler_owns_state_objects(self):
        import inspect

        src = inspect.getsource(DecompilerGeneric.__init__)
        for attr in ("self.prescan", "self.emit", "self.doc"):
            self.assertIn(attr, src)

    def test_decompiler_code_does_not_probe_with_hasattr(self):
        import pathlib

        base = pathlib.Path(__import__("pycrefine").__file__).parent / "decompiler"
        for path in base.glob("*.py"):
            with self.subTest(file=path.name):
                self.assertNotIn("hasattr(self", path.read_text())


if __name__ == "__main__":
    unittest.main()
