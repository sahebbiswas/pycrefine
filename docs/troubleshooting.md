# Troubleshooting

Find the message you see, or the symptom, and its fix. If nothing here matches, see
[Reporting a problem](#reporting-a-problem).

- [Installation and setup](#installation-and-setup)
- [Error messages](#error-messages)
- [The output is wrong or does not compile](#the-output-is-wrong-or-does-not-compile)
- [Reporting a problem](#reporting-a-problem)

## Installation and setup

### `pycrefine: command not found`

pip installed the command into a directory that is not on your `PATH` (typically
`~/.local/bin` for `pip install --user`, or `%APPDATA%\Python\Python3XX\Scripts` on
Windows; pip prints a warning naming it). Either:

- run it through Python, which always works: `python -m pycrefine file.pyc`;
- install with [pipx](https://pipx.pypa.io/) (`pipx install pycrefine`, then
  `pipx ensurepath`), which manages the `PATH` for you; or
- add the directory pip named to your `PATH`.

### `error: externally-managed-environment`

Your system Python (Debian, Ubuntu, Fedora, Homebrew, …) does not let pip install into
it (PEP 668). Do not use `--break-system-packages`; install into an environment instead:

```bash
python3 -m venv .venv && . .venv/bin/activate && python -m pip install pycrefine
# or, for the command only:
pipx install pycrefine
# or, without installing:
uvx pycrefine file.pyc
```

### `No matching distribution found for pycrefine`

Your Python is probably older than 3.9: pip skips releases whose `Requires-Python` your
interpreter does not meet. Check `python --version` and use a newer interpreter. If the
version is right, update pip (`python -m pip install --upgrade pip`) and try again. To
install the unreleased `main` instead, use GitHub:

```bash
python -m pip install "git+https://github.com/sahebbiswas/pycrefine"
```

### Behind a proxy or offline

pycrefine has no dependencies, so a wheel or a source checkout is all you need. Copy the
wheel (`pycrefine-X.Y.Z-py3-none-any.whl` from PyPI or a release) and run
`python -m pip install pycrefine-X.Y.Z-py3-none-any.whl`, or clone the repository and run
`python pycrefine.py file.pyc` without installing.

### I need several Python versions

Bytecode from 3.10 onwards is only readable under the same Python minor version that
wrote it (any 3.12.x reads 3.12 bytecode), so
recovering a project may need several interpreters. The simplest way is
[uv](https://docs.astral.sh/uv/), which downloads them on demand:
`uvx --python 3.12 pycrefine file.pyc`. Alternatives are the Windows `py` launcher,
pyenv, or the `python:3.X` Docker images; see
[Getting started](getting-started.md#run-under-the-python-that-wrote-the-file).

## Error messages

### `Cannot decompile Python 3.12 bytecode from '...' under Python 3.14`

```text
Error: Cannot decompile Python 3.12 bytecode from 'app.cpython-312.pyc' under Python 3.14: Python 3.10+ bytecode can only be decompiled by the same Python minor version that wrote it. Run pycrefine under Python 3.12. (Python 3.9 bytecode works on any host.)
```

The file was written by a different Python than the one running pycrefine. Run it under
the version the message names, for example `uvx --python 3.12 pycrefine app.cpython-312.pyc`
([#94](https://github.com/sahebbiswas/pycrefine/issues/94) tracks removing this limit).

### `Invalid or unsupported Python magic number: 0x000000e3 (version id: 227)`

```text
Error: Invalid or unsupported Python magic number: 0x000000e3 (version id: 227).
- Input file version is unrecognized or may be corrupt.
- Current interpreter is Python 3.12 (magic: 0x0a0d0dcb).
```

The file has no `.pyc` header: it starts directly with a marshalled code object (`0xe3`).
This is typical for files extracted from PyInstaller archives or written with
`marshal.dump`. Add a header and try again; see
[Use cases](use-cases.md#decompile-a-pyc-without-a-header-pyinstaller-marshal-dumps).

### `Invalid or unsupported Python magic number ... appears to be from Python 3.15+` (or `3.8`, `3.7`, ...)

The message names the Python the file seems to come from and the versions pycrefine supports
(3.9 to 3.14). A Python newer or older than that is refused instead of being decompiled with the
nearest backend.


If the file is not really from a future Python, it is not a `.pyc` file at all: a source
file, a text file or a compressed archive also starts with four bytes that pycrefine reads
as a magic number. Check that you passed the `.pyc` (usually in `__pycache__/`) and not
the `.py` next to it. ([#153](https://github.com/sahebbiswas/pycrefine/issues/153) will
make this message clearer.)

### `Could not find valid marshal code object ... (Inferred version: Python 3.12)`

The file's version is supported but its code object cannot be read. (Files from Python 3.8 or
older are refused earlier, with `appears to be from Python 3.8`; use a decompiler that targets
those versions, for example `uncompyle6` or `decompyle3`.) The message ends with
`Tried each header size` and a list of why each place a code object could start failed to load
(`marshal at offset 16: EOFError: ...`). That happens to a file that passes the first checks (at
least 16 bytes, a supported magic number) but has a truncated or corrupt body, and it fails at
every offset. A file that is shorter than 16 bytes or has an unrecognized or unsupported magic
number is rejected before any offset is tried, with its own message
(`Invalid or unsupported Python magic number` above, or `too short` below) and no such list.

### `Invalid .pyc file '...': too short (found N bytes)`

The file is shorter than a `.pyc` header, so it is truncated or not a `.pyc` file. Check
that it was copied completely (binary mode, no line-ending conversion).

### `Warning: Decompiler returned no source code for ...`

The module has no code, for example an empty `__init__.py`. The exit status is 1 so that
scripts notice; the empty result is correct. From Python, `decompile()` returns `"\n"`.

### `Warning: unsupported_opcode: ...` or a `# pycrefine:` header in the output

The decompiler reached a place it could not reconstruct and guessed. The warning names the
kind, the function and the instruction; the source near it should not be trusted. Run with
`--strict` to stop at the first such place. See [Diagnostics](usage.md#diagnostics).

### `Error: strict mode: ...` (exit status 3)

`--strict` found a diagnostic. Drop `--strict` to get the best-effort source with the
diagnostics listed, or decompile under the Python version that wrote the file.

### `Error: [Errno 2] No such file or directory: ...`

The input path is wrong or unreadable. Quote paths with spaces, and remember that shells
do not expand `*` inside quotes.

### `Internal error: ...` (exit status 4)

pycrefine hit a bug. The message ends with a pointer to the issue tracker. Run again with
`--debug` to get the traceback, and include it when you
[report the problem](#reporting-a-problem).

### `__main__.py: error: argument --beautification-level: invalid choice`

The level must be `none`, `core` or `aggressive`; see
[Command line](usage.md#beautification-levels). (The program shows up as `__main__.py`
because of [#130](https://github.com/sahebbiswas/pycrefine/issues/130).)

## The output is wrong or does not compile

Decompiling is a reconstruction, and some constructs are not supported yet. First check
which situation you are in:

```bash
pycrefine file.pyc -o out.py
python -m py_compile out.py && echo compiles
```

**Signs of a construct pycrefine did not understand.** Look for these in the output:

| You see | It usually means |
|---|---|
| `func(...)`, `None(None, None)`, `?` | a call or value whose callee was lost, often around `with`, a conditional expression, or unsupported bytecode |
| `_item`, `_res`, `_exc_match`, `_exc_info` | internal names from loops, comprehensions or `except` handling that were not folded back |
| `('_with_info', ...)`, `('func', ...)` | internal placeholders that leaked into the output |
| `# <genexpr/lambda — not reconstructable>` | a generator expression or `lambda` that could not be rebuilt |
| `yield None` in a function that never yielded | `async def` or `yield from` (not supported yet) |
| `if a < b:` followed by a stray `a < b` line | a chained comparison used as a value ([#154](https://github.com/sahebbiswas/pycrefine/issues/154)) |
| `__annotations__['x'] = int` in a class | a variable annotation; the output raises `NameError` when it runs ([#173](https://github.com/sahebbiswas/pycrefine/issues/173)) |

**What to do.**

1. Check [Limitations](limitations.md): the construct may be a known gap with an open
   issue.
2. If the bytecode is from Python 3.10 or 3.11, expect more problems: those versions are
   only partly supported.
3. Try `--beautification-level none`. If that output is right, the tidy-up step is at
   fault, which is worth reporting.
4. Narrow it down: compile a small file with just the suspicious function
   (`python -m py_compile small.py`) and decompile `__pycache__/small.cpython-3XX.pyc`.
   A few lines that reproduce the problem make the best bug report.

**Equivalent but different is expected.** Comments, blank lines and parentheses are not in
the bytecode (docstrings are, and come back with the same text; 3.13+ stores them with the
indentation removed and tabs expanded). The compiler also rewrites some code, so you may get
`if not x: continue` instead of an `if` block at the end of a loop, an `if/else` spelled
as separate `if` blocks that each `return`, `print(a)` and `return None` instead of a
conditional expression at the end of a function, or `x = 2 if c else 3` instead of an
`if/else` assignment. These run the same way.

## Reporting a problem

Open an issue at <https://github.com/sahebbiswas/pycrefine/issues> with:

- the pycrefine version (`python -c "import pycrefine; print(pycrefine.__version__)"`)
  and the Python version that runs it;
- the Python version that compiled the file;
- the smallest source that reproduces the problem (or the `.pyc`, if you have no source
  and may share it);
- the output you got and what you expected.

Search the existing issues first; adding a reproduction to an open issue helps as much as
a new one. [CONTRIBUTING.md](../CONTRIBUTING.md#reporting-gaps) describes how issues are
organized.
