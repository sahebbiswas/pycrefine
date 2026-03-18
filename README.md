# pycrefine 🐍

A Python `.pyc` decompiler that reconstructs readable source code from compiled bytecode. Built with a layered, version-aware architecture to correctly handle `.pyc` files from Python **3.9 through 3.14**, regardless of which Python version you run it under.

![ Python 3.9 ](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py39.yml/badge.svg)

![ Python 3.12 ](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py312.yml/badge.svg)

![ Python 3.14 ](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py314.yml/badge.svg)

---

## Features

### Multi-version decompilation
Pycrefine reads the magic number from the `.pyc` header and routes to the correct decompiler automatically. Each major Python release changed bytecode significantly — pycrefine handles all of them:

| Python version | Magic number range | Decompiler class |
|---|---|---|
| 3.9 | 3410–3429 | `Decompiler39` |
| 3.10 – 3.13 | 3430–3559 | `Decompiler311Plus` |
| 3.14+ | 3560+ | `Decompiler314` |

### Cross-version `.pyc` parsing
A hand-written `MarshalParser` reads the raw `.pyc` binary, including the magic header and marshal-encoded code object, without relying on the host Python's `marshal` module. This means you can run pycrefine under Python 3.12 and correctly parse a `.pyc` compiled by Python 3.9 — even though the two versions use incompatible `CodeType` constructors and marshal type encodings.

When the host Python version matches the target `.pyc`, the native `marshal.load()` path is used as a faster fallback.

### Reconstructed Python constructs

