"""Guards for the decompiler's state objects (issues #37 and #83)."""

import dataclasses
import os
import sys
import unittest

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
    def test_prescan_shares_exactly_three_fields(self):
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
