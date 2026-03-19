#!/usr/bin/env python3
"""
hamming.py
==========
Computes the Hamming distance between the original python source and the
decompiled source output from pycrefine. The distance is computed disregarding
mutable elements like comments, string quotes, and imports.
"""

import argparse
import difflib
import os
import py_compile
import re
import sys
import tempfile
from pathlib import Path

_DEBUG = Path(__file__).parent
_HERE  = _DEBUG.parent
sys.path.insert(0, str(_HERE))
sys.path.insert(0, str(_DEBUG))

try:
    from pycrefine import get_decompiler
except ImportError as e:
    sys.exit(f"Cannot import pycrefine: {e}\nMake sure pycrefine.py is in the same directory.")

try:
    from check_coherency import normalise_source
except ImportError as e:
    sys.exit(f"Cannot import check_coherency utilities: {e}")

def is_import_line(line: str) -> bool:
    """Check if a line is an import statement."""
    return bool(re.match(r'^\s*(import|from)\b', line))

def filter_normalised_lines(lines: list[str]) -> list[str]:
    """Remove import lines from normalised lines."""
    return [line for line in lines if not is_import_line(line)]

def compute_hamming_distance(s1: str, s2: str) -> int:
    """Compute standard Hamming distance extended for variable lengths."""
    dist = sum(1 for c1, c2 in zip(s1, s2) if c1 != c2)
    dist += abs(len(s1) - len(s2))
    return dist

def compute_edit_ratio(seq1, seq2) -> float:
    """Compute similarity ratio using SequenceMatcher (returns 0.0 to 1.0)."""
    sm = difflib.SequenceMatcher(None, seq1, seq2)
    return sm.ratio()

def get_hamming_distance_for_file(source_path: str, verbose: bool = False) -> None:
    source_path = str(source_path)
    if not os.path.exists(source_path):
        print(f"Error: file not found: {source_path}", file=sys.stderr)
        return

    with open(source_path, 'r', encoding='utf-8') as f:
        original_text = f.read()

    with tempfile.NamedTemporaryFile(suffix='.pyc', delete=False) as tf:
        pyc_path = tf.name

    try:
        py_compile.compile(source_path, cfile=pyc_path, doraise=True)
        decompiled_text = get_decompiler(pyc_path).decompile()
    except Exception as e:
        print(f"Error during compilation or decompilation: {e}", file=sys.stderr)
        return
    finally:
        if os.path.exists(pyc_path):
            os.unlink(pyc_path)

    orig_lines = filter_normalised_lines(normalise_source(original_text))
    dec_lines = filter_normalised_lines(normalise_source(decompiled_text))

    orig_str = '\n'.join(orig_lines)
    dec_str = '\n'.join(dec_lines)

    dist_char = compute_hamming_distance(orig_str, dec_str)
    max_char_len = max(len(orig_str), len(dec_str))
    hamming_char_pct = 100.0 * (max_char_len - dist_char) / max_char_len if max_char_len > 0 else 100.0
    
    orig_tokens = orig_str.split()
    dec_tokens = dec_str.split()
    dist_word = sum(1 for t1, t2 in zip(orig_tokens, dec_tokens) if t1 != t2) + abs(len(orig_tokens) - len(dec_tokens))
    max_word_len = max(len(orig_tokens), len(dec_tokens))
    hamming_word_pct = 100.0 * (max_word_len - dist_word) / max_word_len if max_word_len > 0 else 100.0

    edit_ratio_char = compute_edit_ratio(orig_str, dec_str) * 100.0
    edit_ratio_lines = compute_edit_ratio(orig_lines, dec_lines) * 100.0

    print(f"Similarity Report for: {source_path}")
    print("-" * 50)
    print("Strict Hamming Similarity (Position-based, sensitive to shifts):")
    print(f"  Character-level : {hamming_char_pct:.1f}%")
    print(f"  Token-level     : {hamming_word_pct:.1f}%")
    print()
    print("Edit/Levenshtein Similarity (Robust to inserts/deletes):")
    print(f"  Character-level : {edit_ratio_char:.1f}%")
    print(f"  Line-level      : {edit_ratio_lines:.1f}%")
    
    if verbose:
        print("\nOriginal Normalized Sample (first 500 chars):")
        print(orig_str[:500])
        print("\nDecompiled Normalized Sample (first 500 chars):")
        print(dec_str[:500])

def main():
    parser = argparse.ArgumentParser(description="Compute Hamming distance between original and decompiled source.")
    parser.add_argument("source", nargs="?", default=str(_HERE / "pycrefine.py"), help="Python source file to score")
    parser.add_argument("-v", "--verbose", action="store_true", help="Print verbose output")
    args = parser.parse_args()
    get_hamming_distance_for_file(args.source, args.verbose)

if __name__ == "__main__":
    main()
