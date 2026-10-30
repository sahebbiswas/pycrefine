# pycrefine

[![PyPI](https://img.shields.io/pypi/v/pycrefine.svg)](https://pypi.org/project/pycrefine/)
[![Python versions](https://img.shields.io/pypi/pyversions/pycrefine.svg)](https://pypi.org/project/pycrefine/)
[![Python 3.9](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py39.yml/badge.svg?branch=main&event=push)](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py39.yml)
[![Python 3.10](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py310.yml/badge.svg?branch=main&event=push)](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py310.yml)
[![Python 3.11](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py311.yml/badge.svg?branch=main&event=push)](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py311.yml)
[![Python 3.12](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py312.yml/badge.svg?branch=main&event=push)](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py312.yml)
[![Python 3.13](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py313.yml/badge.svg?branch=main&event=push)](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py313.yml)
[![Python 3.14](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py314.yml/badge.svg?branch=main&event=push)](https://github.com/sahebbiswas/pycrefine/actions/workflows/ci-py314.yml)
[![Lint](https://github.com/sahebbiswas/pycrefine/actions/workflows/lint.yml/badge.svg?branch=main&event=push)](https://github.com/sahebbiswas/pycrefine/actions/workflows/lint.yml)

pycrefine turns compiled Python bytecode (`.pyc` files) back into readable Python source.
It reads `.pyc` files written by CPython 3.9 and later, has no third-party dependencies,
and works both as a command and as a library.

```console
$ pycrefine __pycache__/report.cpython-312.pyc
def summarize(rows, limit=10):
    totals = {}
    for name, amount in rows:
        totals[name] = (totals.get(name, 0) + amount)
    return sorted(totals.items(), key=(lambda kv: -kv[1]))[:limit]
```

## Supported Python versions

| Bytecode written by | Status | Run pycrefine under |
|---|---|---|
| Python 3.9 | supported, tested in CI | any Python 3.9 or later |
| Python 3.12, 3.13, 3.14 | supported, tested in CI | the same minor version (3.12 for 3.12 bytecode) |
| Python 3.11 | partial, tested in CI: keyword-argument calls, `try`/`finally` and some loops come out wrong ([known failures](https://github.com/sahebbiswas/pycrefine/blob/main/tests/known_failures.json)) | Python 3.11 |
| Python 3.10 | partial, tested in CI: `try`/`except`, `while` loops and some `if`/`else` shapes come out wrong ([known failures](https://github.com/sahebbiswas/pycrefine/blob/main/tests/known_failures.json)) | Python 3.10 |
| Python 3.8 and older | not supported | |

Bytecode from 3.10 onwards can only be decompiled by the Python version that wrote it
([#94](https://github.com/sahebbiswas/pycrefine/issues/94)). pycrefine tells you which
version to use, and [Getting started](https://github.com/sahebbiswas/pycrefine/blob/main/docs/getting-started.md#run-under-the-python-that-wrote-the-file)
shows how to run it under another Python in one command. Some constructs (`match`,
`async`/`await`, `yield from`) are not reconstructed yet; see
[Limitations](https://github.com/sahebbiswas/pycrefine/blob/main/docs/limitations.md).

## Install

pycrefine is published on [PyPI](https://pypi.org/project/pycrefine/):

```bash
python -m pip install pycrefine
python -m pip install --upgrade pycrefine    # update an existing install
```

The `pycrefine` command and `import pycrefine` are available after that. To try it once
without installing anything, use `uvx pycrefine file.pyc`; to keep the command apart from
your projects, use `pipx install pycrefine`.

Releases on PyPI may lag behind `main`. Use the PyPI release for stable installs; if you need
a fix that is on `main` but not yet released, install from GitHub instead:

```bash
python -m pip install "git+https://github.com/sahebbiswas/pycrefine"
```

Or run it from a checkout without installing: `python pycrefine.py file.pyc`. Install
problems (`command not found`, `externally-managed-environment`, …) are covered in
[Troubleshooting](https://github.com/sahebbiswas/pycrefine/blob/main/docs/troubleshooting.md#installation-and-setup).

## Quick start

```bash
pycrefine module.cpython-312.pyc                 # print the source
pycrefine module.cpython-312.pyc -o module.py    # save it to a file
python -m pycrefine module.cpython-312.pyc       # same, if the command is not on PATH
```

From Python:

```python
import pycrefine

source = pycrefine.get_decompiler("module.cpython-312.pyc").decompile()
print(source)
```

## Documentation

| Guide | Read it to |
|---|---|
| [Getting started](https://github.com/sahebbiswas/pycrefine/blob/main/docs/getting-started.md) | install, decompile a first file, and run under the right Python version |
| [Use cases](https://github.com/sahebbiswas/pycrefine/blob/main/docs/use-cases.md) | recover a project from `__pycache__`, batch-decompile a tree, decompile PyInstaller or in-memory code, check the output |
| [Command line and Python API](https://github.com/sahebbiswas/pycrefine/blob/main/docs/usage.md) | look up every option, exit status and API call |
| [Troubleshooting](https://github.com/sahebbiswas/pycrefine/blob/main/docs/troubleshooting.md) | fix setup problems and understand each error message |
| [Limitations](https://github.com/sahebbiswas/pycrefine/blob/main/docs/limitations.md) | see what is not reconstructed yet, and how to tell |
| [Architecture](https://github.com/sahebbiswas/pycrefine/blob/main/docs/architecture.md) | find your way around the code |
| [Extending pycrefine](https://github.com/sahebbiswas/pycrefine/blob/main/docs/extending.md) | add an opcode handler or a Python version, and debug a bad decompile |
| [Contributing](https://github.com/sahebbiswas/pycrefine/blob/main/CONTRIBUTING.md) | set up a development environment, run the checks, version and release |

## Contributing

Bug reports with the `.pyc` file (or the source it came from) and the Python version that
compiled it are the most useful contribution. See [CONTRIBUTING.md](https://github.com/sahebbiswas/pycrefine/blob/main/CONTRIBUTING.md) for
the development setup, the checks every pull request runs, and the versioning policy.

## License

[MIT](https://github.com/sahebbiswas/pycrefine/blob/main/LICENSE)
