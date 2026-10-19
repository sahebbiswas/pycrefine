# Linting and type checking

CI runs two checks beside the tests and the [correctness gate](correctness.md), in the
`Lint` workflow (`.github/workflows/lint.yml`, Python 3.12). Neither changes test or
gate results.

| Check | Command | Fails when |
|---|---|---|
| Lint ratchet (pylint) | `python quality/check_lint.py` | a finding appears that `quality/lint_baseline.json` does not allow |
| Types (mypy) | `mypy` | a strict module has a type error |

Install the pinned tools with `pip install -e ".[dev]"`. Use Python 3.12 for the lint
ratchet; pylint infers types from the interpreter's standard library, so other versions can
report slightly different findings (the script says so when it notices).

## The lint ratchet

pylint runs with the repository's `.pylintrc`. Existing findings are debt that is recorded
but not blocking; the check fails only on findings that are **new**, so debt cannot grow
and the refactors planned in #117 cannot quietly make things worse.

A finding is identified by `path::symbol::enclosing-object` (for example
`src/pycrefine/decompiler/generic.py::attribute-defined-outside-init::DecompilerGeneric._prescan_ternaries`),
and the baseline stores how many of each exist. Line numbers are deliberately not part of
the identity, so edits elsewhere in a file do not disturb it.

`cyclic-import` and `duplicate-code` concern several modules at once, and pylint reports
them on whichever module it happens to visit first (which differs between machines), so
they are identified by their message alone: `<project>::cyclic-import::Cyclic import (a -> b)`.

Checks switched on for this ratchet, beyond pylint's defaults:

* `broad-exception-caught` and `bare-except`, for the audit in #122
* `attribute-defined-outside-init`, for the state objects of #37 and #83
* `too-many-branches`, `too-many-statements`, `too-many-lines`, and `too-complex`
  (McCabe complexity above 15, from `pylint.extensions.mccabe`), as the complexity budget

### When the check fails

Fix the finding. If you believe it is a false positive, add a targeted
`# pylint: disable=<symbol>` with a reason next to the code.

### Paying debt down

When your change removes baselined findings the check passes and prints how many are gone.
Record the lower counts in the same pull request:

```bash
python quality/check_lint.py --update-baseline
```

This command refuses to raise any count, so it is always safe to run.

### Code that moved

Because the identity includes the enclosing object, moving or renaming a function (for
example when #119 or #120 split `generic.py`) makes its existing findings look new. After
checking that the refactor added no findings of its own, accept the move explicitly and
say so in the pull request:

```bash
python quality/check_lint.py --update-baseline --allow-increase
git diff quality/lint_baseline.json   # the diff should only show renamed keys
```

Use `-v` to list every current finding, baselined or not.

## Types

mypy is permissive by default (`ignore_errors = true` in `pyproject.toml`) and strict for
the modules listed in the override at the bottom of that file, currently the small ones:
`_constants`, `cli`, `codeobj`, `expr`, `instructions` and `versions`. Add a module to the
list once it passes with the same flags; new modules (such as the `analysis/` package
planned in #120) should start on the list.

mypy 2.x cannot target Python 3.9, so it checks against 3.10 (`python_version`). Syntax
that is not valid on 3.9 is still caught by the 3.9 test and gate jobs.
