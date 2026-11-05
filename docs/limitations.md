# Limitations

What pycrefine does not reconstruct correctly yet, by Python version and by construct.
Each entry links to its issue, where progress is tracked and reproductions are welcome.
Everything here was checked against the current `main`.

## Python versions

| Bytecode written by | Status |
|---|---|
| 3.9 | Supported and tested in CI. Decompiles under any Python 3.9 or later. `async with` is not rebuilt: it reports `unsupported_opcode` ([#85](https://github.com/sahebbiswas/pycrefine/issues/85)). |
| 3.11, 3.12, 3.13, 3.14 | Supported and tested in CI. Needs the same Python minor version to run pycrefine ([#94](https://github.com/sahebbiswas/pycrefine/issues/94)). |
| 3.10 | Partial, and tested in CI: the failures that are known are expected failures in `tests/known_failures.json`. 3.10's block-based handlers (`try`/`except`/`finally`/`with`) are read into the 3.11 form, which has its own gaps: the code after a `try` or a `with` that every path copies can land in the last handler or body ([#301](https://github.com/sahebbiswas/pycrefine/issues/301)). |
| 3.8 and older | Not supported. |

## Constructs that come out wrong

These change what the code does, or produce code that does not compile:

| Construct | Versions | What you get | Issue |
|---|---|---|---|
| `match` / `case` | 3.10+ | a single `if` with the wrong condition | [#85](https://github.com/sahebbiswas/pycrefine/issues/85) |
| `async for` (and a comprehension with `async for`) on 3.9 | 3.9 | wrong code, with an `unsupported_version_construct` diagnostic | [#85](https://github.com/sahebbiswas/pycrefine/issues/85) |
| A conditional expression that is the whole condition of a `while` (`while (a if b else c):`), or one operand of `and`/`or` in it | all | the condition is rebuilt as nested `if` statements around the loop, so `b` is tested once instead of on every pass | [#292](https://github.com/sahebbiswas/pycrefine/issues/292) |
| `try` nested at the very start of another `try` | 3.12, 3.14 | the outer `except` clauses attach to the inner `try` | [#169](https://github.com/sahebbiswas/pycrefine/issues/169) |
| Some code in the test corpus | 3.12, 3.14 | output that does not parse | [#91](https://github.com/sahebbiswas/pycrefine/issues/91) |

Whenever the decompiler knows it is guessing, it records a diagnostic instead of staying
silent: see [Diagnostics](usage.md#diagnostics). Constructs below that it cannot detect still
come out wrong without one.

## Constructs that come out different but equivalent

The code runs the same way, but it does not read like the original:

| Construct | What you get | Issue |
|---|---|---|
| A short `if/elif/else` or conditional expression at the end of a function (3.12+) | separate `if` blocks, each ending in `return` | the compiler duplicates the tail |
| `if` at the end of a loop body | `if not cond: continue` followed by the body | the compiler's layout |
| Dict and list displays with `**` / `*` | `{**{**{}, **b}, **{'x': 1}}`, `[*list(a), *rest]` | how the bytecode builds them |

## Never recovered

Some things are not in the bytecode at all, so no decompiler can bring them back:
comments, blank lines and formatting, the original parentheses, type annotations on local
variables, and code the compiler optimized away (for example `if False:` blocks or
unreachable code after `return`). Files compiled with `python -O` have no `assert`
statements, and with `-OO` no docstrings either.

## Other limits

- **Not every file.** pycrefine reads standard CPython `.pyc` files. It does not unpack
  PyInstaller or zip archives, decrypt protected bytecode, or read PyPy, Jython or
  Cython output. Files without a header can be fixed; see
  [Use cases](use-cases.md#decompile-a-pyc-without-a-header-pyinstaller-marshal-dumps).
- **Silent gaps.** An instruction pycrefine does not know is skipped rather than reported.
  [Troubleshooting](troubleshooting.md#the-output-is-wrong-or-does-not-compile) lists the
  signs to look for. [#79](https://github.com/sahebbiswas/pycrefine/issues/79) tracks
  reporting them explicitly.

## How the project measures this

The test suite checks each construct it covers by running the original and the
decompiled code and comparing what they do, on Python 3.9 to 3.14. The correctness gate
(`quality/check_correctness.py`, described in [correctness.md](correctness.md)) decompiles
a corpus on every pull request and fails if any file gets worse. Both run in CI on 3.9,
3.12 and 3.14.
