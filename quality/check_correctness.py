#!/usr/bin/env python3
"""
check_correctness.py
====================
Correctness-first quality gate for pycrefine.

Every corpus entry is decompiled and the output is placed on an ordered
correctness ladder (see docs/correctness.md for the full model):

    decompile_error  <  syntax_error  <  compile_error  <  compiles

Behavioral equivalence is the next rung up; it is defined by the model
but not evaluated here yet (see issue #77).

Source entries (.py) are additionally split into *units* -- each top-level
statement, and each member of a top-level class -- which are compiled,
decompiled and checked on their own. Units give the gate a fine enough
grain to catch regressions inside files that do not yet compile as a whole.

The run is compared against a committed per-Python-version baseline
(quality/baselines/py<major>.<minor>.json). Any file or unit that drops
to a lower rung than its baseline is a regression and fails the run; so
does any change that stops a baseline record from being compared (see
FAILING_CHANGES).

The coherency checker (debug/check_coherency.py) is a readability
diagnostic only: --coherency adds its score to the report, but it never
affects pass/fail.

Usage
-----
    # Gate the default corpus (test_files/) against the baseline:
    python quality/check_correctness.py

    # Machine-readable report on stdout:
    python quality/check_correctness.py --json

    # Write the JSON report to a file, keep the text summary on stdout:
    python quality/check_correctness.py --report correctness.json

    # Measure arbitrary files without gating:
    python quality/check_correctness.py pycrefine.py --no-baseline

    # Accept the current results as the new baseline:
    python quality/check_correctness.py --update-baseline

Exit codes
----------
    0  No failing changes against the baseline
    1  At least one failing change (regressed, missing, lost, ambiguous)
    2  Configuration error (no baseline, wrong-version baseline, bad path)
"""

from __future__ import annotations

import argparse
import ast
import contextlib
import io
import json
import os
import py_compile
import re
import signal
import sys
import tempfile
import tokenize
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

_QUALITY = Path(__file__).resolve().parent
_ROOT = _QUALITY.parent
sys.path.insert(0, str(_ROOT))

from pycrefine import get_decompiler  # noqa: E402

SCHEMA_VERSION = 1

DEFAULT_CORPUS = [_ROOT / 'test_files']
BASELINE_DIR = _QUALITY / 'baselines'

# The correctness ladder, lowest to highest. ``source_error`` (the corpus
# source itself does not compile on this interpreter) is deliberately not on
# the ladder: it says nothing about the decompiler and is never gated.
LEVELS = ['decompile_error', 'syntax_error', 'compile_error', 'compiles']
SOURCE_ERROR = 'source_error'

DEFAULT_TIMEOUT = 60

# Besides ``regressed``, every change that stops a baseline record from being
# compared fails the gate, so it must be acknowledged with --update-baseline
# instead of hiding a regression:
#   missing    the record no longer matches any entry or unit
#   lost       a scored record now has a source_error and can't be scored
#   ambiguous  the number of units sharing a label changed, so the ``#n``
#              suffixes may now point at different statements
FAILING_CHANGES = ('regressed', 'missing', 'lost', 'ambiguous')


def level_of(status: str) -> Optional[int]:
    return LEVELS.index(status) if status in LEVELS else None


# ---------------------------------------------------------------------------
# Checking a single .pyc
# ---------------------------------------------------------------------------

@dataclass
class Check:
    status: str
    error: Optional[dict] = None
    output: str = ''


class _Timeout(Exception):
    pass


@contextlib.contextmanager
def _time_limit(seconds: int):
    """Abort the body after *seconds* (POSIX only; a no-op elsewhere)."""
    if not seconds or not hasattr(signal, 'SIGALRM'):
        yield
        return

    def _raise(signum, frame):
        raise _Timeout(f'decompilation exceeded {seconds}s')

    previous = signal.signal(signal.SIGALRM, _raise)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous)


def _error(stage: str, exc: BaseException) -> dict:
    err = {'stage': stage, 'type': type(exc).__name__, 'message': str(exc).strip()}
    if isinstance(exc, SyntaxError):
        err['message'] = exc.msg
        err['lineno'] = exc.lineno
    if len(err['message']) > 300:
        err['message'] = err['message'][:300] + '...'
    return err


