#!python3

import marshal
import struct
import sys
import types
from typing import List, Optional, Any, Dict, Union, Tuple
from dataclasses import dataclass

@dataclass
class BytecodeInstruction:
    opcode: int
    opname: str
    arg: Optional[int]
    argval: Any
    offset: int
    starts_line: Optional[int]
    is_jump_target: bool

class DecompilerBase:
    def __init__(self, code_obj: types.CodeType, indent_level: int = 0):
        self.code_obj = code_obj
        self.instructions: List[BytecodeInstruction] = []
        self.reconstructed: List[str] = []
        self.indent_level = indent_level
        self.blocks: List[Tuple[int, str]] = [] # Stack of (end_offset, type)
        self.pc = 0

    def _disassemble(self):
        """Convert code object bytecode into a list of BytecodeInstruction."""
        import dis
        for instr in dis.get_instructions(self.code_obj):
            self.instructions.append(BytecodeInstruction(
                opcode=instr.opcode,
                opname=instr.opname,
                arg=instr.arg,
                argval=instr.argval,
                offset=instr.offset,
                starts_line=instr.starts_line,
                is_jump_target=instr.is_jump_target
            ))

    def decompile(self) -> str:
        raise NotImplementedError("Subclasses must implement decompile()")

    def _get_jump_target(self, instr: BytecodeInstruction) -> int:
        """Calculate absolute jump target. Default assumes absolute offset in arg."""
        return int(instr.arg) if (instr.arg is not None) else 0

