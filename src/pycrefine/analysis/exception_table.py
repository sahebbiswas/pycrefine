"""Read the exception table of a 3.11+ code object (``co_exceptiontable``) without ``dis``.

``dis`` decodes with the running interpreter's conventions; this reader takes the raw bytes, so
it gives the same answer for a table written by another minor version (#94).

The table is a run of entries, each four variable-length integers: the first byte of a number
carries 6 bits of it, bit 6 (0x40) says another byte follows, and bit 7 (0x80) marks the first byte
of an entry. The numbers are the start, the length and the target, all in 2-byte code units, and
the depth with the ``lasti`` flag in its lowest bit.
"""
from itertools import chain
from typing import Iterator, List, NamedTuple, Sequence


class ExceptionTableEntry(NamedTuple):
    """One protected range: the same fields, in the same units (bytes), as ``dis``'s entry."""
    start: int
    end: int
    target: int
    depth: int
    lasti: bool


def _varint(data: Iterator[int]) -> int:
    """The next number; ``ValueError`` when the bytes end inside it."""
    try:
        byte = next(data)
        value = byte & 63
        while byte & 64:
            value <<= 6
            byte = next(data)
            value |= byte & 63
    except StopIteration:
        raise ValueError("the exception table ends inside an entry") from None
    return value


def parse_exception_table(table: Sequence[int]) -> List[ExceptionTableEntry]:
    """
    The entries of an exception table, in table order.

    Raises ``ValueError`` when the bytes end inside an entry (a table cut off or corrupt).
    """
    data = iter(table)
    entries: List[ExceptionTableEntry] = []
    for first in data:
        start = _varint(chain((first,), data)) * 2
        length = _varint(data) * 2
        target = _varint(data) * 2
        depth_and_lasti = _varint(data)
        entries.append(ExceptionTableEntry(start, start + length, target, depth_and_lasti >> 1,
                                           bool(depth_and_lasti & 1)))
    return entries
