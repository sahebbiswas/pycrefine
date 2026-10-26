#!/usr/bin/env python3
"""
Differential behavior check: does the decompiled program do what the original did?

For every fixture under quality/differential/fixtures/ the harness

  1. compiles the source to a ``.pyc`` with the running interpreter,
  2. decompiles it with pycrefine,
  3. compiles the result, and
  4. runs the original and the reconstructed source in separate, limited
     subprocesses and compares what they did: the exception (type and message),
     what they wrote to stdout and stderr, and the repr of a module-level
     ``RESULT`` if the fixture defines one.

Each fixture lands on a ladder, lowest to highest:

  decompile_error  pycrefine raised, produced nothing, or ran out of time
  syntax_error     the decompiled source does not compile
  mismatch         it runs, but not like the original
  equivalent       same exception, output and RESULT

The run is compared against a committed per-Python-version baseline
(quality/differential/baselines/py<major>.<minor>.json). A fixture that drops to a lower
rung than its baseline is a regression and fails the run, as does a fixture
that is not in the baseline or that the baseline has but the run did not find.
Improvements never fail; record them with --update-baseline.

Bytecode from Python 3.10 onwards can only be decompiled by the interpreter that wrote it,
so each run covers the interpreter it runs under. CI runs it once per supported version;
``--python EXE`` (repeatable) runs it under other interpreters from here and merges the
results.

Usage:
    # Every fixture, compared with the baseline for this Python:
    python quality/check_differential.py

    # A few fixtures, with the original/reconstructed source of the failures:
    python quality/check_differential.py flow_while exc_finally --verbose

    # Accept the current results as the new baseline:
    python quality/check_differential.py --update-baseline

    # Other fixtures, another interpreter, a JSON report:
    python quality/check_differential.py --fixtures path/to/dir --python python3.9 --report report.json

Exit codes:
    0  No regressions against the baseline
    1  At least one regression, or a fixture/baseline mismatch
    2  Configuration error (no baseline, wrong-version baseline, bad path)

Safety: the fixtures are committed and trusted, but they are still executed, so every run
happens in a subprocess with a wall-clock timeout, no inherited environment, an empty
scratch directory as its working directory and, on POSIX, limits on CPU time, memory and
file size. That bounds a runaway fixture; it is not a sandbox. Do not point --fixtures at
code you do not trust without running the whole harness in a container.
"""

import argparse
import contextlib
import io
import json
import os
import py_compile
import subprocess
import sys
import tempfile
import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence

_QUALITY = Path(__file__).resolve().parent
_ROOT = _QUALITY.parent

SCHEMA_VERSION = 1

DEFAULT_FIXTURES = _QUALITY / 'differential' / 'fixtures'
BASELINE_DIR = _QUALITY / 'differential' / 'baselines'

# The ladder, lowest to highest. ``source_error`` (the fixture itself does not run on this
# interpreter) says nothing about the decompiler and is never gated.
LEVELS = ['decompile_error', 'syntax_error', 'mismatch', 'equivalent']
SOURCE_ERROR = 'source_error'

DECOMPILE_TIMEOUT = 60
RUN_TIMEOUT = 20

# POSIX limits for each subprocess.
CPU_SECONDS = 30
MEMORY_BYTES = 1 << 30
FILE_BYTES = 8 << 20

# Longest stdout/stderr/RESULT kept in a report.
MAX_CAPTURE = 20000

FIELDS = ('exception', 'stdout', 'stderr', 'result')


def level_of(status: str) -> Optional[int]:
    return LEVELS.index(status) if status in LEVELS else None


def python_tag() -> str:
    return f'{sys.version_info[0]}.{sys.version_info[1]}'


def default_baseline_path() -> Path:
    return BASELINE_DIR / f'py{python_tag()}.json'


# ---------------------------------------------------------------------------
# Workers: the code that runs inside the limited subprocesses
# ---------------------------------------------------------------------------

def _worker_decompile(pyc_path: str) -> int:
    """Decompile *pyc_path* and print the source (stdout) or the failure (stderr)."""
    sys.path.insert(0, str(_ROOT))
    from pycrefine import get_decompiler
    try:
        source = get_decompiler(pyc_path).decompile()
    except BaseException as exc:  # noqa: BLE001 - every failure is a result
        sys.stderr.write(f'{type(exc).__name__}: {exc}\n')
        traceback.print_exc(file=sys.stderr)
        return 3
    sys.stdout.write(source)
    return 0


