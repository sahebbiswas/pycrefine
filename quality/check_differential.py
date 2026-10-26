#!/usr/bin/env python3
"""
Differential behavior check: does the decompiled program do what the original did?

For every entry of the corpus (quality/corpus/manifest.json) that applies to the running
Python, the harness

  1. compiles the source to a ``.pyc`` with the running interpreter,
  2. decompiles it with pycrefine,
  3. compiles the result, and
  4. runs the original and the reconstructed source in separate, limited
     subprocesses and compares what they did: the exception (type and message),
     what they wrote to stdout and stderr, and the repr of a module-level
     ``RESULT`` if the program defines one.

Each entry lands on a ladder, lowest to highest:

  decompile_error  pycrefine raised, produced nothing, or ran out of time
  syntax_error     the decompiled source does not compile
  compiles         it compiles (the best an entry with ``equivalence: compile_only`` can do)
  mismatch         it runs, but not like the original
  equivalent       same exception, output and RESULT

An entry whose manifest says ``equivalence: compile_only`` (a standard-library module, which
is not worth running) is never executed. The two rungs above ``syntax_error`` are never
mixed: ``compiles`` is the top for those entries, ``equivalent`` for the others.

The run is compared against a committed per-Python-version baseline
(quality/corpus/baselines/py<major>.<minor>.json). An entry that drops to a lower rung than
its baseline is a regression and fails the run, as does an entry that is not in the
baseline or that the baseline has but the run did not find. Improvements never fail; record
them with --update-baseline.

Bytecode from Python 3.10 onwards can only be decompiled by the interpreter that wrote it,
so each run covers the interpreter it runs under. CI runs it once per supported version;
``--python EXE`` (repeatable) runs it under other interpreters from here, one after the
other, each against its own baseline (``--report`` and ``--baseline`` name a single file,
so they are only accepted with one interpreter).

Usage:
    # Every entry that applies to this Python, compared with its baseline:
    python quality/check_differential.py

    # A few entries (by id or file name), with the generated source of the failures:
    python quality/check_differential.py flow_while exc_finally --verbose

    # One category: feature, generated, realworld or stdlib
    python quality/check_differential.py --category stdlib

    # Accept the current results as the new baseline:
    python quality/check_differential.py --update-baseline

    # Another corpus (a directory of .py files, or with its own manifest.json), another
    # interpreter, a JSON report:
    python quality/check_differential.py --corpus path/to/dir --python python3.9 --report report.json

Exit codes:
    0  No regressions against the baseline
    1  At least one regression, or an entry/baseline mismatch
    2  Configuration error (no baseline, wrong-version baseline, bad path or manifest)

Safety: the corpus is committed and trusted, but it is still executed, so every run
happens in a subprocess with a wall-clock timeout, no inherited environment, an empty
scratch directory as its working directory and, on POSIX, limits on CPU time, memory and
file size. That bounds a runaway program; it is not a sandbox. Do not point --corpus at
code you do not trust without running the whole harness in a container.
"""

import argparse
import contextlib
import importlib.util
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
from typing import Dict, List, Optional, Sequence, Tuple

_QUALITY = Path(__file__).resolve().parent
_ROOT = _QUALITY.parent

SCHEMA_VERSION = 1

DEFAULT_CORPUS = _QUALITY / 'corpus'

# The ladder, lowest to highest. ``source_error`` (the source itself does not compile on
# this interpreter) says nothing about the decompiler and is never gated.
LEVELS = ['decompile_error', 'syntax_error', 'compiles', 'mismatch', 'equivalent']
SOURCE_ERROR = 'source_error'
PASSING = ('equivalent', 'compiles')

CATEGORIES = ('feature', 'generated', 'realworld', 'stdlib')
EQUIVALENCE = ('required', 'compile_only')
SUPPORT = ('supported', 'partial', 'unsupported')

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


def baseline_path_for(corpus: Path) -> Path:
    return corpus / 'baselines' / f'py{python_tag()}.json'


def parse_version(text: str) -> Tuple[int, int]:
    major, minor = text.split('.')[:2]
    return int(major), int(minor)


# ---------------------------------------------------------------------------
# The corpus
# ---------------------------------------------------------------------------

@dataclass
class Limitation:
    """A known gap behind a non-passing entry: the issue, the versions it affects, a summary."""
    issue: int
    versions: Optional[List[str]]  # None means every version
    summary: str

    def applies_to(self, tag: str) -> bool:
        return self.versions is None or tag in self.versions


