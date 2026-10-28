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
  [`quality/check_coherency.py`](../quality/check_coherency.py). The coherency
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
Python version (3.9, 3.10, 3.11, 3.12, 3.13 and 3.14) and commit the diff:

```bash
python quality/check_correctness.py --update-baseline
```

([uv](https://docs.astral.sh/uv/) makes this easy:
`uv run --no-project --python 3.9 quality/check_correctness.py --update-baseline`.)

The CI workflow runs the gate after pytest and uploads the JSON report as the
`correctness-report-py<version>` artifact. `tests/test_correctness.py` also
asserts no regressions against the committed baseline, so a plain `pytest`
run catches them locally.

**Hangs and crashes.** The gate decompiles in a child process (one process serves many
entries and is replaced when it dies). `--timeout SEC` (default 60, `0` for none) stops a
decompilation that runs too long, on every platform and from any thread, even when it is
stuck in C code; the entry is recorded as a `decompile_error` ("exceeded 60s") and the
run continues. The timeout starts once the child has finished starting up, so interpreter
start-up and imports are never charged to an entry. A child that crashes is reported the
same way.

The default corpus is `corpus/` (`.py` sources) plus the committed `.pyc`
fixtures in `tests/fixtures/foreign_pyc/`. Any other file or directory can be measured without gating:

```bash
python quality/check_correctness.py src/pycrefine/ src/pycrefine/decompiler/ src/pycrefine/opcodes/ tests/ --no-baseline -v
```

## Differential behavior check

The gate above asks whether the output *compiles*. `quality/check_differential.py` asks
whether it *behaves*, over the versioned corpus in `quality/corpus/`. For each entry it
compiles the source with the running interpreter, decompiles it, compiles the result,
runs the original and the reconstructed source in separate subprocesses, and compares
the exception (type and message), stdout, stderr and the repr of a module-level `RESULT`
if the program defines one. Each entry lands on a ladder, lowest to highest:

| Status | Meaning |
|---|---|
| `decompile_error` | pycrefine raised, produced nothing, or ran out of time |
| `syntax_error` | the decompiled source does not compile |
| `compiles` | it compiles; the entry is `compile_only`, so it is not run |
| `mismatch` | it runs, but not like the original; the report shows the differing field |
| `equivalent` | same exception, output and `RESULT` |

`compiles` and `equivalent` both count as passing. An entry that cannot run on the
interpreter at all (`source_error`) is reported but not scored.

```bash
python quality/check_differential.py                       # the whole corpus, against the baseline
python quality/check_differential.py flow_while -v         # one entry, with the generated source
python quality/check_differential.py --category stdlib     # one category
python quality/check_differential.py --update-baseline     # record the current results
python quality/check_differential.py --python python3.9 --python python3.12   # other interpreters
```

### The corpus

`quality/corpus/manifest.json` describes every entry. Four categories cover different
risks:

| Category | Where | What it is |
|---|---|---|
| `feature` | `features/` | one language feature per small program, including modern syntax (`match`, `async`, walrus, generics) gated by Python version |
| `generated` | `generated/` | control-flow combinations written by `generate_control_flow.py` (`--check` fails when the files are stale) |
| `realworld` | `realworld/` | small programs shaped like real code: a tokenizer, a state machine, an LRU cache |
| `stdlib` | `module` entries | standard-library modules as shipped with the running Python, checked to `compile_only` because their behavior depends on the platform. The baselines are per minor version, and these entries read the running interpreter's own library, so a patch release can change a result; the report says so when a stdlib entry fails |

Each entry records its `summary`, the Python range its source needs (`source_python`
`min`/`max`), how it is compiled (`compile.optimize`, so `python -O`/`-OO` bytecode is
covered), whether it must be `equivalent` or only `compile_only`, a `support` summary
(`supported`, `partial` or `unsupported`) and the `limitations` that explain every
version where it falls short: the tracking `issue`, the `versions` it affects and a
one-line `summary`.

### Baselines and limitations

Each run is compared with a committed baseline for the Python it runs under
(`quality/corpus/baselines/py3.X.json`). It fails when an entry does worse than its
baseline, or when an entry is added or removed without updating the baseline.
Improvements never fail; record them with `--update-baseline` on every CI Python
version. `tests/test_corpus.py` keeps the manifest and the baselines honest: every file
has an entry and the reverse, every failing entry cites an issue for that version, a
limitation is dropped once the entry passes, `support` matches the baselines, and each
supported version has passing entries in every category. A program that exposes a new gap
gets an issue before it gets a baseline entry. CI runs the check after the gate, on every
Python version, and uploads the JSON report with the correctness report.

Bytecode from 3.10 onwards can only be decompiled by the interpreter that wrote it, so each run covers
the interpreter it runs under; `--python` runs the whole check under others in turn.

**Diagnostics.** Both tools decompile with diagnostics on (see
[Diagnostics](usage.md#diagnostics)) and record, in the baseline, how many of each kind
every entry, unit or corpus entry reported. A run fails with `noisier` when something
reports more of a kind than its baseline allows, so a clean entry cannot start guessing
unnoticed, and a construct known to be unsupported cannot silently stop saying so
(`tests/test_diagnostic_constructs.py` checks that for `match`, async, type parameters,
f-string conversions and 3.10 exception blocks). Fewer diagnostics never fail; record
them with `--update-baseline`. Each report also counts **silent failures**: output that
does not compile (gate) or is wrong (differential) and carries no diagnostic. That number is
the measure of the roadmap's "no silent fallbacks" goal; it should only go down.

The correctness gate's baselines (`quality/baselines/`) carry these counts in three optional keys; `schema_version` stays `1`
because a baseline without them is still read:

| Key | Where | Shape |
|---|---|---|
| `diagnostics_recorded` | top level | `true` when the file was made with diagnostics on. Only then is `noisier` enforced; a baseline without it is not held to diagnostics until it is re-recorded. |
| `diagnostics` | an entry | `{kind: count}` for the whole-file run of that source or `.pyc` entry. Absent when it reported nothing, or when the whole-file run failed outright. |
| `unit_diagnostics` | a source entry | `{unit id: {kind: count}}`, only for units that reported something. Every unit id is also a key of `units`. |

The summary (text and JSON) reports them apart too: `diagnostics` totals the units and
`entry_diagnostics` the whole-file runs (a `.pyc` has only the latter), and each entry in the
JSON report carries its own `diagnostics` when it has any. A whole-file result that does not
compile and carries no diagnostic counts as a silent failure alongside the units.

`diagnostics` and `unit_diagnostics` are counted by separate runs (the file, then each unit
on its own), so they are compared separately and need not add up. The kinds are those of
`pycrefine.diagnostics.KINDS`.

The differential check's baselines (`quality/corpus/baselines/`) keep one top-level
`diagnostics` object, `{corpus entry: {kind: count}}`, listing only the entries that
reported something. A baseline without that key is not held to diagnostics.

**Post-processing guard.** The text rewrites after emission (`post_process_source`,
`flatten_elif`) are checked by `tests/test_postprocess_guard.py`: for every corpus entry that
applies to the running Python (compiled at the optimization level the entry declares), every
decompiled unit whose raw output parses, nested function bodies included, must be rewritten
to text that parses to the same syntax tree and that rewriting again leaves unchanged.
Import statements are compared one name per line with repeats within a run dropped, because
merging them is a deliberate rewrite. A rewrite that changes
meaning shows up there, with the entry that exposed it.

**Interpreter patch versions.** Baselines are per minor version, and each also records the
full version it was made on (`python_patch`, e.g. `3.12.3`), as does the JSON report next to
`baseline_python_patch`. When a run fails on a different patch release, the report says so,
because the `stdlib` entries read the running interpreter's library and can change
between patch releases without any pycrefine change. After a deliberate CI interpreter
bump, refresh both sets of baselines on that version and review the diffs: only entries
that changed should differ, plus the `python_patch` line.

```bash
python quality/check_correctness.py --update-baseline    # the correctness gate's baseline
python quality/check_differential.py --update-baseline   # the differential check's baselines
```

**Safety.** The corpus is committed and trusted, but it is executed, so every step
runs in a subprocess with a wall-clock timeout, no inherited environment and an empty working
directory, and (on POSIX) limits on CPU time, memory and file size. That bounds a
runaway program; it is not a sandbox. Do not point `--corpus` at code you do not trust
without running the whole check in a container.

**Adding an entry.** Put a small `.py` program in the matching `quality/corpus/`
directory and add it to `manifest.json`. It should print what it computes and may set
`RESULT`; avoid anything that depends on the time, randomness, the network or the file
system. Run it on every Python version, file an issue for each gap it exposes, record
the issue under `limitations`, and record the baselines.

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
    {"path": "corpus/simple.py", "unit": "def f",   // unit null = whole entry
     "change": "improved", "before": "syntax_error", "after": "compiles"}
  ],
  "entries": [
    {
      "path": "corpus/simple.py",
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
| 3.10        | 7 / 13            | 71 / 77         | 52 / 77         |
| 3.11        | 7 / 13            | 73 / 77         | 52 / 77         |
| 3.12        | 8 / 13            | 73 / 77         | 49 / 77         |
| 3.13        | 9 / 13            | 75 / 77         | 52 / 77         |
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
