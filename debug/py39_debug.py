"""
py39_debug.py
=============
Run on Python 3.9.13 and paste the full output.

    python debug\py39_debug.py > debug\dis_out_39.txt 2>&1
"""
import dis, sys, py_compile, tempfile, os, marshal, io

print("Python:", sys.version)
print()

cases = {
    "augmented_assign":  "x = 1\nx += 3\n",
    "while_simple":      "n = 0\nwhile n < 5:\n    n += 1\n",
    "nested_while":      "i = 0\nwhile i < 3:\n    j = 0\n    while j < 2:\n        j += 1\n    i += 1\n",
    "if_simple":         "x = 1\nif x > 0:\n    y = 2\n",
    "if_else":           "x = 1\nif x > 0:\n    y = 1\nelse:\n    y = 0\n",
    "try_except_typed":  "try:\n    x = int('1')\nexcept ValueError:\n    x = 0\n",
    "try_except_as":     "try:\n    x = int('a')\nexcept ValueError as e:\n    x = 0\n",
    "binary_ops":        "x = 4\na = x & 1\nb = x | 1\nc = x ^ 1\nd = x << 1\ne = x >> 1\n",
    "subscript_write":   "a = [1, 2]\na[0] = 9\n",
    "func_simple":       "def add(a, b):\n    return a + b\n",
    "func_default":      "def f(x, y=10):\n    return x + y\n",
    "while_true_break":  "while True:\n    x = 1\n    if x > 0:\n        break\n",
    "for_loop":          "for i in range(3):\n    print(i)\n",
}

def show(name, src):
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(src)
        sp = f.name
    pp = sp + "c"
    py_compile.compile(sp, cfile=pp, doraise=True)
    with open(pp, "rb") as f:
        data = f.read()
    os.unlink(sp)
    os.unlink(pp)

    # Parse header — try offsets 16, 12, 8
    code = None
    for offset in (16, 12, 8):
        try:
            obj = marshal.load(io.BytesIO(data[offset:]))
            if hasattr(obj, "co_code"):
                code = obj
                break
        except Exception:
            continue
    if code is None:
        print(f"=== {name} === FAILED TO LOAD")
        return

    print(f"=== {name} ===")
    print(f"  co_code bytes: {list(code.co_code)}")
    print(f"  co_consts: {code.co_consts}")
    print(f"  co_names:  {code.co_names}")
    print(f"  co_varnames: {code.co_varnames}")
    print()
    for i in dis.get_instructions(code):
        jt = ">>" if i.is_jump_target else "  "
        print(
            f"  {i.offset:3d}  {jt}  {i.opname:<28}"
            f"  arg={str(i.arg):6}  argval={str(i.argval):<25}"
            f"  is_jump_target={i.is_jump_target}"
        )
    print()

for name, src in cases.items():
    show(name, src)