def _worker_run(source_path: str) -> int:
    """Run *source_path* as ``__main__`` and print a JSON description of what happened."""
    with open(source_path, encoding='utf-8') as handle:
        source = handle.read()
    out, err = io.StringIO(), io.StringIO()
    namespace = {'__name__': '__main__', '__builtins__': __builtins__}
    exception = None
    result = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            exec(compile(source, '<fixture>', 'exec'), namespace)  # noqa: S102
        except SystemExit as exc:
            exception = f'SystemExit: {exc.code}'
        except BaseException as exc:  # noqa: BLE001 - the exception is the observation
            exception = f'{type(exc).__name__}: {exc}'
        if 'RESULT' in namespace:
            try:
                result = repr(namespace['RESULT'])
            except BaseException as exc:  # noqa: BLE001
                result = f'<repr failed: {type(exc).__name__}>'
    report = {'exception': exception, 'stdout': out.getvalue(),
              'stderr': err.getvalue(), 'result': result}
    sys.__stdout__.write(json.dumps(report))
    return 0


# ---------------------------------------------------------------------------
# Running a worker in a limited subprocess
# ---------------------------------------------------------------------------

def _limit_resources() -> None:
    """Runs in the child before exec (POSIX): bound CPU time, memory and file size."""
    import resource
    for name, limit in (('RLIMIT_CPU', CPU_SECONDS), ('RLIMIT_FSIZE', FILE_BYTES),
                        ('RLIMIT_AS', MEMORY_BYTES), ('RLIMIT_CORE', 0)):
        if hasattr(resource, name):
            try:
                resource.setrlimit(getattr(resource, name), (limit, limit))
            except (ValueError, OSError):
                pass  # a limit the platform refuses must not stop the check


@dataclass
class Process:
    returncode: Optional[int]
    stdout: str
    stderr: str
    timed_out: bool = False


def run_worker(python: str, mode: str, path: str, timeout: int, cwd: str) -> Process:
    """Run ``check_differential.py --worker MODE PATH`` under *python*, limited."""
    command = [python, '-I', '-B', str(Path(__file__).resolve()), '--worker', mode, path]
    env = {'PYTHONHASHSEED': '0', 'PYTHONIOENCODING': 'utf-8', 'PYTHONUTF8': '1'}
    for name in ('SYSTEMROOT', 'PATH'):  # Windows cannot start Python without these
        if name in os.environ and os.name == 'nt':
            env[name] = os.environ[name]
    options = {'preexec_fn': _limit_resources} if os.name == 'posix' else {}
    try:
        done = subprocess.run(command, cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                              capture_output=True, timeout=timeout, **options)
    except subprocess.TimeoutExpired as exc:
        return Process(None, _text(exc.stdout), _text(exc.stderr), timed_out=True)
    return Process(done.returncode, _text(done.stdout), _text(done.stderr))


def _text(data: Optional[bytes]) -> str:
    return (data or b'').decode('utf-8', 'replace')


# ---------------------------------------------------------------------------
# Checking one fixture
# ---------------------------------------------------------------------------

@dataclass
class Observation:
    exception: Optional[str]
    stdout: str
    stderr: str
    result: Optional[str]

    def as_dict(self) -> dict:
        return {name: _clip(getattr(self, name)) for name in FIELDS}


def _clip(value: Optional[str]) -> Optional[str]:
    if value is not None and len(value) > MAX_CAPTURE:
        return value[:MAX_CAPTURE] + f'... [{len(value) - MAX_CAPTURE} more characters]'
    return value


@dataclass
class FixtureResult:
    name: str
    status: str
    detail: str = ''
    mismatches: List[dict] = field(default_factory=list)
    original_source: str = ''
    decompiled_source: str = ''

    def as_dict(self) -> dict:
        data = {'name': self.name, 'status': self.status}
        if self.detail:
            data['detail'] = self.detail
        if self.mismatches:
            data['mismatches'] = self.mismatches
        if self.status not in ('equivalent', SOURCE_ERROR) and self.decompiled_source:
            data['decompiled_source'] = _clip(self.decompiled_source)
        return data