class DecompilerGeneric(DecompilerBase):
    def __init__(self, code_obj: types.CodeType, indent_level: int = 0):
        super().__init__(code_obj, indent_level)
        self.stack: List[str] = []

    def decompile(self) -> str:
        self._disassemble()
        self.pc = 0
        self.blocks = [] # Stack of (end_offset, type)
        
        # Check for module docstring
        if self.code_obj.co_consts and isinstance(self.code_obj.co_consts[0], str) and self.indent_level == 0:
            doc = self.code_obj.co_consts[0]
            if doc:
                self.reconstructed.append(f'"""\n{doc.strip()}\n"""\n')
        
        while self.pc < len(self.instructions):
            instr = self.instructions[self.pc]
            
            # Check for block ends
            while self.blocks and instr.offset >= self.blocks[-1][0]:
                self.blocks.pop()
                self.indent_level -= 1

            self.pc += 1
            self._handle_instruction(instr)
        
        return "\n".join(self.reconstructed).strip()

    def _append_reconstructed(self, line: str):
        if "\n" in line:
            # For multi-line blocks like functions, we assume they are already formatted or need block indentation
            indented_lines = []
            for l in line.split("\n"):
                if l.strip():
                    indented_lines.append("    " * self.indent_level + l)
                else:
                    indented_lines.append("")
            self.reconstructed.append("\n".join(indented_lines))
        else:
            self.reconstructed.append("    " * self.indent_level + line)

    def _handle_instruction(self, instr: BytecodeInstruction):
        if instr.opname in ("LOAD_CONST", "LOAD_NAME", "LOAD_FAST", "LOAD_GLOBAL"):
            if isinstance(instr.argval, types.CodeType):
                self.stack.append(("code", instr.argval))
            elif instr.opname == "LOAD_CONST" and isinstance(instr.argval, str) and instr.offset == 0:
                # Skip if already handled as docstring
                pass
            else:
                self.stack.append(repr(instr.argval) if instr.opname == "LOAD_CONST" else str(instr.argval))
                
        elif instr.opname in ("STORE_NAME", "STORE_FAST"):
            if self.stack:
                val = self.stack.pop()
                if isinstance(val, tuple) and val[0] == "func":
                    # Function already reconstructed by MAKE_FUNCTION
                    self._append_reconstructed(val[1])
                elif str(instr.argval) == "__doc__":
                    # Module or class docstring stored in __doc__
                    if isinstance(val, str):
                        doc_text = val.strip("'\"").strip()
                        self._append_reconstructed(f'"""{doc_text}"""')
                    else:
                        self._append_reconstructed(f'"""{val}"""')
                elif val == str(instr.argval):
                    # Suppress redundant assignments like item = item from loops
                    pass
                else:
                    self._append_reconstructed(f"{instr.argval} = {val}")
                    
        elif instr.opname == "MAKE_FUNCTION":
            # In 3.13, MAKE_FUNCTION has no arg and only code object on stack
            # In 3.9/3.11, MAKE_FUNCTION pushes name then code object
            code_obj_val = None
            if self.stack and isinstance(self.stack[-1], tuple) and self.stack[-1][0] == "code":
                code_obj_val = self.stack.pop()
            elif len(self.stack) >= 2:
                self.stack.pop()  # pop name string
                if self.stack and isinstance(self.stack[-1], tuple) and self.stack[-1][0] == "code":
                    code_obj_val = self.stack.pop()
            if code_obj_val is not None:
                name = None  # unused in new path
                if isinstance(code_obj_val, tuple) and code_obj_val[0] == "code":
                    code_obj = code_obj_val[1]
                    args = list(code_obj.co_varnames[:code_obj.co_argcount])
                    
                    # Decompile function body
                    if isinstance(self, Decompiler39): dec_class = Decompiler39
                    elif isinstance(self, Decompiler311Plus): dec_class = Decompiler311Plus
                    else: dec_class = DecompilerGeneric

                    # Temporary reset indentation for body as it will be indented by _append_reconstructed
                    dec = dec_class(code_obj, indent_level=0)
                    body = dec.decompile()
                    sig = f"def {code_obj.co_name}({', '.join(args)}):"
                    self.stack.append(("func", f"{sig}\n{body}"))
                else:
                    self.stack.append(f"make_function({name})")

        elif instr.opname == "RETURN_VALUE":
            if self.stack:
                val = self.stack.pop()
                if val != "None":
                    self._append_reconstructed(f"return {val}")
                    
        elif instr.opname == "POP_TOP":
            if self.stack:
                stmt = self.stack.pop()
                if stmt != "None" and not (isinstance(stmt, tuple) and stmt[0] in ("code", "func")):
                    self._append_reconstructed(str(stmt))
                    
        elif "BINARY" in instr.opname:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                op_map = {
                    "BINARY_ADD": "+", "BINARY_SUBTRACT": "-", "BINARY_MULTIPLY": "*",
                    "BINARY_TRUE_DIVIDE": "/", "BINARY_FLOOR_DIVIDE": "//",
                    "BINARY_MODULO": "%", "BINARY_POWER": "**", "BINARY_LSHIFT": "<<",
                    "BINARY_RSHIFT": ">>", "BINARY_AND": "&", "BINARY_OR": "|", "BINARY_XOR": "^"
                }
                op = op_map.get(instr.opname, "unknown_op")
                self.stack.append(f"({left} {op} {right})")

        elif "CALL" in instr.opname:
            args = []
            num_args = int(instr.arg) if (instr.arg is not None) else 0
            for _ in range(num_args):
                if self.stack: args.insert(0, str(self.stack.pop()))
            func = str(self.stack.pop()) if self.stack else "unknown_func"
            call_expr = f"{func}({', '.join(args)})"
            self.stack.append(call_expr)
            
        elif instr.opname == "COMPARE_OP":
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                # Use argval directly — dis resolves it correctly for all Python versions
                # (In 3.9 it's an index; in 3.12+ it's already the string)
                op = str(instr.argval) if instr.argval else "=="
                
                if str(left) == "__name__" and str(right) == "'__main__'" and op == "==":
                    self.stack.append('__name__ == "__main__"')
                else:
                    self.stack.append(f"{left} {op} {right}")

        elif instr.opname == "JUMP_FORWARD":
            # JUMP_FORWARD after an if-body marks end of if, possibly start of else.
            # The if block's jump target (from POP_JUMP_IF_FALSE) is the offset
            # of this JUMP_FORWARD instruction - check by peeking at current instr offset.
            else_end = self._get_jump_target(instr)
            if self.blocks and self.blocks[-1][1] == "if" and self.blocks[-1][0] <= instr.offset + 2:
                # We are inside an if block and JUMP_FORWARD transitions to else body
                self.blocks.pop()
                self.indent_level -= 1
                self._append_reconstructed("else:")
                self.indent_level += 1
                self.blocks.append((else_end, "else"))

        elif instr.opname in ("PUSH_NULL", "RESUME", "PRECALL", "CACHE"):
            pass  # no-ops for decompilation

        elif instr.opname == "POP_JUMP_IF_FALSE":
            if self.stack:
                cond = self.stack.pop()
                self._append_reconstructed(f"if {cond}:")
                self.indent_level += 1
                jump_target = self._get_jump_target(instr)
                self.blocks.append((jump_target, "if"))


        elif instr.opname == "FOR_ITER":
            if self.stack:
                iterator = self.stack.pop()
                self._append_reconstructed(f"for item in {iterator}:")
                self.indent_level += 1
                jump_target = self._get_jump_target(instr)
                self.blocks.append((jump_target, "for"))
                self.stack.append("item")

        elif instr.opname == "BUILD_TUPLE":
            items = []
            num = int(instr.arg) if (instr.arg is not None) else 0
            for _ in range(num):
                if self.stack: items.insert(0, str(self.stack.pop()))
            self.stack.append(f"({', '.join(items)})")

        elif instr.opname == "BUILD_LIST":
            items = []
            num = int(instr.arg) if (instr.arg is not None) else 0
            for _ in range(num):
                if self.stack: items.insert(0, str(self.stack.pop()))
            self.stack.append("[" + ", ".join(items) + "]")

        elif instr.opname == "BUILD_SET":
            items = []
            num = int(instr.arg) if (instr.arg is not None) else 0
            for _ in range(num):
                if self.stack: items.insert(0, str(self.stack.pop()))
            self.stack.append("{" + ", ".join(items) + "}")

        elif instr.opname in ("GET_ITER", "UNPACK_SEQUENCE"):
            pass

        elif instr.opname in ("LOAD_ATTR", "LOAD_METHOD"):
            obj = self.stack.pop() if self.stack else "obj"
            self.stack.append(str(obj) + "." + str(instr.argval))

        elif instr.opname == "RETURN_CONST":
            val = repr(instr.argval) if instr.argval is not None else "None"
            if val != "None":
                self._append_reconstructed("return " + val)


