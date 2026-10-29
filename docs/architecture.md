# Architecture

A map of the code for contributors: where things live and how a `.pyc` file becomes
source. For the development workflow, see [CONTRIBUTING.md](../CONTRIBUTING.md).

## Repository layout

| Path | Contents |
|---|---|
| `src/pycrefine/` | the package (below) |
| `pycrefine.py` | launcher so `python pycrefine.py file.pyc` works from a checkout; not part of the wheel. Imported as `pycrefine` from the repository root, it hands back the real package from `src/`. |
| `tests/` | the test suite (pytest, unittest-style classes). `tests/fixtures/foreign_pyc/` holds the few committed `.pyc` files written by other CPython versions, with `manifest.json` and `regenerate_foreign_pyc.py`; every other test compiles its input on the fly. |
| `corpus/` | sources the correctness gate decompiles |
| `quality/` | the correctness gate and its per-version baselines, the differential behavior check (`check_differential.py`, with the versioned `corpus/`), the coherency checker, the lint ratchet and its baseline |
| `docs/` | user and design documentation ([index](README.md)) |
| `.github/workflows/` | CI: one test workflow per Python version (`ci-py39.yml`, `ci-py312.yml`, `ci-py314.yml`, sharing `reusable-test.yml`), `lint.yml`, `version.yml`, `release.yml` |
| `.github/scripts/` | helpers the workflows run (`check_version_bump.py`) |

## The package