def observe(python: str, source_path: str, workdir: str) -> Optional[Observation]:
    """Run a source file under the worker; None when the run itself could not be observed."""
    process = run_worker(python, 'run', source_path, RUN_TIMEOUT, workdir)
    if process.timed_out:
        return Observation(f'Timeout: exceeded {RUN_TIMEOUT}s', '', '', None)
    try:
        data = json.loads(process.stdout)
    except ValueError:
        detail = process.stderr.strip().splitlines()[-1:] or [f'exit status {process.returncode}']
        return Observation(f'WorkerFailure: {detail[0]}', '', '', None)
    return Observation(data['exception'], data['stdout'], data['stderr'], data['result'])


def compare(original: Observation, rebuilt: Observation) -> List[dict]:
    """The fields in which the two runs differ, as ``{field, original, decompiled}``."""
    return [{'field': name, 'original': _clip(getattr(original, name)),
             'decompiled': _clip(getattr(rebuilt, name))}
            for name in FIELDS if getattr(original, name) != getattr(rebuilt, name)]


def check_fixture(python: str, path: Path, workdir: str) -> FixtureResult:
    """Compile, decompile, validate and run one fixture."""
    name = path.stem
    source = path.read_text(encoding='utf-8')
    result = FixtureResult(name, 'decompile_error', original_source=source)

    original = observe(python, str(path), workdir)
    if original is None or (original.exception or '').startswith(('SyntaxError', 'WorkerFailure')):
        result.status = SOURCE_ERROR
        result.detail = (original.exception if original else 'could not run the original')
        return result

    pyc = os.path.join(workdir, name + '.pyc')
    try:
        py_compile.compile(str(path), cfile=pyc, doraise=True)
    except py_compile.PyCompileError as exc:
        result.status = SOURCE_ERROR
        result.detail = f'the fixture does not compile on Python {python_tag()}: {exc.msg}'
        return result

    process = run_worker(python, 'decompile', pyc, DECOMPILE_TIMEOUT, workdir)
    if process.timed_out:
        result.detail = f'decompilation exceeded {DECOMPILE_TIMEOUT}s'
        return result
    if process.returncode != 0 or not process.stdout.strip():
        lines = process.stderr.strip().splitlines()
        result.detail = lines[0] if lines else f'exit status {process.returncode}, no output'
        return result
    result.decompiled_source = process.stdout

    rebuilt_path = os.path.join(workdir, name + '.decompiled.py')
    with open(rebuilt_path, 'w', encoding='utf-8') as handle:
        handle.write(process.stdout)
    try:
        compile(process.stdout, rebuilt_path, 'exec')
    except (SyntaxError, ValueError) as exc:
        result.status = 'syntax_error'
        result.detail = f'{type(exc).__name__}: {exc}'
        return result

    rebuilt = observe(python, rebuilt_path, workdir)
    result.mismatches = compare(original, rebuilt) if rebuilt else []
    result.status = 'mismatch' if result.mismatches else 'equivalent'
    return result


def run_fixtures(python: str, fixtures: Sequence[Path]) -> List[FixtureResult]:
    results = []
    for path in fixtures:
        with tempfile.TemporaryDirectory(prefix='pycrefine-diff-') as workdir:
            results.append(check_fixture(python, path, workdir))
    return results


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

def to_baseline(results: Sequence[FixtureResult], previous: Optional[dict] = None) -> dict:
    """
    The baseline for *results*. ``issues`` maps every fixture that is not ``equivalent`` to
    the issues that track why; they are written by hand, and carried over from *previous*
    for fixtures that still fail (a fixture that now passes loses its entry).
    """
    old_issues = (previous or {}).get('issues', {})
    ordered = sorted(results, key=lambda r: r.name)
    baseline: dict = {
        'schema_version': SCHEMA_VERSION,
        'python_version': python_tag(),
        'fixtures': {r.name: r.status for r in ordered},
    }
    issues = {r.name: old_issues[r.name] for r in ordered
              if r.status not in ('equivalent', SOURCE_ERROR) and r.name in old_issues}
    if issues:
        baseline['issues'] = issues
    return baseline


