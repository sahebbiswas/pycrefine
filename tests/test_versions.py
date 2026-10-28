"""The version table (versions.py) is the one place that decides what a magic number means."""
import os
import struct
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from pycrefine import get_decompiler
from pycrefine import loader
from pycrefine import versions as vs

ROOT = Path(__file__).resolve().parent.parent


class TestTable(unittest.TestCase):

    def test_bands_ascend_without_overlap(self):
        previous = None
        for row in vs.VERSIONS:
            self.assertLessEqual(row.magic_low, row.magic_high, row.tag)
            if previous is not None:
                self.assertGreater(row.magic_low, previous.magic_high, f"{previous.tag} overlaps {row.tag}")
                self.assertGreater(row.version, previous.version)
            previous = row

    def test_support_level_decides_what_a_row_must_have(self):
        for row in vs.VERSIONS:
            with self.subTest(version=row.tag):
                self.assertIn(row.support, vs.SUPPORT_LEVELS)
                if row.reconstructable:
                    self.assertIn(row.backend, loader._BACKENDS)
                else:
                    self.assertIsNone(row.backend)
                    self.assertFalse(row.cross_host)

    def test_a_tested_version_has_ci_and_recorded_baselines(self):
        for row in vs.VERSIONS:
            if row.support != vs.TESTED:
                continue
            minor = row.version[1]
            with self.subTest(version=row.tag):
                self.assertTrue((ROOT / ".github" / "workflows" / f"ci-py3{minor}.yml").exists())
                self.assertTrue((ROOT / "quality" / "baselines" / f"py{row.tag}.json").exists())
                self.assertTrue((ROOT / "quality" / "corpus" / "baselines" / f"py{row.tag}.json").exists())

    def test_every_supported_version_has_a_backend_that_matches_its_features(self):
        by_tag = {row.tag: row.backend for row in vs.VERSIONS}
        self.assertEqual(by_tag["3.9"], "Decompiler39")
        self.assertEqual({by_tag[t] for t in ("3.10", "3.11", "3.12")}, {"Decompiler311Plus"})
        self.assertEqual({by_tag[t] for t in ("3.13", "3.14")}, {"Decompiler314"})

    def test_only_3_9_reads_across_hosts(self):
        self.assertEqual([row.tag for row in vs.VERSIONS if row.cross_host], ["3.9"])


class TestLookup(unittest.TestCase):

    def test_final_release_numbers(self):
        for number, tag in ((3425, "3.9"), (3439, "3.10"), (3495, "3.11"), (3531, "3.12"), (3571, "3.13"),
                            (3627, "3.14"), (3413, "3.8"), (3394, "3.7")):
            self.assertEqual(vs._get_python_version_from_magic(number), tag, number)

    def test_newer_than_the_table_is_future_not_the_newest_backend(self):
        self.assertIsNone(vs.version_for_magic(3650))
        self.assertTrue(vs.is_future_magic(3650))
        self.assertTrue(vs.is_future_magic(vs.FUTURE_MAGIC_LIMIT))
        self.assertEqual(vs._get_python_version_from_magic(3650), "3.15+")

    def test_implausible_numbers_are_unrecognized(self):
        for number in (0, 227, 3000, vs.FUTURE_MAGIC_LIMIT + 1, 0xBEEF):
            self.assertIsNone(vs._get_python_version_from_magic(number), number)

    def test_supported_range(self):
        self.assertEqual(vs.supported_range(), "3.9 to 3.14")

    def test_supported_range_needs_a_reconstructable_version(self):
        with mock.patch.object(vs, "VERSIONS", ()):
            with self.assertRaises(ValueError):
                vs.supported_range()


class TestLoaderUsesTheTable(unittest.TestCase):

    def pyc(self, version_id):
        with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
            f.write(struct.pack("<I", (0x0A0D << 16) | version_id) + b"\x00" * 12)
            return f.name

    def refused(self, version_id):
        path = self.pyc(version_id)
        try:
            with self.assertRaises(ValueError) as caught:
                get_decompiler(path)
            return str(caught.exception)
        finally:
            os.unlink(path)

    def test_a_newer_python_is_refused_with_its_name(self):
        message = self.refused(3660)
        self.assertIn("Invalid or unsupported Python magic number", message)
        self.assertIn("appears to be from Python 3.15+", message)
        self.assertIn("supports Python 3.9 to 3.14", message)

    def test_an_older_python_is_refused_with_its_name(self):
        for number, tag in ((3413, "3.8"), (3418, "3.8"), (3394, "3.7"), (3310, "3.4")):
            self.assertIn(f"appears to be from Python {tag}", self.refused(number))

    def test_an_unknown_number_is_called_unrecognized(self):
        self.assertIn("unrecognized or may be corrupt", self.refused(227))

    def test_each_supported_band_selects_its_backend(self):
        # The host check refuses a foreign 3.10+ file before any parsing, so only 3.9 loads anywhere;
        # the others are covered through the table above and by the CI runs under each Python.
        for row in vs.VERSIONS:
            if not row.reconstructable:
                continue
            self.assertIs(loader._BACKENDS[row.backend], getattr(loader, row.backend))


if __name__ == "__main__":
    unittest.main()
