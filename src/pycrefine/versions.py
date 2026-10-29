"""The table of Python versions pycrefine knows about, and how it treats each.

Everything that depends on the version of a ``.pyc`` is read from ``VERSIONS`` below: which
magic numbers belong to it, which backend rebuilds its source, whether its bytecode can be
read on a host of another minor version, and how far it is supported. Adding a version is a
new row and a backend (see ``docs/extending.md``), not a new ``if`` in the loader.

Support is a ladder; each level includes the one above it:

``recognized``
    The magic number identifies the version, so an error can name it. No source is rebuilt.
``reconstructable``
    A backend exists and rebuilds source for it.
``tested``
    A backend exists, CI runs the test suite on it and the correctness and differential
    baselines are recorded for it.

Whether the bytecode can be *parsed* on another host is a separate fact (``cross_host``).
"""
from dataclasses import dataclass
from typing import Optional, Tuple

RECOGNIZED = "recognized"
RECONSTRUCTABLE = "reconstructable"
TESTED = "tested"
SUPPORT_LEVELS = (RECOGNIZED, RECONSTRUCTABLE, TESTED)


@dataclass(frozen=True)
class PythonVersion:
    """One CPython minor version: its magic numbers and what pycrefine does with it."""
    version: Tuple[int, int]
    magic_low: int                 # inclusive, magic number & 0xFFFF
    magic_high: int                # inclusive
    support: str = RECOGNIZED
    backend: Optional[str] = None  # name of the decompiler class; None when nothing is rebuilt
    cross_host: bool = False       # bytecode can be read under a host of another minor version

    @property
    def tag(self) -> str:
        return f"{self.version[0]}.{self.version[1]}"

    @property
    def reconstructable(self) -> bool:
        return self.support in (RECONSTRUCTABLE, TESTED)


# Magic-number bands, from the history in CPython's Lib/importlib/_bootstrap_external.py.
# Final releases: 3.9 = 3425, 3.10 = 3439, 3.11 = 3495, 3.12 = 3531, 3.13 = 3571, 3.14 = 3627.
# Bands are generous so pre-releases are accepted.
VERSIONS: Tuple[PythonVersion, ...] = (
    PythonVersion((3, 4), 3310, 3349),
    PythonVersion((3, 5), 3350, 3378),
    PythonVersion((3, 6), 3379, 3389),
    PythonVersion((3, 7), 3390, 3399),
    PythonVersion((3, 8), 3400, 3419),
    PythonVersion((3, 9), 3420, 3429, TESTED, "Decompiler39", cross_host=True),
    PythonVersion((3, 10), 3430, 3449, TESTED, "Decompiler311Plus"),
    PythonVersion((3, 11), 3450, 3499, TESTED, "Decompiler311Plus"),
    PythonVersion((3, 12), 3500, 3549, TESTED, "Decompiler311Plus"),
    # 3.13 introduced the fused superinstructions that only Decompiler314 understands.
    PythonVersion((3, 13), 3550, 3599, TESTED, "Decompiler314"),
    PythonVersion((3, 14), 3600, 3649, TESTED, "Decompiler314"),
)

# Magic numbers above the newest band, up to this limit, belong to a Python newer than this
# table. It is refused rather than decompiled with the newest backend. Anything higher is
# not a plausible magic number at all (a source file or archive read as a header).
FUTURE_MAGIC_LIMIT = 5000
FUTURE_LABEL = "3.15+"


def version_for_magic(version_id: int) -> Optional[PythonVersion]:
    """The row whose band holds *version_id* (the magic number & 0xFFFF), or None."""
    for row in VERSIONS:
        if row.magic_low <= version_id <= row.magic_high:
            return row
    return None


def is_future_magic(version_id: int) -> bool:
    """True for a magic number above every band that could still be a real one: a newer Python."""
    return VERSIONS[-1].magic_high < version_id <= FUTURE_MAGIC_LIMIT


def supported_range() -> str:
    """The reconstructable versions as text, e.g. ``3.9 to 3.14``.

    Raises:
        ValueError: if the table lists no reconstructable version.
    """
    tags = [row.tag for row in VERSIONS if row.reconstructable]
    if not tags:
        raise ValueError("no reconstructable Python version is configured")
    return f"{tags[0]} to {tags[-1]}"


def _get_python_version_from_magic(version_id: int) -> Optional[str]:
    """Return a Python version string (e.g., '3.12') corresponding to the given magic number."""
    row = version_for_magic(version_id)
    if row is not None:
        return row.tag
    return FUTURE_LABEL if is_future_magic(version_id) else None