def diff_baseline(results: Sequence[FixtureResult], baseline: dict) -> List[dict]:
    """
    Compare a run with a baseline. Each change has ``name``, ``kind``, ``baseline`` and
    ``current``. Kinds: ``regressed``, ``improved``, ``new`` (not in the baseline),
    ``missing`` (in the baseline, not run) and ``lost`` (could not be scored any more).
    """
    expected: Dict[str, str] = dict(baseline.get('fixtures', {}))
    changes = []
    seen = set()
    for r in results:
        seen.add(r.name)
        before = expected.get(r.name)
        if before is None:
            changes.append({'name': r.name, 'kind': 'new', 'baseline': None, 'current': r.status})
            continue
        if before == r.status:
            continue
        if r.status == SOURCE_ERROR:
            changes.append({'name': r.name, 'kind': 'lost', 'baseline': before, 'current': r.status})
            continue
        old, new = level_of(before), level_of(r.status)
        if old is None:
            kind = 'improved'
        else:
            kind = 'regressed' if new < old else 'improved'
        changes.append({'name': r.name, 'kind': kind, 'baseline': before, 'current': r.status})
    for name in sorted(set(expected) - seen):
        changes.append({'name': name, 'kind': 'missing', 'baseline': expected[name], 'current': None})
    return changes


FAILING_CHANGES = ('regressed', 'new', 'missing', 'lost')


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

def build_report(python: str, results: Sequence[FixtureResult], changes: Sequence[dict],
                 baseline_path: Optional[Path]) -> dict:
    counts: Dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    return {
        'schema_version': SCHEMA_VERSION,
        'python_version': python_tag(),
        'interpreter': python,
        'baseline': _rel(baseline_path) if baseline_path else None,
        'summary': {'fixtures': len(results), **dict(sorted(counts.items()))},
        'fixtures': [r.as_dict() for r in results],
        'changes': list(changes),
    }


def _rel(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(_ROOT)).replace(os.sep, '/')
    except ValueError:
        return str(path)


def print_report(results: Sequence[FixtureResult], changes: Sequence[dict],
                 baseline_path: Optional[Path], verbose: bool,
                 issues: Optional[Dict[str, List[int]]] = None) -> None:
    print(f'Differential check, Python {python_tag()}')
    issues = issues or {}
    width = max((len(r.name) for r in results), default=10)
    for r in results:
        line = f'  {r.name:<{width}}  {r.status}'
        if r.status != 'equivalent' and r.detail:
            line += f'  ({r.detail.splitlines()[0][:100]})'
        if r.name in issues and r.status != 'equivalent':
            line += '  ' + ' '.join(f'#{n}' for n in issues[r.name])
        print(line)
        if r.status in ('mismatch', 'syntax_error') or (verbose and r.status != 'equivalent'):
            for m in r.mismatches:
                print(f'      {m["field"]}: original={m["original"]!r:.200} decompiled={m["decompiled"]!r:.200}')
        if verbose and r.decompiled_source and r.status not in ('equivalent', SOURCE_ERROR):
            print('      --- decompiled source ---')
            print('      ' + r.decompiled_source.rstrip().replace('\n', '\n      '))
    equivalent = sum(1 for r in results if r.status == 'equivalent')
    print(f'\n  {equivalent} of {len(results)} fixtures behave like the original')
    if baseline_path is None:
        print('  baseline: not compared')
        return
    print(f'  baseline: {_rel(baseline_path)}')
    for c in changes:
        print(f'  {c["kind"]:<9} {c["name"]}: {c["baseline"]} -> {c["current"]}')
    failing = [c for c in changes if c['kind'] in FAILING_CHANGES]
    print('\nRESULT: ' + ('FAIL' if failing else 'PASS'))
    if failing:
        print('A regression is a fixture that does worse than its baseline; a new or missing '
              'fixture needs the baseline updated. Once the changes are intended, run with '
              '--update-baseline on every CI Python version.')
    elif changes:
        print('Improvements are not recorded yet; run with --update-baseline to ratchet them.')


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def find_fixtures(directory: Path, names: Sequence[str]) -> List[Path]:
    available = sorted(directory.glob('*.py'))
    if not names:
        return available
    by_name = {p.stem: p for p in available}
    unknown = [n for n in names if n not in by_name]
    if unknown:
        raise SystemExit(f'Error: no such fixture in {_rel(directory)}: {", ".join(unknown)}')
    return [by_name[n] for n in names]