| Construct | Notes |
|---|---|
| Assignments | Simple, multi-target, attribute (`self.x = 1`), subscript (`a[0] = v`) |
| All binary operators | `+`, `-`, `*`, `/`, `//`, `%`, `**`, `&`, `\|`, `^`, `<<`, `>>`, `@` |
| Augmented assignments | `+=`, `-=`, `*=`, `/=`, `//=`, `%=`, `**=`, `&=`, `\|=`, `^=`, `<<=`, `>>=` |
| Comparison operators | `==`, `!=`, `<`, `<=`, `>`, `>=`, `is`, `is not`, `in`, `not in` |
| `if` / `elif` / `else` | Including `is None` / `is not None` guards |
| `for` loops | Including tuple-unpacking targets (`for a, b in pairs:`) |
| `while` loops | Conditional and `while True:`, including nested loops |
| `break` | Inside `while True:` bodies |
| Functions | Definitions with positional args and default values (both `MAKE_FUNCTION` flags and 3.14's `SET_FUNCTION_ATTRIBUTE`) |
| Classes | Class definitions, inheritance, method bodies |
| `try` / `except` / `except X as e` | Including multi-except, bare except, compiler cleanup suppression |
| `raise` / `raise X from Y` | Both forms |
| Imports | `import X`, `from X import Y`, multi-name from-imports |
| Docstrings | Module and class-level |
| `del` | Name deletion |
| `yield` | Inside generator functions |
| Collection literals | `{}` dict, `[...]` list, `{...}` set, `(...)` tuple |
| f-strings | `FORMAT_VALUE` / `BUILD_STRING` |
| Subscript access | `a[0]`, including 3.14's unified `BINARY_OP` encoding |

### PEP 552 header support
Correctly parses both timestamp-based and hash-based `.pyc` headers (the 4-byte flags field introduced in Python 3.7). Tries header offsets of 16, 12, 8, and 4 bytes to handle all variants.

### Pre-scan while-loop detection
Rather than attempting to retroactively rewrite `if` blocks into `while` loops after the fact, pycrefine runs a lightweight pre-scan pass (`_prescan_while_loops`) before the main decode. It identifies every backward jump, the corresponding loop guard `POP_JUMP_IF_*`, and the duplicate condition region at the bottom of the loop body (which CPython emits for optimisation). This eliminates duplicate headers and stray `if` blocks in the output.

---

## Usage

```bash
python pycrefine.py path/to/compiled_file.pyc
```

The script is self-contained — no third-party dependencies. It requires Python 3.9 or later to run.

### Example

Source (`example.py`):
```python
import os

def process(items, threshold=0):
    result = []
    for item in items:
        if item > threshold:
            result.append(item)
    return result

class Worker:
    def __init__(self, name):
        self.name = name
        self.count = 0

    def run(self):
        while self.count < 10:
            self.count += 1
```

Decompiled output:
```python
import os

def process(items, threshold=0):
    result = []

    for item in items:
        if item > threshold:
            result.append(item)
    return result

class Worker:
    def __init__(self, name):
        self.name = name
        self.count = 0

    def run(self):
        while self.count < 10:
            self.count += 1
```

---

## Architecture

```
pycrefine.py
├── get_decompiler(filepath)        # Entry point — reads header, dispatches
│
├── MarshalParser                   # Cross-version .pyc binary reader
│   ├── load()                      # Top-level marshal object loader
│   ├── _load_inner()               # Type dispatch (None/bool/int/str/tuple/…)
│   └── _load_code()                # CodeType constructor (version-branched)
│
├── DecompilerBase                  # Abstract base
│   └── _disassemble()              # Uses dis.get_instructions() on host version
│
├── DecompilerGeneric(Base)         # Main stack-machine decompiler
│   ├── decompile()                 # Main loop — block tracking, block close
│   ├── _prescan_while_loops()      # Pre-scan: identifies while guards & dup regions
│   ├── _handle_instruction()       # ~60 opcode handlers
│   └── helpers                     # _format_val, _append_reconstructed, …
│
├── Decompiler39(Generic)           # Python 3.9 override
│   ├── _disassemble()              # Manual bytecode walker with EXTENDED_ARG
│   ├── _get_opname_39()            # Hardcoded 3.9 opcode table (70+ entries)
│   └── _handle_instruction()       # CALL_FUNCTION, CALL_METHOD, CALL_FUNCTION_KW/EX
│
├── Decompiler311Plus(Generic)      # Python 3.10–3.13 override
│   ├── _handle_instruction()       # BINARY_OP with full NB_* enum, BINARY_OP arg 26
│   └── _get_jump_target()          # 3.11+ relative jump offset calculation
│
└── Decompiler314(311Plus)          # Python 3.14 override
    └── _handle_instruction()       # SET_FUNCTION_ATTRIBUTE for defaults
```

### How the stack machine works

Python bytecode is a stack-based virtual machine. Pycrefine simulates this with a Python list used as the operand stack, where each element is a string fragment of reconstructed source. Instructions like `LOAD_NAME` push a name string; `BINARY_OP` pops two strings, formats `"(left op right)"`, and pushes the result; `STORE_NAME` pops the top string and emits `"name = value"` to the output buffer.

Control flow (`if`, `for`, `while`, `try`) is tracked with a `blocks` stack of `(end_offset, block_type)` tuples. The main loop checks this stack at each instruction and decrements indentation when a block's end offset is reached.

---

## Version-specific bytecode differences handled

| Change | Python version | Handling |
|---|---|---|
| `CALL_FUNCTION` / `CALL_METHOD` → `CALL` | 3.11 | `Decompiler311Plus` |
| `BINARY_ADD` etc. → unified `BINARY_OP` | 3.11 | `BINARY_OP` with NB_* index map |
| `BINARY_OP` index mapping changed | 3.12 | Empirically verified index table |
| `JUMP_BACKWARD` for while loops | 3.11 | `_is_backward_jump()` substring match |
| Duplicate condition at while-loop bottom | 3.11–3.13 | Pre-scan dup-region suppression |
| `PUSH_EXC_INFO` / `CHECK_EXC_MATCH` for try/except | 3.11 | Structured handler with `POP_TOP` skip |
| `NOP` as try-body marker | 3.11 | `_has_exception_handler()` gate |
| `LOAD_SMALL_INT` | 3.14 | `DecompilerGeneric` (shared) |
| `BINARY_SUBSCR` → `BINARY_OP` arg 26 | 3.14 | Early-return in `BINARY_OP` handler |
| `MAKE_FUNCTION` flags → `SET_FUNCTION_ATTRIBUTE` | 3.14 | `Decompiler314._handle_instruction` |
| `POP_TOP` before `STORE_NAME e` in except-as | 3.14 | `CHECK_EXC_MATCH` peek skips `POP_TOP` |
| `EXTENDED_ARG` for indices > 255 | 3.9 | Accumulator in `Decompiler39._disassemble` |
| `CALL_FUNCTION_KW` / `CALL_FUNCTION_EX` | 3.9 | `Decompiler39._handle_instruction` |

---

## Test suite

```
tests/test_pycrefine.py
```

Run with pytest from the project root:

```bash
pytest tests/test_pycrefine.py -v
```

**112 test functions, 142 total test executions** (30 parametrized expansions). Verified passing on Python 3.12.3 and designed to pass on Python 3.14.3.

| Test class | Executions | Covers |
|---|---|---|
| `TestBasicStatements` | 15 | Assignments, dicts, lists, sets, tuples, booleans, None, subscript read/write, `del` |
| `TestControlFlow` | 12 | `if/else`, `for`, `while`, `while True`, `break`, nested loops, duplicate-header regression |
| `TestAugmentedAssignment` | 14 | All 12 in-place operators parametrized; `+=` ≠ `^=` regression guard |
| `TestImports` | 6 | `import`, `from X import Y`, multi-symbol, sentinel-leakage check |
| `TestFunctions` | 9 | Simple defs, default args, default-as-docstring regression, body indent, nested, `yield` |
| `TestClasses` | 5 | Class definition, `__init__`, methods, base classes, `self.attr` |
| `TestExceptions` | 10 | Typed `except`, `except X as e`, cleanup suppression, bare except, multi-except indent equality, `raise`, `raise X from Y` |
| `TestNoneGuards` | 4 | `POP_JUMP_IF_NONE` / `POP_JUMP_IF_NOT_NONE` inversion fix |
| `TestOperators` | 24 | 12 binary ops + 6 comparisons + `in`, `not in`, `is`, `is not` — all parametrized |
| `TestEdgeCases` | 7 | `del`, empty module, nested-while body-count regression, real-module composite, inheritance, deeply nested functions |
| `TestErrorHandling` | 5 | Bad path, too-short file, invalid magic, return type, no-crash on complex source |
| `TestDecompilerDispatch` | 9 | Version routing across all magic-number bands; class existence checks |
| `TestMarshalParser` | 14 | Unit tests for the binary reader: all scalar types, EOF error, unknown type, ref-flag (0x80) round-trip |
| `TestMarshalParserCodeType` | 5 | `CodeType` constructor version-branching; round-trips a `.pyc` and validates `co_name`, `co_consts`, `co_argcount`, `dis.dis()` compatibility |
| `TestWhilePrescan` | 5 | White-box: guard detection count, nested-loop count, dup-offset registration, no false positives on plain `if` |

---

## Known limitations

These are inherent constraints of the single-pass stack-machine approach rather than bugs:

- **`elif` chains** — sometimes rendered as nested `if/else` blocks
- **`while True: break`** (trivially optimised) — CPython compiles this to `NOP + RETURN_CONST`, producing empty output
- **Attribute augmented assignment** — `self.x += 1` may render as `pass` when the `LOAD_FAST/STORE_ATTR` pair doesn't match the simple-name augassign heuristic
- **List comprehensions / generator expressions** — appear as opaque `<listcomp>()` calls rather than inline syntax
- **`with` statement** — header is emitted but `SETUP_WITH` body handling is partial
- **`lambda`** — not yet distinguished from anonymous `MAKE_FUNCTION` results
- **`match/case`** (3.10+) — structural pattern matching opcodes are not handled
- **`async/await`** — async generator opcodes are not reconstructed

---

## File structure

```
pycrefine/
├── pycrefine.py          # Decompiler (single file, no dependencies)
├── tests/
│   └── test_pycrefine.py # pytest test suite (142 test executions)
└── README.md
```