"""Structured diagnostics: what the decompiler could not reconstruct, and where.

A decompiler that cannot rebuild a construct has two honest choices. In *normal* mode
it still produces its best source, and records a :class:`Diagnostic` for every place it
had to guess, so the output can be marked as not to be trusted there. In *strict* mode
it stops at the first such place with a :class:`DecompilationError`, because a decompile
that guessed is not a decompile.
"""
from dataclasses import dataclass
from typing import Optional, Tuple

# What went wrong. A diagnostic's ``kind`` is one of these.
UNSUPPORTED_OPCODE = "unsupported_opcode"                    # no handler for this instruction
UNKNOWN_CONTROL_FLOW = "unknown_control_flow"                # jumps or blocks that do not form a known shape
STACK_UNDERFLOW = "stack_underflow"                          # an instruction needed an operand that is not there
AMBIGUOUS_EXPRESSION = "ambiguous_expression"                # an operator or operand that could not be determined
INCOMPLETE_EXCEPTION = "incomplete_exception_reconstruction"  # try/except/finally the backend cannot rebuild
UNSUPPORTED_VERSION_CONSTRUCT = "unsupported_version_construct"  # a construct this Python version's backend lacks
INTERNAL_ERROR = "internal_error"                            # the decompiler itself failed on this part

KINDS = (UNSUPPORTED_OPCODE, UNKNOWN_CONTROL_FLOW, STACK_UNDERFLOW, AMBIGUOUS_EXPRESSION,
         INCOMPLETE_EXCEPTION, UNSUPPORTED_VERSION_CONSTRUCT, INTERNAL_ERROR)


@dataclass(frozen=True)
class Diagnostic:
    """One place where reconstruction was uncertain."""

    kind: str
    reason: str
    python_version: Tuple[int, int]
    opcode: Optional[str] = None
    offset: Optional[int] = None
    code_name: Optional[str] = None  # the function, class or module whose code it was

    def __str__(self) -> str:
        where = []
        if self.code_name:
            where.append(f"in {self.code_name}")
        if self.opcode is not None:
            where.append(f"at {self.opcode}" + (f" (offset {self.offset})" if self.offset is not None else ""))
        elif self.offset is not None:
            where.append(f"at offset {self.offset}")
        place = " ".join(where)
        version = f"Python {self.python_version[0]}.{self.python_version[1]}"
        return f"{self.kind}: {self.reason}" + (f" [{place}; {version}]" if place else f" [{version}]")

    def as_dict(self) -> dict:
        return {"kind": self.kind, "reason": self.reason,
                "python_version": f"{self.python_version[0]}.{self.python_version[1]}",
                "opcode": self.opcode, "offset": self.offset, "code_name": self.code_name}


class DecompilationError(ValueError):
    """Raised in strict mode at the first diagnostic. ``diagnostic`` says what and where."""

    def __init__(self, diagnostic: Diagnostic):
        super().__init__(str(diagnostic))
        self.diagnostic = diagnostic


def comment_block(diagnostics) -> str:
    """The diagnostics as a block of ``#`` comment lines to put above decompiled source.

    Empty when there is nothing to report, so clean output is unchanged.
    """
    if not diagnostics:
        return ""
    count = len(diagnostics)
    lines = [f"# pycrefine: {count} place{'s' if count != 1 else ''} could not be reconstructed "
             "reliably; treat the source below as unverified there:"]
    lines.extend(f"#   - {d}" for d in diagnostics)
    return "\n".join(lines) + "\n"
