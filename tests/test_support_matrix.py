"""docs/support-matrix.md is generated from the version table and the files the gates keep (#84)."""
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from pycrefine.versions import VERSIONS  # noqa: E402  pylint: disable=wrong-import-position

DOC = ROOT / "docs" / "support-matrix.md"
START, END = "<!-- support-matrix:start -->", "<!-- support-matrix:end -->"


def _yes(flag):
    return "yes" if flag else "no"


def matrix():
    """The markdown table: one row per Python version in the table, newest first."""
    rows = ["| Python | Support | Backend | Reads on another host | CI workflow | Correctness baseline | "
            "Differential baseline |", "| --- | --- | --- | --- | --- | --- | --- |"]
    for row in sorted(VERSIONS, key=lambda r: r.version, reverse=True):
        tag = row.tag
        workflow = ROOT / ".github" / "workflows" / f"ci-py{tag.replace('.', '')}.yml"
        correctness = ROOT / "quality" / "baselines" / f"py{tag}.json"
        differential = ROOT / "quality" / "corpus" / "baselines" / f"py{tag}.json"
        rows.append(f"| {tag} | {row.support} | {'`' + row.backend + '`' if row.backend else '-'} | "
                    f"{_yes(row.cross_host)} | {_yes(workflow.is_file())} | {_yes(correctness.is_file())} | "
                    f"{_yes(differential.is_file())} |")
    return "\n".join(rows)


def render(text):
    head, rest = text.split(START, 1)
    _, tail = rest.split(END, 1)
    return f"{head}{START}\n{matrix()}\n{END}{tail}"


class TestSupportMatrix(unittest.TestCase):

    def test_the_page_matches_the_table(self):
        text = DOC.read_text(encoding="utf-8")
        self.assertEqual(text, render(text),
                         "docs/support-matrix.md is out of date: run python3 tests/test_support_matrix.py --write")

    def test_a_tested_version_has_its_workflow_and_both_baselines(self):
        for row in VERSIONS:
            if row.support != "tested":
                continue
            tag = row.tag
            with self.subTest(version=tag):
                self.assertTrue((ROOT / ".github" / "workflows" / f"ci-py{tag.replace('.', '')}.yml").is_file())
                self.assertTrue((ROOT / "quality" / "baselines" / f"py{tag}.json").is_file())
                self.assertTrue((ROOT / "quality" / "corpus" / "baselines" / f"py{tag}.json").is_file())

    def test_a_version_without_a_backend_is_only_recognized(self):
        for row in VERSIONS:
            with self.subTest(version=row.tag):
                self.assertEqual(row.backend is None, row.support == "recognized")


if __name__ == "__main__":
    if "--write" in sys.argv:
        DOC.write_text(render(DOC.read_text(encoding="utf-8")), encoding="utf-8")
    else:
        unittest.main()
