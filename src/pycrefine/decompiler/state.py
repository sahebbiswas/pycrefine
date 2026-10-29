"""
The decompiler's working state, declared in one place.

A decompiler instance used to grow attributes in several methods (the prescans created
their results as they ran), and every sub-decompiler copied an ad-hoc subset by hand. The
state is now three small objects, each with all of its fields declared here:

``PrescanState``
    What the analysis passes found before emission starts: offsets to suppress, jump
    classifications, where blocks start and end. Written by the ``_prescan_*`` passes;
    read while replaying instructions.

``EmissionState``
    Bookkeeping that changes while instructions are replayed: open except headers, text
    deferred until a merge label, names bound by ``except ... as``.

``DocState``
    Where a docstring was found and how many output lines it took.

A sub-decompiler that renders part of the code (an except handler, say) starts from fresh
state and receives exactly what the ``for_handler_section`` methods below hand over.
``tests/test_state.py`` pins that list.
"""
from dataclasses import dataclass, field, fields
from typing import Any, Dict, List, Optional, Set


# These are deliberate data holders: one field per fact, grouped by who writes it.
@dataclass
class PrescanState:  # pylint: disable=too-many-instance-attributes
    # --- while loops (_prescan_while_loops) --------------------------------------------
    while_body_offsets: Set[int] = field(default_factory=set)       # where a while body starts, to drop the repeated condition
    while_header_targets: Dict[int, int] = field(default_factory=dict)  # body start -> guard POP_JUMP offset
    while_true_ends: Set[int] = field(default_factory=set)          # end offsets of while-True loops
    inner_back_jumps: Dict[int, int] = field(default_factory=dict)  # inner back-jump -> the back-edge that closes the loop
    dup_guard_exits: Set[int] = field(default_factory=set)          # exit targets of suppressed bottom-of-loop condition copies, and (3.10/3.11) the repeated implicit return after one
    for_iter_targets: Set[int] = field(default_factory=set)         # jump targets of FOR_ITER (3.9 break detection)
    # --- `and` / `or` as values (_prescan_value_boolops) -------------------------------
    boolop_or_pop: Dict[int, Any] = field(default_factory=dict)     # 3.9-3.10: offset -> (op, target) for *_OR_POP jumps
    boolop_copy: Dict[int, Any] = field(default_factory=dict)       # 3.12+: COPY offset -> (op, target, resume offset)
    boolop_groups: Dict[int, Any] = field(default_factory=dict)     # 3.9-3.10: plain POP_JUMP offset -> (op, target)
    # --- conditional expressions and compound conditions -------------------------------
    ternary_jumps: Dict[int, Any] = field(default_factory=dict)     # jump offset -> how to render the conditional expression
    ternary_suppress: Set[int] = field(default_factory=set)         # instruction offsets a ternary already covers
    compound_cond_map: Dict[int, str] = field(default_factory=dict)  # controlling offset -> combined condition text
    compound_suppress: Set[int] = field(default_factory=set)        # instruction offsets a compound condition already covers
    # --- try / except / finally / with (_prescan_try_structure) ------------------------
    try_nop_offsets: Set[int] = field(default_factory=set)          # NOPs that mark where a try starts
    nop_to_push_exc: Dict[int, int] = field(default_factory=dict)   # try NOP -> its PUSH_EXC_INFO
    nop_to_finally_merge: Dict[int, int] = field(default_factory=dict)  # try NOP -> finally start, for try/finally without except
    suppress_push_exc_offsets: Set[int] = field(default_factory=set)  # PUSH_EXC_INFO that opens a handler rendered elsewhere
    push_exc_to_finally_merge: Dict[int, List[int]] = field(default_factory=dict)  # merge offset -> handler entry points that rejoin there
    finally_merge_offsets: Set[int] = field(default_factory=set)    # merge labels where deferred finally/except text is emitted
    except_only_merge_offsets: Set[int] = field(default_factory=set)
    finally_body_suppress: Set[int] = field(default_factory=set)
    finally_targets: Set[int] = field(default_factory=set)          # 3.9
    finally_from_handler: Dict[int, int] = field(default_factory=dict)  # 3.9: handler offset -> its RERAISE
    handler_section_suppress: Set[int] = field(default_factory=set)
    wrapper_body_suppress: Set[int] = field(default_factory=set)    # re-raise and with-exit wrappers
    exc_handler_jump_offsets: Set[int] = field(default_factory=set)  # jumps that leave a handler
    exc_handler_continue_offsets: Set[int] = field(default_factory=set)  # ... and those that are a `continue`
    exc_cleanup_offsets: Set[int] = field(default_factory=set)      # compiler-generated `e = None; del e`
    header_indent_by_cleanup: Dict[int, int] = field(default_factory=dict)
    handler_return_close: Set[int] = field(default_factory=set)     # returns that end an except clause
    explicit_returns: Set[int] = field(default_factory=set)         # `return None` that is in the source
    empty_try_handlers: Dict[int, Any] = field(default_factory=dict)  # try NOP -> handler of a `try: pass`
    empty_try_skip: Set[int] = field(default_factory=set)           # NOPs of such a statement
    else_starts: Dict[int, int] = field(default_factory=dict)       # else start offset -> else end offset
    with_exit_suppress_offsets: Set[int] = field(default_factory=set)  # with-exit epilogue instructions
    with_exit_suppress: Set[int] = field(default_factory=set)       # 3.14
    with_body_end: Dict[int, int] = field(default_factory=dict)     # first protected offset of a with body -> where it ends
    with_exit_owner: Dict[int, int] = field(default_factory=dict)   # POP_TOP of an inlined __exit__ call -> its with's body start

    def for_handler_section(self) -> "PrescanState":
        """
        The analysis a sub-decompiler of an except handler starts from: empty, except for
        what describes the code around the handler (how handlers are left, where while
        loops start, which copies of their conditions are suppressed), which it shares with its parent.
        """
        state = PrescanState()
        state.exc_handler_jump_offsets = self.exc_handler_jump_offsets
        state.exc_handler_continue_offsets = self.exc_handler_continue_offsets
        state.while_header_targets = self.while_header_targets
        state.while_body_offsets = self.while_body_offsets
        state.dup_guard_exits = self.dup_guard_exits
        return state


