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
| `quality/` | the correctness gate and its per-version baselines, the coherency checker, the lint ratchet and its baseline |
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
| `expr.py` | expression helpers and stack sentinels (`StackNULL`, `_BoolExpr`) |
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

## Where to make a change

- **A construct renders wrong in one version.** Start with that version's backend. If the
  bytecode shape is shared, the fix usually belongs in a prescan in `generic.py`.
  `python -m dis` on a minimal example under each Python shows the difference.
- **A new opcode.** Add a handler to the dispatch table in `generic.py`, or to the
  backend whose Python introduced it.
- **A new Python version.** Map its magic numbers in `versions.py`, pick or add a backend
  in `loader.py` and `decompiler/`, add a CI workflow and a correctness baseline
  (`quality/baselines/py3.X.json`), and update [Limitations](limitations.md).
- **Output formatting only.** `postprocess.py`, gated on the beautification level when it
  changes what users see by default.

The refactoring roadmap ([#117](https://github.com/sahebbiswas/pycrefine/issues/117))
tracks the move towards a control-flow graph and a typed intermediate representation
([#81](https://github.com/sahebbiswas/pycrefine/issues/81),
[#82](https://github.com/sahebbiswas/pycrefine/issues/82)), which will change parts of
this picture.
