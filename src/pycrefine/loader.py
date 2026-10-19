"""``.pyc`` loading: marshal reader for foreign versions and backend selection."""
import importlib.util
import io
import marshal
import struct
import sys
import types
from typing import Any, Dict, List, Optional, Tuple
from .codeobj import ForeignCode, _is_code
from .decompiler.base import DecompilerBase
from .decompiler.generic import DecompilerGeneric
from .decompiler.py311 import Decompiler311Plus
from .decompiler.py314 import Decompiler314
from .decompiler.py39 import Decompiler39
from .versions import _get_python_version_from_magic


# ---------------------------------------------------------------------------
# Marshal parser  (cross-version .pyc reading)
# ---------------------------------------------------------------------------

# TYPE_NULL: terminates a marshalled dict, distinct from a None key
_MARSHAL_NULL = object()


# Kinds in a 3.11+ code object's co_localspluskinds (Include/internal/pycore_code.h)
_CO_FAST_LOCAL = 0x20


_CO_FAST_CELL = 0x40


_CO_FAST_FREE = 0x80


class MarshalParser:
    """
    Pure-Python reader for the marshal format, used when a .pyc was written
    by a different Python version than the host.

    *target_version* is the (major, minor) version that wrote the data; it
    selects the code-object layout, which changed in 3.11.
    """

    def __init__(self, data: bytes, target_version: Optional[Tuple[int, int]] = None):
        self.data = data
        self.offset = 0
        self.refs: List[Any] = []
        self.target_version = target_version or sys.version_info[:2]

    def _read(self, n: int) -> bytes:
        if self.offset + n > len(self.data):
            raise EOFError(
                f"Marshal read past EOF at offset {self.offset} "
                f"(tried {n} bytes, total {len(self.data)})"
            )
        res = self.data[self.offset: self.offset + n]
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
        flagged = bool(byte & 0x80)
        type_char = chr(byte & 0x7F)

        if type_char == "r":  # TYPE_REF
            ref_idx = self._read_long()
            return self.refs[ref_idx]

        ref_idx = None
        if flagged:
            ref_idx = self._reserve_ref()

        result = self._load_inner(type_char)

        if ref_idx is not None:
            self._set_ref(ref_idx, result)

        return result

    def _read_float_str(self) -> float:
        return float(self._read(self._read_byte()).decode("ascii"))

    def _load_inner(self, type_char: str) -> Any:
        # Type codes from CPython's Python/marshal.c
        if type_char == "0":
            return _MARSHAL_NULL               # TYPE_NULL
        if type_char == "N":
            return None
        if type_char == "T":
            return True
        if type_char == "F":
            return False
        if type_char == "S":
            return StopIteration
        if type_char == ".":
            return Ellipsis
        if type_char == "i":
            return self._read_long()
        if type_char == "I":                   # TYPE_INT64 (pre-3.4 writers)
            return struct.unpack("<q", self._read(8))[0]
        if type_char == "l":
            size = self._read_long()
            # 3.4+ uses 15-bit digits stored in 2 bytes
            res = 0
            for i in range(abs(size)):
                digit = struct.unpack("<H", self._read(2))[0]
                res += digit * (2 ** (15 * i))
            return -res if size < 0 else res
        if type_char == "g":
            return struct.unpack("<d", self._read(8))[0]
        if type_char == "f":
            return self._read_float_str()
        if type_char == "y":                   # TYPE_BINARY_COMPLEX
            real, imag = struct.unpack("<dd", self._read(16))
            return complex(real, imag)
        if type_char == "x":
            real = self._read_float_str()
            return complex(real, self._read_float_str())
        if type_char in ("s", "u", "t", "a", "A", "z", "Z"):
            size = self._read_byte() if type_char in ("z", "Z") else self._read_long()
            raw = self._read(size)
            if type_char == "s":
                return raw
            if type_char in ("a", "A", "z", "Z"):
                return raw.decode("latin-1")
            return raw.decode("utf-8", "surrogatepass")
        if type_char in (")", "("):
            size = self._read_byte() if type_char == ")" else self._read_long()
            return tuple(self.load() for _ in range(size))
        if type_char == "[":
            size = self._read_long()
            return [self.load() for _ in range(size)]
        if type_char == "{":
            res_dict: Dict[Any, Any] = {}
            while True:
                key = self.load()
                if key is _MARSHAL_NULL:       # end of dict; a None key is 'N'
                    break
                res_dict[key] = self.load()
            return res_dict
        if type_char in ("<", ">"):
            size = self._read_long()
            items = [self.load() for _ in range(size)]
            return set(items) if type_char == "<" else frozenset(items)
        if type_char == ":":                   # TYPE_SLICE (3.14+)
            return slice(self.load(), self.load(), self.load())
        if type_char == "c":
            return self._load_code()

        raise ValueError(
            f"Unsupported marshal type: {type_char!r} (hex: {hex(ord(type_char))})"
        )

    @staticmethod
    def _names(x: Any) -> Tuple[str, ...]:
        if not x:
            return ()
        return tuple(s.decode("utf-8", "replace") if isinstance(s, bytes) else str(s) for s in x)

    @staticmethod
    def _bytes(x: Any) -> bytes:
        if isinstance(x, str):
            return x.encode("latin-1")
        return bytes(x) if x else b""

    def _load_code(self) -> ForeignCode:
        """Read a code object laid out the way *target_version* writes it."""
        argcount = self._read_long()
        posonlyargcount = self._read_long()
        kwonlyargcount = self._read_long()

        if self.target_version >= (3, 11):
            # 3.11+: no nlocals; locals, cells and frees share one table
            # (co_localsplusnames) tagged by co_localspluskinds.
            stacksize = self._read_long()
            flags = self._read_long()
            code = self.load()
            consts = self.load()
            names = self.load()
            localsplusnames = self._names(self.load())
            localspluskinds = self._bytes(self.load())
            filename = self.load()
            name = self.load()
            qualname = self.load()
            firstlineno = self._read_long()
            linetable = self.load()
            exceptiontable = self.load()
            kinds = list(zip(localsplusnames, localspluskinds))
            varnames = tuple(n for n, k in kinds if k & _CO_FAST_LOCAL)
            cellvars = tuple(n for n, k in kinds if k & _CO_FAST_CELL)
            freevars = tuple(n for n, k in kinds if k & _CO_FAST_FREE)
            nlocals = len(varnames)
        else:
            # 3.8 - 3.10
            nlocals = self._read_long()
            stacksize = self._read_long()
            flags = self._read_long()
            code = self.load()
            consts = self.load()
            names = self.load()
            varnames = self._names(self.load())
            freevars = self._names(self.load())
            cellvars = self._names(self.load())
            filename = self.load()
            name = self.load()
            qualname = name
            firstlineno = self._read_long()
            linetable = self.load()
            exceptiontable = b""

        return ForeignCode(
            co_argcount=argcount,
            co_posonlyargcount=posonlyargcount,
            co_kwonlyargcount=kwonlyargcount,
            co_nlocals=nlocals,
            co_stacksize=stacksize,
            co_flags=flags,
            co_code=self._bytes(code),
            co_consts=tuple(consts) if consts else (),
            co_names=self._names(names),
            co_varnames=varnames,
            co_freevars=freevars,
            co_cellvars=cellvars,
            co_filename=str(filename),
            co_name=str(name),
            co_qualname=str(qualname),
            co_firstlineno=firstlineno,
            co_linetable=self._bytes(linetable),
            co_exceptiontable=self._bytes(exceptiontable),
        )


