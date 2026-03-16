
import types
import struct
import sys
from pycrefine import MarshalParser

def print_code_info(obj, indent=""):
    if not isinstance(obj, types.CodeType):
        return
    print(f"{indent}co_name: {obj.co_name}")
    print(f"{indent}co_argcount: {obj.co_argcount}")
    print(f"{indent}co_posonlyargcount: {obj.co_posonlyargcount}")
    print(f"{indent}co_kwonlyargcount: {obj.co_kwonlyargcount}")
    print(f"{indent}co_nlocals: {obj.co_nlocals}")
    print(f"{indent}co_stacksize: {obj.co_stacksize}")
    print(f"{indent}co_flags: {hex(obj.co_flags)}")
    print(f"{indent}co_names ({len(obj.co_names)}): {obj.co_names}")
    print(f"{indent}co_consts ({len(obj.co_consts)}):")
    for i, c in enumerate(obj.co_consts):
        print(f"{indent}  [{i}]: {type(c)} {repr(c)[:100]}")
        if isinstance(c, types.CodeType):
            print_code_info(c, indent + "    ")
    print(f"{indent}co_varnames ({len(obj.co_varnames)}): {obj.co_varnames}")
    print(f"{indent}co_freevars ({len(obj.co_freevars)}): {obj.co_freevars}")
    print(f"{indent}co_cellvars ({len(obj.co_cellvars)}): {obj.co_cellvars}")
    print(f"{indent}co_filename: {obj.co_filename}")
    print(f"{indent}co_firstlineno: {obj.co_firstlineno}")
    
    import dis
    print(f"\n{indent}Bytecode disassembly:")
    try:
        for instr in dis.get_instructions(obj):
            print(f"{indent}  {instr.offset:4} {instr.opname:20} {instr.argval}")
    except Exception as e:
        print(f"{indent}Disassembly failed: {e}")

def inspect_pyc(filepath):
    print(f"Inspecting: {filepath}")
    with open(filepath, "rb") as f:
        data = f.read()
    
    code_data = data[16:]
    parser = MarshalParser(code_data)
    try:
        obj = parser.load()
        print(f"Loaded object type: {type(obj)}")
        print_code_info(obj)
    except Exception as e:
        print(f"Loading failed: {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"Loading failed: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    if len(sys.argv) > 1:
        inspect_pyc(sys.argv[1])
    else:
        print("Usage: python inspect_code.py <file.pyc>")
