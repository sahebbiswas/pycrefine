"""Read 3.10's block-stack exception handling in the form the 3.11+ backend understands (#179).

3.10 protects a `try` body with a `SETUP_FINALLY` ... `POP_BLOCK` pair and starts the handler with
three values on the stack; 3.11 replaced that with an exception table, `PUSH_EXC_INFO`,
`CHECK_EXC_MATCH` and one value. `normalise_block_handlers` rewrites a 3.10 instruction list into
that spelling and computes the matching exception table, as the 3.11 spellings of loops and
conditions are produced for 3.10 elsewhere (#4). Instructions are renumbered (two bytes each),
because one 3.10 instruction can become two.
"""
from dataclasses import replace
from typing import Dict, List, Optional, Set, Tuple

from ..instructions import BytecodeInstruction

try:  # 3.11+ names the entry type; 3.10 has none, and nothing else reads this one
    from dis import _ExceptionTableEntry as _Entry  # type: ignore[attr-defined]
except ImportError:
    from collections import namedtuple
    _Entry = namedtuple("_ExceptionTableEntry", "start end target depth lasti")  # type: ignore[misc]

_SETUP_OPS = frozenset(("SETUP_FINALLY", "SETUP_WITH", "SETUP_ASYNC_WITH"))
_TERMINATORS = frozenset(("RETURN_VALUE", "RAISE_VARARGS", "RERAISE", "JUMP_FORWARD", "JUMP_ABSOLUTE"))
_STORES = frozenset(("STORE_FAST", "STORE_NAME", "STORE_GLOBAL", "STORE_DEREF"))


def has_block_handlers(instructions: List[BytecodeInstruction]) -> bool:
    return any(ins.opname in _SETUP_OPS for ins in instructions)


def _jump_target(ins: BytecodeInstruction) -> Optional[int]:
    if ins.opname in _SETUP_OPS or "JUMP" in ins.opname or ins.opname == "FOR_ITER":
        return ins.argval if isinstance(ins.argval, int) else None
    return None


def _cover(instructions: List[BytecodeInstruction]) -> Dict[int, int]:
    """``{instruction index: handler offset}`` for every instruction inside a SETUP block."""
    by_offset = {ins.offset: i for i, ins in enumerate(instructions)}
    covered: Dict[int, int] = {}
    seen: Set[Tuple[int, Tuple]] = set()
    work: List[Tuple[int, Tuple]] = [(0, ())]
    while work:
        i, stack = work.pop()
        while 0 <= i < len(instructions):
            key = (i, stack)
            if key in seen:
                break
            seen.add(key)
            ins = instructions[i]
            blocks = [b for b in stack if b[0] == "B"]
            if blocks:
                covered.setdefault(i, blocks[-1][1])
            name = ins.opname
            if name in _SETUP_OPS:
                handler = by_offset.get(ins.argval)
                if handler is not None:
                    work.append((handler, stack + (("E",),)))
                stack = stack + (("B", ins.argval),)
            elif name in ("POP_BLOCK", "POP_EXCEPT"):
                stack = stack[:-1]
            else:
                target = _jump_target(ins)
                if target is not None and target in by_offset:
                    work.append((by_offset[target], stack))
                if name == "JUMP_IF_NOT_EXC_MATCH":
                    pass
            if name in _TERMINATORS:
                break
            i += 1
    return covered


_EXIT_PRECEDERS = frozenset(("POP_EXCEPT", "POP_TOP", "DELETE_FAST", "DELETE_NAME", "DELETE_GLOBAL", "DELETE_DEREF", "POP_BLOCK"))


def _is_with_exit_call(instructions: List[BytecodeInstruction], i: int) -> bool:
    """`LOAD_CONST None; DUP_TOP; DUP_TOP; CALL_FUNCTION 3`: the normal exit of a `with`."""
    window = instructions[i:i + 4]
    return (len(window) == 4 and window[0].argval is None
            and [x.opname for x in window[1:]] == ["DUP_TOP", "DUP_TOP", "CALL_FUNCTION"] and window[3].arg == 3)


