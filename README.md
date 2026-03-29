# pycrefine 🐍

A Python `.pyc` decompiler that reconstructs readable source code from compiled bytecode. Built to correctly handle `.pyc` files from Python **3.9 and higher**, regardless of which Python version you run the decompiler under. It is currently being actively tested for Python **3.9, 3.12, and 3.14**.

![ Python 3.9 ](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py39.yml/badge.svg)
![ Python 3.12 ](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py312.yml/badge.svg)
![ Python 3.14 ](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py314.yml/badge.svg)

---

## Usage

Using `pycrefine` to decompile a Python bytecode `.pyc` file is straightforward for any end-user:

```bash
python pycrefine.py path/to/compiled_file.pyc
```

Or save the output to a file:

```bash
python pycrefine.py path/to/compiled_file.pyc -o decompiled_output.py
```

The script is entirely self-contained with no third-party dependencies required. It needs Python 3.9 or later to execute.

### Features
*   **Automatic Version Navigation:** Reads the magic number from the `.pyc` header and seamlessly routes execution to the appropriate decompiler logic.
*   **Cross-Version Parsing:** You can run `pycrefine` on newer Python versions (e.g., 3.12) and perfectly parse a `.pyc` compiled by an older version (e.g., 3.9), avoiding any native bytecode incompatibility issues.
*   **PEP 552 Support:** Correctly processes both timestamp-based and hash-based `.pyc` headers introduced dynamically in Python 3.7.
*   **Dispatch Table Architecture:** The decompiler engine uses a fast, modular opcode dispatch mapping, making it trivial to extend and maintain handlers for newer Python versions.

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

### `test_pycrefine.py` (Unit Tests)

The core test suite is located at `tests/test_pycrefine.py`. It uses `pytest` and contains comprehensive unit tests ranging from basic statements to complex control flow scenarios and edge cases.

To run the full test suite from the project root:
```bash
pytest tests/test_pycrefine.py -v
```

This test suite strictly verifies the core mechanics of `pycrefine`, asserting its ability to restructure assignments, variables, data structures, exception handling blocks, functions, loops, and conditional chains.

### `check_coherency.py` (Decompilation Coherency Checker)

Located at `debug/check_coherency.py`, this tool is an advanced decompilation coherency checker. It works by compiling an arbitrary Python source file to `.pyc`, decompiling it backward with `pycrefine`, and scoring how faithfully the decompiler reproduced the semantic and syntactic structure of the original source.

It performs a multi-dimensional analysis with scoring based on:
- Line and Token fidelity/recall
- Keyword density mapping
- Emitted artefacts and "garbage" penalty checking

#### Usage Examples

Run a test against its own source (self-scoring):
```bash
python debug/check_coherency.py pycrefine.py
```

Score the coherency of any other python file, enabling verbose output to view the per-dimension grading statistics:
```bash
python debug/check_coherency.py path/to/any_file.py --verbose
```

For Continuous Integration pipelines, you can format the scoring output as JSON by passing the `--json` flag. The script predictably returns an exit code of `0` for passing scores (>= 70%), `1` for failing scores, and `2` for compilation/configuration errors.

---

## Known limitations

These constraints are an inherent part of the stack-machine approach parsing strategy in CPython bytecode:

- **`match/case`** (3.10+) — structural pattern matching opcodes are not natively supported.
- **`async/await`** — concurrent execution coroutine opcodes are not completely reconstructed.