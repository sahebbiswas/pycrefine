"""pycrefine: a Python .pyc decompiler that rebuilds source code from compiled bytecode.

Every name that used to live in the single-file ``pycrefine`` module is re-exported here,
so ``import pycrefine`` and ``from pycrefine import ...`` behave as before.
"""
from ._constants import (  # noqa: F401
    _AUG_ASSIGN_MAP,
    _COMPOUND_EXPR_OPS,
    _COMPOUND_SKIP,
    _GENERIC_SKIP_OPS,
    _INPLACE_ASSIGN_MAP,
    _IS_COMP_GEN_SKIP,
    _IS_COMP_GEN_STOP,
    _MAX_TERNARY_MINI_SEARCH_WINDOW,
    _MAX_TERNARY_SEARCH_WINDOW,
    _MAX_TERNARY_SHORT_SEARCH_WINDOW,
    _MAX_WITH_EXIT_SEARCH_WINDOW,
    _NO_INDENT_TYPES,
    _NO_PASS_TYPES,
    _STATEMENT_BOUNDARY_OPS,
    _TERNARY_PURE,
    _TERNARY_SKIP,
    _TERNARY_STORES,
    _TERNARY_TERM,
    _TRY_HANDLER_EXIT_OPS,
    _TRY_LOAD_OPS,
    _TRY_LOOP_HEADS,
    _TRY_SKIP_NOP,
    _UNARY_SYMBOLS,
    _WHILE_TRIVIAL_OPS,
)
from .expr import StackNULL, _BoolExpr, _CollectionAccumSentinel, _unary_operand  # noqa: F401
from .instructions import (  # noqa: F401
    BytecodeInstruction,
    _is_anonymous_func_body,
    _render_func_tuple,
)
from .postprocess import (  # noqa: F401
    _block_opener_keyword,
    _collect_multiline_header,
    _line_is_in_triple_quoted_string,
    collapse_chained_comparisons,
    flatten_elif,
    post_process_source,
)
from .codeobj import ForeignCode, _is_code  # noqa: F401
from .decompiler.base import DecompilerBase  # noqa: F401
from .decompiler.generic import DecompilerGeneric, _pick_decompiler_class  # noqa: F401
from .opcodes.py39 import _OPCODES_39  # noqa: F401
from .decompiler.py39 import Decompiler39  # noqa: F401
from .decompiler.py311 import Decompiler311Plus  # noqa: F401
from .decompiler.py314 import Decompiler314  # noqa: F401
from .loader import (  # noqa: F401
    MarshalParser,
    _CO_FAST_CELL,
    _CO_FAST_FREE,
    _CO_FAST_LOCAL,
    _MARSHAL_NULL,
    get_decompiler,
)
from .versions import _get_python_version_from_magic  # noqa: F401
from .cli import main  # noqa: F401
