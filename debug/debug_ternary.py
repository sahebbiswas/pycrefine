"""
debug_ternary.py -- run on Python 3.14, share the FULL output.

    python debug\\debug_ternary.py > debug\\ternary_314.txt 2>&1
"""
import dis, sys, py_compile, tempfile, os, marshal, io

print("Python:", sys.version)
print()

cases = {
    "chain_ternary": (
        "def f(x):\n"
        "    a = 1\n"
        "    b = x if x > 0 else -x\n"
        "    return a + b\n"
    ),
    "jf_ternary": (
        "def test(lnotab):\n"
        "    lnotab = bytes(lnotab) if not isinstance(lnotab, bytes) else lnotab\n"
        "    print(lnotab)\n"
    ),
    "simple_ternary": (
        "def f(x):\n"
        "    y = 1 if x > 0 else 0\n"
        "    return y\n"
    ),
}

for name, src in cases.items():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(src); sp = f.name
    pp = sp + "c"
    py_compile.compile(sp, cfile=pp, doraise=True)
    with open(pp, "rb") as f: data = f.read()
    os.unlink(sp); os.unlink(pp)

    code = None
    for offset_try in (16, 12, 8):
        try:
            obj = marshal.load(io.BytesIO(data[offset_try:]))
            if hasattr(obj, "co_consts"):
                code = obj; break
        except Exception:
            continue

    func = code.co_consts[0] if code else None
    print("=" * 60)
    print(f"CASE: {name}")
    print(src.rstrip())
    print("=" * 60)
    if func:
        print(f"co_varnames: {func.co_varnames}")
        print()
        for i in dis.get_instructions(func):
            jt = ">>" if i.is_jump_target else "  "
            print(f"  {i.offset:4d} {jt} {i.opname:<30} arg={str(i.arg):<8} argval={str(i.argval):<30} is_jt={i.is_jump_target}")
    print()