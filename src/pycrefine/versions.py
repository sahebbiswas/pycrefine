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

Whether the bytecode can be read on a host of another minor version is a separate fact (``cross_host``):
the 3.9 backend decodes it itself, and ``opcodes.decode`` does for 3.10 and later (#94).
"""
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

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
    PythonVersion((3, 10), 3430, 3449, TESTED, "Decompiler311Plus", cross_host=True),
    PythonVersion((3, 11), 3450, 3499, TESTED, "Decompiler311Plus", cross_host=True),
    PythonVersion((3, 12), 3500, 3549, TESTED, "Decompiler311Plus", cross_host=True),
    # 3.13 introduced the fused superinstructions that only Decompiler314 understands.
    PythonVersion((3, 13), 3550, 3599, TESTED, "Decompiler314", cross_host=True),
    PythonVersion((3, 14), 3600, 3649, TESTED, "Decompiler314", cross_host=True),
)

# Magic numbers above the newest band, up to this limit, belong to a Python newer than this
# table. It is refused rather than decompiled with the newest backend. Anything higher is
# not a plausible magic number at all (a source file or archive read as a header).
FUTURE_MAGIC_LIMIT = 5000
FUTURE_LABEL = "3.15+"


# ---------------------------------------------------------------------------------------------
# Capabilities: what the bytecode of a version looks like.
#
# The decompiler asks `supports("name", version)` instead of comparing the version with a tuple,
# so every difference between versions is named, described and listed here, and adding a version
# means reading this table. Each capability holds for the versions in one or more half-open
# intervals ``[since, until)``; ``None`` means unbounded. tests/test_capabilities.py checks that
# every name the code asks for is defined here, that every name defined here is asked for, and
# that no comparison with a version tuple is left in the decompiler.
# ---------------------------------------------------------------------------------------------
Version = Tuple[int, int]
Interval = Tuple[Optional[Version], Optional[Version]]


@dataclass(frozen=True)
class Capability:
    """One version-dependent property of the bytecode and the intervals of versions that have it."""
    description: str
    intervals: Tuple[Interval, ...]


def _since(version: Version, description: str) -> Capability:
    return Capability(description, ((version, None),))


CAPABILITIES: Dict[str, Capability] = {
    # 3.10
    "anext_opcode": _since((3, 10), "GET_ANEXT is read as the `__anext__` call of an async loop."),
    "normalised_block_stack": Capability(
        "The exception handlers are found from the block stack (SETUP_FINALLY and POP_BLOCK) and rewritten "
        "to the exception-table form before the passes run: 3.10 only.", (((3, 10), (3, 11)),)),
    "loop_condition_copy": Capability(
        "The condition of a `while` is copied to the bottom of the body, with its own exit jumps.",
        (((3, 10), (3, 12)),)),
    "opcode_tables": _since((3, 10), "The instructions can be decoded from the packaged opcode tables, so a host of another version reads them."),
    # 3.11
    "exception_table": _since((3, 11), "Exception handlers are listed in an exception table (no SETUP_FINALLY)."),
    "null_sentinels": _since((3, 11), "A NULL is pushed under a callee and popped with the call (PUSH_NULL, LOAD_GLOBAL bit 0)."),
    "awaited_exit_args": _since((3, 11), "GET_AWAITABLE's argument says whether it awaits the entry or the exit of an `async with`."),
    "store_name_batches": _since((3, 11), "The stores of one statement are grouped, so a name stored twice in one is one batch."),
    "load_method_null_slot": _since((3, 11), "LOAD_METHOD leaves [NULL, method] under the call's arguments."),
    # 3.12
    "cold_handlers": _since((3, 12), "Exception handlers are laid out after all the ordinary code, not inline after their try."),
    "chain_comparison_values": _since((3, 12), "A chained comparison used as a value is a run of links with a shared clean-up."),
    "iterator_drop_uses_jump_backward": _since((3, 12), "The jump after the drop of a `for` loop's iterator is JUMP_BACKWARD."),
    "load_attr_method_flag": _since((3, 12), "LOAD_ATTR bit 0 marks a method load, which pushes the object and a NULL."),
    # 3.13
    "dedented_docstrings": _since((3, 13), "The compiler strips the indentation of a docstring, so the text has to be re-indented."),
    "separate_iterator_pop": _since((3, 13), "The end of a `for` loop pops the iterator with its own POP_ITER."),
    # 3.14
    "docstring_flag": _since((3, 14), "A function's docstring is co_consts[0] only when CO_HAS_DOCSTRING is set."),
    "lazy_annotations": _since((3, 14), "Annotations are computed by an `__annotate__` function, not stored while the body runs."),
    "call_ex_keyword_slot": _since((3, 14), "CALL_FUNCTION_EX always has a keyword slot (a NULL when unused), with no flag."),
    "inverted_loop_guards": _since((3, 14), "A loop's guard can jump into the body, with the fall-through leaving the loop."),
    "condition_tested_once": _since((3, 14), "A `while` condition is tested once, at the loop head, and not copied to the bottom."),
    "loop_head_condition": Capability(
        "The condition of a `while` is tested at the head of the loop, so a conditional jump above the head is "
        "an enclosing `if`: 3.9 and 3.14.", ((None, (3, 10)), ((3, 14), None))),
}


def supports(capability: str, version: Version) -> bool:
    """Whether the bytecode of *version* has *capability*.

    Raises:
        KeyError: if *capability* is not in ``CAPABILITIES`` (a misspelt name must not read as False).
    """
    version = (int(version[0]), int(version[1]))
    return any((since is None or version >= since) and (until is None or version < until)
               for since, until in CAPABILITIES[capability].intervals)


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
