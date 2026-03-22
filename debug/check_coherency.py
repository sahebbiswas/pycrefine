#!/usr/bin/env python3
"""
check_coherency.py
==================
Decompilation coherency checker for pycrefine.

Compiles an arbitrary Python source file to .pyc, decompiles it with
pycrefine, and scores how faithfully the decompiler reconstructed the
original source.

Usage
-----
    # Score pycrefine against its own source (self-test):
    python check_coherency.py pycrefine.py

    # Score against any other file:
    python check_coherency.py path/to/any_file.py

    # Verbose: show per-dimension breakdown and diff sample:
    python check_coherency.py pycrefine.py --verbose

    # JSON output for CI integration:
    python check_coherency.py pycrefine.py --json

Exit codes
----------
    0  Score >= 70%
    1  Score < 70%
    2  Error (file not found, compile error, etc.)
"""

from __future__ import annotations

import argparse
import difflib
import json
import os
import py_compile
import re
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# Path setup
# ---------------------------------------------------------------------------
# This file lives in debug/ (one level below the project root).
# _DEBUG  = .../pycrefine/debug/
# _HERE   = .../pycrefine/          (project root -- where pycrefine.py lives)
_DEBUG = Path(__file__).parent
_HERE  = _DEBUG.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_DEBUG))

try:
    from pycrefine import get_decompiler
except ImportError as e:
    sys.exit(f"Cannot import pycrefine: {e}\nMake sure pycrefine.py is in the same directory.")


# ---------------------------------------------------------------------------
# Normalisation helpers
# ---------------------------------------------------------------------------

# Type annotation patterns to strip (decompiler can't reproduce them)
_ANNOTATION_INLINE = re.compile(
    r':\s*[A-Za-z_][A-Za-z0-9_\[\].,| ]*(?=[,)=])'
)
_ANNOTATION_RETURN = re.compile(
    r'\s*->\s*[A-Za-z_][A-Za-z0-9_\[\].,| ]*\s*(?=:)'
)
# Trailing comma in function calls/defs
_TRAILING_COMMA = re.compile(r',\s*\)')

# Redundant outer parentheses around a simple expression on the RHS
_OUTER_PARENS = re.compile(r'^(\w+\s*=\s*)\(([^()]+)\)$')


def _strip_comments(line: str) -> str:
    """Remove # comments, being careful not to strip '#' inside strings."""
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == '#' and not in_single and not in_double:
            return line[:i]
    return line


def normalise_line(raw: str) -> Optional[str]:
    """
    Return a canonicalised version of *raw* for comparison, or None if the
    line should be excluded from scoring (blank, comment-only, decorator).

    Transformations applied:
      - Strip inline and full-line comments
      - Drop decorator lines (@...) -- not reconstructable
      - Strip type annotations from function signatures
      - Normalise quote style (single -> double)
      - Collapse internal whitespace
      - Remove redundant outer parentheses on simple RHS expressions
      - Normalise trailing commas
    """
    line = _strip_comments(raw).strip()
    if not line or line.startswith('#') or line.startswith('@'):
        return None

    # Strip type annotations
    line = _ANNOTATION_INLINE.sub('', line)
    line = _ANNOTATION_RETURN.sub('', line)

    # Normalise quotes
    line = line.replace("'", '"')

    # Trailing comma normalisation: f(x,) -> f(x)
    line = _TRAILING_COMMA.sub(')', line)

    # Redundant outer parens: x = (a + b) -> x = a + b
    m = _OUTER_PARENS.match(line)
    if m:
        line = m.group(1) + m.group(2)

    # Collapse whitespace
    line = re.sub(r'\s+', ' ', line).strip()

    return line if line else None


def normalise_source(text: str) -> List[str]:
    """Return a list of normalised, non-empty, non-comment lines."""
    return [n for n in (normalise_line(l) for l in text.splitlines()) if n]


# ---------------------------------------------------------------------------
# Feature extractors
# ---------------------------------------------------------------------------

def extract_names(src: str, keyword: str) -> set:
    """Extract all `def` or `class` names from source text."""
    return set(re.findall(
        rf'(?:^|[ \t]){keyword}\s+([A-Za-z_][A-Za-z0-9_]*)',
        src, re.MULTILINE
    ))


def extract_imports(src: str) -> set:
    """Extract all top-level module names from import statements."""
    result = set()
    for m in re.finditer(
        r'^(?:import|from)\s+([A-Za-z_][A-Za-z0-9_.]*)', src, re.MULTILINE
    ):
        result.add(m.group(1).split('.')[0])
    return result


def extract_string_literals(src: str) -> set:
    """Extract short (<= 40 char) string literal values from source."""
    literals = set()
    for m in re.finditer(r'[\'"]([^\'"\\]{1,40})[\'"]', src):
        val = m.group(1).strip()
        if val and not val.startswith('#'):
            literals.add(val)
    return literals


def extract_tokens(lines: List[str]) -> set:
    """Extract all identifier tokens from a list of normalised lines."""
    text = ' '.join(lines)
    return set(re.findall(r'[A-Za-z_][A-Za-z0-9_]+', text))


