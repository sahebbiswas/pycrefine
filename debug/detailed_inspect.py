
import types
import struct
import sys
import pycrefine

def dump_code(c, indent=""):
    if not isinstance(c, types.CodeType):
        print(f"{indent}!! Not a code object: {type(c)} {repr(c)[:60]}")
        return
        
    print(f"{indent}CODE: {c.co_name}")
    print(f"{indent}  names ({len(c.co_names)}): {c.co_names}")
    print(f"{indent}  varnames ({len(c.co_varnames)}): {c.co_varnames}")
    print(f"{indent}  consts ({len(c.co_consts)}):")
    for i, co in enumerate(c.co_consts):
        print(f"{indent}    [{i}]: {type(co)} {repr(co)[:60]}")
        if isinstance(co, types.CodeType):
            dump_code(co, indent + "      ")
    print(f"{indent}  flags: {hex(c.co_flags)}")
    print(f"{indent}  argcount: {c.co_argcount}")
    print(f"{indent}  nlocals: {c.co_nlocals}")

if __name__ == "__main__":
    filepath = "test_files/simple.cpython-39.pyc"
    with open(filepath, "rb") as f:
        data = f.read()
    
    # Header is 16 bytes for 3.9
    parser = pycrefine.MarshalParser(data[16:])
    try:
        obj = parser.load()
        print(f"ROOT Object: {type(obj)}")
        dump_code(obj)
    except Exception as e:
        print(f"FAILED TO LOAD: {e}")
        import traceback
        traceback.print_exc()
