"""Read the bytecode of CPython 3.10 to 3.14 without the host's ``dis`` module (#94).

``dis`` decodes with the opcode table, cache layout and argument conventions of the interpreter
that runs it, so it cannot read bytecode that another version wrote. ``decode_instructions``
does what ``dis.get_instructions`` does for one version, from the data in ``opcodes.tables``;
tests/test_decode.py compares the two for the host's own version, and CI runs that on every version.

The result matches ``dis`` field for field, with the line number of ``starts_line`` an ``int``
or ``None`` on every version (3.13's flag and ``line_number`` are already folded together).
"""
from typing import Any, Dict, Iterator, List, Optional, Tuple

from ..instructions import BytecodeInstruction
from .tables import TABLES

class _Unknown:  # pylint: disable=too-few-public-methods  # a sentinel with a repr
    """What 3.11's ``dis`` gives for the argument of a constant-table instruction other than LOAD_CONST (``dis.UNKNOWN``)."""

    def __repr__(self) -> str:
        return "<unknown>"


UNKNOWN = _Unknown()
_INT_OVERFLOW = 1 << 31
_FORMAT_VALUE_CONVERTERS = ((None, ""), (str, "str"), (repr, "repr"), (ascii, "ascii"))
_PAIR_OPS = frozenset(("LOAD_FAST_LOAD_FAST", "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
                       "STORE_FAST_LOAD_FAST", "STORE_FAST_STORE_FAST"))


class _Opcodes:  # pylint: disable=too-few-public-methods  # read-only indexes of one version's data
    """The data of one version, indexed the ways the decoder needs it."""

    def __init__(self, version: Tuple[int, int]) -> None:
        table = TABLES[version]
        self.version = version
        self.table = table
        self.names: List[Optional[str]] = table["names"]
        self.numbers: Dict[str, int] = {name: i for i, name in enumerate(self.names) if name}
        self.sets: Dict[str, frozenset] = {kind: frozenset(table[kind]) for kind in (
            "hasconst", "hasname", "hasjrel", "hasjabs", "haslocal", "hasfree", "hascompare", "hasexc", "hasarg")}
        self.caches: Dict[str, int] = table["caches"]
        self.have_argument: int = table["have_argument"]

    def has_arg(self, name: str) -> bool:
        return name in self.sets["hasarg"]


_CACHE: Dict[Tuple[int, int], _Opcodes] = {}


def opcodes_for(version: Tuple[int, int]) -> _Opcodes:
    if version not in TABLES:
        raise ValueError(f"no opcode data for Python {version[0]}.{version[1]}")
    if version not in _CACHE:
        _CACHE[version] = _Opcodes(version)
    return _CACHE[version]


# ---------------------------------------------------------------------------
# Instruction stream
# ---------------------------------------------------------------------------

def _unpack(code: bytes, ops: _Opcodes) -> Iterator[Tuple[int, int, str, Optional[int]]]:
    """``(offset, opcode, name, arg)`` of every instruction; inline cache entries are skipped."""
    extended = 0
    skip = 0
    wraps = ops.version >= (3, 11)
    for i in range(0, len(code) - 1, 2):
        if skip:
            skip -= 1
            continue
        op = code[i]
        name = ops.names[op] or f"<{op}>"
        skip = ops.caches.get(name, 0)
        if ops.has_arg(name):
            arg = code[i + 1] | extended
            extended = (arg << 8) if name == "EXTENDED_ARG" else 0
            if wraps and extended >= _INT_OVERFLOW:
                extended -= 2 * _INT_OVERFLOW
        else:
            arg = None
            extended = 0
        yield i, op, name, arg