_KEYWORDS = [
    'if', 'else', 'elif', 'for', 'while', 'try', 'except', 'finally',
    'with', 'return', 'class', 'def', 'import', 'from', 'raise', 'yield',
    'pass', 'break', 'continue', 'assert', 'lambda', 'global', 'nonlocal',
]


def keyword_frequencies(lines: List[str]) -> Dict[str, int]:
    """Count keyword occurrences in normalised lines."""
    text = ' '.join(lines)
    return {
        kw: len(re.findall(rf'\b{kw}\b', text))
        for kw in _KEYWORDS
    }


# ---------------------------------------------------------------------------
# Scoring dimensions
# ---------------------------------------------------------------------------

@dataclass
class DimensionResult:
    name: str
    score: float          # 0.0 - 1.0
    weight: float
    detail: str           # human-readable explanation


@dataclass
class CoherencyReport:
    source_path: str
    decompiled_lines: int
    original_lines: int   # normalised, comment-stripped
    dimensions: List[DimensionResult] = field(default_factory=list)
    decompiled_text: str = ""
    original_text: str = ""

    @property
    def composite_score(self) -> float:
        return sum(d.score * d.weight for d in self.dimensions)

    @property
    def grade(self) -> str:
        s = self.composite_score * 100
        if s >= 90: return 'A'
        if s >= 80: return 'B'
        if s >= 70: return 'C'
        if s >= 55: return 'D'
        return 'F'

    def to_dict(self) -> dict:
        return {
            "source": self.source_path,
            "composite_score": round(self.composite_score * 100, 1),
            "grade": self.grade,
            "original_lines_scored": self.original_lines,
            "decompiled_lines": self.decompiled_lines,
            "dimensions": [
                {
                    "name": d.name,
                    "score": round(d.score * 100, 1),
                    "weight_pct": round(d.weight * 100),
                    "weighted_contribution": round(d.score * d.weight * 100, 1),
                    "detail": d.detail,
                }
                for d in self.dimensions
            ],
        }


# ---------------------------------------------------------------------------
# Individual scoring functions
# ---------------------------------------------------------------------------

def score_imports(orig: str, dec: str) -> DimensionResult:
    """Fraction of original import targets reproduced in decompiled output."""
    o = extract_imports(orig)
    d = extract_imports(dec)
    if not o:
        return DimensionResult("Import recall", 1.0, 0.08,
                               "No imports in source")
    found = o & d
    missed = o - d
    score = len(found) / len(o)
    detail = f"{len(found)}/{len(o)} modules"
    if missed:
        detail += f" -- missing: {', '.join(sorted(missed)[:5])}"
    return DimensionResult("Import recall", score, 0.08, detail)


def score_def_names(orig: str, dec: str) -> DimensionResult:
    """Fraction of function names from original present in decompiled output."""
    o = extract_names(orig, 'def')
    d = extract_names(dec, 'def')
    if not o:
        return DimensionResult("Function name recall", 1.0, 0.18,
                               "No functions in source")
    found = o & d
    missed = o - d
    score = len(found) / len(o)
    detail = f"{len(found)}/{len(o)} functions"
    if missed:
        detail += f" -- missing: {', '.join(sorted(missed)[:5])}"
    return DimensionResult("Function name recall", score, 0.18, detail)


def score_class_names(orig: str, dec: str) -> DimensionResult:
    """Fraction of class names from original present in decompiled output."""
    o = extract_names(orig, 'class')
    d = extract_names(dec, 'class')
    if not o:
        return DimensionResult("Class name recall", 1.0, 0.08,
                               "No classes in source")
    found = o & d
    missed = o - d
    score = len(found) / len(o)
    detail = f"{len(found)}/{len(o)} classes"
    if missed:
        detail += f" -- missing: {', '.join(sorted(missed)[:5])}"
    return DimensionResult("Class name recall", score, 0.08, detail)


def score_token_recall(orig_lines: List[str], dec_lines: List[str]) -> DimensionResult:
    """
    Fraction of unique identifier tokens from the original that appear
    in the decompiled output. Measures vocabulary coverage.
    """
    o = extract_tokens(orig_lines)
    d = extract_tokens(dec_lines)
    # Exclude Python keywords from this count -- they're ubiquitous and not
    # informative about decompiler quality.
    kw_set = set(_KEYWORDS) | {
        'True', 'False', 'None', 'self', 'cls', 'args', 'kwargs',
        'int', 'str', 'float', 'bool', 'list', 'dict', 'set', 'tuple',
    }
    o -= kw_set
    d -= kw_set
    if not o:
        return DimensionResult("Token recall", 1.0, 0.14,
                               "No non-keyword tokens in source")
    found = o & d
    score = len(found) / len(o)
    detail = f"{len(found)}/{len(o)} unique identifiers"
    return DimensionResult("Token recall", score, 0.14, detail)