def _follows_with_exit_call(instructions: List[BytecodeInstruction], i: int) -> bool:
    """True if the instruction at *i* follows the result-discarding `POP_TOP` of a `with` exit call."""
    return (i > 1 and instructions[i - 1].opname == "POP_TOP" and instructions[i - 2].opname == "CALL_FUNCTION"
            and instructions[i - 2].arg == 3)


def _is_with_suppress(instructions: List[BytecodeInstruction], i: int) -> bool:
    """`POP_TOP x3; POP_EXCEPT; POP_TOP`: the end of a `with` handler that swallowed the error."""
    return [x.opname for x in instructions[i:i + 5]] == ["POP_TOP", "POP_TOP", "POP_TOP", "POP_EXCEPT", "POP_TOP"]


_JUMPS = frozenset(("JUMP_FORWARD", "JUMP_ABSOLUTE", "FOR_ITER", "SETUP_FINALLY", "SETUP_WITH", "SETUP_ASYNC_WITH"))
_END_OF_PATH = frozenset(("RETURN_VALUE", "RERAISE", "RAISE_VARARGS"))
_MAX_COPY = 40


def _starts_copy(instructions: List[BytecodeInstruction], s: int) -> bool:
    """True if a copy of an exit block can start at *s*: it follows the end of a path, or a handler's end."""
    prev = instructions[s - 1]
    if prev.opname in _TERMINATORS or prev.opname in ("POP_EXCEPT", "POP_BLOCK") or prev.opname.startswith("DELETE_"):
        return True
    # the result of the exit call of a `with`, and the end of the handler that swallowed an error
    return (s > 1 and prev.opname == "POP_TOP"
            and (instructions[s - 2].opname == "POP_EXCEPT"
                 or (instructions[s - 2].opname == "CALL_FUNCTION" and instructions[s - 2].arg == 3)))


def _straight_tail(instructions: List[BytecodeInstruction], s: int) -> Optional[int]:
    """Length of the straight-line run at *s* that ends the path (a return or a raise), or None."""
    k = s
    while k < len(instructions) and k - s < _MAX_COPY:
        ins = instructions[k]
        if (k > s and ins.is_jump_target) or "JUMP" in ins.opname or ins.opname in _JUMPS | {"POP_BLOCK", "POP_EXCEPT"}:
            return None
        k += 1
        if ins.opname in _END_OF_PATH:
            return k - s if k - s >= 2 else None
    return None


def _copied_exits(instructions: List[BytecodeInstruction]) -> Dict[int, Tuple[int, int]]:
    """
    ``{index where a copy starts: (offset of the shared copy, instructions in the copy)}``.

    3.10 copies the code that ends a path (the `finally` body and the `return` that follow a `try`)
    onto the end of the try body and of each handler, keeping the line numbers of the original; 3.11
    jumps to one shared copy. Copies are runs of straight-line code that end the path, with the
    same instructions on the same lines. The last copy is the shared one.
    """
    groups: Dict[Tuple, List[Tuple[int, int]]] = {}
    for s in range(1, len(instructions)):
        if not _starts_copy(instructions, s):
            continue
        # the `POP_BLOCK` that ends a `finally`'s protected range is not part of the copy
        length = _straight_tail(instructions, s)
        if length is None:
            continue
        key = tuple((x.opname, x.argval if not isinstance(x.argval, int) or x.opname.startswith("LOAD")
                     or x.opname in ("RERAISE", "RAISE_VARARGS") else None, x.starts_line)
                    for x in instructions[s:s + length])
        if instructions[s].starts_line is None and instructions[s].opname == "LOAD_CONST":
            pass    # a copy of an implicit `return None` carries no line of its own
        groups.setdefault(key, []).append((s, length))
    copies: Dict[int, Tuple[int, int]] = {}
    covered: Set[int] = set()
    for key, members in sorted(groups.items(), key=lambda item: -len(item[0])):
        members = [m for m in members if not covered & set(range(m[0], m[0] + m[1]))]
        if len(members) < 2 or not instructions[members[0][0]].starts_line:
            continue
        main_start, _ = members[-1]
        for position, (start, length) in enumerate(members[:-1]):
            after_with = _follows_with_exit_call(instructions, start)
            target = members[position + 1][0] if after_with else main_start
            copies[start] = (instructions[target].offset, length)
            covered.update(range(start, start + length))
        covered.update(range(main_start, main_start + members[-1][1]))
    return copies