def _jump_target(ops: _Opcodes, name: str, arg: int, offset: int) -> Optional[int]:
    """The offset a jump instruction leads to, or None for an instruction that does not jump."""
    sets = ops.sets
    if name in sets["hasjabs"]:
        return arg * 2
    if name in sets["hasjrel"]:
        if ops.version < (3, 11):
            return offset + 2 + arg * 2
        # (3.14's END_ASYNC_FOR names the SEND it ends, which lies behind it)
        backward = "JUMP_BACKWARD" in name or (name == "END_ASYNC_FOR" and ops.version >= (3, 14))
        signed = -arg if backward else arg
        target = offset + 2 + signed * 2
        # (from 3.12 the offset counts from the end of the inline cache entries)
        return target + 2 * ops.caches.get(name, 0) if ops.version >= (3, 12) else target
    return None


# ---------------------------------------------------------------------------
# Line numbers
# ---------------------------------------------------------------------------

def _lines_310(table: bytes, first_line: int) -> Iterator[Tuple[int, Optional[int]]]:
    """``(start offset, line or None)`` of the ranges in 3.10's ``co_linetable``."""
    line = first_line
    start = 0
    for k in range(0, len(table) - 1, 2):
        delta = table[k]
        step = table[k + 1] - 256 if table[k + 1] > 127 else table[k + 1]
        end = start + delta
        if step == -128:
            current: Optional[int] = None
        else:
            line += step
            current = line
        if end > start:
            yield start, current
        start = end


def _varint(table: bytes, i: int) -> Tuple[int, int]:
    byte = table[i]
    i += 1
    value = byte & 63
    shift = 0
    while byte & 64:
        byte = table[i]
        i += 1
        shift += 6
        value |= (byte & 63) << shift
    return value, i


def _lines_311(table: bytes, first_line: int) -> Iterator[Tuple[int, Optional[int]]]:
    """``(start offset, line or None)`` of the entries in the location table of 3.11 and later."""
    line = first_line
    offset = 0
    i = 0
    while i < len(table):
        first = table[i]
        i += 1
        code = (first >> 3) & 15
        length = ((first & 7) + 1) * 2
        if code == 15:
            current: Optional[int] = None
        else:
            if code in (13, 14):
                raw, i = _varint(table, i)
                line += -(raw >> 1) if raw & 1 else raw >> 1
                if code == 14:
                    for _ in range(3):
                        _, i = _varint(table, i)
            elif code in (10, 11, 12):
                line += code - 10
                i += 2
            else:
                i += 1
            current = line
        yield offset, current
        offset += length


def line_starts(code: Any, version: Tuple[int, int]) -> Dict[int, Optional[int]]:
    """``{offset: line}`` for the offsets where a new source line starts, as ``dis.findlinestarts`` reads them.

    Until 3.12 a range with no line is left out and a range with the line before it is not a new start.
    From 3.13 ``None`` is a line too: an offset without a line starts one.
    """
    ranges = (_lines_310 if version < (3, 11) else _lines_311)(code.co_linetable, code.co_firstlineno)
    starts: Dict[int, Optional[int]] = {}
    unset = object()    # (nothing was seen yet; line 0 is a line, and so is None)
    last: Any = unset if version >= (3, 13) else None
    for start, line in ranges:
        if version < (3, 13):
            new = line is not None and line != last
        else:
            new = last is unset or line != last
        if new:
            last = line
            starts[start] = line
    return starts


# ---------------------------------------------------------------------------
# Arguments
# ---------------------------------------------------------------------------