def classify(text: str, filename: str = '<decompiled>') -> Check:
    """Place already-decompiled *text* on the syntax/compile part of the ladder."""
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        try:
            ast.parse(text, filename)
        except (SyntaxError, ValueError) as exc:
            return Check('syntax_error', _error('syntax', exc), text)
        try:
            compile(text, filename, 'exec', dont_inherit=True)
        except (SyntaxError, ValueError) as exc:
            return Check('compile_error', _error('compile', exc), text)
    return Check('compiles', None, text)


def check_pyc(pyc_path: str, timeout: int = DEFAULT_TIMEOUT) -> Check:
    """Decompile *pyc_path* and classify the output."""
    sink = io.StringIO()
    try:
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink), \
                _time_limit(timeout):
            text = get_decompiler(pyc_path).decompile()
    except Exception as exc:          # includes RecursionError and _Timeout
        return Check('decompile_error', _error('decompile', exc))
    if not isinstance(text, str):
        return Check('decompile_error', {
            'stage': 'decompile', 'type': 'TypeError',
            'message': f'decompile() returned {type(text).__name__}'})
    return classify(text)


def check_source_text(source: str, workdir: str, name: str,
                      timeout: int = DEFAULT_TIMEOUT, encoding: str = 'utf-8') -> Check:
    """Compile *source* on this interpreter, then decompile and classify it."""
    py_path = os.path.join(workdir, name + '.py')
    pyc_path = os.path.join(workdir, name + '.pyc')
    # *encoding* must match any coding cookie still present in *source*.
    with open(py_path, 'w', encoding=encoding) as f:
        f.write(source)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            py_compile.compile(py_path, cfile=pyc_path, doraise=True)
    except py_compile.PyCompileError as exc:
        return Check(SOURCE_ERROR, {'stage': 'source', 'type': exc.exc_type_name,
                                    'message': exc.msg.strip()[:300]})
    try:
        return check_pyc(pyc_path, timeout)
    finally:
        for p in (py_path, pyc_path):
            if os.path.exists(p):
                os.unlink(p)


# ---------------------------------------------------------------------------
# Units
# ---------------------------------------------------------------------------

@dataclass
class Unit:
    id: str
    source: str


def _segment(lines: List[str], node: ast.stmt) -> str:
    start = min([node.lineno] + [d.lineno for d in getattr(node, 'decorator_list', [])])
    text = ''.join(lines[start - 1:node.end_lineno])
    return text if text.endswith('\n') else text + '\n'


def _label(lines: List[str], node: ast.stmt) -> str:
    # Ids must not depend on line numbers, or moving a statement would turn
    # it into a missing + new pair instead of comparing its status.
    if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
        return f'def {node.name}'
    if isinstance(node, ast.ClassDef):
        return f'class {node.name}'
    first = lines[node.lineno - 1][node.col_offset:].strip()
    if len(first) > 60:
        first = first[:57] + '...'
    return f'{type(node).__name__}: {first}'


def extract_units(source: str) -> List[Unit]:
    """
    Split *source* into independently compilable units.

    A unit is a top-level statement, or -- for a top-level class -- one
    member of the class body wrapped in a bare ``class Name:`` header, so
    that large classes are not a single all-or-nothing unit. Module-level
    ``from __future__`` imports are prepended to every unit because they
    change how the unit compiles.
    """
    tree = ast.parse(source)
    lines = source.splitlines(keepends=True)

    def is_future(n):
        return isinstance(n, ast.ImportFrom) and n.module == '__future__'

    prefix = ''.join(_segment(lines, n) for n in tree.body if is_future(n))
    units: List[Unit] = []
    seen: Dict[str, int] = {}

    def add(label: str, text: str):
        seen[label] = seen.get(label, 0) + 1
        uid = label if seen[label] == 1 else f'{label}#{seen[label]}'
        units.append(Unit(uid, prefix + text))

    for node in tree.body:
        if is_future(node):
            continue
        if isinstance(node, ast.ClassDef) and node.lineno != node.body[0].lineno:
            header = f'class {node.name}:\n'
            for child in node.body:
                add(f'class {node.name}/{_label(lines, child)}', header + _segment(lines, child))
        else:
            add(_label(lines, node), _segment(lines, node))
    return units


def _ast_equal(a: str, b: str) -> bool:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            return ast.dump(ast.parse(a)) == ast.dump(ast.parse(b))
    except (SyntaxError, ValueError):
        return False


# ---------------------------------------------------------------------------
# Corpus entries
# ---------------------------------------------------------------------------

