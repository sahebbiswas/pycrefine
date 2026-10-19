# pycrefine 🐍

A Python `.pyc` decompiler that reconstructs readable source code from compiled bytecode. Built to handle `.pyc` files from Python **3.9 and higher**. Python 3.9 bytecode can be decompiled under any supported Python version; 3.10+ bytecode currently needs the same Python minor version that wrote it (see [#94](https://github.com/sahebbiswas/pycrefine/issues/94)). It is currently being actively tested for Python **3.9, 3.12, and 3.14**.

![ Python 3.9 ](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py39.yml/badge.svg)
![ Python 3.12 ](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py312.yml/badge.svg)
![ Python 3.14 ](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py314.yml/badge.svg)

---

## Install

Install the published package (Python 3.9 or later):

```bash
python -m pip install pycrefine
```

After installation, use the `pycrefine` command from your terminal. To use the source checkout without installing, run `python pycrefine.py` as shown below.

## Usage

Using `pycrefine` to decompile a Python bytecode `.pyc` file is straightforward for any end-user:

```bash
python pycrefine.py path/to/compiled_file.pyc
```

Or save the output to a file:

```bash
python pycrefine.py path/to/compiled_file.pyc -o decompiled_output.py
```

You can optionally enhance output readability by adjusting the formatting with `--beautification-level`. The `core` level (default) gracefully flattens nested `else/if` blocks into single `elif` statements, while the `aggressive` level is reserved as a stub for future styling logic:

```bash
python pycrefine.py path/to/compiled_file.pyc --beautification-level core
```

pycrefine has no third-party dependencies and needs Python 3.9 or later to run. In a source checkout, `pycrefine.py` is a small launcher for the package in `src/pycrefine/`, so no installation is needed; once installed, `pycrefine` and `python -m pycrefine` do the same.

### API Usage

You can also import `pycrefine` as a library to programmatically decompile `.pyc` files directly into a string format:

```python
import pycrefine

# Path to the compiled Python file
pyc_file = "path/to/compiled_file.pyc"

# Seamlessly load the appropriate decompiler for the given file
decompiler = pycrefine.get_decompiler(pyc_file)

# Reconstruct the source code into a single string
source_code = decompiler.decompile(beautification_level="core")

print(source_code)
```

### Features
*   **Automatic Version Navigation:** Reads the magic number from the `.pyc` header and seamlessly routes execution to the appropriate decompiler logic.
*   **Cross-Version Parsing:** A built-in marshal reader parses `.pyc` files written by other Python versions, so you can run `pycrefine` on a newer Python (e.g., 3.12) and decompile a `.pyc` compiled by Python 3.9. For 3.10+ bytecode, run `pycrefine` under the Python version that compiled the file.
*   **PEP 552 Support:** Correctly processes both timestamp-based and hash-based `.pyc` headers introduced dynamically in Python 3.7.
*   **Dispatch Table Architecture:** The decompiler engine uses a modular opcode dispatch mapping designed to simplify extending and maintaining opcode handlers across newer Python versions.

### Code layout

The implementation is the `pycrefine` package in `src/pycrefine/`. `import pycrefine` still exposes every name the former single-file module had, so existing code keeps working.

| Module | Contents |
|---|---|
| `loader.py`, `versions.py`, `codeobj.py` | `.pyc` loading, the marshal reader for foreign versions, magic-number identification |
| `decompiler/` | `base.py`, `generic.py` (version-independent logic), `py39.py`, `py311.py` (3.10-3.12), `py314.py` (3.13/3.14) |
| `opcodes/py39.py` | the Python 3.9 opcode table |
| `postprocess.py`, `expr.py`, `instructions.py`, `_constants.py` | output clean-up, expression helpers, the instruction record, shared constants |
| `cli.py` | the command-line interface |

Elsewhere in the repository:

| Path | Contents |
|---|---|
| `pycrefine.py` | launcher so `python pycrefine.py file.pyc` works from a checkout (not part of the wheel) |
| `tests/` | the pytest suite; `tests/fixtures/foreign_pyc/` holds the few committed `.pyc` files written by other CPython versions, with a `manifest.json` and `tests/fixtures/regenerate_foreign_pyc.py` (everything else compiles on the fly) |
| `corpus/` | sources decompiled by the correctness gate (with the foreign `.pyc` fixtures, its default corpus) |
| `quality/` | the correctness gate, its per-version baselines, and the coherency checker |
| `docs/` | correctness model and design notes |

The package version lives in one place, `pycrefine.__version__`; `pyproject.toml` reads it.

### Example Decompilation

**Original Source** (`example.py`):
```python
import os

def process(items, threshold=0):
    result = []
    for item in items:
        if item > threshold:
            result.append(item)
    return result
```

**Decompiled Output:**
```python
import os

def process(items, threshold=0):
    result = []
    
    for item in items:
        if item > threshold:
            result.append(item)
    return result
```

---

## 🛠️ Developer Tools

For developers looking to contribute, improve, or maintain `pycrefine`, the project includes dedicated tooling to ensure decompilation accuracy and prevent regressions during architecture upgrades.

### Modular Unit Test Suite

The core test suite is located in the `tests/` directory. It uses `pytest` and contains comprehensive unit tests partitioned across specific domains (e.g., `test_basic.py`, `test_exceptions.py`, `test_control_flow.py`) covering everything from basic statements to complex edge cases.

To run the full test suite from the project root:
```bash
pytest tests/ -v
```

This test suite strictly verifies the core mechanics of `pycrefine`, asserting its ability to restructure assignments, variables, data structures, exception handling blocks, functions, loops, and conditional chains.

### `check_correctness.py` (Correctness Gate)

Located at `quality/check_correctness.py`, this is the primary quality gate. Each corpus entry (`corpus/` sources and the foreign-version `.pyc` files in `tests/fixtures/foreign_pyc/`), and each top-level unit within it, is decompiled and placed on a correctness ladder: `decompile_error` < `syntax_error` < `compile_error` < `compiles`. The results are compared with a committed per-Python-version baseline in `quality/baselines/`. CI fails when any entry or unit drops below its baseline:

```bash
python quality/check_correctness.py              # gate against the baseline
python quality/check_correctness.py --json       # machine-readable report
python quality/check_correctness.py --update-baseline   # record improvements
```

See [docs/correctness.md](docs/correctness.md) for the full model, CI semantics, report schema, and current baseline.

### `check_coherency.py` (Decompilation Coherency Checker)

Located at `quality/check_coherency.py`, this tool is an advanced decompilation coherency checker. It measures textual resemblance to the original source, so treat it as a readability diagnostic rather than a correctness check: output can score highly and still fail to compile. It works by compiling an arbitrary Python source file to `.pyc`, decompiling it backward with `pycrefine`, and scoring how faithfully the decompiler reproduced the semantic and syntactic structure of the original source.

It performs a multi-dimensional analysis with scoring based on:
- Line and Token fidelity/recall
- Keyword density mapping
- Emitted artefacts and "garbage" penalty checking

#### Usage Examples

Run a test against its own source (self-scoring):
```bash
python quality/check_coherency.py src/pycrefine/decompiler/generic.py
```

Score the coherency of any other python file, enabling verbose output to view the per-dimension grading statistics:
```bash
python quality/check_coherency.py path/to/any_file.py --verbose
```

For Continuous Integration pipelines, you can format the scoring output as JSON by passing the `--json` flag. The script predictably returns an exit code of `0` for passing scores (>= 70%), `1` for failing scores, and `2` for compilation/configuration errors.

---

## Known limitations

These constraints are an inherent part of the stack-machine approach parsing strategy in CPython bytecode:

- **`match/case`** (3.10+) — structural pattern matching opcodes are not natively supported.
- **`async/await`** — concurrent execution coroutine opcodes are not completely reconstructed.