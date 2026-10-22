# Limitations

What pycrefine does not reconstruct correctly yet, by Python version and by construct.
Each entry links to its issue, where progress is tracked and reproductions are welcome.
Everything here was checked against the current `main`.

## Python versions

| Bytecode written by | Status |
|---|---|
| 3.9 | Supported and tested in CI. Decompiles under any Python 3.9 or later. |
| 3.12, 3.14 | Supported and tested in CI. Needs the same Python minor version to run pycrefine ([#94](https://github.com/sahebbiswas/pycrefine/issues/94)). |
| 3.13 | Mostly works (the test suite has two known failures); not in CI ([#6](https://github.com/sahebbiswas/pycrefine/issues/6)). |
| 3.10, 3.11 | Partial. Loops, exception handling and `try`/`finally` often come out wrong ([#3](https://github.com/sahebbiswas/pycrefine/issues/3), [#4](https://github.com/sahebbiswas/pycrefine/issues/4), [#105](https://github.com/sahebbiswas/pycrefine/issues/105)). |
| 3.8 and older | Not supported. |

## Constructs that come out wrong

These change what the code does, or produce code that does not compile:

| Construct | Versions | What you get | Issue |
|---|---|---|---|
| `global` and `nonlocal` declarations | all | the declaration is dropped, so assignments become local | [#155](https://github.com/sahebbiswas/pycrefine/issues/155) |
| Starred assignment, `first, *rest = x` | all | `first = x` (3.9, 3.12) or `first, rest = x` (3.14) | [#156](https://github.com/sahebbiswas/pycrefine/issues/156) |
| `match` / `case` | 3.10+ | a single `if` with the wrong condition | [#85](https://github.com/sahebbiswas/pycrefine/issues/85) |
| `async def`, `await`, `async for`, `async with` | all | a generator full of `yield None` | [#85](https://github.com/sahebbiswas/pycrefine/issues/85) |
| `yield from`, and `x = yield` | 3.12, 3.14 | `yield None` and the value is lost | [#152](https://github.com/sahebbiswas/pycrefine/issues/152) |
| Walrus (`:=`) inside a comprehension, or a comprehension variable captured by a `lambda` | 3.12+ | a broken explicit loop | [#99](https://github.com/sahebbiswas/pycrefine/issues/99) |
| Keyword-only defaults, `assert`, f-string `!r`/format specs | 3.14 | the default and the format are lost; `assert` raises `0(...)` | [#157](https://github.com/sahebbiswas/pycrefine/issues/157) |
| Conditional expression used as a callee, `(f if c else g)(x)` | all | an `if/else` that loses one branch's call | [#178](https://github.com/sahebbiswas/pycrefine/issues/178) |
| Conditional expression whose branch builds a list/dict or calls with `*args`/keywords | all | an empty `if/else` and the wrong assignment | [#147](https://github.com/sahebbiswas/pycrefine/issues/147) |
| `try`/`except` around a `with` that always raises, inside another `with` | 3.12, 3.14 | the code after the outer `with` is misplaced | [#146](https://github.com/sahebbiswas/pycrefine/issues/146) |
| Code after a `with` whose body never falls through (always `raise`s or `continue`s) | 3.12, 3.14 | lost; it runs only when `__exit__` suppresses the exception | [#177](https://github.com/sahebbiswas/pycrefine/issues/177) |
| `except E: continue` inside a `with` block inside a loop | all | the `continue` or the code after the `with` lands in the wrong block | [#176](https://github.com/sahebbiswas/pycrefine/issues/176) |
| `try` / `except` / `else` | 3.12, 3.14 | the `else:` body is merged into the `try`, so exceptions it raises are caught | [#158](https://github.com/sahebbiswas/pycrefine/issues/158) |
| `try` / `except` / `else` whose `else` returns, with code after the `try` | 3.12, 3.14 (3.9 with `finally`) | the code after the `try` lands in the last `except` clause; invalid output with `finally` on 3.9 | [#162](https://github.com/sahebbiswas/pycrefine/issues/162) |
| Empty `finally: pass` in a loop | 3.9 | the `finally:` block is lost | [#141](https://github.com/sahebbiswas/pycrefine/issues/141) |
| `if c: continue` directly followed by `return` in a `while` loop | all | the `if` is dropped, so the loop returns on its first pass | [#160](https://github.com/sahebbiswas/pycrefine/issues/160) |
| `while` loop that ends a function | 3.12 | a `return None` inside the loop body, so it runs once | [#161](https://github.com/sahebbiswas/pycrefine/issues/161) |
| Some code in the test corpus | 3.12, 3.14 | output that does not parse | [#91](https://github.com/sahebbiswas/pycrefine/issues/91) |

## Constructs that come out different but equivalent

The code runs the same way, but it does not read like the original:

| Construct | What you get | Issue |
|---|---|---|
| Chained comparison used as a value (`x = a < b < c`) | an `if/else` with stray expression lines | [#154](https://github.com/sahebbiswas/pycrefine/issues/154) |
| A short `if/elif/else` or conditional expression at the end of a function (3.12+) | separate `if` blocks, each ending in `return` | the compiler duplicates the tail |
| `if` at the end of a loop body | `if not cond: continue` followed by the body | the compiler's layout |
| Class-level annotations (`x: int = 1`) | `x = 1` and `__annotations__['x'] = int` | how the bytecode stores them |
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