class _Rewriter:  # pylint: disable=too-many-instance-attributes,too-few-public-methods
    """Writes the 3.11 spelling of 3.10's block-based handlers, one instruction (or one group) at a time."""

    def __init__(self, instructions: List[BytecodeInstruction]) -> None:
        self.instructions = instructions
        self.by_offset = {ins.offset: i for i, ins in enumerate(instructions)}
        setups = {self.by_offset[ins.argval] for ins in instructions
                  if ins.opname in ("SETUP_FINALLY", "SETUP_WITH") and ins.argval in self.by_offset}
        # a handler is entered from the `try`; the cleanup of `except ... as name` starts with LOAD_CONST None
        self.handlers = {h for h in setups
                         if not (instructions[h].opname == "LOAD_CONST" and instructions[h].argval is None)}
        self.cleanups = setups - self.handlers
        self.next_clause = {self.by_offset[ins.argval] for ins in instructions
                            if ins.opname == "JUMP_IF_NOT_EXC_MATCH" and ins.argval in self.by_offset}
        self.copies = _copied_exits(instructions)
        self.out: List[Tuple[BytecodeInstruction, Optional[int]]] = []   # (new instruction, old index)
        self.first_new: Dict[int, int] = {}     # old index -> position in out of its first replacement

    def run(self) -> None:
        n = len(self.instructions)
        i = 0
        while i < n:
            self.first_new[i] = len(self.out)
            i = self._step(i)
        self.first_new[n] = len(self.out)
        # an old index that was dropped continues at the next kept one
        for k in range(n - 1, -1, -1):
            self.first_new.setdefault(k, self.first_new[k + 1])

    def _emit(self, ins: BytecodeInstruction, old: int, **changes) -> None:
        self.out.append((replace(ins, **changes) if changes else ins, old))

    def _fold(self, first: int, last: int) -> None:
        """Old instructions *first* to *last* are written as what has just been emitted (or nothing)."""
        for k in range(first, last + 1):
            self.first_new[k] = len(self.out)

    def _step(self, i: int) -> int:
        for rule in (self._setup, self._with, self._copy, self._handler_start, self._match):
            following = rule(i, self.instructions[i])
            if following is not None:
                return following
        self.out.append((self.instructions[i], i))
        return i + 1

    def _setup(self, i: int, ins: BytecodeInstruction) -> Optional[int]:
        name = ins.opname
        if name == "SETUP_FINALLY":
            after_setup = i > 0 and self.instructions[i - 1].opname == "SETUP_FINALLY"
            if self.by_offset.get(ins.argval) not in self.cleanups and not after_setup:
                # (the cleanup of `except ... as name` has only a table entry in 3.11, and the
                # pair of a `try`/`except`/`finally` has one NOP)
                self._emit(ins, i, opname="NOP", opcode=9, arg=None, argval=None, argrepr="")
            return i + 1
        if name == "SETUP_WITH":
            self._emit(ins, i, opname="BEFORE_WITH", arg=None, argval=None, argrepr="")
            return i + 1
        return i + 1 if name == "POP_BLOCK" else None

    def _with(self, i: int, ins: BytecodeInstruction) -> Optional[int]:
        instructions, name = self.instructions, ins.opname
        if name == "LOAD_CONST" and _is_with_exit_call(instructions, i):
            for k in range(3):
                self._emit(ins, i, starts_line=ins.starts_line if k == 0 else None)
            call = instructions[i + 3]
            self._emit(call, i + 3, opname="PRECALL", arg=2, argval=2, argrepr="", starts_line=None)
            self._emit(call, i + 3, opname="CALL", arg=2, argval=2, argrepr="", starts_line=None)
            self.first_new.update({i + 1: self.first_new[i] + 1, i + 2: self.first_new[i] + 2,
                                   i + 3: self.first_new[i] + 3})
            return i + 4
        if name == "ROT_TWO" and (_is_with_exit_call(instructions, i + 1) or _follows_with_exit_call(instructions, i)):
            # a `return` that leaves a `with`: the value goes under the exit function and back
            self._emit(ins, i, opname="SWAP", opcode=0, arg=2, argval=2, argrepr="")
            return i + 1
        if name == "RERAISE" and i > 1 and instructions[i - 2].opname == "WITH_EXCEPT_START":
            self._emit(ins, i, arg=2, argval=2)
            return i + 1
        if name == "POP_TOP" and _is_with_suppress(instructions, i):
            self.out.append((ins, i))
            self._emit(instructions[i + 3], i + 3, starts_line=None)
            self._emit(instructions[i + 4], i + 4, starts_line=None)
            self._emit(instructions[i + 1], i + 1, starts_line=None, is_jump_target=False)
            self.first_new.update({i + k: self.first_new[i] + k for k in range(1, 5)})
            return i + 5
        return None

    def _copy(self, i: int, ins: BytecodeInstruction) -> Optional[int]:
        if i not in self.copies:
            return None
        target, length = self.copies[i]
        self._emit(ins, i, opname="JUMP_FORWARD", opcode=110, arg=None, argval=target, argrepr="", starts_line=None)
        self._fold(i + 1, i + length)
        return i + length

    def _handler_start(self, i: int, ins: BytecodeInstruction) -> Optional[int]:
        name = ins.opname
        push = {"opname": "PUSH_EXC_INFO", "opcode": 0, "arg": None, "argval": None, "argrepr": ""}
        three_pops = [x.opname for x in self.instructions[i:i + 3]] == ["POP_TOP"] * 3
        if name == "DUP_TOP" and i in self.handlers:
            self._emit(ins, i, **push)
            return i + 1
        if name == "DUP_TOP" and i in self.next_clause:
            return i + 1
        if name == "POP_TOP" and three_pops and (i in self.handlers or i in self.next_clause):
            # a bare `except:`: three values are popped, 3.11 has one (and a handler pushes the old state)
            if i in self.handlers:
                self._emit(ins, i, **push)
            self.out.append((ins, i))
            self._fold(i + 1, i + 2)
            return i + 3
        if i in self.handlers and name not in ("POP_TOP", "DUP_TOP"):
            # a `finally` handler: 3.11 starts it with PUSH_EXC_INFO as well
            self._emit(ins, i, **push)
            self.out.append((replace(ins, starts_line=None), i))
            return i + 1
        return None

    def _match(self, i: int, ins: BytecodeInstruction) -> Optional[int]:
        if ins.opname != "JUMP_IF_NOT_EXC_MATCH":
            return None
        self._emit(ins, i, opname="CHECK_EXC_MATCH", arg=None, argval=None, argrepr="", is_jump_target=False)
        self._emit(ins, i, opname="POP_JUMP_FORWARD_IF_FALSE", is_jump_target=False)
        self._fold(i + 1, i + 3)    # the three values the match leaves, as one
        window = self.instructions[i + 1:i + 4]
        if len(window) == 3 and window[0].opname == "POP_TOP" and window[2].opname == "POP_TOP":
            if window[1].opname in _STORES:
                self.out.append((window[1], i + 2))
                return i + 4
            if window[1].opname == "POP_TOP":
                self.out.append((window[0], i + 1))
                return i + 4
        return i + 1


