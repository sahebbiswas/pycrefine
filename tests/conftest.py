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
# the tests, and runs them one of two ways:
#
# * ``"mode": "xfail"`` (the default): a strict expected failure. The suite stays green
#   while the gap exists, and fails as soon as the test passes (an "unexpected pass"),
#   which is the prompt to delete the entry. Fixing a gap therefore includes removing its
#   entries.
# * ``"mode": "skip"``: for tests that check several cases with ``self.subTest``. pytest
#   reports each failing subtest on its own, so the test as a whole cannot be marked as
#   an expected failure; it is skipped on that version instead.

KNOWN_FAILURES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "known_failures.json")
KNOWN_FAILURE_MODES = ("xfail", "skip")


def load_known_failures(path=KNOWN_FAILURES_PATH):
    """Return ``{"3.11": [{"issue": 105, "reason": "...", "tests": ["tests/...::Class::test"]}]}``."""
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def pytest_collection_modifyitems(config, items):
    import pytest

    tag = f"{sys.version_info[0]}.{sys.version_info[1]}"
    marks = {}
    for group in load_known_failures().get(tag, []):
        reason = f"known failure on Python {tag}, tracked in #{group['issue']}: {group['reason']}"
        if group.get("mode", "xfail") == "skip":
            mark = pytest.mark.skip(reason=reason)
        else:
            mark = pytest.mark.xfail(reason=reason, strict=True)
        for node_id in group["tests"]:
            marks[node_id] = mark
    for item in items:
        mark = marks.get(item.nodeid)
        if mark is not None:
            item.add_marker(mark)
