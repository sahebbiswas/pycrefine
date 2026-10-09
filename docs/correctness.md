# Correctness-first quality framework

pycrefine output is judged first on whether it is *correct* Python, and only
then on whether it *looks like* the original. This page defines the
correctness model, the CI gate built on it, its machine-readable report, and
the current baseline.

The gate is implemented by [`quality/check_correctness.py`](../quality/check_correctness.py).

## Correctness model

Each decompilation is placed on an ordered ladder. A result sits on the
highest rung it reaches; every rung implies the ones below it.

| Level | Status            | Meaning                                                                 |
|------:|-------------------|-------------------------------------------------------------------------|
| 0     | `decompile_error` | pycrefine raised, timed out, or returned something that is not a string |
| 1     | `syntax_error`    | Output does not parse (`ast.parse` fails)                               |
| 2     | `compile_error`   | Output parses but the compiler rejects it (e.g. `'return' outside function`) |
| 3     | `compiles`        | Output compiles to a code object                                         |
| 4     | *behavior*        | Output behaves like the original. **Defined, not yet evaluated** (#77); reported as `"behavior": "not_evaluated"` |

`source_error` is not on the ladder: it means the corpus *source* does not
compile on the current interpreter, which says nothing about the decompiler.
Such entries are reported but never gated.

Syntax and compile checks run on the interpreter executing the gate, so
results are per Python version. The decompiler itself is version-aware, but
"does this output compile?" depends on the host grammar.

### Units

Most corpus files do not yet compile as a whole, so file-level status alone
would hide regressions inside them. Each `.py` corpus entry is therefore also
split into **units**, which are checked independently:

* every top-level statement is a unit (`def name`, `class name`, or
  `<StmtType>: <first source line>` for other statements, e.g.
  `If: if __name__ == "__main__":`). Ids never contain line numbers, so
  moving a statement keeps its identity;
* a top-level class is split further, one unit per body member, wrapped in a
  bare `class Name:` header (`class Name/def method`);
* module-level `from __future__` imports are prepended to every unit;
* repeated ids get a `#2`, `#3`, ... suffix.

Each unit is compiled to bytecode, decompiled and placed on the same ladder.
A unit that does not compile on its own in the original source is reported
as `source_error`.

### Optional metrics (diagnostic only)

These are reported but never affect pass/fail:

* **`ast_equal`** (per compiling unit): the decompiled unit's AST is
  identical to the original's (`ast.dump`, positions ignored). A strict
  measure of structural fidelity.
* **Coherency** (`--coherency`, per source entry): the composite score from
  [`debug/check_coherency.py`](../debug/check_coherency.py). The coherency
  checker scores textual resemblance (token and line recall, keyword
  coverage, cleanliness). It is a readability diagnostic, **not** a semantic
  oracle: output can score highly and still fail to compile, as the baseline
  below shows.

## CI semantics

The gate compares each run against a committed baseline for the running
Python version, `quality/baselines/py<major>.<minor>.json`. The baseline
records the status of every entry and every unit.

| Change       | When                                                          | Fails CI? |
|--------------|---------------------------------------------------------------|-----------|
| `regressed`  | an entry or unit is on a lower rung than its baseline          | **yes**   |
| `improved`   | an entry or unit is on a higher rung than its baseline         | no        |
| `new`        | an entry or unit has no baseline record                        | no        |
| `missing`    | a baseline record has no corresponding entry or unit           | **yes**   |
| `lost`       | a scored record now has a `source_error`                       | **yes**   |
| `ambiguous`  | the number of units sharing one label (`label`, `label#2`, ...) changed | **yes** |
| `unscorable` | the baseline record was a `source_error`                       | no        |

Exit codes: `0` pass, `1` at least one failing change, `2`
configuration error (no baseline, a baseline for another Python version or
schema, bad path).

`missing`, `lost` and `ambiguous` fail because the baseline record can no
longer be compared reliably. (`ambiguous` covers duplicate labels: inserting a
statement whose first line matches an existing one would otherwise shift the
`#n` suffixes onto different statements.) For example,
editing a statement's first line renames its unit, and without this rule a
regression in it would pass as `missing` + `new`. Changing the corpus
therefore needs a baseline update in the same change.

Improvements never break CI. Recording them
ratchets the baseline upward, so the improvement can't silently regress
later. When a change improves results, regenerate the baseline on **every** CI
Python version (3.9, 3.12 and 3.14) and commit the diff:

```bash
python quality/check_correctness.py --update-baseline
```

([uv](https://docs.astral.sh/uv/) makes this easy:
`uv run --no-project --python 3.9 quality/check_correctness.py --update-baseline`.)

The CI workflow runs the gate after pytest and uploads the JSON report as the
`correctness-report-py<version>` artifact. `tests/test_correctness.py` also
asserts no regressions against the committed baseline, so a plain `pytest`
run catches them locally.

The default corpus is `test_files/` (`.py` sources and the committed `.pyc`
fixtures). Any other file or directory can be measured without gating:

```bash
python quality/check_correctness.py pycrefine.py tests/ --no-baseline -v
```

A larger, versioned corpus is tracked in #78.

## Machine-readable output

`--json` prints the report to stdout; `--report PATH` writes it to a file.
The report (schema version 1) looks like this:

```jsonc
{
  "schema_version": 1,
  "python_version": "3.12",
  "levels": ["decompile_error", "syntax_error", "compile_error", "compiles"],
  "baseline": "quality/baselines/py3.12.json",    // null with --no-baseline
  "result": "pass",                               // or "fail"
  "summary": {
    "entries": {"total": 13, "by_status": {"compiles": 3, "...": 0}},
    "units":   {"total": 77, "by_status": {"compiles": 70, "...": 0}, "ast_equal": 45},
    "changes": {"improved": 1}                     // absent with --no-baseline
  },
  "changes": [                                     // null with --no-baseline
    {"path": "test_files/simple.py", "unit": "def f",   // unit null = whole entry
     "change": "improved", "before": "syntax_error", "after": "compiles"}
  ],
  "entries": [
    {
      "path": "test_files/simple.py",
      "kind": "source",                            // or "pyc"
      "status": "compile_error",
      "level": 2,                                  // null for source_error
      "error": {"stage": "compile", "type": "SyntaxError",
                "message": "'return' outside function", "lineno": 13},
      "behavior": "not_evaluated",
      "unit_summary": {"total": 3, "by_status": {"compiles": 2, "compile_error": 1}, "ast_equal": 1},
      "units": {"def f": {"status": "compiles", "ast_equal": true}},
      "coherency": {"composite_score": 93.9, "grade": "A"}   // only with --coherency
    }
  ]
}
```

`error.stage` is one of `decompile`, `syntax`, `compile`, `source` or
`coherency`.

## Baseline (October 2026)

Default corpus: 13 entries (7 sources, 6 `.pyc` fixtures) and 77 units.

| Host Python | Entries compiling | Units compiling | Units AST-equal |
|-------------|------------------:|----------------:|----------------:|
| 3.9         | 9 / 13            | 77 / 77         | 58 / 77         |
| 3.12        | 8 / 13            | 73 / 77         | 49 / 77         |
| 3.14        | 9 / 13            | 73 / 77         | 48 / 77         |

The coherency composite for every source entry is 90% or higher on every
version, including the entries that fail to compile.

Main gaps visible in the baseline:

* Python 3.10+ `.pyc` fixtures decompile only on a host of the same minor
  version (#94); Python 3.9 fixtures decompile on every host (#89).
* Some output compiles but behaves differently from the source, which the
  ladder cannot see until behavioral testing lands (#77). Known cases:
  `while True` with a conditional `break` (#96), and `if` decompiled as
  `while` inside nested `with`/`for` (#97).
* Some exception-handling and conditional constructs produce unparsable
  output on 3.12 and 3.14 (`cannot assign to literal`, empty `except` bodies).