class Decompiler39(DecompilerGeneric):
    def _disassemble(self):
        """Manually disassemble 3.9a2 bytecode to avoid host version issues."""
        bytecode = self.code_obj.co_code
        # 3.9a2 uses 2-byte wordcode, but has some 0-padding
        for i in range(0, len(bytecode), 2):
            opcode = bytecode[i]
            arg = bytecode[i+1]
            opname = self._get_opname_39(opcode)
            
            # Skip suspected padding (00) if it's not a known opcode
            if opcode == 0 and opname == "OP_0":
               continue

            # Standard argval lookup
            argval = arg
            if opcode < 90: # HAS_ARG boundary 
                argval = None 
            elif opname in ("LOAD_CONST", "STORE_NAME", "LOAD_NAME", "LOAD_GLOBAL", "LOAD_FAST", "STORE_FAST", "DELETE_NAME", "DELETE_FAST", "STORE_GLOBAL", "DELETE_GLOBAL"):
                if "CONST" in opname and arg < len(self.code_obj.co_consts):
                    argval = self.code_obj.co_consts[arg]
                elif "NAME" in opname or "GLOBAL" in opname:
                    if arg < len(self.code_obj.co_names):
                        argval = self.code_obj.co_names[arg]
                elif "FAST" in opname:
                    if arg < len(self.code_obj.co_varnames):
                        argval = self.code_obj.co_varnames[arg]
            elif opname == "COMPARE_OP":
                # 3.9 comparison operators
                ops = ['<', '<=', '==', '!=', '>', '>=', 'in', 'not in', 'is', 'is not', 'exception match', 'BAD']
                if arg < len(ops):
                    argval = ops[arg]

            self.instructions.append(BytecodeInstruction(
                opcode=opcode,
                opname=opname,
                arg=arg if opcode >= 90 else None,
                argval=argval,
                offset=i,
                starts_line=None,
                is_jump_target=False
            ))

    def _get_opname_39(self, opcode: int) -> str:
        # Hardcoded 3.9 opcodes to avoid host version issues
        maps = {
            1: "POP_TOP", 2: "ROT_TWO", 3: "ROT_THREE", 4: "DUP_TOP", 5: "DUP_TOP_TWO",
            20: "BINARY_MULTIPLY", 22: "BINARY_MODULO", 23: "BINARY_ADD", 24: "BINARY_SUBTRACT",
            25: "BINARY_SUBSCR", 26: "BINARY_FLOOR_DIVIDE", 27: "BINARY_TRUE_DIVIDE",
            60: "STORE_NAME", 66: "BINARY_MODULO", 68: "GET_ITER",
            71: "LOAD_BUILD_CLASS", 83: "RETURN_VALUE", 90: "STORE_NAME", 
            91: "DELETE_NAME", 92: "UNPACK_SEQUENCE", 93: "FOR_ITER",
            95: "STORE_ATTR", 97: "STORE_GLOBAL", 100: "LOAD_CONST", 
            101: "LOAD_NAME", 102: "BUILD_TUPLE", 103: "BUILD_LIST", 
            104: "BUILD_SET", 105: "BUILD_MAP", 106: "LOAD_ATTR", 
            107: "COMPARE_OP", 108: "IMPORT_NAME", 109: "IMPORT_FROM",
            110: "JUMP_FORWARD", 113: "JUMP_ABSOLUTE", 114: "POP_JUMP_IF_FALSE",
            115: "POP_JUMP_IF_TRUE", 116: "LOAD_GLOBAL", 
            124: "LOAD_FAST", 125: "STORE_FAST", 131: "CALL_FUNCTION",
            132: "MAKE_FUNCTION", 160: "LOAD_METHOD", 161: "CALL_METHOD"
        }
        return maps.get(opcode, f"OP_{opcode}")

    def _handle_instruction(self, instr: BytecodeInstruction):
        binary_ops = {
            "BINARY_ADD": "+", "BINARY_SUBTRACT": "-", "BINARY_MULTIPLY": "*",
            "BINARY_TRUE_DIVIDE": "/", "BINARY_FLOOR_DIVIDE": "//",
            "BINARY_MODULO": "%", "BINARY_POWER": "**",
        }
        if instr.opname in binary_ops:
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                self.stack.append(f"({left} {binary_ops[instr.opname]} {right})")
        elif instr.opname == "COMPARE_OP":
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                # 3.9 uses indices for compare ops, but argval might already be the string
                op = str(instr.argval)
                self.stack.append(f"({left} {op} {right})")
        elif instr.opname in ("LOAD_METHOD", "CALL_METHOD"):
            if instr.opname == "LOAD_METHOD":
                obj = self.stack.pop() if self.stack else "unknown"
                self.stack.append(f"{obj}.{instr.argval}")
            else: # CALL_METHOD
                args = []
                num_args = instr.arg if isinstance(instr.arg, int) else 0
                for _ in range(num_args):
                    if self.stack: args.insert(0, self.stack.pop())
                meth = self.stack.pop() if self.stack else "unknown_meth"
                self.stack.append(f"{meth}({', '.join(args)})")
        else:
            super()._handle_instruction(instr)