def normalise_block_handlers(
        instructions: List[BytecodeInstruction]) -> Tuple[List[BytecodeInstruction], List]:
    """The 3.11-style instruction list for *instructions*, and its exception table."""
    covered = _cover(instructions)
    rewriter = _Rewriter(instructions)
    rewriter.run()
    by_offset, first_new, handlers, n = rewriter.by_offset, rewriter.first_new, rewriter.handlers, len(instructions)
    out, newpos = _move_else_clauses(instructions, rewriter.out, first_new, by_offset)

    def position(old_index: int) -> int:
        return newpos[first_new[old_index]]

    out, newpos2, cleanup_at = _add_cleanups(out, handlers, by_offset, position)
    new_offset = [2 * p for p in range(len(out) + 1)]
    old_to_new = {instructions[k].offset: new_offset[newpos2[position(k)]] for k in range(n)}
    result: List[BytecodeInstruction] = []
    targets: Set[int] = set()
    for p, (ins, _) in enumerate(out):
        target = _jump_target(ins)
        argval = ins.argval
        if target is not None and ins.opname != "NOP":
            argval = old_to_new.get(target, target)
            targets.add(argval)
        result.append(replace(ins, offset=new_offset[p], argval=argval,
                              argrepr=f"to {argval}" if target is not None and ins.opname != "NOP" else ins.argrepr,
                              is_jump_target=False))
    for ins in result:
        if ins.offset in targets:
            ins.is_jump_target = True
    # a dropped instruction can carry the line number of the one after it
    for p, (ins, old) in enumerate(out):
        if (old is not None and instructions[old].starts_line and not result[p].starts_line
                and result[p].opname != "JUMP_FORWARD"):
            result[p] = replace(result[p], starts_line=instructions[old].starts_line)

    return result, _table(out, covered, old_to_new, new_offset, cleanup_at)


