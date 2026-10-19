"""pycrefine: a Python .pyc decompiler.

Temporary scaffolding for the package move: re-exports every name of the former
single-file module so existing imports keep working while the code is split up.
"""
from .decompiler import generic as _monolith

globals().update(
    {k: v for k, v in vars(_monolith).items() if not (k.startswith("__") and k.endswith("__"))}
)