@dataclass
class EmissionState:  # pylint: disable=too-many-instance-attributes
    except_header_indents: List[int] = field(default_factory=list)  # indent levels of the except headers being emitted
    except_end_offsets: List[int] = field(default_factory=list)     # end offsets of exception regions
    exc_as_store_offset: int = -1                                   # the `as e` STORE to skip
    exc_bound_names: Set[str] = field(default_factory=set)          # every name bound by `except ... as`
    deferred_finally_lines: Dict[int, List[str]] = field(default_factory=dict)  # merge offset -> rendered finally body
    deferred_except_lines: Dict[int, List[str]] = field(default_factory=dict)   # merge offset -> rendered handler
    pending_finally_merge: Optional[int] = None                     # merge offset awaiting emission
    boolop_pending: List[Any] = field(default_factory=list)         # operators opened but not yet joined
    else_stack_snapshots: Dict[int, List[Any]] = field(default_factory=dict)  # second arm's offset -> stack to restore
    open_with_sentinels: List[Any] = field(default_factory=list)    # 3.14: (body end, body start) of open with bodies
    pei_indents: Dict[int, int] = field(default_factory=dict)
    finally_wrapper_indent: Dict[int, int] = field(default_factory=dict)  # 3.9
    kw_names: Any = ()                                              # keyword names of the call being built

    def for_handler_section(self) -> "EmissionState":
        """A handler's sub-decompiler starts at indent level 0 with nothing open."""
        state = EmissionState()
        state.except_header_indents = [0]
        return state


@dataclass
class DocState:
    doc_load_offset: Optional[int] = None                           # LOAD_CONST of a module/class docstring
    class_doc_store_offsets: Dict[int, str] = field(default_factory=dict)  # STORE_NAME __doc__ of a class docstring
    doc_line_count: int = 0                                         # output lines the docstring takes


# Which state object holds each field; used by the tools that rename and check references.
FIELD_OWNER: Dict[str, str] = {f.name: group for group, cls in
                               (("prescan", PrescanState), ("emit", EmissionState), ("doc", DocState))
                               for f in fields(cls)}
