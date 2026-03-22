import re

def post_process_source(source: str) -> str:
    """Clean up decompiled output to be more Pythonic."""
    lines = source.split('\n')
    out_lines = []
    
    current_imports = []
    current_froms = {}
    current_indent = None
    
    def flush_imports():
        nonlocal current_imports, current_froms, current_indent
        if current_imports:
            unique_mods = []
            for m in current_imports:
                if m not in unique_mods:
                    unique_mods.append(m)
            out_lines.append(f"{current_indent}import {', '.join(unique_mods)}")
            current_imports.clear()
            
        for mod, syms in current_froms.items():
            unique_syms = []
            for s in syms:
                if s not in unique_syms:
                    unique_syms.append(s)
            out_lines.append(f"{current_indent}from {mod} import {', '.join(unique_syms)}")
        current_froms.clear()
        current_indent = None

    assignment_parens_re = re.compile(r'^(\s*[A-Za-z_][A-Za-z0-9_.]*\s*(?:\+|-|\*|/|//|%|&|\||\^|<<|>>)?=\s*)\(([^,()]+)\)$')
    return_parens_re = re.compile(r'^(\s*return\s+)\(([^,()]+)\)$')
    if_parens_re = re.compile(r'^(\s*(?:if|elif)\s+)\(([^,()]+)\):$')
    while_parens_re = re.compile(r'^(\s*while\s+)\(([^,()]+)\):$')
    
    for line in lines:
        imp_m = re.match(r'^([ \t]*)import\s+(.+)$', line)
        from_m = re.match(r'^([ \t]*)from\s+([A-Za-z0-9_.]+)\s+import\s+(.+)$', line)
        
        handled = False
        if imp_m:
            indent, mods = imp_m.groups()
            mods_list = [m.strip() for m in mods.split(',')]
            if indent == current_indent and not current_froms:
                current_imports.extend(mods_list)
                handled = True
            elif current_indent is None:
                current_indent = indent
                current_imports.extend(mods_list)
                handled = True
                
        elif from_m:
            indent, mod, syms = from_m.groups()
            sym_list = [s.strip() for s in syms.split(',')]
            if indent == current_indent and not current_imports:
                current_froms.setdefault(mod, []).extend(sym_list)
                handled = True
            elif current_indent is None:
                current_indent = indent
                current_froms.setdefault(mod, []).extend(sym_list)
                handled = True
                
        if not handled:
            flush_imports()
            if imp_m:
                indent, mods = imp_m.groups()
                current_indent = indent
                current_imports.extend([m.strip() for m in mods.split(',')])
            elif from_m:
                indent, mod, syms = from_m.groups()
                current_indent = indent
                current_froms.setdefault(mod, []).extend([s.strip() for s in syms.split(',')])
            else:
                line = assignment_parens_re.sub(r'\1\2', line)
                line = return_parens_re.sub(r'\1\2', line)
                line = if_parens_re.sub(r'\1\2:', line)
                line = while_parens_re.sub(r'\1\2:', line)
                out_lines.append(line)
                
    flush_imports()
    
    text = '\n'.join(out_lines)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip() + '\n'

test_case = """
import os
import sys

from collections import defaultdict
from collections import Counter
from math import ceil

def foo():
    import math
    import json
    x = (y + z)
    return (x)
    if (x == y):
        while (True):
            pass
"""
print("OUTPUT:")
print(post_process_source(test_case))