@dataclass
class EntryResult:
    path: str
    kind: str                       # 'source' | 'pyc'
    status: str
    error: Optional[dict] = None
    units: Dict[str, dict] = field(default_factory=dict)
    coherency: Optional[dict] = None

    def unit_counts(self) -> Dict[str, int]:
        counts: Dict[str, int] = {}
        for u in self.units.values():
            counts[u['status']] = counts.get(u['status'], 0) + 1
        return counts

    def to_dict(self) -> dict:
        d = {'path': self.path, 'kind': self.kind, 'status': self.status,
             'level': level_of(self.status), 'error': self.error,
             'behavior': 'not_evaluated'}
        if self.kind == 'source':
            d['unit_summary'] = {
                'total': len(self.units),
                'by_status': self.unit_counts(),
                'ast_equal': sum(1 for u in self.units.values() if u.get('ast_equal')),
            }
            d['units'] = self.units
        if self.coherency is not None:
            d['coherency'] = self.coherency
        return d


def _rel(path: Path) -> str:
    try:
        return path.resolve().relative_to(_ROOT).as_posix()
    except ValueError:
        return path.resolve().as_posix()


def collect_corpus(paths: List[Path]) -> List[Path]:
    files: List[Path] = []
    for p in paths:
        if p.is_dir():
            files.extend(sorted(c for c in p.iterdir()
                                if c.is_file() and c.suffix in ('.py', '.pyc')))
        elif p.is_file():
            files.append(p)
        else:
            raise FileNotFoundError(str(p))
    return files


def _coherency(path: Path) -> dict:
    debug_dir = str(_ROOT / 'debug')
    if debug_dir not in sys.path:
        sys.path.insert(0, debug_dir)
    try:
        import check_coherency
        sink = io.StringIO()
        with contextlib.redirect_stdout(sink), contextlib.redirect_stderr(sink), \
                warnings.catch_warnings():
            warnings.simplefilter('ignore')
            report = check_coherency.score(str(path))
        return {'composite_score': round(report.composite_score * 100, 1),
                'grade': report.grade}
    except Exception as exc:
        return {'error': _error('coherency', exc)}


def check_entry(path: Path, workdir: str, timeout: int = DEFAULT_TIMEOUT,
                with_coherency: bool = False) -> EntryResult:
    rel = _rel(path)
    if path.suffix == '.pyc':
        c = check_pyc(str(path), timeout)
        return EntryResult(rel, 'pyc', c.status, c.error)

    try:
        with tokenize.open(str(path)) as f:     # honours PEP 263 coding cookies
            source, encoding = f.read(), f.encoding
    except (SyntaxError, UnicodeDecodeError, LookupError) as exc:
        return EntryResult(rel, 'source', SOURCE_ERROR, _error('source', exc))
    whole = check_source_text(source, workdir, 'entry', timeout, encoding)
    result = EntryResult(rel, 'source', whole.status, whole.error)
    if whole.status == SOURCE_ERROR:
        return result

    for i, unit in enumerate(extract_units(source)):
        c = check_source_text(unit.source, workdir, f'unit{i}', timeout)
        u = {'status': c.status}
        if c.error:
            u['error'] = c.error
        if c.status == 'compiles':
            u['ast_equal'] = _ast_equal(unit.source, c.output)
        result.units[unit.id] = u

    if with_coherency:
        result.coherency = _coherency(path)
    return result


# ---------------------------------------------------------------------------
# Baseline comparison
# ---------------------------------------------------------------------------

def python_tag() -> str:
    return f'{sys.version_info[0]}.{sys.version_info[1]}'


def default_baseline_path() -> Path:
    return BASELINE_DIR / f'py{python_tag()}.json'


def to_baseline(results: List[EntryResult]) -> dict:
    entries = {}
    for r in results:
        e = {'kind': r.kind, 'status': r.status}
        if r.kind == 'source' and r.status != SOURCE_ERROR:
            e['units'] = {uid: u['status'] for uid, u in sorted(r.units.items())}
        entries[r.path] = e
    return {'schema_version': SCHEMA_VERSION, 'python_version': python_tag(),
            'entries': entries}


def _label_counts(unit_ids) -> Dict[str, int]:
    """Count unit ids per label, folding the ``#n`` duplicate suffixes."""
    counts: Dict[str, int] = {}
    for uid in unit_ids:
        label = re.sub(r'#\d+$', '', uid)
        counts[label] = counts.get(label, 0) + 1
    return counts


