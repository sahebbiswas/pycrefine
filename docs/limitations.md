# Limitations

What pycrefine does not reconstruct correctly yet, by Python version and by construct.
Each entry links to its issue, where progress is tracked and reproductions are welcome.
Everything here was checked against the current `main`.

## Python versions

| Bytecode written by | Status |
|---|---|
| 3.9 | Supported and tested in CI. Decompiles under any Python 3.9 or later. |
| 3.12, 3.13, 3.14 | Supported and tested in CI. Needs the same Python minor version to run pycrefine ([#94](https://github.com/sahebbiswas/pycrefine/issues/94)). |
| 3.11 | Partial, and tested in CI: the failure that is known is an expected failure in `tests/known_failures.json`, tracked in an issue: a conditional expression inside a call in a comprehension ([#192](https://github.com/sahebbiswas/pycrefine/issues/192)). A `break` or `continue` in an `except` clause inside a loop is also still wrong, tracked in [#193](https://github.com/sahebbiswas/pycrefine/issues/193) without an expected-failure test. |
| 3.10 | Partial, and tested in CI: the failures that are known are expected failures in `tests/known_failures.json`. Block-based exception handlers (`try`/`except`/`finally`) are not decompiled ([#179](https://github.com/sahebbiswas/pycrefine/issues/179)), and tail-duplicated `if`/`else` and conditional expressions come out as separate `if`s, one of them with invalid syntax ([#197](https://github.com/sahebbiswas/pycrefine/issues/197)). |
| 3.8 and older | Not supported. |

## Constructs that come out wrong

These change what the code does, or produce code that does not compile:

| Construct | Versions | What you get | Issue |
|---|---|---|---|
| `match` / `case` | 3.10+ | a single `if` with the wrong condition | [#85](https://github.com/sahebbiswas/pycrefine/issues/85) |
| `async def`, `await`, `async for`, `async with` | all | a generator full of `yield None` | [#85](https://github.com/sahebbiswas/pycrefine/issues/85) |
| `yield from`, and `x = yield` | 3.12, 3.14 | `yield None` and the value is lost | [#152](https://github.com/sahebbiswas/pycrefine/issues/152) |
| Walrus (`:=`) inside a comprehension, or a comprehension variable captured by a `lambda` | 3.12+ | a broken explicit loop | [#99](https://github.com/sahebbiswas/pycrefine/issues/99) |
| `try`/`except` around a `with` that always raises, inside another `with` | 3.12, 3.14 | the code after the outer `with` is misplaced | [#146](https://github.com/sahebbiswas/pycrefine/issues/146) |
| Code after a `with` whose body never falls through (always `raise`s or `continue`s) | 3.12, 3.14 | lost; it runs only when `__exit__` suppresses the exception | [#177](https://github.com/sahebbiswas/pycrefine/issues/177) |
| `while` loop whose `try` body has an `except` that does `continue`, followed by code after the loop (or an `else:`) | 3.12, 3.13 | the code after the loop is written inside the `try` body and the `continue` is dropped | [#291](https://github.com/sahebbiswas/pycrefine/issues/291) |
| A conditional expression in a `while` condition | 3.12+ | a constant condition and empty `if`/`else` statements | [#292](https://github.com/sahebbiswas/pycrefine/issues/292) |
| `except E: continue` in a `while` loop | 3.9, 3.11 | the `continue` lands after the `try`, or the statements after it join the `try` body | [#286](https://github.com/sahebbiswas/pycrefine/issues/286) |
| A `return` in a handler inside a `with` in a loop | 3.9 | the `return` lands after the `try`, or the statements after it join the `try` body | [#286](https://github.com/sahebbiswas/pycrefine/issues/286) |
| A `while` loop with code after it, whose `except` clause ends in an explicit `return None` | 3.12, 3.13 | the code after the loop is written inside the `try` body | [#293](https://github.com/sahebbiswas/pycrefine/issues/293) |
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
| A tail the compiler copies into an `except` handler (3.12+) | the same statements twice, the second unreachable | [#148](https://github.com/sahebbiswas/pycrefine/issues/148) |

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
