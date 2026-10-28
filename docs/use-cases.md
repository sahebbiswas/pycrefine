# Use cases

Recipes for the things pycrefine is most often used for. Every command and script here
is run as written against pycrefine's own tests and fixtures. They assume pycrefine is
installed ([Getting started](getting-started.md)); from a checkout, replace `pycrefine`
with `python pycrefine.py`.

- [Recover one module](#recover-one-module)
- [Recover a project from `__pycache__`](#recover-a-project-from-__pycache__)
- [Find out which Python wrote a `.pyc` file](#find-out-which-python-wrote-a-pyc-file)
- [Decompile files written by another Python version](#decompile-files-written-by-another-python-version)
- [Decompile a `.pyc` without a header (PyInstaller, marshal dumps)](#decompile-a-pyc-without-a-header-pyinstaller-marshal-dumps)
- [Decompile a code object in memory](#decompile-a-code-object-in-memory)
- [Check that decompiled code matches the original](#check-that-decompiled-code-matches-the-original)
- [Use pycrefine in a script or service](#use-pycrefine-in-a-script-or-service)

## Recover one module

```bash
pycrefine path/to/__pycache__/settings.cpython-312.pyc -o settings.py
python -m py_compile settings.py      # check that the result compiles
```

Run it under the Python version in the file name (here 3.12); see
[Decompile files written by another Python version](#decompile-files-written-by-another-python-version).

## Recover a project from `__pycache__`

When the sources are gone but the `__pycache__` directories are left, this script
rebuilds the package tree next to them. It maps `pkg/__pycache__/mod.cpython-312.pyc`
back to `pkg/mod.py`, skips files written by a different Python version than the one
running it (and lists them), skips the `-O`/`-OO` variants (`mod.cpython-312.opt-1.pyc`,
which have no `assert` statements or docstrings) and lists any module that only has one of
those, and reports
the files that did not decompile or do not compile:

```python
# recover_tree.py: python recover_tree.py <project dir> <output dir>
import pathlib
import sys

import pycrefine

src_root, out_root = map(pathlib.Path, sys.argv[1:3])
tag = sys.implementation.cache_tag            # e.g. "cpython-312"
failed, other_versions, optimized = [], [], []

for pyc in sorted(src_root.rglob("__pycache__/*.pyc")):
    module, _, rest = pyc.name.partition(".")
    if rest != f"{tag}.pyc":                  # exactly mod.<tag>.pyc
        if rest.startswith(f"{tag}.opt-"):    # -O/-OO build: no asserts/docstrings
            optimized.append(pyc)
        else:
            other_versions.append(pyc)
        continue
    target = out_root / pyc.parent.parent.relative_to(src_root) / f"{module}.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        source = pycrefine.get_decompiler(str(pyc)).decompile()
        compile(source, str(target), "exec")  # does the result compile?
    except Exception as exc:                  # keep going; report at the end
        failed.append((pyc, exc))
        continue
    target.write_text(source, encoding="utf-8")

print(f"recovered into {out_root}; {len(failed)} failed")
for pyc, exc in failed:
    print(f"  FAILED {pyc}: {exc}")
if other_versions:
    print(f"{len(other_versions)} files are for another Python (not {tag}); run again under it")
if optimized:
    print(f"{len(optimized)} optimized (-O/-OO) files skipped")
    for pyc in optimized:                     # no plain .pyc next to it: nothing recovered
        plain = pyc.with_name(pyc.name.split(".opt-")[0] + ".pyc")
        if not plain.exists():
            print(f"  NOT RECOVERED {pyc}: only an optimized cache exists")
```

```bash
python recover_tree.py old_project/ recovered/
```

Files written by another Python version are skipped rather than reported as errors, so you
can run the script once per version found (see the next section). For legacy `.pyc` files
that sit next to the source instead of in `__pycache__` (`mod.pyc`, with no version tag in
the name), change the glob to `**/*.pyc`, drop the `rest` check, take the module name from
`pyc.stem`, and build the target from `pyc.parent.relative_to(src_root)`, since there is no
`__pycache__` level to strip.

## Find out which Python wrote a `.pyc` file

The file name usually says it (`cpython-312` is 3.12). When it does not, read the magic
number in the first two bytes of the file:

```bash
python -c "import sys; print(int.from_bytes(open(sys.argv[1], 'rb').read(2), 'little'))" file.pyc
```

| Magic number | Python |
|---|---|
| 3425 | 3.9 |
| 3439 | 3.10 |
| 3495 | 3.11 |
| 3531 | 3.12 |
| 3571 | 3.13 |
| 3627 | 3.14 |
| 3419 or lower | 3.8 or older (not supported) |

Pre-release Pythons use numbers between these; pycrefine accepts the whole range of each
version. If you try the wrong interpreter, pycrefine's error names the right one anyway.

## Decompile files written by another Python version

Bytecode from Python 3.9 decompiles under any Python 3.9 or later. Bytecode from 3.10
onwards must be decompiled by the same minor version
([#94](https://github.com/sahebbiswas/pycrefine/issues/94)). With
[uv](https://docs.astral.sh/uv/) that is one command per version, and uv fetches the
interpreter if it is missing:

```bash
uvx --python 3.12 pycrefine app.cpython-312.pyc -o app.py
uvx --python 3.14 pycrefine tool.cpython-314.pyc -o tool.py
```

For a tree with several versions, run the [recovery script](#recover-a-project-from-__pycache__)
once per version:

```bash
for v in 3.11 3.12 3.13 3.14; do
  uv run --no-project --python "$v" --with pycrefine python recover_tree.py old_project/ recovered/
done
```

Each run only writes the modules compiled by its own version, so the runs fill in the
tree together. A module cached by several versions is written by each of them, and the
last run wins.

Without uv, use the Windows launcher (`py -3.12 -m pycrefine file.pyc`), pyenv, or the
official Docker image (`python:3.12`); see
[Getting started](getting-started.md#run-under-the-python-that-wrote-the-file).

## Decompile a `.pyc` without a header (PyInstaller, marshal dumps)

A normal `.pyc` file starts with a 16-byte header (magic number, flags, and a timestamp or
hash) followed by the marshalled code object. Tools that store bytecode themselves, such
as PyInstaller archives, `marshal.dump` output, or some obfuscators, often keep only the code
object. pycrefine then reports an unknown magic number:

```text
Error: Invalid or unsupported Python magic number: 0x000000e3 (version id: 227).
- Input file version is unrecognized or may be corrupt.
```

`0xe3` is the marshal type code of a code object, which means the header is missing.
Put one back, using the magic number of the Python that wrote the code (run this under
that Python, as `importlib.util.MAGIC_NUMBER` is the running interpreter's):

```python
# add_header.py: python add_header.py headerless.pyc fixed.pyc
import importlib.util
import sys

data = open(sys.argv[1], "rb").read()
with open(sys.argv[2], "wb") as out:
    out.write(importlib.util.MAGIC_NUMBER)   # 4 bytes: this Python's magic number
    out.write(b"\0" * 12)                    # flags, timestamp and size: unused here
    out.write(data)
```

```bash
python3.12 add_header.py extracted/main headerless_fixed.pyc
pycrefine headerless_fixed.pyc
```

pycrefine does not extract files from PyInstaller archives; use an extractor such as
`pyinstxtractor` first, and check which Python the archive was built with (it usually
names it). Encrypted or obfuscated bytecode has to be decrypted by the tool that made it;
pycrefine only reads standard CPython bytecode.

## Decompile a code object in memory

`get_decompiler` takes a path, so write the code object to a temporary `.pyc` file with
a header:

```python
import importlib.util
import marshal
import os
import tempfile

import pycrefine


def decompile_code(code):
    """Source for a code object compiled by the running Python."""
    with tempfile.NamedTemporaryFile(suffix=".pyc", delete=False) as f:
        f.write(importlib.util.MAGIC_NUMBER + b"\0" * 12 + marshal.dumps(code))
    try:
        return pycrefine.get_decompiler(f.name).decompile()
    finally:
        os.unlink(f.name)


print(decompile_code(compile("total = sum(x * x for x in range(10))", "<demo>", "exec")))
```

```python
total = sum(((x * x) for x in range(10)))
```

For a function, pass the module-level code that defines it, or `func.__code__` to get its
body (the `def` line itself is not part of the function's code object).

## Check that decompiled code matches the original

Text comparison is too strict: formatting, comments and parentheses never survive
compilation. Compare what the two programs mean instead.

**Same syntax tree.** If you have the original source, compare abstract syntax trees.
Parentheses and formatting do not matter; different but equivalent code does:

```python
import ast
import sys

original, recovered = (open(p, encoding="utf-8").read() for p in sys.argv[1:3])
same = ast.dump(ast.parse(original)) == ast.dump(ast.parse(recovered))
print("AST-equal" if same else "different syntax trees (may still be equivalent)")
```

**Same behaviour.** Run both against the module's tests, or exec both and compare what
they do. pycrefine's own tests do this for every construct they cover.

**Coherency score.** In a source checkout, `quality/check_coherency.py` compiles a source
file, decompiles it and scores how close the result is (lines, tokens, keywords, and
known decompiler artifacts). It measures resemblance, not correctness, so use it to find
files worth a closer look:

```bash
python quality/check_coherency.py path/to/module.py --verbose
python quality/check_coherency.py path/to/module.py --json      # for scripts; exit 0 at >= 70%
```

## Use pycrefine in a script or service

```python
import pycrefine


def try_decompile(path):
    try:
        return pycrefine.get_decompiler(path).decompile()
    except ValueError as exc:   # unsupported or invalid .pyc, wrong Python version
        print(f"cannot decompile {path}: {exc}")
    except OSError as exc:      # missing or unreadable file
        print(f"cannot read {path}: {exc}")
    return None
```

- pycrefine is pure Python with no dependencies and no global state, so separate
  `get_decompiler` calls can run in parallel processes.
- It only reads the file; decompiling never runs the bytecode, so code you do not trust
  cannot execute. It does load the whole file and unmarshal it, so a huge or deliberately
  malformed file can still use a lot of memory or CPU; for untrusted input from outside,
  run it in a separate process with time and memory limits.
- Results are deterministic: the same file gives the same output on every run,
  regardless of `PYTHONHASHSEED`.
- A broken construct does not stop the rest of the file from being decompiled; check the
  output for the signs listed in [Troubleshooting](troubleshooting.md#the-output-is-wrong-or-does-not-compile).

See [Command line and Python API](usage.md#python-api) for the full API.