def run_under(interpreter: str, argv: Sequence[str]) -> int:
    """Re-run this script under another interpreter (without --python), passing the exit code on."""
    return subprocess.call([interpreter, str(Path(__file__).resolve()), *argv])


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Compare the behavior of fixtures with their decompiled versions.')
    parser.add_argument('fixtures_named', nargs='*', metavar='FIXTURE',
                        help='Fixture names to run (default: all)')
    parser.add_argument('--fixtures', type=Path, default=DEFAULT_FIXTURES, metavar='DIR',
                        help='Directory of .py fixtures (default: quality/differential/fixtures)')
    parser.add_argument('--baseline', type=Path,
                        help='Baseline file (default: quality/differential/baselines/py<ver>.json)')
    parser.add_argument('--no-baseline', action='store_true',
                        help='Report only; do not compare against a baseline')
    parser.add_argument('--update-baseline', action='store_true',
                        help='Write the current results as the baseline')
    parser.add_argument('--report', type=Path, metavar='FILE', help='Write a JSON report')
    parser.add_argument('--python', action='append', default=[], metavar='EXE',
                        help='Run under this interpreter instead (repeatable); each runs the '
                             'whole check against its own baseline')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show the decompiled source of fixtures that are not equivalent')
    parser.add_argument('--worker', nargs=2, metavar=('MODE', 'PATH'), help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(argv)

    if args.worker:
        mode, path = args.worker
        return _worker_decompile(path) if mode == 'decompile' else _worker_run(path)

    if args.python:
        forwarded = _strip_python_options(argv)
        codes = []
        for interpreter in args.python:
            print(f'=== {interpreter} ===', flush=True)
            codes.append(run_under(interpreter, forwarded))
        return max(codes)

    if args.no_baseline and args.update_baseline:
        print('Error: --no-baseline and --update-baseline are mutually exclusive', file=sys.stderr)
        return 2
    if not args.fixtures.is_dir():
        print(f'Error: {args.fixtures} is not a directory', file=sys.stderr)
        return 2
    fixtures = find_fixtures(args.fixtures, args.fixtures_named)
    if not fixtures:
        print(f'Error: no fixtures in {_rel(args.fixtures)}', file=sys.stderr)
        return 2

    baseline_path = None if args.no_baseline else (args.baseline or default_baseline_path())
    results = run_fixtures(sys.executable, fixtures)

    if args.update_baseline:
        if args.fixtures_named:
            print('Error: --update-baseline records every fixture; do not name fixtures', file=sys.stderr)
            return 2
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        previous = (json.loads(baseline_path.read_text(encoding='utf-8'))
                    if baseline_path.exists() else None)
        baseline_path.write_text(json.dumps(to_baseline(results, previous), indent=2) + '\n',
                                 encoding='utf-8')
        print(f'Baseline written to {_rel(baseline_path)}', file=sys.stderr)
        baseline_path = None if args.no_baseline else baseline_path

    changes: List[dict] = []
    issues: Dict[str, List[int]] = {}
    if baseline_path is not None:
        if not baseline_path.exists():
            print(f'Error: no baseline at {_rel(baseline_path)}. Create it with --update-baseline, '
                  'or pass --no-baseline to report only.', file=sys.stderr)
            return 2
        baseline = json.loads(baseline_path.read_text(encoding='utf-8'))
        if baseline.get('schema_version') != SCHEMA_VERSION:
            print(f'Error: {_rel(baseline_path)} has schema_version {baseline.get("schema_version")}, '
                  f'expected {SCHEMA_VERSION}', file=sys.stderr)
            return 2
        if baseline.get('python_version') != python_tag():
            print(f'Error: {_rel(baseline_path)} is for Python {baseline.get("python_version")}, '
                  f'but this is Python {python_tag()}', file=sys.stderr)
            return 2
        changes = diff_baseline(results, baseline)
        issues = baseline.get('issues', {})
        if args.fixtures_named:
            changes = [c for c in changes if c['kind'] != 'missing']

    print_report(results, changes, baseline_path, args.verbose, issues)
    if args.report:
        args.report.write_text(
            json.dumps(build_report(sys.executable, results, changes, baseline_path), indent=2) + '\n',
            encoding='utf-8')
    return 1 if any(c['kind'] in FAILING_CHANGES for c in changes) else 0


def _strip_python_options(argv: Sequence[str]) -> List[str]:
    """argv without ``--python EXE`` pairs (and ``--python=EXE``)."""
    kept: List[str] = []
    skip = False
    for item in argv:
        if skip:
            skip = False
        elif item == '--python':
            skip = True
        elif not item.startswith('--python='):
            kept.append(item)
    return kept


if __name__ == '__main__':
    sys.exit(main())
