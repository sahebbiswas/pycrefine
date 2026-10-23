"""Test-session setup.

With ``PYCREFINE_REQUIRE_INSTALLED=1`` (the release workflow sets it), the suite runs
against the installed distribution: ``pycrefine`` is imported here, before any test
module puts the repository root on ``sys.path``, and must come from site-packages.
"""
import os

if os.environ.get("PYCREFINE_REQUIRE_INSTALLED"):
    import pycrefine

    if "site-packages" not in pycrefine.__file__:
        raise RuntimeError(
            f"PYCREFINE_REQUIRE_INSTALLED is set but pycrefine was imported from {pycrefine.__file__}; "
            "run pytest from outside the checkout with --import-mode=importlib")