class Decompiler311Plus(DecompilerGeneric):
    def _handle_instruction(self, instr: BytecodeInstruction):
        if instr.opname == "RESUME":
            pass
        elif instr.opname == "BINARY_OP":
            if len(self.stack) >= 2:
                right = self.stack.pop()
                left = self.stack.pop()
                # 3.11+ Binary opcodes (simplified)
                op_map = {
                    0: "+", 1: "&", 2: "//", 3: "<<", 4: "@", 5: "*", 
                    6: "%", 7: "|", 8: "**", 9: ">>", 10: "-", 11: "/", 13: "^",
                    16: "+=", 17: "&=", 18: "//=", 19: "<<=", 20: "@=", 21: "*=",
                    22: "%=", 23: "|=", 24: "**=", 25: ">>=", 26: "-=", 27: "/=", 29: "^="
                }
                op_idx = int(instr.arg) if (instr.arg is not None) else -1
                op_symbol = op_map.get(op_idx, f"<op:{op_idx}>")
                self.stack.append(f"({left} {op_symbol} {right})")
        elif instr.opname == "FORMAT_VALUE":
            if self.stack:
                val = self.stack.pop()
                self.stack.append(f"{{{val}}}")
        elif instr.opname == "BUILD_STRING":
            parts = []
            num = int(instr.arg) if (instr.arg is not None) else 0
            for _ in range(num):
                if self.stack: parts.insert(0, str(self.stack.pop()))
            has_fmt = any('{' in p for p in parts)
            content = "".join(p.strip("'\"") if has_fmt else p for p in parts)
            self.stack.append(f'f"{content}"' if has_fmt else f'"{content}"')
        else:
            # Leverage DecompilerGeneric for STORE_NAME, MAKE_FUNCTION, etc.
            super()._handle_instruction(instr)

    def _get_jump_target(self, instr: BytecodeInstruction) -> int:
        """3.11+ relative jumps in words (usually)."""
        arg = int(instr.arg) if (instr.arg is not None) else 0
        if "BACKWARD" in instr.opname:
            return instr.offset + 2 - (arg * 2)
        # FOR_ITER and POP_JUMP_IF_* forward jumps
        return instr.offset + 2 + (arg * 2)

