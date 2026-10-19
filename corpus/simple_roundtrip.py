import py_compile
import pycrefine
import os

source = """def f(x):
    y = x * 2
    return y
"""

with open("temp.py", "w") as f:
    f.write(source)

py_compile.compile("temp.py", cfile="temp.pyc")
decompiler = pycrefine.get_decompiler("temp.pyc")
decompiler.decompile() # to populate reconstructed
raw = "\n".join(str(s) for s in decompiler.reconstructed).rstrip()
print("=== RAW ===")
print(raw)
out = pycrefine.post_process_source(raw)
print("=== POST ===")
print(out)
