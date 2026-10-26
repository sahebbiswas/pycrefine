"""The versioned corpus (quality/corpus): its manifest, its baselines and the limitations they cite."""
import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
CORPUS = _ROOT / "quality" / "corpus"
_spec = importlib.util.spec_from_file_location("check_differential_corpus", _ROOT / "quality" / "check_differential.py")
cd = importlib.util.module_from_spec(_spec)
sys.modules["check_differential_corpus"] = cd
_spec.loader.exec_module(cd)

VERSIONS = ["3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]


def _tuple(tag):
    return cd.parse_version(tag)


def load():
    return cd.load_corpus(CORPUS)


def baselines():
    return {tag: json.loads((CORPUS / "baselines" / f"py{tag}.json").read_text(encoding="utf-8"))
            for tag in VERSIONS}


class TestManifest(unittest.TestCase):

    def test_entries_have_complete_metadata(self):
        raw = json.loads((CORPUS / "manifest.json").read_text(encoding="utf-8"))
        for item in raw["entries"]:
            with self.subTest(entry=item["id"]):
                for key in ("id", "category", "summary", "source_python", "compile", "equivalence", "support"):
                    self.assertIn(key, item)
                self.assertTrue(item["summary"].strip())
                self.assertIn("min", item["source_python"])
                self.assertIn(item["compile"]["optimize"], (0, 1, 2))

    def test_ids_are_unique_and_match_the_path(self):
        entries = load()
        ids = [e.id for e in entries]
        self.assertEqual(len(ids), len(set(ids)))
        for e in entries:
            if e.path is not None:
                self.assertEqual(e.id, str(e.path.relative_to(CORPUS).with_suffix("")).replace("\\", "/"))
                self.assertTrue(e.path.is_file(), e.id)

    def test_every_corpus_file_has_an_entry(self):
        listed = {e.path for e in load() if e.path is not None}
        on_disk = {p for sub in ("features", "generated", "realworld") for p in (CORPUS / sub).glob("*.py")}
        self.assertEqual(sorted(on_disk - listed), [])
        self.assertEqual(sorted(listed - on_disk), [])

    def test_categories_match_the_directories_and_how_they_are_checked(self):
        for e in load():
            with self.subTest(entry=e.id):
                if e.category == "stdlib":
                    self.assertEqual((e.equivalence, e.module is not None), ("compile_only", True))
                else:
                    self.assertEqual(e.equivalence, "required")
                    self.assertTrue(e.id.startswith({"feature": "features/", "generated": "generated/",
                                                     "realworld": "realworld/"}[e.category]))

    def test_the_corpus_has_all_four_kinds_of_entry(self):
        counts = {}
        for e in load():
            counts[e.category] = counts.get(e.category, 0) + 1
        self.assertEqual(sorted(counts), sorted(cd.CATEGORIES))
        self.assertGreaterEqual(counts["feature"], 30)
        self.assertGreaterEqual(counts["generated"], 24)
        self.assertGreaterEqual(counts["realworld"], 5)
        self.assertGreaterEqual(counts["stdlib"], 6)

    def test_entries_are_small_enough_to_diagnose(self):
        for e in load():
            if e.path is not None:
                with self.subTest(entry=e.id):
                    self.assertLess(len(e.path.read_text(encoding="utf-8").splitlines()), 120)

    def test_some_entry_uses_a_non_default_compile_option(self):
        self.assertTrue(any(e.optimize for e in load()))

    def test_generated_cases_are_up_to_date(self):
        done = subprocess.run([sys.executable, str(CORPUS / "generate_control_flow.py"), "--check"],
                              capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stderr)

    def test_a_bad_manifest_is_rejected(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "manifest.json").write_text(json.dumps(
                {"schema_version": 1, "entries": [{"id": "x", "category": "feature", "path": "x.py",
                                                  "module": "os", "equivalence": "required"}]}), encoding="utf-8")
            with self.assertRaises(cd.ManifestError):
                cd.load_corpus(Path(tmp))


class TestBaselinesAndLimitations(unittest.TestCase):

    def applicable(self, entry, tag):
        return entry.applies_to(_tuple(tag))

    def test_each_baseline_covers_exactly_the_entries_that_apply(self):
        entries, recorded = load(), baselines()
        for tag in VERSIONS:
            with self.subTest(version=tag):
                data = recorded[tag]
                self.assertEqual(data["schema_version"], cd.SCHEMA_VERSION)
                self.assertEqual(data["python_version"], tag)
                self.assertEqual(set(data["entries"]), {e.id for e in entries if self.applicable(e, tag)})
                for status in data["entries"].values():
                    self.assertIn(status, cd.LEVELS)

    def test_compile_only_entries_never_claim_to_be_equivalent(self):
        recorded = baselines()
        for e in load():
            for tag in VERSIONS:
                if e.id in recorded[tag]["entries"] and e.equivalence == "compile_only":
                    self.assertIn(recorded[tag]["entries"][e.id], ("compiles", "syntax_error", "decompile_error"), e.id)

    def test_every_failing_entry_cites_an_issue_for_that_version(self):
        """A gap the corpus finds is tracked in an issue, and the manifest says which."""
        recorded = baselines()
        for e in load():
            for tag in VERSIONS:
                status = recorded[tag]["entries"].get(e.id)
                if status is None or status in cd.PASSING:
                    continue
                with self.subTest(entry=e.id, version=tag):
                    self.assertTrue(e.issues_for(tag), f"{e.id} on {tag} is {status} with no issue")

    def test_limitations_do_not_outlive_the_gap(self):
        """A limitation names only versions where the entry still fails, so a fix removes it."""
        recorded = baselines()
        for e in load():
            for lim in e.limitations:
                self.assertIsInstance(lim.issue, int)
                self.assertGreater(lim.issue, 0)
                self.assertTrue(lim.summary.strip())
                for tag in (lim.versions or VERSIONS):
                    status = recorded[tag]["entries"].get(e.id)
                    if status is None:
                        continue  # the entry does not apply to that version
                    with self.subTest(entry=e.id, issue=lim.issue, version=tag):
                        self.assertNotIn(status, cd.PASSING, f"#{lim.issue} is listed but {e.id} passes on {tag}")

    def test_support_summarizes_the_baselines(self):
        recorded = baselines()
        for e in load():
            statuses = [recorded[tag]["entries"][e.id] for tag in VERSIONS if e.id in recorded[tag]["entries"]]
            failing = [s for s in statuses if s not in cd.PASSING]
            expected = "supported" if not failing else ("unsupported" if len(failing) == len(statuses) else "partial")
            with self.subTest(entry=e.id):
                self.assertEqual(e.support, expected)

    def test_every_version_has_representative_coverage(self):
        """Each supported version runs and passes entries of every category."""
        entries, recorded = load(), baselines()
        for tag in VERSIONS:
            for category in cd.CATEGORIES:
                ids = [e.id for e in entries if e.category == category and self.applicable(e, tag)]
                passing = [i for i in ids if recorded[tag]["entries"][i] in cd.PASSING]
                with self.subTest(version=tag, category=category):
                    self.assertGreaterEqual(len(ids), 5 if category != "realworld" else 4)
                    self.assertGreaterEqual(len(passing), 1)


class TestSelection(unittest.TestCase):

    def test_entries_are_filtered_by_python_version_and_category(self):
        entries = load()
        old = {e.id for e in cd.select_entries(entries, [], None, (3, 9))}
        new = {e.id for e in cd.select_entries(entries, [], None, (3, 12))}
        self.assertNotIn("features/modern_generics", old)
        self.assertIn("features/modern_generics", new)
        stdlib = cd.select_entries(entries, [], "stdlib", (3, 12))
        self.assertTrue(stdlib and all(e.category == "stdlib" for e in stdlib))

    def test_entries_can_be_named_by_file_name(self):
        (entry,) = cd.select_entries(load(), ["flow_while"], None, (3, 12))
        self.assertEqual(entry.id, "features/flow_while")
        with self.assertRaises(LookupError):
            cd.select_entries(load(), ["no_such_entry"], None, (3, 12))

    def test_a_module_entry_finds_the_running_interpreters_source(self):
        entry = [e for e in load() if e.id == "stdlib/bisect"][0]
        self.assertTrue(entry.source_path().name.startswith("bisect"))


if __name__ == "__main__":
    unittest.main()