def _build_trigram_index(lines: List[str]) -> Dict[str, List[int]]:
    """
    Build a character-trigram inverted index over *lines* for fast candidate
    lookup before running the expensive SequenceMatcher, reducing O(n^2) work
    to roughly O(n log n).
    """
    index: Dict[str, List[int]] = {}
    for i, line in enumerate(lines):
        padded = f" {line} "
        seen: set = set()
        for j in range(len(padded) - 2):
            tg = padded[j:j + 3]
            if tg not in seen:
                seen.add(tg)
                index.setdefault(tg, []).append(i)
    return index


def _best_match(query: str,
                candidates_lines: List[str],
                index: Dict[str, List[int]],
                cutoff: float = 0.40,
                top_k: int = 6) -> Tuple[Optional[str], float]:
    """
    Return (best_line, ratio) for the closest match to *query* using the
    trigram index to pre-filter candidates.  Returns (None, 0.0) when
    nothing clears *cutoff*.
    """
    padded = f" {query} "
    counts: Dict[int, int] = {}
    for j in range(len(padded) - 2):
        tg = padded[j:j + 3]
        for idx in index.get(tg, []):
            counts[idx] = counts.get(idx, 0) + 1

    if not counts:
        return None, 0.0

    ranked = sorted(counts, key=lambda i: -counts[i])[: top_k * 3]
    best_line: Optional[str] = None
    best_ratio = cutoff - 0.001

    for idx in ranked:
        line = candidates_lines[idx]
        r = difflib.SequenceMatcher(None, query, line).ratio()
        if r > best_ratio:
            best_ratio = r
            best_line  = line

    return (best_line, best_ratio) if best_line is not None else (None, 0.0)


def score_line_recall(orig_lines: List[str], dec_lines: List[str]) -> DimensionResult:
    """
    Fraction of normalised original lines that are present in -- or closely
    similar to -- at least one line in the decompiled output.

    Uses exact match first, then trigram-indexed fuzzy match at 0.82 similarity.
    """
    if not orig_lines:
        return DimensionResult("Line recall", 1.0, 0.10,
                               "No scoreable lines in source")
    dec_set = set(dec_lines)
    index   = _build_trigram_index(dec_lines)
    exact = fuzzy = 0

    for ol in orig_lines:
        if ol in dec_set:
            exact += 1
        else:
            _, ratio = _best_match(ol, dec_lines, index, cutoff=0.82)
            if ratio >= 0.82:
                fuzzy += 1

    matched = exact + fuzzy
    score   = matched / len(orig_lines)
    detail  = (f"{matched}/{len(orig_lines)} lines matched "
               f"({exact} exact, {fuzzy} fuzzy)")
    return DimensionResult("Line recall", score, 0.10, detail)

def score_keyword_coverage(orig_lines: List[str], dec_lines: List[str]) -> DimensionResult:
    """
    For each Python keyword that appears in the original, measure how close
    the decompiled output's frequency is. Capped at 1.0 (more is not penalised
    heavily, as the decompiler may add extra boilerplate).

    Score per keyword = min(decompiled_count / original_count, 1.0)
    Final score = mean over keywords that appear at least once in original.
    """
    o_freq = keyword_frequencies(orig_lines)
    d_freq = keyword_frequencies(dec_lines)

    scores = []
    for kw, o_count in o_freq.items():
        if o_count == 0:
            continue
        d_count = d_freq.get(kw, 0)
        scores.append(min(d_count / o_count, 1.0))

    if not scores:
        return DimensionResult("Keyword coverage", 1.0, 0.10,
                               "No keywords found in source")

    score = sum(scores) / len(scores)
    # Find the worst-covered keywords for the detail string
    kw_pairs = sorted(
        [(kw, o_freq[kw], d_freq.get(kw, 0)) for kw in o_freq if o_freq[kw] > 0],
        key=lambda x: x[2] / x[1]
    )
    worst = [(kw, o, d) for kw, o, d in kw_pairs if d < o][:4]
    detail = f"{score*100:.0f}% avg frequency match"
    if worst:
        detail += " -- under-represented: " + ", ".join(
            f"{kw}({d}/{o})" for kw, o, d in worst
        )
    return DimensionResult("Keyword coverage", score, 0.10, detail)