def _change(before: str, after: str) -> Optional[str]:
    if before == after:
        return None
    lb, la = level_of(before), level_of(after)
    if lb is not None and la is None:
        return 'lost'
    if lb is None or la is None:
        return 'unscorable'
    return 'regressed' if la < lb else 'improved'


def compare(results: List[EntryResult], baseline: dict) -> List[dict]:
    """
    Diff *results* against *baseline*. Each change is a dict with ``path``,
    ``unit`` (None for the whole entry), ``change`` (regressed | improved |
    lost | unscorable | ambiguous | new | missing), ``before`` and ``after``.
    Changes listed in FAILING_CHANGES fail the gate.
    """
    changes: List[dict] = []
    base_entries = baseline.get('entries', {})

    def add(path, unit, change, before, after):
        changes.append({'path': path, 'unit': unit, 'change': change,
                        'before': before, 'after': after})

    for r in results:
        b = base_entries.get(r.path)
        if b is None:
            add(r.path, None, 'new', None, r.status)
            continue
        ch = _change(b['status'], r.status)
        if ch:
            add(r.path, None, ch, b['status'], r.status)
        b_units = b.get('units', {})
        b_counts, r_counts = _label_counts(b_units), _label_counts(r.units)
        for label in sorted(set(b_counts) & set(r_counts)):
            if b_counts[label] != r_counts[label]:
                add(r.path, label, 'ambiguous',
                    f'{b_counts[label]} units', f'{r_counts[label]} units')
        for uid, u in r.units.items():
            if uid not in b_units:
                add(r.path, uid, 'new', None, u['status'])
            else:
                ch = _change(b_units[uid], u['status'])
                if ch:
                    add(r.path, uid, ch, b_units[uid], u['status'])
        if r.status != SOURCE_ERROR:
            for uid in b_units:
                if uid not in r.units:
                    add(r.path, uid, 'missing', b_units[uid], None)

    seen = {r.path for r in results}
    for path, b in base_entries.items():
        if path not in seen:
            add(path, None, 'missing', b['status'], None)
    return changes


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def summarize(results: List[EntryResult], changes: Optional[List[dict]]) -> dict:
    entries: Dict[str, int] = {}
    units: Dict[str, int] = {}
    ast_equal = total_units = 0
    for r in results:
        entries[r.status] = entries.get(r.status, 0) + 1
        for u in r.units.values():
            total_units += 1
            units[u['status']] = units.get(u['status'], 0) + 1
            ast_equal += bool(u.get('ast_equal'))
    s = {'entries': {'total': len(results), 'by_status': entries},
         'units': {'total': total_units, 'by_status': units, 'ast_equal': ast_equal}}
    if changes is not None:
        by_change: Dict[str, int] = {}
        for c in changes:
            by_change[c['change']] = by_change.get(c['change'], 0) + 1
        s['changes'] = by_change
    return s


def build_report(results, changes, baseline_path) -> dict:
    failures = [c for c in changes or [] if c['change'] in FAILING_CHANGES]
    return {
        'schema_version': SCHEMA_VERSION,
        'python_version': python_tag(),
        'levels': LEVELS,
        'baseline': _rel(baseline_path) if baseline_path else None,
        'result': 'fail' if failures else 'pass',
        'summary': summarize(results, changes),
        'changes': changes,
        'entries': [r.to_dict() for r in results],
    }


def _fmt_counts(counts: Dict[str, int]) -> str:
    order = LEVELS[::-1] + [SOURCE_ERROR]
    return ', '.join(f'{counts[k]} {k}' for k in order if counts.get(k))