@dataclass
class Entry:
    id: str
    category: str
    path: Optional[Path] = None          # a source file in the corpus
    module: Optional[str] = None         # or a module of the running interpreter's standard library
    summary: str = ''
    python_min: Tuple[int, int] = (3, 9)
    python_max: Optional[Tuple[int, int]] = None
    optimize: int = 0
    equivalence: str = 'required'
    support: str = 'supported'
    limitations: List[Limitation] = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.id.rsplit('/', 1)[-1]

    def applies_to(self, version: Tuple[int, int]) -> bool:
        return version >= self.python_min and (self.python_max is None or version <= self.python_max)

    def source_path(self) -> Optional[Path]:
        """The file to compile: the corpus file, or the module's source in the running Python."""
        if self.path is not None:
            return self.path
        spec = importlib.util.find_spec(self.module) if self.module else None
        origin = getattr(spec, 'origin', None)
        return Path(origin) if origin and origin.endswith('.py') else None

    def issues_for(self, tag: str) -> List[int]:
        return [lim.issue for lim in self.limitations if lim.applies_to(tag)]


class ManifestError(Exception):
    pass


def load_corpus(directory: Path) -> List[Entry]:
    """
    The entries of the corpus in *directory*: those of its ``manifest.json``, or, without
    one, every ``*.py`` file below it as a required-equivalence feature.
    """
    manifest_path = directory / 'manifest.json'
    if not manifest_path.exists():
        return [Entry(id=str(p.relative_to(directory).with_suffix('')).replace(os.sep, '/'),
                      category='feature', path=p)
                for p in sorted(directory.rglob('*.py'))]
    try:
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    except ValueError as exc:
        raise ManifestError(f'{_rel(manifest_path)} is not valid JSON: {exc}')
    if manifest.get('schema_version') != SCHEMA_VERSION:
        raise ManifestError(f'{_rel(manifest_path)} has schema_version {manifest.get("schema_version")}, '
                            f'expected {SCHEMA_VERSION}')
    return [_entry_from_manifest(directory, raw) for raw in manifest.get('entries', [])]


def _entry_from_manifest(directory: Path, raw: dict) -> Entry:
    try:
        source = raw.get('source_python', {})
        entry = Entry(
            id=raw['id'],
            category=raw['category'],
            path=(directory / raw['path']) if 'path' in raw else None,
            module=raw.get('module'),
            summary=raw.get('summary', ''),
            python_min=parse_version(source.get('min', '3.9')),
            python_max=parse_version(source['max']) if source.get('max') else None,
            optimize=raw.get('compile', {}).get('optimize', 0),
            equivalence=raw.get('equivalence', 'required'),
            support=raw.get('support', 'supported'),
            limitations=[Limitation(
                issue=lim['issue'],
                versions=None if lim.get('versions', 'all') == 'all' else list(lim['versions']),
                summary=lim.get('summary', '')) for lim in raw.get('limitations', [])],
        )
    except (KeyError, ValueError, AttributeError) as exc:
        raise ManifestError(f'bad manifest entry {raw.get("id", raw)!r}: {exc!r}')
    if entry.category not in CATEGORIES:
        raise ManifestError(f'{entry.id}: category must be one of {", ".join(CATEGORIES)}')
    if entry.equivalence not in EQUIVALENCE:
        raise ManifestError(f'{entry.id}: equivalence must be one of {", ".join(EQUIVALENCE)}')
    if entry.support not in SUPPORT:
        raise ManifestError(f'{entry.id}: support must be one of {", ".join(SUPPORT)}')
    if (entry.path is None) == (entry.module is None):
        raise ManifestError(f'{entry.id}: give exactly one of "path" and "module"')
    return entry


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


def _worker_run(source_path: str, optimize: int = 0) -> int:
    """
    Run *source_path* as ``__main__``, compiled at *optimize* (0 keeps asserts and
    ``__debug__`` code), and print a JSON description of what happened.
    """
    with open(source_path, encoding='utf-8') as handle:
        source = handle.read()
    out, err = io.StringIO(), io.StringIO()
    namespace = {'__name__': '__main__', '__builtins__': __builtins__}
    exception = None
    result = None
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            exec(compile(source, '<fixture>', 'exec', optimize=optimize), namespace)  # noqa: S102
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


def run_worker(python: str, mode: str, path: str, timeout: int, cwd: str, optimize: int = 0) -> Process:
    """Run ``check_differential.py --worker MODE PATH [OPTIMIZE]`` under *python*, limited."""
    command = [python, '-s', '-B', str(Path(__file__).resolve()), '--worker', mode, path]
    if mode == 'run':
        command.append(str(optimize))
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
# Checking one entry
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
    category: str = ''
    mismatches: List[dict] = field(default_factory=list)
    original_source: str = ''
    decompiled_source: str = ''

    def as_dict(self) -> dict:
        data = {'name': self.name, 'status': self.status}
        if self.category:
            data['category'] = self.category
        if self.detail:
            data['detail'] = self.detail
        if self.mismatches:
            data['mismatches'] = self.mismatches
        if self.status not in PASSING and self.status != SOURCE_ERROR and self.decompiled_source:
            data['decompiled_source'] = _clip(self.decompiled_source)
        return data