def _move_else_clauses(instructions, out, first_new, by_offset):
    """
    Put the `else` clause of a `try` ahead of its handlers, where 3.11 lays it out.

    3.10 ends the body with a jump over the handlers to the else code, which follows them;
    3.11 falls into the else code and ends it with a jump to the code after the statement.
    Returns the reordered ``out`` and ``{original position: new position}``.
    """
    entries = [[ins, old, p] for p, (ins, old) in enumerate(out)]
    removed: Dict[int, int] = {}
    end_key = len(out)
    for q_key, (q_ins, q_old) in enumerate(out):
        if (q_ins.opname != "JUMP_FORWARD" or q_old is None or q_old == 0
                or instructions[q_old - 1].opname != "POP_BLOCK" or q_ins.argval not in by_offset):
            continue
        a_key = first_new[by_offset[q_ins.argval]]
        if a_key <= q_key:
            continue
        exits = [e[0].argval for e in entries
                 if e[2] is not None and q_key < e[2] < a_key and e[1] is not None and e[0].opname == "JUMP_FORWARD"
                 and e[1] > 0 and instructions[e[1] - 1].opname in _EXIT_PRECEDERS
                 and isinstance(e[0].argval, int) and e[0].argval in by_offset]
        if not exits:
            continue
        join = max((exits.count(v), v) for v in set(exits))[1]     # the exit most handlers jump to
        j_key = first_new[by_offset[join]]
        if j_key <= a_key:
            continue
        keys = [e[2] for e in entries]
        q, a, j = keys.index(q_key), keys.index(a_key) if a_key in keys else None, None
        if a is None:
            continue
        j = keys.index(j_key) if j_key in keys else len(entries)
        block = entries[a:j]
        withs = sum(e[0].opname == "BEFORE_WITH" for e in block)
        if sum(e[0].opname == "PRECALL" and e[0].arg == 2 for e in block) > withs:
            continue        # the exit call of a `with` that opened outside the block, not an `else`
        ends_path = block and block[-1][0].opname in ("JUMP_FORWARD", "RETURN_VALUE", "RERAISE", "RAISE_VARARGS")
        jump = [] if ends_path else [[replace(q_ins, argval=join, argrepr=""), None, None]]
        removed[q_key] = q
        entries = entries[:q] + block + jump + entries[q + 1:a] + entries[j:]
    keys = [e[2] for e in entries]
    newpos = {k: i for i, k in enumerate(keys) if k is not None}
    # a removed jump stands where the clause it led to now begins
    for q_key, position in removed.items():
        newpos[q_key] = position
    newpos[end_key] = len(entries)
    return [(e[0], e[1]) for e in entries], newpos


