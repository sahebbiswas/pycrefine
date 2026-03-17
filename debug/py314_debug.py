import dis, sys, py_compile, tempfile, os, marshal, io

print("Python:", sys.version)
print()

cases = {
    "except_as":   "try:\n    x = int('a')\nexcept ValueError as e:\n    x = 0\n",
    "except_noas": "try:\n    x = int('a')\nexcept ValueError:\n    x = 0\n",
    "while_loop":  "n = 0\nwhile n < 5:\n    n += 1\n",
}

for name, src in cases.items():
    with tempfile.NamedTemporaryFile(suffix=".py", mode="w", delete=False) as f:
        f.write(src); sp = f.name
    pp = sp + "c"
    py_compile.compile(sp, cfile=pp, doraise=True)
    with open(pp, "rb") as f: data = f.read()
    code = marshal.load(io.BytesIO(data[16:]))
    print(f"=== {name} ===")
    for i in dis.get_instructions(code):
        print(f"  {i.offset:3d}  {'>>':2s}  {i.opname:<30} arg={str(i.arg):6} argval={str(i.argval):<20} is_jump_target={i.is_jump_target}")
    print()
    os.unlink(sp); os.unlink(pp)