def observe(python: str, source_path: str, workdir: str, optimize: int = 0) -> Optional[Observation]:
    """Run a source file under the worker; None when the run itself could not be observed."""
    process = run_worker(python, 'run', source_path, RUN_TIMEOUT, workdir, optimize)
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


def check_entry(python: str, entry: Entry, workdir: str) -> FixtureResult:
    """Compile, decompile, validate and (unless compile-only) run one corpus entry."""
    result = FixtureResult(entry.id, 'decompile_error', category=entry.category)
    path = entry.source_path()
    if path is None or not path.exists():
        result.status = SOURCE_ERROR
        result.detail = f'no source for {entry.module or entry.id} on Python {python_tag()}'
        return result
    source = path.read_text(encoding='utf-8')
    result.original_source = source
    run_it = entry.equivalence == 'required'

    pyc = os.path.join(workdir, entry.name + '.pyc')
    try:
        py_compile.compile(str(path), cfile=pyc, doraise=True, optimize=entry.optimize)
    except py_compile.PyCompileError as exc:
        result.status = SOURCE_ERROR
        result.detail = f'does not compile on Python {python_tag()}: {exc.msg}'
        return result

    # Only an entry that compiles gets here, so a SyntaxError it raises while running is
    # behavior to compare, not a reason to skip it.
    original = None
    if run_it:
        original = observe(python, str(path), workdir, entry.optimize)
        if original is None or (original.exception or '').startswith('WorkerFailure'):
            result.status = SOURCE_ERROR
            result.detail = original.exception if original else 'could not run the original'
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

    rebuilt_path = os.path.join(workdir, entry.name + '.decompiled.py')
    with open(rebuilt_path, 'w', encoding='utf-8') as handle:
        handle.write(process.stdout)
    try:
        compile(process.stdout, rebuilt_path, 'exec')
    except (SyntaxError, ValueError) as exc:
        result.status = 'syntax_error'
        result.detail = f'{type(exc).__name__}: {exc}'
        return result
    if not run_it:
        result.status = 'compiles'
        return result

    rebuilt = observe(python, rebuilt_path, workdir)
    result.mismatches = compare(original, rebuilt) if rebuilt else []
    result.status = 'mismatch' if result.mismatches else 'equivalent'
    return result


def run_entries(python: str, entries: Sequence[Entry]) -> List[FixtureResult]:
    results = []
    for entry in entries:
        with tempfile.TemporaryDirectory(prefix='pycrefine-diff-') as workdir:
            results.append(check_entry(python, entry, workdir))
    return results


# ---------------------------------------------------------------------------
# Baseline
# ---------------------------------------------------------------------------

def to_baseline(results: Sequence[FixtureResult]) -> dict:
    return {
        'schema_version': SCHEMA_VERSION,
        'python_version': python_tag(),
        'entries': {r.name: r.status for r in sorted(results, key=lambda r: r.name) if r.status != SOURCE_ERROR},
    }


def diff_baseline(results: Sequence[FixtureResult], baseline: dict) -> List[dict]:
    """
    Compare a run with a baseline. Each change has ``name``, ``kind``, ``baseline`` and
    ``current``. Kinds: ``regressed``, ``improved``, ``new`` (not in the baseline),
    ``missing`` (in the baseline, not run) and ``lost`` (could not be scored any more).
    """
    expected: Dict[str, str] = dict(baseline.get('entries', {}))
    changes = []
    seen = set()
    for r in results:
        seen.add(r.name)
        before = expected.get(r.name)
        if before is None and r.status == SOURCE_ERROR:
            continue  # it never ran on this interpreter, so the baseline has no rung for it
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
        'summary': {'entries': len(results), **dict(sorted(counts.items()))},
        'entries': [r.as_dict() for r in results],
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
        if r.status not in PASSING and r.detail:
            line += f'  ({r.detail.splitlines()[0][:100]})'
        if r.status not in PASSING and issues.get(r.name):
            line += '  ' + ' '.join(f'#{n}' for n in issues[r.name])
        print(line)
        if r.status in ('mismatch', 'syntax_error') or (verbose and r.status not in PASSING):
            for m in r.mismatches:
                print(f'      {m["field"]}: original={m["original"]!r:.200} decompiled={m["decompiled"]!r:.200}')
        if verbose and r.decompiled_source and r.status not in PASSING + (SOURCE_ERROR,):
            print('      --- decompiled source ---')
            print('      ' + r.decompiled_source.rstrip().replace('\n', '\n      '))
    passing = sum(1 for r in results if r.status in PASSING)
    print(f'\n  {passing} of {len(results)} entries decompile as expected')
    if baseline_path is None:
        print('  baseline: not compared')
        return
    print(f'  baseline: {_rel(baseline_path)}')
    for c in changes:
        print(f'  {c["kind"]:<9} {c["name"]}: {c["baseline"]} -> {c["current"]}')
    failing = [c for c in changes if c['kind'] in FAILING_CHANGES]
    print('\nRESULT: ' + ('FAIL' if failing else 'PASS'))
    if failing:
        print('A regression is an entry that does worse than its baseline; a new or missing '
              'entry needs the baseline updated. Once the changes are intended, run with '
              '--update-baseline on every CI Python version.')
    elif changes:
        print('Improvements are not recorded yet; run with --update-baseline to ratchet them.')


