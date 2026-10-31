"""Constructs the decompiler cannot rebuild must say so, and clean code must stay clean."""
import sys
import unittest

from pycrefine import diagnostics as dg

from .test_helpers import _compile
from pycrefine import get_decompiler
import os

# (id, first Python, last Python, source, diagnostic kind that must be reported)
UNSUPPORTED = [
    ("match", (3, 10), None,
     "def f(x):\n    match x:\n        case [a, b]:\n            return a\n    return 0\n",
     dg.UNSUPPORTED_VERSION_CONSTRUCT),
    ("async_with", (3, 9), None,
     "async def f(x):\n    async with x as y:\n        return y\n",
     dg.UNSUPPORTED_VERSION_CONSTRUCT),
    ("await", (3, 9), None,
     "async def f(x):\n    return await x\n",
     dg.UNSUPPORTED_VERSION_CONSTRUCT),
    ("type_parameters", (3, 12), None,
     "def f[T](x: T) -> T:\n    return x\n",
     dg.UNSUPPORTED_VERSION_CONSTRUCT),
    ("try_blocks_on_3_10", (3, 10), (3, 10),
     "def f():\n    try:\n        return 1\n    except ValueError:\n        return 2\n",
     dg.INCOMPLETE_EXCEPTION),
]

CLEAN = [
    "def f(a, b):\n    return a + b\n",
    "x = [i * 2 for i in range(3)]\n",
    "class C:\n    def m(self):\n        return 1\n",
    "def g(items):\n    total = 0\n    for i in items:\n        total += i\n    return total\n",
]


def _decompile(source, strict=False):
    pyc = _compile(source)
    try:
        dec = get_decompiler(pyc, strict=strict)
        dec.decompile()
        return dec
    finally:
        os.unlink(pyc)


class TestUnsupportedConstructsReport(unittest.TestCase):
    def test_each_construct_reports_its_kind(self):
        for name, first, last, source, kind in UNSUPPORTED:
            host = sys.version_info[:2]
            if host < first or (last is not None and host > last):
                continue
            with self.subTest(construct=name):
                dec = _decompile(source)
                self.assertIn(kind, {d.kind for d in dec.diagnostics},
                              f"{name} no longer reports {kind}: {[str(d) for d in dec.diagnostics]}")

    def test_each_construct_raises_in_strict_mode(self):
        for name, first, last, source, kind in UNSUPPORTED:
            host = sys.version_info[:2]
            if host < first or (last is not None and host > last):
                continue
            with self.subTest(construct=name):
                with self.assertRaises(dg.DecompilationError):
                    _decompile(source, strict=True)


class TestCleanCodeStaysClean(unittest.TestCase):
    def test_plain_code_has_no_diagnostics_even_in_strict_mode(self):
        for i, source in enumerate(CLEAN):
            with self.subTest(case=i):
                dec = _decompile(source, strict=True)
                self.assertEqual(dec.diagnostics, [])


class TestSliceSubDecompilerState(unittest.TestCase):
    """A comprehension slice is run by a fresh sub-decompiler that never saw the prescans."""

    SOURCE = (
        "import re\n"
        "def dedent(text, margins):\n"
        "    while text:\n"
        "        text = re.sub(r'x', '', text)\n"
        "    return [m for m in margins if m and (m[0] if len(m) > 1 else m)]\n"
    )

    def test_fresh_decompiler_has_empty_while_header_state(self):
        pyc = _compile("x = 1\n")
        try:
            dec = get_decompiler(pyc)
            self.assertEqual(dec.prescan.while_header_targets, {})
        finally:
            os.unlink(pyc)

    def test_while_loop_beside_a_comprehension_reports_no_internal_error(self):
        dec = _decompile(self.SOURCE)
        self.assertNotIn(dg.INTERNAL_ERROR, [d.kind for d in dec.diagnostics])


if __name__ == "__main__":
    unittest.main()