_TAIL_ENDS = frozenset(("JUMP_FORWARD", "JUMP_ABSOLUTE", "RETURN_VALUE", "RERAISE", "RAISE_VARARGS"))


def _add_cleanups(out, handlers, by_offset, position):
    """
    Add the ``COPY 3; POP_EXCEPT; RERAISE 1`` block 3.11 puts after the handlers of a `try`,
    which its exception table names as the target for an error raised inside a handler.

    Returns the new ``out``, ``{old position: new position}`` and ``{start position of the
    handler code: (end position, position of the cleanup)}`` in the new numbering.
    """
    regions = []
    for h in sorted(handlers):
        start = position(h)
        furthest = start
        pos = start
        while pos < len(out):
            ins = out[pos][0]
            if "POP_JUMP" in ins.opname and isinstance(ins.argval, int) and ins.argval in by_offset:
                furthest = max(furthest, position(by_offset[ins.argval]))
            pos += 1
            if ins.opname in _TAIL_ENDS and pos > furthest:
                break
        insert = pos
        if out[start + 1][0].opname == "WITH_EXCEPT_START":
            # 3.11 puts the cleanup of a `with` handler between its re-raise and the suppress path
            insert = next((position(by_offset[x.argval]) for x, _ in out[start:pos]
                           if x.opname == "POP_JUMP_IF_TRUE" and x.argval in by_offset), pos)
        regions.append((start, pos, insert))
    insert_at = {ins: start for start, _, ins in regions}
    end_of = {start: end for start, end, _ in regions}
    new_out: List = []
    newpos: Dict[int, int] = {}
    cleanup_at: Dict[int, Tuple[int, int]] = {}
    for p in range(len(out) + 1):
        if p in insert_at:
            base = len(new_out)
            filler = out[min(p, len(out) - 1)][0]
            for name, arg in (("COPY", 3), ("POP_EXCEPT", None), ("RERAISE", 1)):
                new_out.append((replace(filler, opname=name, opcode=0, arg=arg, argval=arg, argrepr="",
                                        starts_line=None, is_jump_target=False), None))
            cleanup_at[insert_at[p]] = (end_of[insert_at[p]], base)
        newpos[p] = len(new_out)
        if p < len(out):
            new_out.append(out[p])
    # the positions recorded are in the numbering before the cleanups were added
    shifted = {}
    for start, (end, base) in cleanup_at.items():
        shifted[newpos[start]] = (newpos[end] - (3 if end in insert_at else 0), base)
    return new_out, newpos, shifted


def _table(out, covered, old_to_new, new_offset, cleanup_at) -> List:
    """The exception table: the protected bodies, and the handler code covered by its cleanup."""
    target: List[Optional[int]] = []
    for ins, old in out:
        handler = covered.get(old) if old is not None else None
        target.append(old_to_new.get(handler) if handler is not None else None)
    for start, (end, cleanup) in cleanup_at.items():
        skip = False
        for pos in range(start, end):
            if out[pos][1] is None:
                continue        # the cleanup block itself
            name = out[pos][0].opname
            if name == "POP_EXCEPT":
                skip = True
            inside = target[pos] is not None and new_offset[start] <= target[pos] < new_offset[end]
            if not skip and (target[pos] is None or not inside):
                target[pos] = new_offset[cleanup]
            if skip and name in _TAIL_ENDS:
                skip = False
    cleanup_targets = {new_offset[i] for i, (ins, _) in enumerate(out) if ins.opname != "PUSH_EXC_INFO"}
    entries: List = []
    run_start = 0
    for pos in range(len(out) + 1):
        current = target[pos] if pos < len(out) else None
        before = target[run_start] if run_start < len(out) else None
        if pos == len(out) or current != before:
            if before is not None and pos > run_start:
                handler_code = before in {new_offset[c] for _, c in cleanup_at.values()} or before in cleanup_targets
                entries.append(_Entry(new_offset[run_start], new_offset[pos], before,
                                      1 if handler_code else 0, handler_code))
            run_start = pos
    return entries