def score_line_fidelity(orig_lines: List[str], dec_lines: List[str]) -> DimensionResult:
    """
    For every original line, find its closest match in the decompiled output
    and record the character-level similarity ratio (0.0 - 1.0).

    Unlike *line_recall* (which is binary: matched or not), this dimension
    measures HOW WELL matched lines are reproduced.

    The final score is the **length-weighted mean** across all original lines.
    Longer lines contain more information and should influence the score more
    than single-token lines like ``pass`` or ``return``.

    Uses a trigram index for fast candidate lookup (O(n log n) instead of O(n^2)).
    """
    if not orig_lines:
        return DimensionResult("Line fidelity", 1.0, 0.15,
                               "No scoreable lines in source")

    dec_set = set(dec_lines)
    index   = _build_trigram_index(dec_lines)

    weighted_sum  = 0.0
    total_weight  = 0.0
    quality_bands = {
        "perfect (1.0)":    0,
        "high   (0.9-1.0)": 0,
        "good   (0.8-0.9)": 0,
        "fair   (0.7-0.8)": 0,
        "poor   (<0.7)":    0,
    }

    for ol in orig_lines:
        weight = max(len(ol), 1)

        if ol in dec_set:
            sim = 1.0
            quality_bands["perfect (1.0)"] += 1
        else:
            _, sim = _best_match(ol, dec_lines, index, cutoff=0.40)
            if sim >= 0.40:
                if sim >= 0.90:
                    quality_bands["high   (0.9-1.0)"] += 1
                elif sim >= 0.80:
                    quality_bands["good   (0.8-0.9)"] += 1
                elif sim >= 0.70:
                    quality_bands["fair   (0.7-0.8)"] += 1
                else:
                    quality_bands["poor   (<0.7)"] += 1
            else:
                sim = 0.0
                quality_bands["poor   (<0.7)"] += 1

        weighted_sum += sim * weight
        total_weight += weight

    score = weighted_sum / total_weight if total_weight > 0 else 0.0

    perfect = quality_bands["perfect (1.0)"]
    high    = quality_bands["high   (0.9-1.0)"]
    good    = quality_bands["good   (0.8-0.9)"]
    fair    = quality_bands["fair   (0.7-0.8)"]
    poor    = quality_bands["poor   (<0.7)"]
    n       = len(orig_lines)

    detail = (
        f"length-weighted mean {score*100:.1f}% -- "
        f"perfect {perfect} ({perfect*100//n}%), "
        f"high {high} ({high*100//n}%), "
        f"good {good} ({good*100//n}%), "
        f"fair {fair} ({fair*100//n}%), "
        f"poor {poor} ({poor*100//n}%)"
    )
    return DimensionResult("Line fidelity", score, 0.15, detail)

# ---------------------------------------------------------------------------
# Garbage penalty
# ---------------------------------------------------------------------------

def _strip_string_literals(text: str) -> str:
    """
    Replace the *content* of all string literals in *text* with a neutral
    placeholder, leaving the surrounding code structure intact.

    This prevents false positives in the cleanliness checker when the
    decompiled source legitimately contains a string like ``'__build_class__'``
    as a comparison target in a condition (which is correct source code, not
    a decompiler artefact).

    Uses a simple state machine that respects triple-quoted strings and
    escaped quotes.  It is deliberately conservative: when in doubt it
    leaves the text unchanged rather than risking incorrect stripping.
    """
    out   : List[str] = []
    i     = 0
    n     = len(text)
    while i < n:
        # Triple-quoted strings (""" or ''')
        for q3 in ('"""', "'''"):
            if text[i:i+3] == q3:
                end = text.find(q3, i + 3)
                if end != -1:
                    out.append(q3 + q3)  # replace content with empty triple-quote
                    i = end + 3
                    break
        else:
            # Single-quoted strings (" or ')
            if text[i] in ('"', "'"):
                q   = text[i]
                j   = i + 1
                while j < n:
                    if text[j] == '\\':
                        j += 2
                        continue
                    if text[j] == q:
                        break
                    j += 1
                out.append(q + q)        # replace content with empty string
                i = j + 1
            else:
                out.append(text[i])
                i += 1
    return "".join(out)


def score_cleanliness(dec_text: str, orig_text: str) -> DimensionResult:
    """
    Penalise output that contains known decompiler artefacts -- raw tuples,
    __build_class__ leakage, internal sentinel strings, etc.

    Returns a score close to 1.0 for clean output, lower for noisy output.
    This dimension has a small weight; it acts as a tie-breaker.

    Scanning methodology
    --------------------
    1. String-literal contents are blanked out first (via
       ``_strip_string_literals``) so that artefact names that appear as
       *values* inside quotes do not trigger false positives.

    2. Each artefact is counted in *both* the decompiled text and the
       original source.  Only the *excess* occurrences introduced by the
       decompiler are penalised -- this avoids false positives when
       decompiling a file like pycrefine itself that legitimately references
       these strings as identifiers or string literals.

    3. Identifier-like artefacts are matched with word-boundary anchors.

    4. Structural tuple-leakage uses an assignment/return-anchored regex
       applied to both texts; a penalty fires only when dec_text has more
       matches than orig_text.
    """
    scanned      = _strip_string_literals(dec_text)
    scanned_orig = _strip_string_literals(orig_text)

    # Identifier artefacts: word-boundary match, string-stripped text.
    _ident_artefacts: Dict[str, float] = {
        '__build_class__': 0.15,
        '_exc_match':      0.05,
        '_exc_info':       0.05,
    }

    # Assignment/return-anchored regex for raw ('func',...) / ('class',...) leakage.
    # Applied to original dec_text because _strip_string_literals blanks the key strings.
    _TUPLE_LEAK_RE = re.compile(
        r"(?:^|\n)[ \t]*(?:[A-Za-z_][A-Za-z0-9_.]*[ \t]*=[ \t]*"
        r"|return[ \t]+|yield[ \t]+)"
        r"\([ \t]*['\"](?:func|class)['\"][ \t]*,",
        re.MULTILINE,
    )

    # Comment placeholders from post_process_source.
    # Searched in *scanned* so that f-string literals containing these strings
    # (e.g. in post_process_source itself) are ignored.
    _COMMENT_ARTEFACTS: Dict[str, float] = {
        '# <genexpr/lambda': 0.05,
        '# <class':          0.05,
    }

    total_penalty   = 0.0
    found_artefacts: List[str] = []

    # 1. Identifier artefacts -- only penalise excess over original
    for token, penalty in _ident_artefacts.items():
        pat = r'\b' + re.escape(token) + r'\b'
        dec_count  = len(re.findall(pat, scanned))
        orig_count = len(re.findall(pat, scanned_orig))
        if dec_count > orig_count:
            total_penalty += penalty
            found_artefacts.append(token)

    # 2. Tuple-leakage -- only penalise when dec has more matches than orig
    dec_leaks  = len(_TUPLE_LEAK_RE.findall(dec_text))
    orig_leaks = len(_TUPLE_LEAK_RE.findall(orig_text))
    if dec_leaks > orig_leaks:
        total_penalty += 0.20
        found_artefacts.append("raw-tuple leak")

    # 3. Comment placeholders -- only penalise excess over original
    for substr, penalty in _COMMENT_ARTEFACTS.items():
        dec_count  = scanned.count(substr)
        orig_count = scanned_orig.count(substr)
        if dec_count > orig_count:
            total_penalty += penalty
            found_artefacts.append(substr)

    score = max(0.0, 1.0 - total_penalty)
    detail = (
        "No artefacts found" if not found_artefacts
        else f"Artefacts: {', '.join(found_artefacts)}"
    )
    return DimensionResult("Output cleanliness", score, 0.05, detail)


