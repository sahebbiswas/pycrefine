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
| `versions.py` | magic-number ranges → Python versions |
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
   `versions.py` maps to a Python version and that to a backend class. A file from the
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
4. **Replay** the instructions in order. Each opcode has an `_op_*` handler in a dispatch
   table (`_build_dispatch`), and version backends override `_handle_instruction` for
   their own opcodes. Handlers push expressions on a symbolic stack, emit lines, and
   open blocks that close at a known offset (`_close_blocks`). Nested code objects
   (functions, classes, lambdas, comprehensions) are decompiled by sub-decompilers.
5. **Post-process** (`postprocess.post_process_source`) the text: compiler-generated
   returns, `elif` flattening at the `core` beautification level, and placeholders for
   constructs that could not be rebuilt.

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

All of it lives on the decompiler instance and is declared in
`DecompilerGeneric.__init__`. Sub-decompilers (handler sections, comprehension slices,
conditional-expression branches) are fresh instances that have a few of these set by hand,
so a new attribute must also be handled where `_pick_decompiler_class` is used
([#37](https://github.com/sahebbiswas/pycrefine/issues/37),
[#83](https://github.com/sahebbiswas/pycrefine/issues/83)). The "written by" column
lists the methods that assign to or mutate the attribute.

| Group | Attribute | Meaning | Written by |
|---|---|---|---|
| Docstrings | `_doc_load_offset`, `_class_doc_store_offsets`, `_doc_line_count`, `has_doc` | the `LOAD_CONST` / `STORE_NAME __doc__` of a docstring and how many lines it took | `decompile`, `_emit_docstring` |
| Loops | `_while_body_offsets` | where a `while` body starts, to drop the repeated condition at the bottom | `_prescan_while_loops` |
| | `_inner_back_jumps` | an inner backward jump → the back-edge that closes the loop | `_prescan_while_loops` |
| | `_dup_guard_exits` | exit targets of suppressed bottom-of-loop condition copies | `_prescan_while_loops` |
| `and` / `or` as values | `_boolop_or_pop`, `_boolop_copy`, `_boolop_groups` | the jumps and `COPY`s that make up the operator, by Python version | `_prescan_value_boolops` |
| | `_boolop_pending` | operators opened but not yet joined | `_prescan_value_boolops`, replay |
| Try, except | `_try_nop_offsets`, `_nop_to_push_exc`, `_nop_to_finally_merge` | the `NOP` that marks the start of a `try`, and what it leads to | `_prescan_try_structure` |
| | `_suppress_push_exc_offsets` | `PUSH_EXC_INFO` that opens a handler already rendered elsewhere | `_prescan_try_structure` |
| | `_exc_handler_jump_offsets`, `_exc_handler_continue_offsets` | jumps that leave a handler (and those that are a `continue`) | `_prescan_try_structure`, `_emit_empty_try` |
| | `_exc_as_store_offset`, `_exc_cleanup_offsets`, `_exc_bound_names` | `except E as e`: the store to skip, the compiler's `e = None; del e`, and every name bound that way | `_op_check_exc_match`, `_record_exc_cleanup_offsets` |
| | `_except_header_indents`, `_except_end_offsets`, `_header_indent_by_cleanup` | indentation and end offsets of the except clauses being emitted | `_op_push_exc_info`, `_handle_instruction` |
| | `_handler_return_close`, `_explicit_returns` | returns that end an except clause; `return None` that is in the source | `_defer_close_to_handler_return` |
| | `_empty_try_handlers`, `_empty_try_skip` | the handlers of a `try: pass` and the `NOP`s of that statement | `_drop_unreachable_handlers` |
| Finally | `_finally_merge_offsets`, `_pending_finally_merge` | merge labels where deferred `finally` or `except` text is emitted | `_prescan_try_structure`, `_close_blocks` |
| | `_finally_body_suppress` | the inlined copy of a `finally` body, skipped in the main pass | `_prescan_try_structure`, `_op_jump` |
| | `_deferred_finally_lines`, `_deferred_except_lines` | pre-rendered text keyed by merge offset | `_prescan_try_structure` |
| | `_push_exc_to_finally_merge`, `_finally_from_handler` | which `PUSH_EXC_INFO` goes to which merge label; 3.9's `finally` rendered from its handler copy | `_prescan_try_structure` |
| | `_handler_section_suppress`, `_wrapper_body_suppress` | handler instructions already replayed by a sub-decompiler; re-raise and with-exit wrappers | `_prescan_try_structure`, `_render_handler_section` |
| | `_else_starts`, `_else_stack_snapshots` | where an `else:` starts and ends; the stack to restore there | `_prescan_try_structure`, `_op_conditional_jump` |
| With | `_with_exit_suppress_offsets`, `_with_body_end`, `_with_exit_owner`, `_open_with_sentinels` | the inlined `__exit__` call to skip, where a `with` body ends, and which `with` an exit belongs to | `_prescan_with_exits`, `_close_blocks` |
| Scopes | `_enclosing_locals` | names bound in enclosing functions, for `global`/`nonlocal` decisions | `_op_make_function` |
| Calls | `_kw_names` | keyword names for the next call (3.11–3.13 `KW_NAMES`) | `_op_kw_names`, `_op_call` |

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
| 3.9–3.10 | exceptions use a block stack (`SETUP_FINALLY`, `POP_BLOCK`), not an exception table | `Decompiler39`; 3.10 handlers are not decompiled yet | [#179](https://github.com/sahebbiswas/pycrefine/issues/179) |
| 3.9–3.10 | `and`/`or` values use `JUMP_IF_*_OR_POP` | `_prescan_value_boolops` | `tests/test_basic.py::TestValueBoolOps` |
| 3.11+ | exceptions use an exception table; handlers are laid out after the code they guard | `_exception_entries`, `_prescan_try_structure` | `tests/test_exceptions.py` |
| 3.11+ | a `try` is introduced by `NOP`s; its `pass` body is another `NOP` | `_empty_try_nops` | [#167](https://github.com/sahebbiswas/pycrefine/issues/167), `tests/test_empty_bodies.py` |
| 3.11+ | `try: pass` still compiles its handlers, which nothing reaches | `_drop_unreachable_handlers` | `tests/test_empty_bodies.py` |
| 3.11+ | a `finally` body is emitted once per path (normal exit, each `return`, `break`, `continue`, and the exception path) | `_prescan_try_structure`, `_empty_try_finally_copy` | [#109](https://github.com/sahebbiswas/pycrefine/issues/109), [#186](https://github.com/sahebbiswas/pycrefine/issues/186) |
| 3.11 | `while cond:` is rotated: the condition is repeated at the bottom | `_prescan_while_loops` | [#105](https://github.com/sahebbiswas/pycrefine/issues/105) |
| 3.12 | PEP 709 inlines comprehensions into the enclosing function | `_collapse_inlined_comprehensions` | `tests/test_basic.py::TestComprehensions` |
| 3.12 | a duplicated `return None` follows some returns | `postprocess`, `_explicit_returns` | `tests/test_except_return.py` |
| 3.13 | `LOAD_FAST_LOAD_FAST` and `STORE_FAST_STORE_FAST` fuse two instructions | `_split_superinstructions`, `_COMPOUND_EXPR_OPS` | [#6](https://github.com/sahebbiswas/pycrefine/issues/6), `tests/test_control_flow.py::TestChainedExpressions` |
| 3.14 | `with` uses `LOAD_SPECIAL`; `any`/`all`/`tuple` over a generator are inlined behind a guard | `Decompiler314`, `_fold_inlined_builtin_calls` | `tests/test_control_flow.py` |
| any | `a, *b = x` compiles to `UNPACK_EX`; attribute and subscript targets need a fallback | `_op_unpack_sequence` | [#156](https://github.com/sahebbiswas/pycrefine/issues/156), `tests/test_assignments.py` |

## Where to make a change

- **A construct renders wrong in one version.** Start with that version's backend. If the
  bytecode shape is shared, the fix usually belongs in a prescan in `generic.py`.
  `python -m dis` on a minimal example under each Python shows the difference.
- **A new opcode.** Add a handler to the dispatch table in `generic.py`, or to the
  backend whose Python introduced it ([Extending pycrefine](extending.md)).
- **A new Python version.** Map its magic numbers in `versions.py`, pick or add a backend
  in `loader.py` and `decompiler/`, add a CI workflow and a correctness baseline
  (`quality/baselines/py3.X.json`), and update [Limitations](limitations.md)
  ([checklist](extending.md#add-a-python-version)).
- **Output formatting only.** `postprocess.py`, gated on the beautification level when it
  changes what users see by default.

The refactoring roadmap ([#117](https://github.com/sahebbiswas/pycrefine/issues/117))
tracks the move towards a control-flow graph and a typed intermediate representation
([#81](https://github.com/sahebbiswas/pycrefine/issues/81),
[#82](https://github.com/sahebbiswas/pycrefine/issues/82)), which will change parts of
this picture.
