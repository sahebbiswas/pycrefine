# Command line and Python API

The full reference for the `pycrefine` command and the Python API. For a walkthrough,
start with [Getting started](getting-started.md); for recipes, see
[Use cases](use-cases.md).

## Command line

```text
pycrefine [-h] [-o OUTPUT] [--beautification-level {none,core,aggressive}] input
```

`python -m pycrefine ...` and, from a source checkout, `python pycrefine.py ...` take the
same arguments.

| Argument | Meaning |
|---|---|
| `input` | The `.pyc` file to decompile. |
| `-o OUTPUT`, `--output OUTPUT` | Write the source to `OUTPUT` (UTF-8) instead of standard output. A confirmation goes to standard error. |
| `--beautification-level LEVEL` | How much the output is tidied after decompiling: `core` (the default), `none` or `aggressive`. See below. |
| `-h`, `--help` | Show the usage and exit. |

There is no `--version` option yet ([#153](https://github.com/sahebbiswas/pycrefine/issues/153));
use `python -c "import pycrefine; print(pycrefine.__version__)"`.

### Beautification levels

| Level | What it does |
|---|---|
| `core` (default) | Folds `else:` blocks that contain only an `if` into `elif`, writes repeated string constants as a multiplication (`'-' * 40` instead of forty dashes), and removes redundant parentheses in `print(...)` calls. |
| `none` | Leaves the reconstructed code as it comes out of the decompiler. Use it to see the raw structure when an `elif` chain looks wrong. |
| `aggressive` | Reserved for future clean-ups; today it behaves like `core`. |

For example, this `if/elif/else` compiled by Python 3.9:

| `--beautification-level none` | `--beautification-level core` |
|---|---|
| <pre>if kind == 'a':<br>    start()<br>else:<br>    if kind == 'b':<br>        stop()<br>    else:<br>        idle()<br>done()</pre> | <pre>if kind == 'a':<br>    start()<br>elif kind == 'b':<br>    stop()<br>else:<br>    idle()<br>done()</pre> |

Newer Pythons often compile a short `if/elif/else` at the end of a function into separate
`if` blocks that each repeat the rest of the function, so the levels can give the same
output there.

### Output and exit status

| Exit status | When | Output |
|---|---|---|
| `0` | The file was decompiled. | The source on standard output, or `Decompiled output saved to OUTPUT` on standard error with `-o`. |
| `1` | The file is not a `.pyc` pycrefine can read: wrong or unknown magic number, unsupported Python version, too short, or bytecode from 3.10+ under a different Python. | `Error: ...` on standard error, explaining why ([Troubleshooting](troubleshooting.md#error-messages)). |
| `1` | The file decompiled to nothing (for example an empty module). | `Warning: Decompiler returned no source code for INPUT` on standard error. |
| `1` | `-o OUTPUT` could not be written. | `Error: Failed to save decompiled output to OUTPUT: ...` |
| `1` | Any other failure, including a missing input file ([#153](https://github.com/sahebbiswas/pycrefine/issues/153)). | A Python traceback on standard error. |
| `2` | The arguments are invalid (for example an unknown `--beautification-level`). | The usage and an error from argparse. |

Decompiled source is only ever written to standard output or `OUTPUT`; every message goes
to standard error, so `pycrefine file.pyc > file.py` is safe to use in scripts.

## Python API

```python
import pycrefine

decompiler = pycrefine.get_decompiler("module.cpython-312.pyc", beautification_level="core")
source = decompiler.decompile()
```

### `pycrefine.get_decompiler(filepath, beautification_level="core")`

Reads the `.pyc` file at `filepath`, works out which Python wrote it from the magic number,
and returns a decompiler for it.

- `filepath`: path to a `.pyc` file (a `str` or an `os.PathLike`).
- `beautification_level`: `"core"`, `"none"` or `"aggressive"`, as on the command line.

Raises:

- `OSError` (such as `FileNotFoundError`) if the file cannot be read.
- `ValueError` if it is not a `.pyc` file pycrefine supports. The message is the one the
  command prints after `Error:`, and names the Python version to use when that is the
  problem.

### `decompiler.decompile()`

Returns the reconstructed source as a `str` ending in a newline. An empty module gives
`"\n"`. Decompiling never runs the bytecode.

`decompile()` reconstructs what it can: a construct it does not support comes out wrong
or simplified instead of raising, and the rest of the file is still decompiled (see
[Limitations](limitations.md)). Compile the result
(`compile(source, filename, "exec")`) when you need to know that it is at least valid
Python.

### `pycrefine.__version__`

The installed version, for example `"1.1.1"`.

### `pycrefine.main()`

The command-line entry point. It reads `sys.argv` and exits, so call it only from a script
that wants to behave exactly like the `pycrefine` command.

### Stability

`get_decompiler`, `decompile()`, `__version__`, `main` and the `pycrefine` command are the
public interface and follow the [versioning policy](../CONTRIBUTING.md#versioning).
`import pycrefine` also exposes the decompiler classes and many helpers (most of them
starting with `_`) for compatibility with the old single-file module and for the tests;
those are internal and can change in any release.