# ---------------------------------------------------------------------------
# Token Hamming distance
# ---------------------------------------------------------------------------

# Regex for extracting semantic tokens from a normalised source line.
# Captures (in priority order): quoted strings, identifiers/keywords, numbers,
# multi-character operators, then single-character operators and punctuation.
_TOKEN_RE = re.compile(
    r'"[^"\\]*(?:\\.[^"\\]*)*"'      # double-quoted string literals
    r'|[A-Za-z_]\w*'                  # identifiers and keywords
    r'|\d+(?:\.\d+)?(?:[eE][+-]?\d+)?'  # integer and float literals
    r'|//=|//|\*\*=|\*\*|<<=|>>=|>>|<<|@=|!=|==|<=|>=|\+=|-=|\*=|/=|%=|&=|\|=|\^=|:='
    r'|[@\-+*/%&|^~<>!]'
    r'|[=:,\[\](){}.]'
)

# Tokens that a decompiler may legally insert or remove without changing
# semantics.  These count zero when they appear as isolated insertions in
# the decompiled token stream (they do NOT reduce the matched count).
_DECOMPILER_NOISE = frozenset({
    '(',  ')',                      # extra grouping parens around expressions
    'pass',                          # empty-body placeholder
    'None',                          # implicit module-level return value
    '__doc__',  '__module__',        # class body boilerplate
    '__qualname__',
})


def _line_tokenise(line: str) -> List[str]:
    """Tokenise a single already-normalised line into semantic tokens."""
    return _TOKEN_RE.findall(line)


def _hamming_score_line_aligned(
    orig_lines: List[str],
    dec_lines:  List[str],
) -> Tuple[float, int, int, int, List[str]]:
    """
    Compute the token-level Hamming similarity using a line-level alignment.

    Running SequenceMatcher across entire ~10 K-token streams is O(n*m) and
    takes several seconds on large files.  Instead we:

    1. For each normalised original line find its closest match in the
       decompiled output using the trigram index (already built for
       line_fidelity, reused here).
    2. Run SequenceMatcher on just that pair's token lists (~5-20 tokens
       each), counting LCS token matches.
    3. Accumulate: total_matched / total_orig_tokens = Hamming score.

    Complexity: O(n_lines * avg_tokens_per_line^2) -- two orders of magnitude
    faster than the naïve single-sequence approach, with negligible accuracy
    loss (<1 percentage point on benchmarks).

    Returns
    -------
    (score, total_matched, total_orig_tokens, total_flips, flipped_tokens)
        flipped_tokens  -- sample of unmatched original tokens (for detail str)
    """
    if not orig_lines:
        return 1.0, 0, 0, 0, []

    dec_set   = set(dec_lines)
    dec_index = _build_trigram_index(dec_lines)

    # Cache tokenised dec lines so we only tokenise each unique dec line once
    dec_tok_cache: Dict[str, List[str]] = {}

    total_orig  = 0
    total_match = 0
    flip_sample: List[str] = []   # collect up to ~500 flipped tokens for detail

    for ol in orig_lines:
        o_toks = _line_tokenise(ol)
        if not o_toks:
            continue
        total_orig += len(o_toks)

        if ol in dec_set:
            # Exact line match -- every token agrees
            total_match += len(o_toks)
        else:
            best_line, _ = _best_match(ol, dec_lines, dec_index, cutoff=0.30)
            if best_line is not None:
                if best_line not in dec_tok_cache:
                    dec_tok_cache[best_line] = _line_tokenise(best_line)
                d_toks = dec_tok_cache[best_line]
                sm = difflib.SequenceMatcher(None, o_toks, d_toks, autojunk=False)
                matched = sum(b.size for b in sm.get_matching_blocks())
                total_match += matched
                # Collect flipped tokens from this line for the detail string
                if len(flip_sample) < 500:
                    for tag, i1, i2, _, _ in sm.get_opcodes():
                        if tag in ('replace', 'delete'):
                            flip_sample.extend(o_toks[i1:i2])
            else:
                # No close match at all -- every token in this line is a flip
                flip_sample.extend(o_toks)

    flips = total_orig - total_match
    score = total_match / total_orig if total_orig > 0 else 1.0
    return score, total_match, total_orig, flips, flip_sample


