"""Split the superinstructions of 3.13+ into the instructions they stand for."""
from typing import List

from ..instructions import BytecodeInstruction


def split_superinstructions(body: List[BytecodeInstruction]) -> List[BytecodeInstruction]:
    """
    Split 3.13+'s ``STORE_FAST_LOAD_FAST`` into a ``STORE_FAST`` and a ``LOAD_FAST``.

    The ``LOAD_FAST`` gets the odd offset+1, which no jump can target, so offset-keyed lookups
    keep resolving to the ``STORE_FAST``. Every other instruction is returned as it is.
    """
    out: List[BytecodeInstruction] = []
    for ins in body:
        if ins.opname == "STORE_FAST_LOAD_FAST" and isinstance(ins.argval, (tuple, list)):
            store, load = ins.argval
            out.append(BytecodeInstruction(ins.opcode, "STORE_FAST", None, store,
                                           ins.offset, ins.starts_line, ins.is_jump_target))
            out.append(BytecodeInstruction(ins.opcode, "LOAD_FAST", None, load,
                                           ins.offset + 1, None, False))
        else:
            out.append(ins)
    return out
