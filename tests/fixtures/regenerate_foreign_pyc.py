#!/usr/bin/env python3
"""Regenerate the committed foreign-version .pyc fixtures and their manifest.

These files exist so tests and the correctness gate can load bytecode written by
a *different* CPython than the one running the suite (cross-version loading,
see #89 and #94). Everything else in the test suite compiles on the fly with
``py_compile``; do not add .pyc files here unless a foreign-version binary is
the thing under test.

Usage (run from anywhere):

    python tests/fixtures/regenerate_foreign_pyc.py 3.9=/usr/bin/python3.9 3.14=/usr/bin/python3.14
    python tests/fixtures/regenerate_foreign_pyc.py --check          # verify manifest only
    python tests/fixtures/regenerate_foreign_pyc.py --out /tmp/x ... # write elsewhere

Each ``VERSION=INTERPRETER`` pair compiles the sources listed in
``SOURCES`` with that interpreter (hash-based pyc, so the output is
reproducible) and records the interpreter's exact patch version in
``manifest.json``. Versions not named on the command line keep their existing
file and manifest entry.
"""
import argparse
import hashlib
import json
import struct
import subprocess
import sys
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent
HERE = FIXTURES / "foreign_pyc"
ROOT = FIXTURES.parent.parent
MANIFEST = HERE / "manifest.json"

# source (relative to the repository root) -> fixture stem
SOURCES = {
    "corpus/comprehensive.py": "comprehensive",
    "corpus/simple.py": "simple",
}

_COMPILE = (
    "import py_compile, sys; "
    "py_compile.compile(sys.argv[1], cfile=sys.argv[2], doraise=True, "
    "invalidation_mode=py_compile.PycInvalidationMode.CHECKED_HASH)"
)
_PATCH = "import sys; print('.'.join(map(str, sys.version_info[:3])))"


def describe(path):
    """Return (sha256, magic_number) for a .pyc file."""
    data = path.read_bytes()
    return hashlib.sha256(data).hexdigest(), struct.unpack("<H", data[:2])[0]


def load_manifest():
    if MANIFEST.exists():
        return json.loads(MANIFEST.read_text())
    return {"schema_version": 1, "files": {}}


def write_manifest(manifest):
    manifest["files"] = dict(sorted(manifest["files"].items()))
    MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n")


def regenerate(pairs, out_dir):
    manifest = load_manifest()
    for spec in pairs:
        version, _, interpreter = spec.partition("=")
        if not interpreter:
            sys.exit(f"expected VERSION=INTERPRETER, got {spec!r}")
        patch = subprocess.run([interpreter, "-c", _PATCH], capture_output=True,
                               text=True, check=True).stdout.strip()
        if not patch.startswith(version + "."):
            sys.exit(f"{interpreter} is Python {patch}, not {version}")
        for source, stem in SOURCES.items():
            name = f"{stem}.cpython-{version.replace('.', '')}.pyc"
            target = out_dir / name
            subprocess.run([interpreter, "-c", _COMPILE, source, str(target)],
                           cwd=ROOT, check=True)
            sha, magic = describe(target)
            if out_dir == HERE:
                manifest["files"][name] = {"source": source, "python": version,
                                           "cpython": patch, "magic": magic, "sha256": sha}
            print(f"{name}: CPython {patch}, magic {magic}")
    if out_dir == HERE:
        write_manifest(manifest)


def check():
    manifest = load_manifest()
    problems = []
    on_disk = {p.name for p in HERE.glob("*.pyc")}
    for name in sorted(on_disk - set(manifest["files"])):
        problems.append(f"{name}: not in manifest")
    for name, entry in manifest["files"].items():
        path = HERE / name
        if not path.exists():
            problems.append(f"{name}: listed but missing")
            continue
        sha, magic = describe(path)
        if sha != entry["sha256"]:
            problems.append(f"{name}: sha256 differs from manifest")
        if magic != entry["magic"]:
            problems.append(f"{name}: magic {magic} != manifest {entry['magic']}")
        if not (ROOT / entry["source"]).exists():
            problems.append(f"{name}: source {entry['source']} missing")
    for problem in problems:
        print(problem)
    return 1 if problems else 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("pairs", nargs="*", metavar="VERSION=INTERPRETER")
    parser.add_argument("--check", action="store_true", help="verify the manifest and exit")
    parser.add_argument("--out", type=Path, default=HERE,
                        help="write .pyc files here instead (manifest is left alone)")
    args = parser.parse_args(argv)
    if args.check:
        return check()
    if not args.pairs:
        parser.error("name at least one VERSION=INTERPRETER pair, or use --check")
    args.out.mkdir(parents=True, exist_ok=True)
    regenerate(args.pairs, args.out.resolve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
