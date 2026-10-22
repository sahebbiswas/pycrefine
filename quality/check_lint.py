#!/usr/bin/env python3
"""
pycrefine lint ratchet.

Runs pylint with the repository's ``.pylintrc`` and compares the findings with the
committed baseline ``quality/lint_baseline.json``. Existing debt is recorded there and
does not fail the run, but nothing new may be added: the job fails when any finding
appears that the baseline does not already allow.

Findings are identified by ``path::symbol::enclosing-object`` and counted, never by
line number, so unrelated edits that shift lines do not disturb the baseline.
(``cyclic-import`` and ``duplicate-code`` are identified by their message alone: they
concern several modules, and pylint reports them on an arbitrary one.)

Usage
-----
    # Fail on findings that are not in the baseline (what CI runs):
    python quality/check_lint.py

    # Show every current finding, including baselined ones:
    python quality/check_lint.py -v

    # After paying down debt, record the lower counts (refuses to raise any count):
    python quality/check_lint.py --update-baseline

    # Deliberately accept more findings, for example after a refactor moved code
    # and renamed its enclosing objects (explain why in the pull request):
    python quality/check_lint.py --update-baseline --allow-increase

Exit status: 0 no new findings, 1 new findings, 2 pylint could not be run.
Details: docs/linting.md. Run it with the interpreter and pylint version pinned in
the ``dev`` extra of pyproject.toml (CI uses Python 3.12).
"""
import argparse
import json
import os
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Optional

_QUALITY = Path(__file__).resolve().parent
_ROOT = _QUALITY.parent

SCHEMA_VERSION = 1
_PYLINT_TIMEOUT = 900  # seconds; a hung child must not stall the job
DEFAULT_BASELINE = _QUALITY / "lint_baseline.json"
DEFAULT_TARGETS = ["src/pycrefine"]

# The ``[start:end]`` line ranges pylint puts in duplicate-code messages move with
# unrelated edits; they are dropped so the module names and duplicated text identify it.
_POSITIONS = re.compile(r":\[\d+:\d+\]")

# Messages whose enclosing object says nothing useful; the text identifies them instead.
_MESSAGE_KEYED = frozenset({"cyclic-import", "duplicate-code"})


def finding_key(message: dict) -> str:
    """Stable identity of a pylint message, independent of line numbers."""
    path = str(message.get("path", "")).replace("\\", "/")
    symbol = message.get("symbol") or message.get("message-id", "?")
    obj = message.get("obj") or "<module>"
    if symbol in _MESSAGE_KEYED:
        # pylint attaches these project-wide findings to whichever module it visits
        # first, which varies between runs and machines; the message identifies them.
        return f"<project>::{symbol}::{_POSITIONS.sub('', str(message.get('message', '')))}"
    return f"{path}::{symbol}::{obj}"


def count_findings(messages: List[dict]) -> Dict[str, int]:
    return dict(sorted(Counter(finding_key(m) for m in messages).items()))


def compare(current: Dict[str, int], baseline: Dict[str, int]) -> dict:
    """Split the differences into increases (fail) and decreases (debt paid)."""
    increased = {k: (baseline.get(k, 0), n) for k, n in current.items() if n > baseline.get(k, 0)}
    decreased = {k: (n, current.get(k, 0)) for k, n in baseline.items() if n > current.get(k, 0)}
    return {"increased": increased, "decreased": decreased}


def run_pylint(targets: List[str]) -> List[dict]:
    cmd = [sys.executable, "-m", "pylint", *targets, "--rcfile", str(_ROOT / ".pylintrc"),
           "--output-format=json", "--exit-zero"]
    try:
        proc = subprocess.run(cmd, cwd=_ROOT, capture_output=True, text=True, timeout=_PYLINT_TIMEOUT)
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"pylint did not finish within {_PYLINT_TIMEOUT} seconds\n")
        raise SystemExit(2) from None
    try:
        messages = json.loads(proc.stdout or "[]")
    except json.JSONDecodeError:
        messages = None
    if proc.returncode != 0 or not isinstance(messages, list):
        sys.stderr.write(f"could not run pylint ({' '.join(cmd[:3])}):\n{proc.stderr or proc.stdout}\n")
        raise SystemExit(2)
    # Fatal/parse failures are reported as messages too; never let them be baselined.
    fatal = [m for m in messages if m.get("type") == "fatal" or m.get("symbol") == "syntax-error"]
    if fatal:
        for m in fatal:
            sys.stderr.write(f"{m['path']}:{m['line']}: {m['symbol']}: {_one_line(m['message'])}\n")
        raise SystemExit(2)
    return messages


def pylint_version() -> str:
    try:
        proc = subprocess.run([sys.executable, "-m", "pylint", "--version"], cwd=_ROOT,
                              capture_output=True, text=True, timeout=_PYLINT_TIMEOUT)
    except subprocess.TimeoutExpired:
        return "unknown"
    first = (proc.stdout or "").splitlines()
    return first[0].replace("pylint", "").strip() if first else "unknown"


def load_baseline(path: Path) -> Optional[dict]:
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_baseline(path: Path, findings: Dict[str, int], pylint: str) -> None:
    data = {
        "schema_version": SCHEMA_VERSION,
        "pylint": pylint,
        "python": f"{sys.version_info[0]}.{sys.version_info[1]}",
        "findings": findings,
    }
    path.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")