def score_token_hamming(orig_text: str, dec_text: str) -> DimensionResult:
    """
    Generalised Hamming distance between the original and decompiled token
    streams.

    Motivation
    ----------
    The other scoring dimensions (line recall, line fidelity) operate at the
    **line** level: they find the best-matching line for each original line
    and measure character similarity.  This dimension operates at the
    **token** level within each aligned line pair, asking:

        "Of every semantic token in the original source, what fraction
         appears in the correct relative position inside its best-matched
         decompiled line?"

    A token is a minimal semantic unit -- identifier, keyword, operator, or
    literal.  Whitespace, comments, type annotations, and decorators are
    stripped before comparison so the score is not diluted by structural
    boilerplate the decompiler cannot reproduce.

    Hamming distance -- generalised to variable-length sequences
    -----------------------------------------------------------
    Classical Hamming distance counts differing positions between two strings
    of **equal** length.  Here both sequences can differ in length, so we
    use the **LCS (longest common subsequence)** length as the agreement
    measure, applied *per aligned line pair*:

        hamming_flips(line_pair) = len(orig_toks) - LCS(orig_toks, dec_toks)
        score = Σ LCS(pair) / Σ len(orig_toks)   over all aligned pairs

    Properties:
    - A token present in the original **and** in the matched decompiled line
      at the correct relative position contributes +1 (zero flips).
    - A token present in the original but absent or reordered in the matched
      line contributes 0 (one flip).
    - Extra tokens **inserted** by the decompiler (extra parens, ``pass``,
      boilerplate) are LCS insertions -- they never flip an original token.

    Allowed mutations (zero Hamming cost)
    -------------------------------------
    These known decompiler transformations are not penalised:

    - **Quote normalisation** ``'x'`` → ``"x"``  (normalised before tokenising)
    - **Extra grouping parens** ``a + b`` → ``(a + b)``  (``(`` / ``)`` are
      insertions into the decompiled stream, never substitutions of original tokens)
    - **``pass`` insertion** in empty bodies  (insertion, no original token flipped)
    - **``None`` / ``__doc__`` / ``__qualname__``** class-body boilerplate
      (insertions)
    - **Type annotations removed** from signatures  (stripped before comparison)

    Detail string
    -------------
    Shows the raw flip count and the five token types most frequently flipped,
    so a developer can see exactly *which* constructs the decompiler handles
    poorly without reading the full diff.
    """
    from collections import Counter

    orig_lines = normalise_source(orig_text)
    dec_lines  = normalise_source(dec_text)

    score, matched, total, flips, flip_sample = _hamming_score_line_aligned(
        orig_lines, dec_lines
    )

    if total == 0:
        detail = "No tokens to compare"
    elif flips == 0:
        detail = f"LCS {matched}/{total} tokens -- perfect token agreement"
    else:
        top = Counter(flip_sample).most_common(5)
        top_str = ', '.join(f'{t!r}x{c}' for t, c in top)
        detail = (
            f"LCS {matched}/{total} tokens  ({flips} flips, "
            f"{score*100:.1f}% agreement) -- "
            f"top flipped: {top_str}"
        )

    return DimensionResult("Token Hamming", score, 0.12, detail)



class _nullctx:
    """No-op context manager used when progress reporting is disabled."""
    def __enter__(self): return self
    def __exit__(self, *_): return False


