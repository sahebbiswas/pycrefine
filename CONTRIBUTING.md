# Contributing to pycrefine

Thank you for helping! This guide gets a development environment running and explains what
every pull request needs. [Every pull request](#every-pull-request) is the checklist,
[Reporting gaps](#reporting-gaps) covers problems you find along the way, and
[Versioning](#versioning) and [Releasing](#releasing) cover how versions and releases work.
[Architecture](docs/architecture.md) is the map of the code.

## Prerequisites

- **Python 3.9 or later**, and ideally the five versions CI tests: **3.9, 3.11, 3.12,
  3.13 and 3.14**. Bytecode from 3.10 onwards is only readable by the matching interpreter, so a
  fix is checked under each of them. [uv](https://docs.astral.sh/uv/) installs and runs
  them on demand (`uv python install 3.9 3.12 3.14`); pyenv or system packages work too.
- Git.

pycrefine has no runtime dependencies, and must not gain any: it only uses the standard
library.

## Setup

```bash
git clone https://github.com/sahebbiswas/pycrefine.git
cd pycrefine

python -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
python -m pip install -e ".[dev]"  # pytest, and the pinned pylint and mypy
```

With uv:

```bash
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
```

Without installing the package, `python pycrefine.py file.pyc` runs the code in `src/`.
The tests need `pytest` (from the `dev` or `test` extra, or `pip install pytest`); with it
installed, `python -m pytest` works from the repository root.

## Running the checks

CI runs all of these on every pull request; run them locally first.

| Check | Command | CI workflow |
|---|---|---|
| Tests | `python -m pytest` | `Python 3.9`, `3.11`, `3.12`, `3.13`, `3.14` |
| Correctness gate | `python quality/check_correctness.py` | the same five |
| Lint ratchet (pylint) | `python quality/check_lint.py` (on Python 3.12) | `Lint` |
| Types (mypy) | `mypy` | `Lint` |
| Version bumped | see [Versioning](#versioning) | `Version` |

**Tests on each Python version.** A change to the decompiler needs the suite on 3.9, 3.11,
3.12, 3.13 and 3.14:

```bash
for v in 3.9 3.11 3.12 3.13 3.14; do uv run --no-project --python "$v" --with pytest python -m pytest -q; done
```

3.11 is only partly supported. Its known failures are listed in
[`tests/known_failures.json`](tests/known_failures.json), grouped by the issue that tracks
each one, and run as strict expected failures: CI stays green while the gap exists and goes
red when a listed test starts to pass. **When you fix a gap, delete its entries** (and
close the issue). A test that checks several cases with `subTest` is listed with
`"mode": "skip"` instead, because pytest reports each subtest separately. A change must not
add new failures on 3.11; if you find one you won't fix, file an issue and list the test.
3.10 is partly supported too and not in CI yet.

**Correctness gate.** `quality/check_correctness.py` decompiles the corpus (`corpus/` and
the foreign-version fixtures) and fails if any file or function gets worse than its
per-version baseline in `quality/baselines/`. When your change improves results, record
the new baseline **on each of 3.9, 3.11, 3.12, 3.13 and 3.14**:

```bash
python quality/check_correctness.py --update-baseline
```

[docs/correctness.md](docs/correctness.md) explains the model and the report.

**Lint and types.** The lint ratchet allows the findings recorded in
`quality/lint_baseline.json` and fails on any new one; when you remove findings, lower
the baseline with `python quality/check_lint.py --update-baseline`. `mypy` checks the
modules listed in `pyproject.toml` strictly; move a module into that list once it is
clean. [docs/linting.md](docs/linting.md) has the details.

**Coherency score** (optional). `python quality/check_coherency.py path/to/file.py --verbose`
compiles a source file, decompiles it and scores how close the result is. It measures
resemblance, not correctness, and does not gate anything.

**Against the built package.** The release workflow runs the suite against the installed
wheel and sdist. To do the same locally, build both and test each in its own environment:

```bash
python -m pip install build && python -m build
for kind in whl tar.gz; do
  rm -rf /tmp/pkg && python -m venv /tmp/pkg
  /tmp/pkg/bin/pip install "$(ls dist/*.$kind)[test]"
  (cd /tmp && PYCREFINE_REQUIRE_INSTALLED=1 /tmp/pkg/bin/pytest -p no:cacheprovider --import-mode=importlib "$OLDPWD/tests") || break
done
```

## Writing tests

- **Test behaviour, not only text.** Decompiled code is right when it does the same thing
  as the original, so most tests compile a snippet, decompile it, and then `exec` both
  versions and compare what they print or return. Where the output should be
  reconstructed exactly, compare `ast.dump(ast.parse(...))` of the two rather than
  strings, so parentheses and formatting do not matter. `tests/test_helpers.py` has
  `decompile(src)` and `assert_contains`.
- **Every fix gets a regression test** with the reproduction from its issue.
- **Mark version limits explicitly**, with the reason:
  `@unittest.skipIf(sys.version_info[:2] in ((3, 10), (3, 11)), "...")`. Do not skip a
  version that CI tests (3.9, 3.11, 3.12, 3.13, 3.14) to make a test pass.
- **Keep fixtures out of the repository.** Tests compile their input on the fly. The only
  committed `.pyc` files are the foreign-version fixtures in `tests/fixtures/foreign_pyc/`,
  rebuilt with `tests/fixtures/regenerate_foreign_pyc.py` (which records their source
  hashes; `--check` verifies them).

## Every pull request

- **Bump the version.** Each pull request merged to `main` changes `__version__` in
  [`src/pycrefine/__init__.py`](src/pycrefine/__init__.py) exactly once (see
  [Versioning](#versioning)).
- **Update the docs** (README, `docs/`, this file) in the same pull request as the code
  they describe. A fixed limitation comes off [docs/limitations.md](docs/limitations.md).
- **Describe the change in the pull request.** There is no changelog file; the pull
  request description is the record of what changed and why, including what you tested
  on which Python versions. Start any change that users can notice (different output,
  newly rejected input, removed or renamed options) with **Behaviour change:**, so it can
  be found when release notes are written.
- **Close the issues it finishes.** Put `Closes #N` in the pull request description, one
  per line, for each issue the pull request completes, so GitHub links them and closes
  them when it merges. Naming the issue (`#N`) in the title, or anywhere without a closing
  keyword, does not close it. A closing keyword in a commit message does close it on
  merge, but does not link the pull request, so use the description. Use `Part of #N` or
  `Refs #N` for an issue the pull request only advances.
- **Gaps you find but don't fix** are tracked in an issue and linked from the pull
  request. See [Reporting gaps](#reporting-gaps).

**Stacked pull requests.** A series of dependent changes can be opened as a stack: each
branch starts from the previous one, and each pull request targets the previous branch
instead of `main`. Each still bumps the version once, one step above its base. When the
bottom pull request merges, GitHub retargets the next one to `main`; rebase the rest of
the stack onto `main` (`git rebase --onto origin/main <old base> <branch>`, one branch at
a time, in order) and push them with `--force-with-lease`. CI runs on a pull request once
it targets `main`.

## Reporting gaps

During development you will notice bugs, edge cases, missing features and documentation
gaps outside the change you are making. Record each one in the issue tracker instead of
leaving it in a code comment or a pull request thread, and don't create duplicates:

1. **Search existing issues first.** Search open *and* closed issues for the construct
   (`match`, `with`, `finally`, …), the opcode, the Python version and the error message,
   and for the issue the work started from. Read the likely matches, not just their
   titles.
2. **If an issue already covers it, add to that issue.** Comment with what is new: a
   minimal reproduction (the source, the Python version that compiled it, and the
   `pycrefine` command), the actual and expected output, the pycrefine version or commit,
   and the pull request or issue where you found it.
3. **If an issue is related but the gap is substantially different**, for example another
   construct, another Python version, or a fix that would be reviewed separately, create
   a new issue and add it as a
   [sub-issue](https://docs.github.com/en/issues/tracking-your-work-with-issues/using-issues/adding-sub-issues)
   of the related one, or link it with "Follow-up from #N" when a sub-issue doesn't fit.
   If a closed issue matches but the problem is back, open a new issue that links to the
   closed one rather than commenting on it.
4. **If nothing matches, create a new issue.** Give it:
   - a title that names the construct and, when it is version-specific, the versions
     (`3.12/3.14: try/except/else merges the else body into the try`);
   - **Problem**: what is wrong or missing, and where you found it;
   - a minimal reproduction with actual and expected output, for a bug;
   - **Scope** or **Options**, when there is more than one reasonable fix;
   - **Acceptance criteria** that a pull request can be checked against.
5. **Link it from the pull request** with `Refs #N` (not `Closes #N`, which would close
   it on merge), so reviewers can see what was deferred.

## Versioning

The project follows [Semantic Versioning 2.0.0](https://semver.org/). The version moves
with `main`: every merged pull request gets its own version, and releases are cut from
`main` whenever it is stable enough (see [Releasing](#releasing)). A version is never
held back waiting for a release.

- The version is defined once, as `__version__` in `src/pycrefine/__init__.py`.
  `pyproject.toml` reads it and `pycrefine.__version__` exposes it. Don't repeat the
  number elsewhere.
- Bump it once per pull request, against the version on `main`, not once per commit.
  Follow-up commits in the same pull request fold into its existing bump. If `main`
  moves to the same version before you merge, bump again on top of it.
- CI enforces this. The *Version bumped* job (`.github/workflows/version.yml`) checks that
  the pull request's version is exactly one step above the version on `main`: patch + 1,
  minor + 1 with patch 0, or major + 1 with the rest 0. It reads the `main` version from
  the first parent of GitHub's test merge commit, so it needs no stored copy and no extra
  fetch. Whether the step is the right one is left to review.
- GitHub computes that merge commit when the check runs and does not re-run the check
  when `main` moves. To stop two pull requests from merging with the same version, turn
  on branch protection for `main` with *Require status checks to pass* (including
  *Version bumped*) and *Require branches to be up to date before merging*.
- Pick the part to bump by the largest change in the pull request:

  | Bump | When | Example |
  |------|------|---------|
  | Patch (`1.4.0` → `1.4.1`) | Bug fixes (including better decompiled output), docs, tests, internal refactors, tooling and CI | Fix a `with` block that lost its `return` |
  | Minor (`1.4.1` → `1.5.0`) | A new feature or capability: a new CLI option, a new public function or parameter, a newly supported Python version or construct, a new workflow such as releasing | Add `--version`; support `match` statements |
  | Major (`1.x` → `2.0.0`) | An incompatible change to the public interface | Rename or remove a CLI option, change what `get_decompiler` returns |

- Get the maintainer's agreement before a major bump.
- While the major version is `0`, a change that breaks compatibility is a minor bump, as
  SemVer allows, and must be announced as a **Behaviour change:** in the pull request.
  pycrefine is past `1.0`, so for it an incompatible change is always a major bump.
- The public interface is the `pycrefine` command (its options, output streams and exit
  statuses) and the documented Python API: `get_decompiler`, `decompile()`,
  `__version__` and `main` (see [Command line and Python API](docs/usage.md)). Better
  decompiled output for the same input is a fix, not a break. Everything else that
  `import pycrefine` exposes, and every module or name starting with `_`, is internal and
  can change in any version.
- Most versions are never published. PyPI only gets the versions that are released, so
  gaps between published versions are expected.

## Releasing

A release publishes whatever version `main` is at, through the `Release` workflow
(`.github/workflows/release.yml`) with PyPI trusted publishing (no API tokens are stored).

1. Pick a commit on `main` that is green and stable. Its `src/pycrefine/__init__.py` is
   the version released, so no version change is needed.
2. Optional dry run: manual runs of the workflow upload to TestPyPI only, and an index
   accepts each version once, so run it on a throwaway branch whose version is a
   pre-release (`1.4.1rc1`, then `rc2`, …), never on `main` itself.
3. Publish a GitHub release with tag `vX.Y.Z` matching the version, on that commit. Use
   *Generate release notes* (since the previous `v` tag) and put the
   **Behaviour change:** items from the merged pull requests at the top. The workflow
   checks and tests the distributions, uploads them to TestPyPI, installs them from
   there, and only then uploads to PyPI.

[docs/releasing.md](docs/releasing.md) has the details and the one-time setup.

The release notes on GitHub are the history of what each release contains. A version
maps to the commit that set it
(`git log --oneline -S'__version__ = "1.4.1"' -- src/pycrefine/__init__.py`), and every
commit on `main` links to its pull request.