def print_text(report: dict, verbose: bool = False) -> None:
    print(f"pycrefine correctness (Python {report['python_version']})")
    print()
    width = max([len(e['path']) for e in report['entries']] + [10])
    for e in report['entries']:
        line = f"  {e['path']:<{width}}  {e['status']:<15}"
        if e['kind'] == 'source' and 'unit_summary' in e:
            us = e['unit_summary']
            ok = us['by_status'].get('compiles', 0)
            line += f"  units {ok}/{us['total']} compile, {us['ast_equal']} ast-equal"
        if 'coherency' in e and 'composite_score' in e['coherency']:
            line += f"  coherency {e['coherency']['composite_score']}%"
        print(line)
        if verbose and e['error']:
            err = e['error']
            loc = f" (line {err['lineno']})" if err.get('lineno') else ''
            print(f"      {err['stage']}: {err['type']}: {err['message']}{loc}")
    s = report['summary']
    print()
    print(f"  entries: {_fmt_counts(s['entries']['by_status'])}")
    print(f"  units:   {_fmt_counts(s['units']['by_status'])}; "
          f"{s['units']['ast_equal']} ast-equal of {s['units']['total']}")

    changes = report['changes']
    if changes is None:
        print('\n  baseline: not compared')
        return
    print(f"\n  baseline: {report['baseline']}")
    for kind in ('regressed', 'lost', 'ambiguous', 'missing', 'improved',
                 'unscorable', 'new'):
        items = [c for c in changes if c['change'] == kind]
        if not items:
            continue
        print(f'  {kind} ({len(items)}):')
        shown = items if verbose or kind in FAILING_CHANGES else items[:10]
        for c in shown:
            where = c['path'] + (f" :: {c['unit']}" if c['unit'] else '')
            print(f"    {where}: {c['before']} -> {c['after']}")
        if len(shown) < len(items):
            print(f'    ... {len(items) - len(shown)} more (use --verbose)')
    print()
    print('RESULT: ' + report['result'].upper())
    if any(c['change'] != 'regressed' for c in changes):
        print('Results differ from the baseline; once the changes are intended, '
              'run with --update-baseline on every CI Python version to record them.')


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def run(paths: List[Path], timeout: int = DEFAULT_TIMEOUT,
        with_coherency: bool = False) -> List[EntryResult]:
    with tempfile.TemporaryDirectory() as workdir:
        return [check_entry(p, workdir, timeout, with_coherency)
                for p in collect_corpus(paths)]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='pycrefine correctness-first quality gate',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument('paths', nargs='*', type=Path,
                        help='Files or directories to check (default: test_files/)')
    parser.add_argument('--baseline', type=Path,
                        help='Baseline file (default: quality/baselines/py<ver>.json)')
    parser.add_argument('--no-baseline', action='store_true',
                        help='Report only; do not compare against a baseline')
    parser.add_argument('--update-baseline', action='store_true',
                        help='Write the current results as the baseline')
    parser.add_argument('--json', '-j', action='store_true',
                        help='Print the JSON report instead of the text summary')
    parser.add_argument('--report', type=Path, metavar='PATH',
                        help='Also write the JSON report to PATH')
    parser.add_argument('--coherency', action='store_true',
                        help='Add coherency scores (diagnostic only, never gated)')
    parser.add_argument('--timeout', type=int, default=DEFAULT_TIMEOUT, metavar='SEC',
                        help=f'Per-decompilation time limit (default: {DEFAULT_TIMEOUT})')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show error details and every change')
    args = parser.parse_args(argv)

    if args.no_baseline and args.update_baseline:
        parser.error('--no-baseline and --update-baseline are mutually exclusive')

    try:
        results = run(args.paths or DEFAULT_CORPUS, args.timeout, args.coherency)
    except FileNotFoundError as exc:
        print(f'Error: path not found: {exc}', file=sys.stderr)
        return 2

    baseline_path = None if args.no_baseline else (args.baseline or default_baseline_path())

    if args.update_baseline:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(to_baseline(results), indent=2) + '\n',
                                 encoding='utf-8')
        print(f'Baseline written to {_rel(baseline_path)}', file=sys.stderr)

    changes = None
    if baseline_path is not None:
        if not baseline_path.exists():
            print(f'Error: no baseline at {_rel(baseline_path)}. Create it with '
                  f'--update-baseline, or pass --no-baseline to report only.',
                  file=sys.stderr)
            return 2
        baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
        if baseline.get('schema_version') != SCHEMA_VERSION:
            print(f'Error: {_rel(baseline_path)} has schema_version '
                  f"{baseline.get('schema_version')}, expected {SCHEMA_VERSION}",
                  file=sys.stderr)
            return 2
        if baseline.get('python_version') != python_tag():
            print(f'Error: {_rel(baseline_path)} is for Python '
                  f"{baseline.get('python_version')}, but this is Python {python_tag()}",
                  file=sys.stderr)
            return 2
        changes = compare(results, baseline)

    report = build_report(results, changes, baseline_path)
    if args.report:
        args.report.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    if args.json:
        print(json.dumps(report, indent=2))
    else:
        print_text(report, args.verbose)
    return 1 if report['result'] == 'fail' else 0


if __name__ == '__main__':
    sys.exit(main())