class _Resolver:  # pylint: disable=too-few-public-methods  # one method: argument to (argval, argrepr)
    """Turns an instruction argument into ``(argval, argrepr)`` the way the version's ``dis`` does."""

    def __init__(self, code: Any, ops: _Opcodes, labels: Dict[int, int]) -> None:
        self.code = code
        self.ops = ops
        self.labels = labels
        self.consts = code.co_consts
        self.names = code.co_names
        if ops.version >= (3, 11):
            only_cells = tuple(c for c in code.co_cellvars if c not in code.co_varnames)
            self.fast = tuple(code.co_varnames) + only_cells + tuple(code.co_freevars)
            self.free = self.fast
        else:
            self.fast = tuple(code.co_varnames)
            self.free = tuple(code.co_cellvars) + tuple(code.co_freevars)

    def resolve(self, name: str, arg: int, offset: int) -> Tuple[Any, str]:  # pylint: disable=too-many-return-statements  # one return per kind of argument
        ops = self.ops
        sets = ops.sets
        table = ops.table
        version = ops.version
        if name in sets["hasconst"]:
            if name == "LOAD_SMALL_INT":
                return arg, repr(arg)
            if version == (3, 11) and name != "LOAD_CONST":
                return UNKNOWN, ""
            value = self.consts[arg]
            return value, repr(value)
        if name in sets["hasname"]:
            return self._name(name, arg)
        if version >= (3, 13):
            result = self._resolve_313(name, arg, offset)
            if result is not None:
                return result
        else:
            target = _jump_target(ops, name, arg, offset)
            if target is not None:
                return target, f"to {target}"
        if name in sets["haslocal"]:
            value = self.fast[arg]
            return value, value
        if name in sets["hasfree"]:
            value = self.free[arg]
            return value, value
        if name in sets["hascompare"]:
            return self._compare(arg)
        if version < (3, 13):
            return self._resolve_old(name, arg)
        return self._resolve_new(name, arg, table)

    def _name(self, name: str, arg: int) -> Tuple[Any, str]:
        version = self.ops.version
        if version >= (3, 11) and name == "LOAD_GLOBAL" or (version >= (3, 12) and name in ("LOAD_ATTR", "LOAD_SUPER_ATTR")):
            shift = 2 if name == "LOAD_SUPER_ATTR" else 1
            value = self.names[arg >> shift]
            if arg & 1 and value:
                if version >= (3, 13):
                    return value, f"{value} + NULL" if name == "LOAD_GLOBAL" else f"{value} + NULL|self"
                return value, f"NULL + {value}" if name == "LOAD_GLOBAL" else f"NULL|self + {value}"
            return value, value
        value = self.names[arg]
        return value, value

    def _compare(self, arg: int) -> Tuple[Any, str]:
        cmp_op = self.ops.table["cmp_op"]
        version = self.ops.version
        if version < (3, 12):
            value = cmp_op[arg]
            return value, value
        if version == (3, 12):
            value = cmp_op[arg >> 4]
            return value, value
        value = cmp_op[arg >> 5]
        return value, f"bool({value})" if arg & 16 else value

    def _resolve_old(self, name: str, arg: int) -> Tuple[Any, str]:
        """3.10 to 3.12: the instructions whose argument is a set of flags or an index into a table."""
        table = self.ops.table
        if name == "FORMAT_VALUE":
            func, text = _FORMAT_VALUE_CONVERTERS[arg & 0x3]
            value: Any = (func, bool(arg & 0x4))
            if value[1]:
                text = f"{text}, with format" if text else "with format"
            return value, text
        if name == "MAKE_FUNCTION":
            return arg, ", ".join(s for i, s in enumerate(table["make_function_flags"]) if arg & (1 << i))
        if name == "BINARY_OP":
            return arg, table["nb_ops"][arg]
        if name == "CALL_INTRINSIC_1":
            return arg, table["intrinsic_1"][arg]
        if name == "CALL_INTRINSIC_2":
            return arg, table["intrinsic_2"][arg]
        return arg, ""

    def _resolve_313(self, name: str, arg: int, offset: int) -> Optional[Tuple[Any, str]]:
        sets = self.ops.sets
        if name in sets["hasjrel"] or name in sets["hasjabs"] or name in sets["hasexc"]:
            target = _jump_target(self.ops, name, arg, offset)
            if target is None:
                return None
            preposition = "from" if name == "END_ASYNC_FOR" and self.ops.version >= (3, 14) else "to"
            return target, f"{preposition} L{self.labels[target]}"
        if name in _PAIR_OPS and name in self.ops.numbers:
            first, second = self.fast[arg >> 4], self.fast[arg & 15]
            return (first, second), f"{first}, {second}"
        return None

    def _resolve_new(self, name: str, arg: int, table: Dict[str, Any]) -> Tuple[Any, str]:  # pylint: disable=too-many-return-statements  # one return per kind of argument
        """3.13 and later."""
        version = self.ops.version
        if name == "CONVERT_VALUE":
            return (None, str, repr, ascii)[arg], ("", "str", "repr", "ascii")[arg]
        if name == "SET_FUNCTION_ATTRIBUTE":
            return arg, ", ".join(s for i, s in enumerate(table["function_attr_flags"]) if arg & (1 << i))
        if name == "BINARY_OP":
            return arg, table["nb_ops"][arg]
        if name == "CALL_INTRINSIC_1":
            return arg, table["intrinsic_1"][arg]
        if name == "CALL_INTRINSIC_2":
            return arg, table["intrinsic_2"][arg]
        if version >= (3, 14):
            if name == "LOAD_COMMON_CONSTANT":
                return arg, table["common_constants"][arg]
            if name == "LOAD_SPECIAL":
                return arg, table["special_method_names"][arg]
            if name == "IS_OP":
                return arg, "is not" if arg else "is"
            if name == "CONTAINS_OP":
                return arg, "not in" if arg else "in"
        return arg, ""


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def _labels(ops: _Opcodes, instructions: List[Tuple[int, int, str, Optional[int]]]) -> set:
    targets = set()
    for offset, _, name, arg in instructions:
        if arg is not None:
            target = _jump_target(ops, name, arg, offset)
            if target is not None:
                targets.add(target)
    return targets