| Module | Contents |
|---|---|
| `__init__.py` | `__version__` (the single source of the version) and re-exports. `import pycrefine` keeps exposing every name the former single-file module had. |
| `cli.py`, `__main__.py` | the `pycrefine` command |
| `loader.py` | `get_decompiler`: reads the header, picks the backend, and loads the code object (with `MarshalParser` for files from another Python version) |
| `versions.py` | the version table: for each Python, its magic-number band, backend, support level (`recognized`, `reconstructable`, `tested`) and whether its bytecode reads on another host |
| `codeobj.py` | `ForeignCode`, a stand-in for code objects of another Python version |
| `decompiler/base.py` | `DecompilerBase`: the shared state and helpers (return-statement checks, literal formatting) |
| `decompiler/generic.py` | `DecompilerGeneric`: the version-independent engine (prescans, dispatch table, opcode handlers) |
| `decompiler/py39.py` | `Decompiler39`: 3.9's block-stack exception handling (`SETUP_FINALLY`, `POP_BLOCK`, …) |
| `decompiler/py311.py` | `Decompiler311Plus`: 3.10–3.12 (exception tables, `BINARY_OP`, `SWAP`/`COPY`) |
| `decompiler/py314.py` | `Decompiler314`: 3.13 and 3.14 (fused superinstructions, 3.14's `LOAD_SPECIAL` `with` blocks); a subclass of `Decompiler311Plus` |
| `opcodes/py39.py` | the 3.9 opcode table, used to read 3.9 bytecode on any host |
| `instructions.py` | `BytecodeInstruction`, constant formatting, finally-copy matching, comprehension rendering |
| `expr.py` | expression helpers and stack sentinels (`StackNULL`, `_BoolExpr`, `UnknownExpression`) |
| `postprocess.py` | text clean-ups after decompiling (`elif` flattening, chained comparisons, placeholders) |
| `_constants.py` | shared opcode sets and limits |

## From `.pyc` to source

1. **Load** (`loader.get_decompiler`). The 16-byte header gives the magic number, which
   `versions.py` maps to a row of its version table, and the row names the backend class. A number in a row that is only recognized is refused with the version it belongs to, and one above every band (a newer Python) is refused as `3.15+`. A number outside both the table and that band is refused as unrecognized or possibly corrupt. A file from the
   running Python is loaded with `marshal`. A 3.9 file under another Python is parsed by
   `MarshalParser` and disassembled with the bundled 3.9 opcode table. 3.10+ files need
   the matching interpreter, because those backends read instructions through the host's
   `dis` ([#94](https://github.com/sahebbiswas/pycrefine/issues/94)).
2. **Normalize the instructions** (`DecompilerGeneric.decompile`). The code is
   disassembled into `BytecodeInstruction`s, fused superinstructions are split, and
   compiler-inlined code is folded back: PEP 709 comprehensions, and 3.14's guarded
   `any`/`all`/`tuple` calls.
3. **Prescan** the structure the stack machine cannot see one instruction at a time:
   loops (`_prescan_while_loops`), `try`/`except`/`finally` and the copies of `finally`
   bodies the compiler inlines (`_prescan_try_structure`), `with` exits
   (`_prescan_with_exits`), conditional expressions (`_prescan_ternaries`), compound
   conditions (`_prescan_compound_conds`) and value-context `and`/`or`
   (`_prescan_value_boolops`). Prescans record offsets to suppress, block ends and
   pre-rendered fragments.
4. **Replay** the instructions in order. `_handle_instruction` is one template: it applies
   the suppression every backend shares (`_suppressed_by_prescan`) and then calls
   `_dispatch_instruction`, which version backends override for their own opcodes. Each
   opcode has an `_op_*` handler named in a table (`_BASE_DISPATCH`; how it is merged per
   backend is below). Handlers push expressions on a symbolic stack, emit lines, and
   open blocks that close at a known offset (`_close_blocks`). Nested code objects
   (functions, classes, lambdas, comprehensions) are decompiled by sub-decompilers.
5. **Post-process** (`postprocess.post_process_source`) the text: compiler-generated
   returns, `elif` flattening at the `core` beautification level, and placeholders for
   constructs that could not be rebuilt.

**How the dispatch table is built.** `_BASE_DISPATCH` maps opcode names to handler method
names (strings). `DecompilerGeneric.__init_subclass__` runs once for each backend class, when
the class is created, and sets `cls._DISPATCH` to the parent class's `_DISPATCH` with that
class's own `DISPATCH_OVERRIDES` laid over it (only the dict declared in that class body is
read, since the parent's overrides are already in the parent's `_DISPATCH`). A backend is
therefore looked up along its inheritance chain, and where two classes name the same opcode
the most derived class wins. An override must supply a replacement handler name; there is
no way to remove an inherited entry, so to turn an opcode off, map it to `_op_no_op` (or
`_op_unknown` to have it reported). Instances do not build a table; they share the class's.

### The order of the passes

`DecompilerGeneric.decompile` runs these in this order, and the order matters: each pass
reads what the earlier ones recorded or removed.

| # | Pass | What it does |
|---|---|---|
| 1 | `_disassemble`, `_split_superinstructions` | instructions as `BytecodeInstruction`s; fused 3.13/3.14 instructions become their parts |
| 2 | `_collapse_inlined_comprehensions` | PEP 709 (3.12+): the comprehension that the compiler inlined into the enclosing function is folded back |
| 3 | `_fold_inlined_builtin_calls` | 3.14: the guard and inlined loop for `any`/`all`/`tuple` over a generator are dropped, leaving the call |
| 4 | `_drop_unreachable_handlers` | `try: pass` compiles its handlers but nothing reaches them; they leave the stream and are replayed where the `try` was ([#167](https://github.com/sahebbiswas/pycrefine/issues/167)) |
| 5 | `_prescan_while_loops` | which backward jumps close a `while`, and the duplicated condition the compiler appends |
| 6 | `_prescan_try_structure` | `try`/`except`/`else`/`finally`: handler ranges, the copies of `finally` bodies, merge points; the largest pass |
| 7 | `_prescan_with_exits` | the inlined `__exit__` calls of `with` |
| 8 | `_prescan_ternaries` | conditional expressions, so their branches become one expression |
| 9 | `_prescan_compound_conds` | `a and b` / `a or b` / chained comparisons used as a condition |
| 10 | `_prescan_value_boolops` | `and` / `or` used as a value |

### The expression stack

`self.stack` holds source text for the values the bytecode has pushed, plus a few typed
markers (`StackNULL`, `_BoolExpr`, tuples for code objects). Its states are distinct:

| State | How it shows |
|---|---|
| a known expression | the text itself |
| an intentionally empty stack | `self.stack == []`; nothing to report until an instruction needs an operand |
| an expression that cannot be determined | an `UnknownExpression` (a `str` that renders as `?`, with `reason`, `opcode`, `offset`); test it with `is_unknown(value)`, never by comparing with `"?"` |
| an invalid stack transition | `self._pop()` on an empty stack records a `stack_underflow` [diagnostic](usage.md#diagnostics) (an error in strict mode) and returns an `UnknownExpression` |

Pass an explicit fallback to `_pop` only for text that is a deliberate default, not as a
way to stay quiet.

### Decompiler state

The state that the prescan, the emission and the docstring handling build up lives in three
small dataclasses declared in `src/pycrefine/decompiler/state.py` and created in
`DecompilerGeneric.__init__` (issues #37 and #83). They do not hold everything: the cursor
and stack (`instructions`, `reconstructed`, `stack`, `blocks`, `pc`) stay on `DecompilerBase`,
and `DecompilerGeneric` keeps a few flags of its own (`has_doc`, `_enclosing_locals`). Nothing
is added to the decompiler as an ad-hoc attribute, and nothing is probed with `hasattr`.
`FIELD_OWNER` maps every field of the three groups to its group so a test can guard against
drift.

Handler sections are decompiled by a sub-decompiler. It receives
`prescan.for_handler_section()` (which shares only `exc_handler_jump_offsets`,
`exc_handler_continue_offsets` and `while_header_targets` with its parent) and
`emit.for_handler_section()` (a fresh state whose `except_header_indents` is
`[0]`).

#### `PrescanState` (`self.prescan`)

What the analysis passes found before emission starts.

| Field | Meaning | Written by |
|---|---|---|
| `while_body_offsets` | where a while body starts, to drop the repeated condition | `_prescan_while_loops` |
| `while_header_targets` | body start -> guard POP_JUMP offset | `_prescan_while_loops`, `decompile` |
| `while_true_ends` | end offsets of while-True loops | `_dispatch_instruction`, `_op_jump`, `_op_nop`, `_prescan_while_loops` |
| `inner_back_jumps` | inner back-jump -> the back-edge that closes the loop | `_prescan_while_loops` |
| `dup_guard_exits` | exit targets of suppressed bottom-of-loop condition copies | `_prescan_while_loops` |
| `for_iter_targets` | jump targets of FOR_ITER (3.9 break detection) | `_prescan_try_structure` |
| `boolop_or_pop` | 3.9-3.10: offset -> (op, target) for *_OR_POP jumps | `_prescan_value_boolops` |
| `boolop_copy` | 3.12+: COPY offset -> (op, target, resume offset) | `_prescan_value_boolops` |
| `boolop_groups` | 3.9-3.10: plain POP_JUMP offset -> (op, target) | `_prescan_value_boolops` |
| `ternary_jumps` | jump offset -> how to render the conditional expression | `_prescan_ternaries` |
| `ternary_suppress` | instruction offsets a ternary already covers | `_prescan_ternaries` |
| `compound_cond_map` | controlling offset -> combined condition text | `_prescan_compound_conds` |
| `compound_suppress` | instruction offsets a compound condition already covers | `_prescan_compound_conds` |
| `try_nop_offsets` | NOPs that mark where a try starts | `_prescan_try_structure` |
| `nop_to_push_exc` | try NOP -> its PUSH_EXC_INFO | `_prescan_try_structure` |
| `nop_to_finally_merge` | try NOP -> finally start, for try/finally without except | `_prescan_try_structure` |
| `suppress_push_exc_offsets` | PUSH_EXC_INFO that opens a handler rendered elsewhere | `_prescan_try_structure` |
| `push_exc_to_finally_merge` | merge offset -> the handler entry points (sorted) that rejoin there, for a `try` inside a loop whose handlers merge after the loop | `_prescan_try_structure` |
| `finally_merge_offsets` | merge labels where deferred finally/except text is emitted | `_close_blocks`, `_dispatch_instruction`, `_prescan_try_structure` |
| `except_only_merge_offsets` | merge offsets that have handlers but no `finally`, so only the handler text is placed there | `_dispatch_instruction`, `_prescan_try_structure` |
| `finally_body_suppress` | instructions of an inlined copy of a `finally` body, left out of the normal flow because the text is emitted once at the merge label | `_elide_early_finally_copies`, `_dispatch_instruction`, `_op_jump`, `_op_setup_with`, `_prescan_try_structure`, `_suppress_copies` |
| `finally_targets` | 3.9: targets of the `SETUP_FINALLY`s that belong to a user `finally:` rather than to a handler | `_prescan_try_structure` |
| `finally_from_handler` | 3.9: handler offset -> its RERAISE | `_elide_early_finally_copies`, `_open_finally_from_handler`, `_prescan_try_structure` |
| `handler_section_suppress` | instructions of an except handler that is decompiled ahead of time as a section, so the main pass skips them | `_prescan_try_structure` |
| `wrapper_body_suppress` | bodies of the compiler's `RERAISE` and with-exit wrappers | `_prescan_try_structure` |
| `exc_handler_jump_offsets` | jumps that leave a handler | `_emit_empty_try`, `_prescan_try_structure` |
| `exc_handler_continue_offsets` | ... and those that are a `continue` | `_emit_empty_try`, `_prescan_try_structure` |
| `exc_cleanup_offsets` | compiler-generated `e = None; del e` | `_record_exc_cleanup_offsets` |
| `header_indent_by_cleanup` | exception clean-up handler -> indent of its try's `except` headers, so a later clause of the same try reopens at the right level | `_op_push_exc_info` |
| `handler_return_close` | returns that end an except clause | `_decompile_step`, `_defer_close_to_handler_return`, `_render_handler_section` |
| `explicit_returns` | `return None` that is in the source | `_defer_close_to_handler_return` |
| `empty_try_handlers` | try NOP -> handler of a `try: pass` | `_drop_unreachable_handlers`, `_emit_empty_try` |
| `empty_try_skip` | NOPs of such a statement | `_drop_unreachable_handlers` |
| `else_starts` | else start offset -> else end offset | `_prescan_try_structure` |
| `with_exit_suppress_offsets` | with-exit epilogue instructions | `_prescan_try_structure`, `_prescan_with_exits` |
| `with_exit_suppress` | 3.14: the inlined `__exit__` clean-up instructions of a with statement, up to and including its `POP_TOP` | `_dispatch_instruction` |
| `with_body_end` | first protected offset of a with body -> where it ends | `_prescan_with_exits` |
| `with_exit_owner` | POP_TOP of an inlined __exit__ call -> its with's body start | `_prescan_with_exits` |

#### `EmissionState` (`self.emit`)

Bookkeeping that changes while instructions are replayed.

| Field | Meaning | Written by |
|---|---|---|
| `except_header_indents` | indent levels of the except headers being emitted | `_close_except_clause`, `_dispatch_instruction`, `_op_before_with`, `_op_check_exc_match`, `_op_push_exc_info` |
| `except_end_offsets` | end offsets of exception regions | `_dispatch_instruction` |
| `exc_as_store_offset` | the `as e` STORE to skip | `_dispatch_instruction`, `_op_check_exc_match`, `_op_store` |
| `exc_bound_names` | every name bound by `except ... as` | `_dispatch_instruction`, `_op_check_exc_match` |
| `deferred_finally_lines` | merge offset -> rendered finally body | `_prescan_try_structure` |
| `deferred_except_lines` | merge offset -> rendered handler | `_prescan_try_structure` |
| `pending_finally_merge` | merge offset awaiting emission | `_dispatch_instruction`, `_prescan_try_structure` |
| `boolop_pending` | operators opened but not yet joined | `_prescan_value_boolops` |
| `else_stack_snapshots` | second arm's offset -> stack to restore | `_decompile_step`, `_op_conditional_jump` |
| `open_with_sentinels` | 3.14: (body end, body start) of open with bodies | `_close_blocks`, `_dispatch_instruction` |
| `pei_indents` | `PUSH_EXC_INFO` offset -> indent of the `try:` that precedes it, which its `except` headers use | `_op_nop` |
| `finally_wrapper_indent` | 3.9: wrapper jump target -> indent at which `finally:` is written when its `POP_BLOCK` is reached | `_dispatch_instruction` |
| `kw_names` | keyword names of the call being built | `_op_call`, `_op_kw_names` |

#### `DocState` (`self.doc`)

Where a docstring was found and how many lines it took.

| Field | Meaning | Written by |
|---|---|---|
| `doc_load_offset` | LOAD_CONST of a module/class docstring | `decompile` |
| `class_doc_store_offsets` | STORE_NAME __doc__ of a class docstring | `decompile` |
| `doc_line_count` | output lines the docstring takes | `_emit_docstring` |

### The block stack

`self.blocks` holds one entry per open construct; `_close_blocks` pops entries whose end
offset has been reached and the entry's type decides the indentation and whether an
empty body gets `pass`. The types are `if`, `else`, `for`, `while`, `try_body`, `except`,
`finally_body`, `with`, `with_body`, `exc_cleanup` and `finally_wrapper`. The last two
are bookkeeping: `exc_cleanup` is never given a `pass` (`_NO_PASS_TYPES`) and
`finally_wrapper` neither indents nor gets one (`_NO_INDENT_TYPES`, both in
`_constants.py`). Indentation is `indent_level`, raised when a block opens and lowered
when it closes.

### Version quirks

Things CPython does that the passes undo, each with the test or issue that taught us.

| Version | What the compiler does | Where it is handled | Test or issue |
|---|---|---|---|
| 3.9 | exceptions use a block stack (`SETUP_FINALLY`, `POP_BLOCK`), not an exception table | `Decompiler39` | [#179](https://github.com/sahebbiswas/pycrefine/issues/179) |
| 3.10 | the same block stack, but 3.10 is rebuilt by `Decompiler311Plus`, which has no block-stack handling; its handlers are not decompiled yet and are reported once per code object | `_report_version_gaps` in `Decompiler311Plus` | [#179](https://github.com/sahebbiswas/pycrefine/issues/179) |
| 3.9–3.10 | `and`/`or` values use `JUMP_IF_*_OR_POP` | `_prescan_value_boolops` | `tests/test_basic.py::TestValueBoolOps` |
| 3.11+ | exceptions use an exception table; handlers are laid out after the code they guard | `_exception_entries`, `_prescan_try_structure` | `tests/test_exceptions.py` |
| 3.11+ | a `try` is introduced by `NOP`s; its `pass` body is another `NOP` | `_empty_try_nops` | [#167](https://github.com/sahebbiswas/pycrefine/issues/167), `tests/test_empty_bodies.py` |
| 3.11+ | `try: pass` still compiles its handlers, which nothing reaches | `_drop_unreachable_handlers` | `tests/test_empty_bodies.py` |
| 3.11+ | a `finally` body is emitted once per path (normal exit, each `return`, `break`, `continue`, and the exception path) | `_prescan_try_structure`, `_empty_try_finally_copy` | [#109](https://github.com/sahebbiswas/pycrefine/issues/109), [#186](https://github.com/sahebbiswas/pycrefine/issues/186) |
| 3.10–3.13 | `while cond:` is rotated: the condition is repeated at the bottom of the body. 3.10 and 3.11 close the loop with the copy's own conditional jump back to the body, 3.12 and 3.13 with a `JUMP_BACKWARD` after it; 3.9 and 3.14 test the condition once, at the head | `_prescan_while_loops`, `_dup_guard_jump_offsets`, `_is_conditional_back_jump` | [#105](https://github.com/sahebbiswas/pycrefine/issues/105), `tests/test_control_flow.py::TestWhileConditionLoops` |
| 3.11 | a `with` statement's exit handler is laid out inline, between the statement and the code after it; 3.12 and later move every handler to the end of the code | `_with_exit_handler_end`, `_prescan_with_exits` | [#209](https://github.com/sahebbiswas/pycrefine/issues/209), `tests/test_control_flow.py::TestCodeAfterWith` |
| 3.12 | PEP 709 inlines comprehensions into the enclosing function | `_collapse_inlined_comprehensions` | `tests/test_basic.py::TestComprehensions` |
| 3.12 | a duplicated `return None` follows some returns | `postprocess`, `prescan.explicit_returns` | `tests/test_except_return.py` |
| 3.13 | `LOAD_FAST_LOAD_FAST` and `STORE_FAST_STORE_FAST` fuse two instructions | `_split_superinstructions`, `_COMPOUND_EXPR_OPS` | [#6](https://github.com/sahebbiswas/pycrefine/issues/6), `tests/test_control_flow.py::TestChainedExpressions` |
| 3.14 | `with` uses `LOAD_SPECIAL`; `any`/`all`/`tuple` over a generator are inlined behind a guard | `Decompiler314`, `_fold_inlined_builtin_calls` | `tests/test_control_flow.py` |
| any | `a, *b = x` compiles to `UNPACK_EX`; attribute and subscript targets need a fallback | `_op_unpack_sequence` | [#156](https://github.com/sahebbiswas/pycrefine/issues/156), `tests/test_assignments.py` |

## Where to make a change

- **A construct renders wrong in one version.** Start with that version's backend. If the
  bytecode shape is shared, the fix usually belongs in a prescan in `generic.py`.
  `python -m dis` on a minimal example under each Python shows the difference.
- **A new opcode.** Add a handler to the dispatch table in `generic.py`, or to the
  backend whose Python introduced it ([Extending pycrefine](extending.md)).
- **A new Python version.** Add a row to the table in `versions.py` (its magic band, backend and
  support level), add the backend to `loader._BACKENDS` if it is a new class, add a CI workflow and a correctness baseline
  (`quality/baselines/py3.X.json`), and update [Limitations](limitations.md)
  ([checklist](extending.md#add-a-python-version)).
- **Output formatting only.** `postprocess.py`, gated on the beautification level when it
  changes what users see by default.

The refactoring roadmap ([#117](https://github.com/sahebbiswas/pycrefine/issues/117))
tracks the move towards a control-flow graph and a typed intermediate representation
([#81](https://github.com/sahebbiswas/pycrefine/issues/81),
[#82](https://github.com/sahebbiswas/pycrefine/issues/82)), which will change parts of
this picture.