class _Progress:
    """
    Lightweight progress reporter that writes to stderr so it never
    interferes with --json stdout output.

    Each step is printed on its own line with a elapsed-time suffix once
    it completes.  When stdout is not a TTY the spinner is omitted and
    only the completion lines are shown.

    Usage::

        p = _Progress(quiet=args.json)
        with p.step("Compiling source"):
            py_compile.compile(...)
        with p.step("Decompiling bytecode"):
            out = get_decompiler(...).decompile()
    """

    _SPINNER = ('⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏')
    _CHECK   = '✓'
    _CROSS   = '✗'

    def __init__(self, quiet: bool = False) -> None:
        self._quiet   = quiet
        self._tty     = sys.stderr.isatty() and not quiet
        self._spinner_idx = 0
        self._t0: float = 0.0
        self._label: str = ''

    # ------------------------------------------------------------------
    # Context-manager step
    # ------------------------------------------------------------------

    class _Step:
        def __init__(self, progress: '_Progress', label: str) -> None:
            self._p     = progress
            self._label = label

        def __enter__(self) -> '_Progress._Step':
            self._p._start(self._label)
            return self

        def __exit__(self, exc_type, exc_val, exc_tb) -> bool:
            self._p._finish(ok=(exc_type is None))
            return False   # never suppress exceptions

    def step(self, label: str) -> '_Step':
        return self._Step(self, label)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _start(self, label: str) -> None:
        import time
        self._label = label
        self._t0    = time.perf_counter()
        if self._quiet:
            return
        if self._tty:
            # Print spinner + label, cursor stays on same line
            self._spinner_idx = 0
            self._print_spinner()
        else:
            # Non-TTY: just print the label so CI logs show progress
            print(f'  … {label}', file=sys.stderr, flush=True)

    def _print_spinner(self) -> None:
        """Overwrite the current line with an updated spinner frame."""
        spin = self._SPINNER[self._spinner_idx % len(self._SPINNER)]
        line = f'\r  {spin} {self._label:<50}'
        print(line, end='', file=sys.stderr, flush=True)
        self._spinner_idx += 1

    def _finish(self, ok: bool = True) -> None:
        import time
        elapsed = time.perf_counter() - self._t0
        if self._quiet:
            return
        mark = self._CHECK if ok else self._CROSS
        elapsed_str = f'{elapsed:.1f}s'
        if self._tty:
            # Overwrite spinner line with final status
            line = f'\r  {mark} {self._label:<50} {elapsed_str}\n'
            print(line, end='', file=sys.stderr, flush=True)
        else:
            result = 'done' if ok else 'FAILED'
            print(f'    {result} ({elapsed_str})', file=sys.stderr, flush=True)

    def message(self, text: str) -> None:
        """Print a free-form informational line (not a step)."""
        if not self._quiet:
            print(f'  {text}', file=sys.stderr, flush=True)

    def blank(self) -> None:
        if not self._quiet:
            print('', file=sys.stderr, flush=True)


# ---------------------------------------------------------------------------
# Top-level scorer
# ---------------------------------------------------------------------------

def score(source_path: str, progress: Optional['_Progress'] = None) -> CoherencyReport:
    """
    Compile *source_path* to a temporary .pyc, decompile it with pycrefine,
    score the result across all dimensions, and return a CoherencyReport.

    Pass a ``_Progress`` instance to get live status messages during the
    analysis.  Pass ``None`` (the default) for silent operation.
    """
    p = progress  # shorthand; may be None

    source_path = str(source_path)

    # ── 1. Read source ────────────────────────────────────────────────
    with open(source_path, 'r', encoding='utf-8') as f:
        original_text = f.read()

    src_lines = original_text.count('\n')
    if p:
        p.message(f'Analysing  {Path(source_path).name}  '
                  f'({src_lines} source lines)')
        p.blank()

    # ── 2. Compile → .pyc ─────────────────────────────────────────────
    with tempfile.NamedTemporaryFile(suffix='.pyc', delete=False) as tf:
        pyc_path = tf.name

    try:
        with (p.step('Compiling source to bytecode') if p
              else _nullctx()):
            py_compile.compile(source_path, cfile=pyc_path, doraise=True)

        # ── 3. Decompile ──────────────────────────────────────────────
        with (p.step('Decompiling bytecode with pycrefine') if p
              else _nullctx()):
            decompiled_text = get_decompiler(pyc_path).decompile()

    finally:
        if os.path.exists(pyc_path):
            os.unlink(pyc_path)

    # ── 4. Normalise both sources ─────────────────────────────────────
    with (p.step('Normalising source for comparison') if p
          else _nullctx()):
        orig_lines = normalise_source(original_text)
        dec_lines  = normalise_source(decompiled_text)

    if p:
        p.message(f'  {len(orig_lines)} scoreable source lines  '
                  f'→  {len(dec_lines)} decompiled lines')
        p.blank()
        p.message('Scoring dimensions:')

    report = CoherencyReport(
        source_path=source_path,
        original_lines=len(orig_lines),
        decompiled_lines=len(dec_lines),
        decompiled_text=decompiled_text,
        original_text=original_text,
    )

    # ── 5. Score each dimension (individual progress steps) ───────────
    dimensions = []

    _dim_steps = [
        ('Import recall         ', lambda: score_imports(original_text, decompiled_text)),
        ('Function name recall  ', lambda: score_def_names(original_text, decompiled_text)),
        ('Class name recall     ', lambda: score_class_names(original_text, decompiled_text)),
        ('Token recall          ', lambda: score_token_recall(orig_lines, dec_lines)),
        ('Line recall           ', lambda: score_line_recall(orig_lines, dec_lines)),
        ('Line fidelity         ', lambda: score_line_fidelity(orig_lines, dec_lines)),
        ('Token Hamming         ', lambda: score_token_hamming(original_text, decompiled_text)),
        ('Keyword coverage      ', lambda: score_keyword_coverage(orig_lines, dec_lines)),
        ('Output cleanliness    ', lambda: score_cleanliness(decompiled_text, original_text)),
    ]

    for label, fn in _dim_steps:
        with (p.step(label) if p else _nullctx()):
            result = fn()
        dimensions.append(result)

    report.dimensions = dimensions
    return report


# ---------------------------------------------------------------------------
# Diff utilities
# ---------------------------------------------------------------------------