class MarshalParser:
    def __init__(self, data: bytes):
        self.data = data
        self.offset = 0
        self.refs: List[Any] = []

    def _read(self, n: int) -> bytes:
        if self.offset + n > len(self.data):
            raise EOFError(f"Marshal read paste EOF at offset {self.offset} (tried to read {n} bytes, total size {len(self.data)})")
        res = self.data[self.offset : self.offset + n]
        self.offset += n
        return res

    def _read_byte(self) -> int:
        return self._read(1)[0]

    def _read_long(self) -> int:
        return struct.unpack("<i", self._read(4))[0]

    def _reserve_ref(self) -> int:
        idx = len(self.refs)
        self.refs.append(None)
        return idx

    def _set_ref(self, idx: int, obj: Any) -> Any:
        self.refs[idx] = obj
        return obj

    def load(self) -> Any:
        byte = self._read_byte()
        # Marshal character codes can have a 'flag' bit set (0x80)
        # to indicate that the object should be added to the ref list.
        flagged = bool(byte & 0x80)
        type_char = chr(byte & 0x7F)
        
        if type_char == 'r': # TYPE_REF
            ref_idx = self._read_long()
            return self.refs[ref_idx]
        
        # Reserve ref strictly if flagged
        ref_idx = None
        if flagged:
            ref_idx = self._reserve_ref()
            
        result = self._load_inner(type_char)
        
        if ref_idx is not None:
            self._set_ref(ref_idx, result)
            
        return result

    def _load_inner(self, type_char: str) -> Any:
        if type_char == 'N': return None
        if type_char == 'T': return True
        if type_char == 'F': return False
        if type_char == 'i': return self._read_long()
        if type_char in ('s', 'u', 'Z', 'a', 'z', 'A', 't'): # String/bytes types
            if type_char in ('z', 'Z'):
                size = self._read_byte()
            else:
                size = self._read_long()
            raw = self._read(size)
            if type_char == 's':
                return raw
            # In 3.9+, 'a' and 'z' are ASCII, 'u' and 't' and 'Z' are Unicode
            return raw.decode('utf-8', 'replace')
        if type_char in ('y', ')', '('): # TYPE_SMALL_TUPLE, TYPE_TUPLE
            if type_char in ('y', ')'):
                size = self._read_byte()
            else:
                size = self._read_long()
            return tuple(self.load() for _ in range(size))
        if type_char == '[': # TYPE_LIST
            size = self._read_long()
            res_list: List[Any] = []
            for _ in range(size):
                res_list.append(self.load())
            return res_list
        if type_char == '{': # TYPE_DICT
            res_dict: Dict[Any, Any] = {}
            while True:
                key = self.load()
                if key is None: break
                val = self.load()
                res_dict[key] = val
            return res_dict
        if type_char in ('I', 'l'): # Integer types
            if type_char == 'I': 
                return struct.unpack("<q", self._read(8))[0]
            size = self._read_long()
            return int.from_bytes(self._read(abs(size) * 2), 'little', signed=(size < 0))
        if type_char == 'S': # TYPE_STOP_ITER
            return StopIteration
        if type_char == 'g': # TYPE_BINARY_FLOAT
            return struct.unpack("<d", self._read(8))[0]
        if 0x01 <= ord(type_char) <= 0x1A: # TYPE_SMALL_INT or similar direct value
            return ord(type_char) - 1 # Simple mapping for small ints if used this way
        if type_char in ('<', '>'): # TYPE_SET, TYPE_FROZENSET
            size = self._read_long()
            items_set = [self.load() for _ in range(size)]
            return set(items_set) if type_char == '<' else frozenset(items_set)
        if type_char == 'c': # TYPE_CODE
            return self._load_code()
        
        raise ValueError(f"Unsupported marshal type: {type_char} (hex: {hex(ord(type_char))})")

    def _load_code(self) -> types.CodeType:
        offset = self.offset - 1 # Include the 'c' char
        argcount = self._read_long()
        posonlyargcount = self._read_long()
        kwonlyargcount = self._read_long()
        nlocals = self._read_long()
        stacksize = self._read_long()
        flags = self._read_long()
        code = self.load()
        consts = self.load()
        names = self.load()
        varnames = self.load()
        freevars = self.load()
        cellvars = self.load()
        filename = self.load()
        name = self.load()
        firstlineno = self._read_long()
        lnotab = self.load()
        
        # Ensure components are of correct type
        # Ensure components are of correct type
        def to_tuple_strings(x):
            if x is None or isinstance(x, int): return ()
            return tuple( s.decode('utf-8', 'replace') if isinstance(s, bytes) else str(s) for s in x )
            
        def to_tuple(x):
            if isinstance(x, tuple): return x
            if x is None or isinstance(x, int): return ()
            return tuple(x)
            
        code = bytes(code) if not isinstance(code, bytes) else code
        consts = to_tuple(consts)
        names = to_tuple_strings(names)
        varnames = to_tuple_strings(varnames)
        freevars = to_tuple_strings(freevars)
        cellvars = to_tuple_strings(cellvars)
        lnotab = bytes(lnotab) if not isinstance(lnotab, bytes) else lnotab
        
        filename = filename.decode('utf-8', 'replace') if isinstance(filename, bytes) else str(filename)
        name = name.decode('utf-8', 'replace') if isinstance(name, bytes) else str(name)
        qualname = name
        linetable = lnotab
        exceptiontable = b""
        
        return types.CodeType(
            argcount, posonlyargcount, kwonlyargcount, nlocals, stacksize, flags,
            code, consts, names, varnames, filename, name, qualname, firstlineno,
            linetable, exceptiontable, freevars, cellvars
        )

