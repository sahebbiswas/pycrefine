"""Tell the real `except` clauses from the compiler's re-raise wrappers (3.11+)."""
from dataclasses import dataclass
from typing import FrozenSet, Iterable, List, Protocol, Set

from .._constants import _EXC_TYPE_EXPR_OPS, _TRY_LOAD_OPS, _TRY_SKIP_NOP
from ..instructions import BytecodeInstruction


class ExceptionEntry(Protocol):
    """What this pass reads of an exception-table entry (``dis``'s entry has more)."""

    @property
    def start(self) -> int: ...

    @property
    def end(self) -> int: ...

    @property
    def target(self) -> int: ...


@dataclass(frozen=True)
class HandlerClassification:
    """
    What the exception table and the PUSH_EXC_INFO instructions say about each handler.

    ``try_covered_starts``: offsets where a protected range starts, at every depth.
    ``except_targets``: handler targets that are real `except` clauses (typed or bare).
    ``reraise_wrapper_targets``: handler targets that are re-raise wrappers or with-exit handlers.
    ``suppressed_push_exc``: PUSH_EXC_INFO offsets that are not a source `except` clause.
    """
    try_covered_starts: FrozenSet[int]
    except_targets: FrozenSet[int]
    reraise_wrapper_targets: FrozenSet[int]
    suppressed_push_exc: FrozenSet[int]


def _first_after_nops(instrs: List[BytecodeInstruction], idx: int) -> int:
    """Index of the first instruction after ``idx`` that is not a NOP a handler may start with."""
    look = idx + 1
    while look < len(instrs) and instrs[look].opname in _TRY_SKIP_NOP:
        look += 1
    return look


def _check_exc_match_within(instrs: List[BytecodeInstruction], start: int, window: int) -> bool:
    """Whether a CHECK_EXC_MATCH is among the ``window`` instructions from index ``start``, or ends a run of type-expression loads."""
    if any(ins.opname == "CHECK_EXC_MATCH" for ins in instrs[start:start + window]):
        return True
    # a long tuple of exception types puts the match beyond any fixed window
    end = start
    while end < len(instrs) and instrs[end].opname in _EXC_TYPE_EXPR_OPS:
        end += 1
    return end < len(instrs) and instrs[end].opname == "CHECK_EXC_MATCH"


def _is_source_except_handler(instrs: List[BytecodeInstruction], idx: int) -> bool:
    """Whether the PUSH_EXC_INFO at ``idx`` starts a real `except` (typed, or bare), not a re-raise wrapper."""
    look = _first_after_nops(instrs, idx)
    if look >= len(instrs):
        return False
    if instrs[look].opname in _TRY_LOAD_OPS:
        # a CHECK_EXC_MATCH within a few steps confirms a typed `except`
        return _check_exc_match_within(instrs, look + 1, 5)
    # a bare `except:` discards the exception with POP_TOP
    return instrs[look].opname == "POP_TOP"


def _is_unlisted_reraise_wrapper(instrs: List[BytecodeInstruction], idx: int) -> bool:
    """Heuristic for a PUSH_EXC_INFO the exception table did not list: a with-exit handler or a re-raise wrapper."""
    look = _first_after_nops(instrs, idx)
    if look >= len(instrs):
        return False
    if instrs[look].opname == "WITH_EXCEPT_START":
        return True
    # a load and no CHECK_EXC_MATCH within 3 steps
    return instrs[look].opname in _TRY_LOAD_OPS and not _check_exc_match_within(instrs, look + 1, 4)


def classify_handlers(instrs: List[BytecodeInstruction], entries: Iterable[ExceptionEntry]) -> HandlerClassification:
    """Classify the handlers of a code object from its instructions and its exception-table ``entries``."""
    entry_list = list(entries)
    index_of_push = {ins.offset: i for i, ins in enumerate(instrs) if ins.opname == "PUSH_EXC_INFO"}
    except_targets: Set[int] = set()
    wrapper_targets: Set[int] = set()
    for e in entry_list:
        idx = index_of_push.get(e.target)
        if idx is not None:
            (except_targets if _is_source_except_handler(instrs, idx) else wrapper_targets).add(e.target)
    suppressed: Set[int] = set()
    for offset, idx in index_of_push.items():
        if offset in wrapper_targets or (offset not in except_targets and _is_unlisted_reraise_wrapper(instrs, idx)):
            suppressed.add(offset)
    return HandlerClassification(frozenset(e.start for e in entry_list), frozenset(except_targets),
                                 frozenset(wrapper_targets), frozenset(suppressed))
