"""Analysis passes over a code object's instructions, as pure functions (#120).

A pass here reads a list of :class:`~pycrefine.instructions.BytecodeInstruction` (and, where it
needs one, the exception-table entries) and returns a new list or a frozen dataclass. It reads no
decompiler, writes no emission state and reports no diagnostics, so it can be tested from
``dis`` output on a few lines of source (``tests/test_analysis.py``) and run in any order its
inputs allow.

Passes, in the order the decompiler runs them, with what each takes and returns:

=============================  ===========================  ============================================
pass                           input                        result
=============================  ===========================  ============================================
``split_superinstructions``    instructions                 instructions (3.13+ pairs split in two)
``parse_exception_table``      ``co_exceptiontable``        list of :class:`ExceptionTableEntry` (3.11+)
``classify_handlers``          instructions, entries        :class:`HandlerClassification`
=============================  ===========================  ============================================

The decompiler methods that used to hold this logic (``_split_superinstructions``,
``_classify_exception_entries``, ``_classify_push_exc_info``) call these and store the results in
``PrescanState``. Passes that still depend on the decompiler's own state are listed in the
roadmap (#117) and move here as that dependency is removed.
"""
from .exception_table import ExceptionTableEntry, parse_exception_table
from .handlers import HandlerClassification, classify_handlers
from .superinstructions import split_superinstructions

__all__ = ["ExceptionTableEntry", "HandlerClassification", "classify_handlers", "parse_exception_table",
           "split_superinstructions"]