# ---------------------------------------------------------------------------
# Entry point: load .pyc and pick decompiler
# ---------------------------------------------------------------------------

def get_decompiler(filepath: str, beautification_level: str = 'core') -> DecompilerBase:
    with open(filepath, "rb") as f:
        all_data = f.read()

    if len(all_data) < 16:
        raise ValueError(f"Invalid .pyc file '{filepath}': too short (found {len(all_data)} bytes)")

    magic = struct.unpack("<I", all_data[0:4])[0]
    version_id = magic & 0xFFFF

    host_magic = int.from_bytes(importlib.util.MAGIC_NUMBER, "little")
    host_version_id = host_magic & 0xFFFF

    if magic != host_magic and not (3410 <= version_id <= 5000):
        # We don't recognize the magic or it's very old/corrupt
        input_ver = _get_python_version_from_magic(version_id)
        host_ver = _get_python_version_from_magic(host_version_id)

        msg = f"Invalid or unsupported Python magic number: 0x{magic:08x} (version id: {version_id})."
        if input_ver:
            msg += f"\n- Input file appears to be from Python {input_ver}."
        else:
            msg += "\n- Input file version is unrecognized or may be corrupt."

        if host_ver:
            msg += f"\n- Current interpreter is Python {host_ver} (magic: 0x{host_magic:08x})."

        raise ValueError(msg)

    tv_str = _get_python_version_from_magic(version_id)
    if tv_str:
        target_version = tuple(map(int, tv_str.rstrip('+').split('.')))
    else:
        target_version = sys.version_info[:2]

    # Only the 3.9 backend decodes bytecode itself; the 3.10+ backends read
    # instructions through the host's dis module, so they need a host of the
    # same minor version.
    if (magic != host_magic and version_id >= 3430
            and target_version != sys.version_info[:2]):
        host_ver = f"{sys.version_info[0]}.{sys.version_info[1]}"
        raise ValueError(
            f"Cannot decompile Python {tv_str or 'unknown'} bytecode from '{filepath}' "
            f"under Python {host_ver}: Python 3.10+ bytecode can only be decompiled "
            f"by the same Python minor version that wrote it. Run pycrefine under "
            f"Python {tv_str or 'that version'}. (Python 3.9 bytecode works on any host.)"
        )

    code_obj = None
    # The marshal format only changes between minor versions, so the native
    # loader also handles e.g. a pre-release .pyc of the host's own version.
    if magic == host_magic or target_version == sys.version_info[:2]:
        for offset in (16, 12, 8, 4):
            try:
                obj = marshal.load(io.BytesIO(all_data[offset:]))
                if isinstance(obj, types.CodeType):
                    code_obj = obj
                    break
            except Exception:
                continue

    if code_obj is None:
        for offset in (16, 12, 8, 4):
            try:
                obj = MarshalParser(all_data[offset:], target_version).load()
                if _is_code(obj):
                    code_obj = obj
                    break
            except Exception:
                continue

    if not _is_code(code_obj):
        msg = f"Could not find valid marshal code object in .pyc file '{filepath}'."
        if tv_str:
            msg += f" (Inferred version: Python {tv_str})"
        raise ValueError(msg)

    if 3410 <= version_id <= 3429:      # 3.9
        dec = Decompiler39(code_obj, beautification_level=beautification_level, target_version=target_version)
    elif version_id >= 3550:            # 3.13+
        # 3.13 introduced the STORE_FAST_STORE_FAST / LOAD_FAST_LOAD_FAST
        # superinstructions, which only Decompiler314 understands.
        dec = Decompiler314(code_obj, beautification_level=beautification_level, target_version=target_version)
    elif version_id >= 3430:            # 3.10 - 3.12
        dec = Decompiler311Plus(code_obj, beautification_level=beautification_level, target_version=target_version)
    else:
        # Fallback for very old or unrecognised versions
        dec = DecompilerGeneric(code_obj, target_version=target_version, beautification_level=beautification_level)

    return dec