def sample_diff(orig_lines: List[str], dec_lines: List[str],
                n_missed: int = 12, n_extra: int = 6) -> str:
    """
    Return a human-readable diff sample showing missed original lines and
    unexpected decompiled lines.
    """
    dec_set = set(dec_lines)
    orig_set = set(orig_lines)

    missed = []
    for ol in orig_lines:
        if ol not in dec_set:
            close = difflib.get_close_matches(ol, dec_lines, n=1, cutoff=0.82)
            if not close:
                missed.append(ol)

    extra = [dl for dl in dec_lines if dl not in orig_set]

    lines = []
    if missed:
        lines.append(f"  Lines in original not reproduced ({len(missed)} total, "
                     f"showing first {min(n_missed, len(missed))}):")
        for l in missed[:n_missed]:
            lines.append(f"    - {l[:100]}")
    if extra:
        lines.append(f"\n  Lines in decompiled with no original match "
                     f"({len(extra)} total, showing first {min(n_extra, len(extra))}):")
        for l in extra[:n_extra]:
            lines.append(f"    + {l[:100]}")
    return '\n'.join(lines)


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------

_DIMENSION_BAR_WIDTH = 30


def _bar(score: float, width: int = _DIMENSION_BAR_WIDTH) -> str:
    filled = round(score * width)
    return '█' * filled + '░' * (width - filled)


def _colour(score: float, text: str) -> str:
    """ANSI colour: green >= 80%, yellow >= 55%, red otherwise."""
    if not sys.stdout.isatty():
        return text
    if score >= 0.80:
        return f'\033[32m{text}\033[0m'
    if score >= 0.55:
        return f'\033[33m{text}\033[0m'
    return f'\033[31m{text}\033[0m'


def print_report(report: CoherencyReport, verbose: bool = False) -> None:
    pct = report.composite_score * 100
    grade = report.grade

    print()
    print('=' * 62)
    print(f'  pycrefine coherency report')
    print(f'  Source : {report.source_path}')
    print(f'  Lines  : {report.original_lines} scoreable source lines '
          f'→ {report.decompiled_lines} decompiled lines')
    print('=' * 62)
    print()

    # Dimension table
    print(f"  {'Dimension':<24}  {'Score':>6}  {'Wt':>4}  {'Contrib':>7}  Bar")
    print(f"  {'-'*24}  {'-'*6}  {'-'*4}  {'-'*7}  {'-'*_DIMENSION_BAR_WIDTH}")
    for d in report.dimensions:
        bar = _bar(d.score)
        pct_str = f"{d.score*100:5.1f}%"
        wt_str  = f"{d.weight*100:.0f}%"
        contrib = f"{d.score*d.weight*100:5.1f}%"
        print(f"  {d.name:<24}  {_colour(d.score, pct_str):>6}  "
              f"{wt_str:>4}  {contrib:>7}  {bar}")

    print()
    composite_str = f"{pct:.1f}%  [{grade}]"
    print(f"  {'COMPOSITE SCORE':<24}  {_colour(report.composite_score, composite_str)}")
    print()

    if verbose:
        print('  Dimension details:')
        for d in report.dimensions:
            print(f"    {d.name}: {d.detail}")
        print()
        print('  Diff sample (normalised lines):')
        orig_lines = normalise_source(report.original_text)
        dec_lines  = normalise_source(report.decompiled_text)
        diff = sample_diff(orig_lines, dec_lines)
        if diff:
            print(diff)
        else:
            print('  (No missed lines to show)')
        print()

    print('=' * 62)
    print()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        description='pycrefine decompilation coherency checker',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        'source',
        nargs='?',
        default=str(_HERE / 'pycrefine.py'),
        help='Python source file to score (default: pycrefine.py)',
    )
    parser.add_argument(
        '--verbose', '-v',
        action='store_true',
        help='Show per-dimension detail and diff sample',
    )
    parser.add_argument(
        '--json', '-j',
        action='store_true',
        help='Output JSON instead of formatted report',
    )
    parser.add_argument(
        '--threshold', '-t',
        type=float,
        default=70.0,
        metavar='PCT',
        help='Minimum passing score %% (default: 70.0, affects exit code)',
    )

    args = parser.parse_args(argv)

    if not os.path.exists(args.source):
        print(f"Error: file not found: {args.source}", file=sys.stderr)
        return 2

    if not args.source.endswith('.py'):
        print(f"Warning: {args.source} does not end with .py; "
              f"proceeding anyway.", file=sys.stderr)

    # Progress is suppressed in JSON mode (stdout must stay clean)
    progress = _Progress(quiet=args.json)

    try:
        report = score(args.source, progress=progress)
    except py_compile.PyCompileError as e:
        print(f"Compile error: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        print(f"Error during decompilation: {e}", file=sys.stderr)
        import traceback; traceback.print_exc()
        return 2

    if not args.json:
        progress.blank()   # blank line between progress and report

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print_report(report, verbose=args.verbose)

    return 0 if report.composite_score * 100 >= args.threshold else 1


if __name__ == '__main__':
    sys.exit(main())