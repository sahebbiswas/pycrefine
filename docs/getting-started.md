# Getting started

This guide installs pycrefine, decompiles a first file, and shows how to run it under the
Python version a `.pyc` file needs. It takes about five minutes.

## Install

pycrefine needs Python 3.9 or later and has no runtime dependencies. Pick one:

| Method | Command | Use it when |
|---|---|---|
| pip, in a virtual environment | `python -m pip install pycrefine` | you also want `import pycrefine` |
| pipx | `pipx install pycrefine` | you only want the `pycrefine` command, isolated from your projects |
| uv | `uvx pycrefine file.pyc` | you want to run it once without installing anything |
| From GitHub | `python -m pip install "git+https://github.com/sahebbiswas/pycrefine"` | you want the latest `main`, or PyPI has no release yet |
| A source checkout | `git clone https://github.com/sahebbiswas/pycrefine` then `python pycrefine.py file.pyc` | you are developing pycrefine (see [CONTRIBUTING.md](../CONTRIBUTING.md)) |

Check the install:

```bash
python -c "import pycrefine; print(pycrefine.__version__)"
pycrefine --help
```

If `pycrefine` is not found, use `python -m pycrefine` instead, or see
[Troubleshooting](troubleshooting.md#pycrefine-command-not-found).

## Decompile a first file

Make a small module and compile it, the way Python does when it imports it:

```bash
mkdir demo && cd demo
cat > shapes.py <<'EOF'
import math

class Circle:
    def __init__(self, radius):
        self.radius = radius

    def area(self):
        return math.pi * self.radius ** 2

def total_area(circles, minimum=0):
    total = 0
    for c in circles:
        if c.radius > minimum:
            total += c.area()
    return total
EOF
python -m py_compile shapes.py
ls __pycache__            # shapes.cpython-312.pyc (the tag is your Python version)
```

Decompile it:

```bash
pycrefine __pycache__/shapes.cpython-*.pyc
```

```python
import math

class Circle:
    def __init__(self, radius):
        self.radius = radius

    def area(self):
        return (math.pi * (self.radius ** 2))

def total_area(circles, minimum=0):
    total = 0
    for c in circles:
        if c.radius > minimum:
            total += c.area()
    return total
```

The structure, names and constants come back. Comments, formatting and the original
parenthesization do not, because they are not in the bytecode. Equivalent forms are also
normal: on Python 3.9 and 3.14 the `if` at the end of the loop comes back as
`if not c.radius > minimum: continue` followed by `total += c.area()`, which does the
same thing.

Save the result instead of printing it:

```bash
pycrefine __pycache__/shapes.cpython-*.pyc -o shapes_recovered.py
```

The confirmation (`Decompiled output saved to ...`) goes to stderr, so it does not end up
in redirected output.

## Use it from Python

```python
import pycrefine

decompiler = pycrefine.get_decompiler("__pycache__/shapes.cpython-312.pyc")
source = decompiler.decompile()
```

[Command line and Python API](usage.md) lists the options and what each call raises.

## Run under the Python that wrote the file

A `.pyc` file records which Python compiled it. Bytecode from Python 3.9 can be decompiled
under any Python 3.9 or later, but bytecode from 3.10 onwards needs the same minor version
([#94](https://github.com/sahebbiswas/pycrefine/issues/94)). Otherwise pycrefine stops with:

```text
Error: Cannot decompile Python 3.12 bytecode from 'shapes.cpython-312.pyc' under Python 3.14: Python 3.10+ bytecode can only be decompiled by the same Python minor version that wrote it. Run pycrefine under Python 3.12. (Python 3.9 bytecode works on any host.)
```

The file name usually tells you the version (`cpython-312` is 3.12); if it does not, see
[Which Python wrote this file?](use-cases.md#find-out-which-python-wrote-a-pyc-file).
Then run pycrefine under that version:

| Tool | Command |
|---|---|
| uv | `uvx --python 3.12 pycrefine file.pyc` (uv downloads Python 3.12 if needed) |
| Windows launcher | `py -3.12 -m pip install pycrefine`, then `py -3.12 -m pycrefine file.pyc` |
| pyenv | `pyenv install 3.12`, then `pyenv shell 3.12` and install pycrefine there |
| Docker | `docker run --rm -v "$PWD:/w" -w /w python:3.12 sh -c "pip install -q pycrefine && pycrefine file.pyc"` |
| A source checkout | `uv run --no-project --python 3.12 python pycrefine.py file.pyc` |

## Check the result

Decompiled code is a reconstruction, so check it before relying on it:

```bash
python -m py_compile shapes_recovered.py   # does it parse and compile?
```

If you still have the original source, compare the two programs rather than the text;
[Use cases](use-cases.md#check-that-decompiled-code-matches-the-original) shows an AST
comparison and the coherency score. If the output does not compile or looks wrong, see
[Limitations](limitations.md) and [Troubleshooting](troubleshooting.md#the-output-is-wrong-or-does-not-compile).

## Next steps

- [Use cases](use-cases.md): recover a whole project, batch processing, PyInstaller
  archives, in-memory code objects.
- [Troubleshooting](troubleshooting.md): every error message and its fix.
