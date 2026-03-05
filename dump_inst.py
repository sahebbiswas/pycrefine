import sys, pycrefine, json
import types
d = pycrefine.get_decompiler('test_files/simple.cpython-39.pyc')
d._disassemble()
import pprint

out = []

def dump_code(code_obj, name):
    dec = pycrefine.Decompiler39(code_obj)
    dec._disassemble()
    insts = []
    for i in dec.instructions:
        argval_repr = repr(i.argval) if i.argval is not None else ''
        if type(i.argval).__name__ == 'code':
            argval_repr = "code:" + i.argval.co_name
            dump_code(i.argval, i.argval.co_name)
        insts.append({"offset": i.offset, "opname": i.opname, "arg": i.arg, "argval": argval_repr})
    out.append({"name": name, "instructions": insts})

dump_code(d.code_obj, "<module>")

with open("dump.json", "w") as f:
    json.dump(out, f, indent=2)