# ---------------------------------------------------------------------------
# Command line
# ---------------------------------------------------------------------------

def select_entries(entries: Sequence[Entry], names: Sequence[str], category: Optional[str],
                   version: Tuple[int, int]) -> List[Entry]:
    """The entries to run: applicable to *version*, in the category, named by id or file name."""
    chosen = [e for e in entries if e.applies_to(version) and (category is None or e.category == category)]
    if not names:
        return chosen
    picked = []
    for name in names:
        found = [e for e in chosen if name in (e.id, e.name)]
        if not found:
            raise LookupError(f'no entry {name!r} that applies to Python {python_tag()}')
        picked.extend(e for e in found if e not in picked)
    return picked


def run_under(interpreter: str, argv: Sequence[str]) -> int:
    """Re-run this script under another interpreter (without --python), passing the exit code on."""
    return subprocess.call([interpreter, str(Path(__file__).resolve()), *argv])


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description='Compare the behavior of corpus programs with their decompiled versions.')
    parser.add_argument('entries_named', nargs='*', metavar='ENTRY',
                        help='Entry ids or file names to run (default: all that apply)')
    parser.add_argument('--corpus', type=Path, default=DEFAULT_CORPUS, metavar='DIR',
                        help='Corpus directory with a manifest.json, or a directory of .py files '
                             '(default: quality/corpus)')
    parser.add_argument('--category', choices=CATEGORIES, help='Only entries of this category')
    parser.add_argument('--baseline', type=Path,
                        help='Baseline file (default: <corpus>/baselines/py<ver>.json)')
    parser.add_argument('--no-baseline', action='store_true',
                        help='Report only; do not compare against a baseline')
    parser.add_argument('--update-baseline', action='store_true',
                        help='Write the current results as the baseline')
    parser.add_argument('--report', type=Path, metavar='FILE', help='Write a JSON report')
    parser.add_argument('--python', action='append', default=[], metavar='EXE',
                        help='Run under this interpreter instead (repeatable); each runs the '
                             'whole check against its own baseline')
    parser.add_argument('--verbose', '-v', action='store_true',
                        help='Show the decompiled source of entries that do not pass')
    parser.add_argument('--worker', nargs='+', metavar='ARG', help=argparse.SUPPRESS)
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    args = parse_args(argv)

    if args.worker:
        # -s keeps the PYTHON* variables the harness sets (hash seed, UTF-8) but, unlike -I,
        # leaves this script's own directory on sys.path; a fixture must not import the harness.
        sys.path[:] = [entry for entry in sys.path if Path(entry or '.').resolve() != _QUALITY]
        mode, path = args.worker[:2]
        if mode == 'decompile':
            return _worker_decompile(path)
        return _worker_run(path, int(args.worker[2]) if len(args.worker) > 2 else 0)

    if len(args.python) > 1 and (args.report or args.baseline):
        print('Error: --report and --baseline name one file, but each --python interpreter needs '
              'its own; run them one at a time', file=sys.stderr)
        return 2
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
    if not args.corpus.is_dir():
        print(f'Error: {args.corpus} is not a directory', file=sys.stderr)
        return 2
    args.corpus = args.corpus.resolve()  # workers run in a scratch directory
    try:
        corpus = load_corpus(args.corpus)
    except ManifestError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    try:
        entries = select_entries(corpus, args.entries_named, args.category, sys.version_info[:2])
    except LookupError as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 2
    if not entries:
        print(f'Error: nothing to run in {_rel(args.corpus)} on Python {python_tag()}', file=sys.stderr)
        return 2
    partial = bool(args.entries_named or args.category)

    baseline_path = None if args.no_baseline else (args.baseline or baseline_path_for(args.corpus))
    results = run_entries(sys.executable, entries)

    if args.update_baseline:
        if partial:
            print('Error: --update-baseline records every entry; do not name entries or a category',
                  file=sys.stderr)
            return 2
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(json.dumps(to_baseline(results), indent=2) + '\n', encoding='utf-8')
        print(f'Baseline written to {_rel(baseline_path)}', file=sys.stderr)

    changes: List[dict] = []
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
        if partial:
            changes = [c for c in changes if c['kind'] != 'missing']

    issues = {e.id: e.issues_for(python_tag()) for e in entries}
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
