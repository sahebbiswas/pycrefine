"""Code-object helpers shared by the loader and the decompilers (``ForeignCode`` for foreign-version .pyc files)."""
import types
from typing import Any


class ForeignCode:
    """
    A code object read from a .pyc of a different Python version.

    The host interpreter cannot build a real ``types.CodeType`` from another
    version's bytecode, so the parsed fields are kept as plain ``co_*``
    attributes. The decompilers only read those attributes.
    """

    co_name: str

    def __init__(self, **fields: Any):
        self.__dict__.update(fields)

    def __repr__(self) -> str:
        return f"<ForeignCode {self.co_name!r}>"


def _is_code(obj: Any) -> bool:
    """True for a host code object or one parsed from another Python version."""
    return isinstance(obj, (types.CodeType, ForeignCode))
