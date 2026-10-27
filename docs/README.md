# pycrefine documentation

## Using pycrefine

- [Getting started](getting-started.md): install, decompile a first file, and run under
  the Python version a `.pyc` file needs.
- [Use cases](use-cases.md): recover a project from `__pycache__`, find out which Python
  wrote a file, decompile files from several versions, PyInstaller and other headerless
  bytecode, in-memory code objects, checking the output, and using pycrefine in scripts.
- [Command line and Python API](usage.md): every option, beautification level, exit
  status and API call, and what counts as the public interface.
- [Troubleshooting](troubleshooting.md): installation problems, every error message and
  its fix, signs of output that went wrong, and how to report a problem.
- [Limitations](limitations.md): supported Python versions, constructs that are not
  reconstructed yet (with their issues), and what no decompiler can recover.

## Developing pycrefine

- [Contributing](../CONTRIBUTING.md): setup, the checks every pull request runs, writing
  tests, reporting gaps, versioning and releasing.
- [Architecture](architecture.md): the repository and package layout, how a `.pyc`
  file becomes source, the passes and their order, the decompiler's state, and the
  quirks of each Python version.
- [Extending pycrefine](extending.md): add an opcode handler or a Python version, and
  debug a bad decompile.
- [Correctness gate](correctness.md): the corpus check that keeps results from getting
  worse, its baselines and its report.
- [Linting and type checking](linting.md): the pylint ratchet and strict mypy.
- [Releasing](releasing.md): the release workflow and its one-time setup.
- [Self-decompilation findings](self-decompilation-issues.md): an early analysis of
  decompiling pycrefine's own code (historical).

The [README](../README.md) is the short landing page, also shown on PyPI; detailed
behaviour belongs in these guides, so each topic has one place where it is documented.