def _one_line(text: object) -> str:
    """Finding text on a single line, so source quoted in a message cannot start a log line."""
    return " | ".join(str(text).splitlines())


def _escape_command(text: object, *, prop: bool = False) -> str:
    """Escape *text* for a GitHub Actions workflow command (message, or property when *prop*)."""
    out = str(text).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
    return out.replace(":", "%3A").replace(",", "%2C") if prop else out


def _describe(messages: List[dict], keys: Dict[str, tuple], verbose: bool) -> List[str]:
    by_key = defaultdict(list)
    for m in messages:
        by_key[finding_key(m)].append(m)
    lines = []
    for key, (old, new) in sorted(keys.items()):
        lines.append(f"  {key}  ({old} -> {new})")
        shown = by_key[key] if verbose else by_key[key][-(new - old):]
        for m in shown:
            lines.append(f"      line {m['line']}: {_one_line(m['message'])}")
    return lines


def _annotate(messages: List[dict], keys: Dict[str, tuple]) -> None:
    """On GitHub Actions, also report the new findings as annotations on the files."""
    if os.environ.get("GITHUB_ACTIONS") != "true":
        return
    by_key = defaultdict(list)
    for m in messages:
        by_key[finding_key(m)].append(m)
    for key, (old, new) in keys.items():
        # Only the occurrences beyond what the baseline allows are new, as in _describe.
        for m in by_key[key][-(new - old):]:
            where = f"file={_escape_command(m['path'], prop=True)},line={int(m['line'])}"
            print(f"::error {where}::{_escape_command(str(m['symbol']) + ': ' + str(m['message']))}")


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description="pycrefine lint ratchet", epilog=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("targets", nargs="*", default=DEFAULT_TARGETS,
                        help=f"Paths to lint (default: {' '.join(DEFAULT_TARGETS)})")
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE,
                        help="Baseline file (default: quality/lint_baseline.json)")
    parser.add_argument("--update-baseline", action="store_true",
                        help="Write the current findings as the baseline (never raises a count)")
    parser.add_argument("--allow-increase", action="store_true",
                        help="With --update-baseline: also accept new or larger counts")
    parser.add_argument("--verbose", "-v", action="store_true", help="List all current findings")
    args = parser.parse_args(argv)

    if args.allow_increase and not args.update_baseline:
        parser.error("--allow-increase only makes sense with --update-baseline")
    if args.update_baseline and sorted(args.targets) != sorted(DEFAULT_TARGETS):
        # A scoped run sees only part of the findings and would drop the rest of the baseline.
        parser.error(f"--update-baseline needs the full scope ({' '.join(DEFAULT_TARGETS)}); "
                     "do not pass targets")

    messages = run_pylint(args.targets)
    current = count_findings(messages)
    version = pylint_version()
    baseline_doc = load_baseline(args.baseline)

    if baseline_doc is None and not args.update_baseline:
        sys.stderr.write(f"no baseline at {args.baseline}; create it with --update-baseline\n")
        return 2
    baseline = (baseline_doc or {}).get("findings", {})

    if baseline_doc is not None:
        if baseline_doc.get("pylint") != version:
            print(f"note: baseline was recorded with pylint {baseline_doc.get('pylint')}, "
                  f"this is {version}; install the version pinned in pyproject.toml")
        running = f"{sys.version_info[0]}.{sys.version_info[1]}"
        if baseline_doc.get("python") != running:
            print(f"note: baseline was recorded on Python {baseline_doc.get('python')}, "
                  f"this is {running}; results may differ slightly")

    diff = compare(current, baseline)

    if args.update_baseline:
        if baseline_doc is not None and diff["increased"] and not args.allow_increase:
            print("refusing to raise the baseline; these findings are new or more frequent:")
            print("\n".join(_describe(messages, diff["increased"], False)))
            print("fix them, or pass --allow-increase and justify it in the pull request")
            return 1
        write_baseline(args.baseline, current, version)
        print(f"baseline written: {sum(current.values())} findings in {len(current)} groups "
              f"({len(diff['decreased'])} lowered, {len(diff['increased'])} raised)")
        return 0

    total = sum(current.values())
    print(f"pylint {version}: {total} findings, {sum(baseline.values())} allowed by the baseline")
    if args.verbose:
        for m in messages:
            print(f"  {m['path']}:{m['line']}: {m['symbol']}: {_one_line(m['message'])}")

    if diff["decreased"]:
        paid = sum(o - n for o, n in diff["decreased"].values())
        print(f"\n{paid} baselined finding(s) are gone; lower the baseline with:\n"
              f"  python quality/check_lint.py --update-baseline")
    if diff["increased"]:
        extra = sum(n - o for o, n in diff["increased"].values())
        print(f"\nFAIL: {extra} finding(s) not allowed by the baseline:")
        print("\n".join(_describe(messages, diff["increased"], False)))
        _annotate(messages, diff["increased"])
        print("\nFix them (preferred). If the code merely moved and its findings were already "
              "in the baseline, see docs/linting.md.")
        return 1
    print("RESULT: PASS")
    return 0


if __name__ == "__main__":
    sys.exit(main())