def get_decompiler(filepath: str) -> DecompilerBase:
    with open(filepath, "rb") as f:
        all_data = f.read()
    
    if len(all_data) < 16:
        raise ValueError("Invalid .pyc file: too short")
    
    magic = struct.unpack("<I", all_data[0:4])[0]
    
    import importlib.util
    host_magic = int.from_bytes(importlib.util.MAGIC_NUMBER, 'little')
    
    code_obj = None
    if magic == host_magic:
        # Native marshal parser fallback
        for offset in (16, 12, 8, 4):
            try:
                import io
                f_in = io.BytesIO(all_data[offset:])
                obj = marshal.load(f_in)
                if isinstance(obj, types.CodeType):
                    code_obj = obj
                    break
            except Exception:
                continue

    if code_obj is None:
        # Custom logic to find code object: search for 'c' (TYPE_CODE) 
        # Usually at offset 16 or 12 or 8
        for offset in (16, 12, 8, 4):
            try:
                parser = MarshalParser(all_data[offset:])
                obj = parser.load()
                if isinstance(obj, types.CodeType):
                    code_obj = obj
                    break
            except:
                continue
            
    if not isinstance(code_obj, types.CodeType):
        raise ValueError("Could not find valid marshal code object in .pyc file")
    
    # Decompiler dispatch logic
    version_id = magic & 0xFFFF
    host_version_id = host_magic & 0xFFFF
    
    if 3410 <= version_id <= 3425: # Python 3.9 range (including 3413, 3421, 3425)
        return Decompiler39(code_obj)
    elif version_id >= 3495:
        return Decompiler311Plus(code_obj)
    
    return DecompilerGeneric(code_obj)

def main():
    if len(sys.argv) < 2:
        print("Usage: pycrefine <file.pyc>")
        return

    pyc_path = sys.argv[1]
    try:
        decompiler = get_decompiler(pyc_path)
        print(decompiler.decompile())
    except Exception:
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    main()