def decode_instructions(code: Any, version: Tuple[int, int]) -> List[BytecodeInstruction]:
    """The instructions of *code* (a code object, or a ``ForeignCode``) as Python *version* wrote them."""
    ops = opcodes_for(version)
    raw = list(_unpack(code.co_code, ops))
    targets = _labels(ops, raw)
    starts = line_starts(code, version)
    resolver = _Resolver(code, ops, {offset: i + 1 for i, offset in enumerate(sorted(targets))})
    result: List[BytecodeInstruction] = []
    for offset, op, name, arg in raw:
        argval: Any = None
        argrepr = ""
        if arg is not None:
            argval, argrepr = resolver.resolve(name, arg, offset)
        result.append(BytecodeInstruction(
            opcode=op, opname=name, arg=arg, argval=argval, offset=offset,
            starts_line=starts.get(offset), is_jump_target=offset in targets, argrepr=argrepr))
    return result


def stack_effect(version: Tuple[int, int], name: str, arg: Optional[int]) -> int:
    """``dis.stack_effect(opcode, arg, jump=False)`` for an instruction of Python *version*; raises ValueError for an unknown one."""
    entry = opcodes_for(version).table["stack"].get(name)
    if entry is None:
        raise ValueError(f"no stack effect for {name}")
    kind = entry[0]
    if kind == "c":
        return int(entry[1])
    if arg is None:
        raise ValueError(f"{name} needs an argument")     # as dis.stack_effect says for an opcode that takes one
    value = arg
    # (two arguments are not read one byte at a time: CPython counts the items before and after the starred target of
    # UNPACK_EX, and BUILD_SLICE compares the whole argument with 3)
    if name == "UNPACK_EX":
        return (value & 0xFF) + (value >> 8)
    if name == "BUILD_SLICE" and version < (3, 14):
        return -2 if value == 3 else -1
    if kind == "lin":
        return int(entry[1]) * value + int(entry[2])
    index = value & 0xFF
    for effect, count in entry[1]:
        if index < count:
            if effect is None:
                raise ValueError(f"invalid argument {value} for {name}")
            return int(effect)
        index -= count
    raise ValueError(f"invalid argument {value} for {name}")
