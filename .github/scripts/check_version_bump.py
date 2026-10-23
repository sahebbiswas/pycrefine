"""Check that a pull request bumps the version by one SemVer step.

Usage: check_version_bump.py OLD_VERSION_FILE NEW_VERSION_FILE

Both files are copies of ``src/pycrefine/__init__.py``: the one on the base branch and
the one in the pull request. CI takes the old one from the first parent of
GitHub's test merge commit, so nothing is stored or fetched for this check. See CONTRIBUTING.md, "Versioning".

The new version must be exactly one step above the old one: patch + 1, or
minor + 1 with patch 0, or major + 1 with minor and patch 0. Whether the step
is the right one (patch or minor) is left to review.
"""

import ast
import re
import sys

_SEMVER = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)")


def read_version(path):
    """The ``__version__`` string assigned in the Python file at *path*.

    The file must assign ``__version__`` exactly once, at top level, to a
    string literal, so the value checked is the one the package exposes.
    """
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    assignments = [node for node in tree.body if _assigns_version(node)]
    if any(isinstance(node, ast.AugAssign) for node in assignments):
        raise SystemExit(
            f"error: {path} must assign __version__ with '=', "
            f"not an augmented assignment such as '+='"
        )
    if len(assignments) != 1:
        raise SystemExit(
            f"error: {path} must assign __version__ exactly once at top "
            f"level, found {len(assignments)}"
        )
    value = assignments[0].value
    if not (isinstance(value, ast.Constant) and isinstance(value.value, str)):
        raise SystemExit(f"error: __version__ in {path} must be a string literal")
    return value.value


def _assigns_version(node):
    if isinstance(node, ast.Assign):
        targets = node.targets
    elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
        targets = [node.target]
    else:
        return False
    return any(isinstance(t, ast.Name) and t.id == "__version__" for t in targets)


def parse(version, where):
    match = _SEMVER.fullmatch(version)
    if match is None:
        raise SystemExit(f"error: {where} version {version!r} is not MAJOR.MINOR.PATCH")
    return tuple(int(part) for part in match.groups())


def next_versions(old):
    major, minor, patch = old
    return {
        (major, minor, patch + 1): "patch",
        (major, minor + 1, 0): "minor",
        (major + 1, 0, 0): "major",
    }


def main(argv):
    if len(argv) != 3:
        raise SystemExit(__doc__.split("\n\n")[1])
    old_text, new_text = read_version(argv[1]), read_version(argv[2])
    old, new = parse(old_text, "base"), parse(new_text, "pull request")
    steps = next_versions(old)
    if new in steps:
        print(f"ok: {old_text} -> {new_text} ({steps[new]} bump)")
        return 0
    allowed = ", ".join(f"{'.'.join(map(str, v))} ({kind})" for v, kind in steps.items())
    if new == old:
        problem = "is not bumped"
    else:
        problem = f"goes from {old_text} to {new_text}, which is not one step"
    print(
        f"error: the version in src/pycrefine/__init__.py {problem}.\n"
        f"Every pull request bumps it once; from {old_text} use one of: {allowed}.\n"
        f"If main has moved, bump again on top of the version on main. "
        f'See CONTRIBUTING.md, "Versioning".',
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
