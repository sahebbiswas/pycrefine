"""Test-session setup.

With ``PYCREFINE_REQUIRE_INSTALLED=1`` (the release workflow sets it), the suite runs
against the installed distribution: ``pycrefine`` is imported here, before any test
module puts the repository root on ``sys.path``, and must come from site-packages.
"""
import json
import os
import sys

if os.environ.get("PYCREFINE_REQUIRE_INSTALLED"):
    import pycrefine

    if "site-packages" not in pycrefine.__file__:
        raise RuntimeError(
            f"PYCREFINE_REQUIRE_INSTALLED is set but pycrefine was imported from {pycrefine.__file__}; "
            "run pytest from outside the checkout with --import-mode=importlib")


# --- Known failures on the partly supported Python versions -------------------------
#
# tests/known_failures.json lists, per Python minor version, the tests that fail on that
# version because of a decompiler gap tracked in an issue. Each group names the issue and
# the reason, and lists the failures in one or both of two ways:
#
# * ``"tests"``: whole tests, as a strict expected failure. The suite stays green while
#   the gap exists, and fails as soon as the test passes (an "unexpected pass"), which is
#   the prompt to delete the entry. Fixing a gap therefore includes removing its entries.
# * ``"cases"``: for a test that checks several cases with ``self.subTest(case=name)``,
#   a map from the test to the names of the cases that fail. pytest reports every
#   subtest on its own, so the test cannot be marked as a whole; instead only the listed
#   cases are expected to fail, and the others keep running and keep guarding against
#   regressions. A listed case that passes fails as an "unexpected pass", like a test.

KNOWN_FAILURES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_failures.json")


def load_known_failures(path=KNOWN_FAILURES_PATH):
    """Return ``{"3.11": [{"issue": 105, "reason": "...", "tests": ["tests/...::Class::test"]}]}``."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _python_tag():
    return f"{sys.version_info[0]}.{sys.version_info[1]}"


def _short_id(node_id):
    """``tests/test_x.py::Class::test_y`` -> ``("test_x", "Class", "test_y")``."""
    path, class_name, method = node_id.split("::")
    return os.path.splitext(os.path.basename(path))[0], class_name, method


def expected_failing_cases(tag=None):
    """
    ``{(module, class, method): {case: reason}}`` for the subtest cases expected to fail
    on this Python, from the ``"cases"`` entries of tests/known_failures.json.
    """
    expected = {}
    tag = tag or _python_tag()
    for group in load_known_failures().get(tag, []):
        reason = f"known failure on Python {tag}, tracked in #{group['issue']}: {group['reason']}"
        for node_id, names in group.get("cases", {}).items():
            cases = expected.setdefault(_short_id(node_id), {})
            for name in names:
                cases[name] = reason
    return expected


def _expect_known_subtest_failures():
    """
    Make ``TestCase.subTest(case=...)`` treat the cases listed under ``"cases"`` as
    strict expected failures: a failure inside them is swallowed, and finishing without
    one is reported as a failing subtest, so a fixed case has to come off the list.
    """
    import contextlib
    import unittest

    expected = expected_failing_cases()
    if not expected:
        return
    original = unittest.TestCase.subTest

    @contextlib.contextmanager
    def sub_test(self, msg=unittest.case._subtest_msg_sentinel, **params):
        reason = None
        case = params.get("case")
        if isinstance(case, str):
            reason = expected.get(tuple(self.id().split(".")[-3:]), {}).get(case)
        if reason is None:
            with original(self, msg, **params):
                yield
            return
        try:
            yield
        except Exception:  # pylint: disable=broad-except
            return  # the expected failure
        with original(self, msg, **params):
            self.fail(f"unexpectedly passed ({reason}); remove it from tests/known_failures.json")

    unittest.TestCase.subTest = sub_test


_expect_known_subtest_failures()


def pytest_collection_modifyitems(config, items):
    import pytest

    tag = _python_tag()
    marks = {}
    for group in load_known_failures().get(tag, []):
        reason = f"known failure on Python {tag}, tracked in #{group['issue']}: {group['reason']}"
        mark = pytest.mark.xfail(reason=reason, strict=True)
        for node_id in group.get("tests", []):
            marks[node_id] = mark
    for item in items:
        mark = marks.get(item.nodeid)
        if mark is not None:
            item.add_marker(mark)
