# Releasing

A release publishes whatever version `main` is at. Releases go to PyPI through the
`Release` workflow ([`.github/workflows/release.yml`](../.github/workflows/release.yml)),
which uses [PyPI trusted publishing](https://docs.pypi.org/trusted-publishers/), so no
API tokens are stored in the repository.

| Trigger | TestPyPI | PyPI |
|---|---|---|
| A GitHub release is published (tag `vX.Y.Z`) | yes | yes, after the TestPyPI upload and install check |
| *Run workflow* (manual, any branch) | yes | no |

Every run builds the sdist and wheel once and checks them before anything is uploaded:

1. `twine check --strict` on both files, and on a release, that the tag is `v` + the
   package version.
2. The wheel and the sdist are each installed into a clean virtual environment on
   Python 3.9 and 3.14. The `pycrefine` command decompiles a sample and the output must
   match the source's AST, and then the whole test suite runs against the installed
   package from outside the checkout (`PYCREFINE_REQUIRE_INSTALLED=1` makes
   `tests/conftest.py` fail the run if `pycrefine` would come from the source tree).
3. The same files are uploaded to TestPyPI, installed from there, and only then, for a
   release, uploaded to PyPI in the `pypi` environment.

## One-time setup

1. **PyPI** (*Your account → Publishing*, https://pypi.org/manage/account/publishing/):
   pycrefine is not on PyPI yet, so add a *pending* trusted publisher:
   - PyPI project name: `pycrefine`
   - Owner: `sahebbiswas`, repository: `pycrefine`
   - Workflow name: `release.yml`
   - Environment name: `pypi`
2. **TestPyPI** (https://test.pypi.org/manage/account/publishing/): add the same pending
   publisher, but leave **Environment name empty**. The TestPyPI job runs without an
   environment, and a publisher that names one would not match it.
3. **GitHub** (*Settings → Environments*): GitHub creates the `pypi` environment on its
   first use; create it beforehand to add required reviewers (every PyPI upload then
   waits for an approval) or to restrict it to `v*` tags.

After the first upload the pending publishers become ordinary trusted publishers of the
project; nothing else changes.

## Cutting a release

1. **Pick a commit on `main`** that is green and stable. Its `__version__` in
   `src/pycrefine/__init__.py` is the version released; every pull request already
   bumped it (see [Versioning](../CONTRIBUTING.md#versioning)), so nothing needs changing.
2. **Optional dry run.** Manual runs upload to TestPyPI only, but an index accepts each
   version only once, and a release fails if its version is already on TestPyPI. So
   never run it on `main` itself. Push a temporary branch from the chosen commit that
   changes `__version__` to a pre-release of that version (`1.1.0` → `1.1.0rc1`, then
   `rc2`, …), run the workflow on that branch (*Actions → Release → Run workflow*), check
   `pip install -i https://test.pypi.org/simple/ pycrefine==1.1.0rc1`, and delete the
   branch without merging it. Use a new pre-release for every run: the workflow never
   skips a version that is already on TestPyPI, so a repeated one fails instead of
   quietly testing old files.
3. **Publish a GitHub release** with a new tag `vX.Y.Z` matching the version, on that
   commit. Use *Generate release notes* (since the previous `v` tag) and put the
   **Behaviour change:** items from the merged pull requests at the top. The workflow
   does the rest, waiting for the `pypi` environment's approval if one is configured.

PyPI never accepts the same version twice, so a failed or wrong upload is fixed by
bumping the version on `main` and releasing again, not by re-running with the same files.

The release notes on GitHub are the history of what each release contains. A version
maps to the commit that set it
(`git log --oneline -S'__version__ = "1.1.0"' -- src/pycrefine/__init__.py`), and every
commit on `main` links to its pull request.
