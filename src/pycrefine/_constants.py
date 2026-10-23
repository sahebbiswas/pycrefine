"""Shared constant instruction sets, lookup maps and search-window sizes."""


# --- Magic Numbers & Search Windows ---
_MAX_WITH_EXIT_SEARCH_WINDOW = 200


_MAX_TERNARY_SEARCH_WINDOW = 20


_MAX_TERNARY_SHORT_SEARCH_WINDOW = 12


_MAX_TERNARY_MINI_SEARCH_WINDOW = 8


# --- Constant Instruction Sets ---
_NO_PASS_TYPES = frozenset({"exc_cleanup", "finally_wrapper"})


_NO_INDENT_TYPES = frozenset({"finally_wrapper"})


_UNARY_SYMBOLS = {
    "UNARY_NEGATIVE": "-", "UNARY_POSITIVE": "+", "UNARY_INVERT": "~", "UNARY_NOT": "not ",
}


_TERNARY_STORES = frozenset(("STORE_FAST", "STORE_NAME", "STORE_GLOBAL", "STORE_DEREF"))


_TERNARY_SKIP = frozenset(("CACHE", "RESUME", "NOT_TAKEN", "COPY_FREE_VARS"))


_TERNARY_TERM = frozenset(("RETURN_CONST", "RETURN_VALUE"))


_TERNARY_PURE = frozenset((
    "LOAD_FAST", "LOAD_NAME", "LOAD_GLOBAL", "LOAD_CONST", "LOAD_DEREF",
    "LOAD_SMALL_INT", "LOAD_GLOBAL_MODULE", "LOAD_ATTR", "LOAD_METHOD",
    "GET_ATTR", "LOAD_FAST_BORROW", "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
    "LOAD_CONST_BORROW", "CALL", "CALL_FUNCTION", "CALL_METHOD",
    "COMPARE_OP", "BINARY_OP", "IS_OP", "CONTAINS_OP",
    "UNARY_NOT", "UNARY_NEGATIVE", "UNARY_POSITIVE", "UNARY_INVERT", "CALL_INTRINSIC_1",
    "BUILD_TUPLE", "BUILD_LIST", "BUILD_SET", "BUILD_MAP",
    "PRECALL", "PUSH_NULL", "FORMAT_VALUE", "FORMAT_SIMPLE", "BUILD_STRING",
    "BINARY_SUBSCR", "TO_BOOL", "BINARY_SLICE", "BUILD_SLICE",
    # Python 3.9 named binary opcodes (3.11+ uses BINARY_OP instead):
    "BINARY_ADD", "BINARY_SUBTRACT", "BINARY_MULTIPLY", "BINARY_TRUE_DIVIDE",
    "BINARY_FLOOR_DIVIDE", "BINARY_MODULO", "BINARY_POWER", "BINARY_LSHIFT",
    "BINARY_RSHIFT", "BINARY_AND", "BINARY_OR", "BINARY_XOR",
    "BINARY_MATRIX_MULTIPLY",
)) | _TERNARY_SKIP | _TERNARY_TERM


_COMPOUND_SKIP = frozenset(("CACHE", "RESUME", "NOT_TAKEN", "COPY_FREE_VARS", "PRECALL", "PUSH_NULL"))


_COMPOUND_EXPR_OPS = frozenset((
    "LOAD_FAST", "LOAD_NAME", "LOAD_GLOBAL", "LOAD_CONST", "LOAD_DEREF",
    "LOAD_SMALL_INT", "LOAD_FAST_BORROW", "LOAD_CONST_BORROW", "LOAD_FAST_BORROW_LOAD_FAST_BORROW",
    "LOAD_GLOBAL_MODULE", "LOAD_ATTR", "LOAD_METHOD", "GET_ATTR",
    "CALL", "CALL_FUNCTION", "CALL_METHOD", "COMPARE_OP", "BINARY_OP", "IS_OP", "CONTAINS_OP",
    "UNARY_NOT", "UNARY_NEGATIVE", "UNARY_POSITIVE", "UNARY_INVERT",
    "BUILD_TUPLE", "BUILD_LIST", "BUILD_SET", "BUILD_MAP",
    "TO_BOOL", "FORMAT_VALUE", "FORMAT_SIMPLE", "BUILD_STRING", "BINARY_SUBSCR", "BINARY_SLICE", "BUILD_SLICE",
    "DUP_TOP", "DUP_TOP_TWO", "ROT_TWO", "ROT_THREE", "ROT_FOUR", "SWAP", "COPY",
    # Python 3.9 binary opcodes:
    "BINARY_ADD", "BINARY_SUBTRACT", "BINARY_MULTIPLY", "BINARY_TRUE_DIVIDE",
    "BINARY_FLOOR_DIVIDE", "BINARY_MODULO", "BINARY_POWER", "BINARY_LSHIFT",
    "BINARY_RSHIFT", "BINARY_AND", "BINARY_OR", "BINARY_XOR",
    "BINARY_MATRIX_MULTIPLY"
)) | _COMPOUND_SKIP


_WHILE_TRIVIAL_OPS = frozenset({"NOP", "RESUME", "NOT_TAKEN", "CACHE", "COPY_FREE_VARS"})


_STATEMENT_BOUNDARY_OPS = frozenset({
    "POP_TOP", "POP_ITER", "END_FOR", "RETURN_VALUE", "RETURN_CONST", "RAISE_VARARGS",
    "RERAISE", "POP_BLOCK", "PUSH_EXC_INFO", "POP_EXCEPT",
})


_TRY_SKIP_NOP = frozenset({"RESUME", "CACHE", "NOT_TAKEN"})

# Placeholders between a protected range's end and the code that follows it (a with
# block's inlined exit call, a finally copy): skipped when matching positions.
_FILLER_OPS = frozenset({"NOP", "CACHE"})


_TRY_LOAD_OPS = frozenset({"LOAD_NAME", "LOAD_GLOBAL", "LOAD_FAST",
                          "LOAD_DEREF", "LOAD_SMALL_INT", "LOAD_GLOBAL_MODULE"})


_TRY_LOOP_HEADS = frozenset({"FOR_ITER", "GET_ITER", "GET_ANEXT", "GET_AWAITABLE"})


_TRY_HANDLER_EXIT_OPS = frozenset({"POP_EXCEPT", "DELETE_FAST", "DELETE_NAME",
                                  "DELETE_GLOBAL", "STORE_FAST", "STORE_NAME"})


_IS_COMP_GEN_SKIP = frozenset({"POP_TOP", "CACHE", "NOP", "RESUME", "COPY", "NOT_TAKEN"})


_IS_COMP_GEN_STOP = frozenset({"LOAD_CONST", "LOAD_NAME", "LOAD_FAST", "LOAD_GLOBAL", "LOAD_DEREF", "LOAD_SMALL_INT",
                              "LOAD_ATTR", "PUSH_NULL", "RERAISE", "RAISE_VARARGS", "RETURN_CONST", "RETURN_VALUE", "JUMP_FORWARD"})


_GENERIC_SKIP_OPS = frozenset({"RESUME", "CACHE", "NOP", "NOT_TAKEN", "COPY_FREE_VARS", "EXTENDED_ARG"})


# --- Augmented Assignment Opcodes ---
_AUG_ASSIGN_MAP = {
    13: "+=",  14: "&=",  15: "//=", 16: "<<=", 17: "@=",
    18: "*=",  19: "%=",  20: "|=",  21: "**=", 22: ">>=",
    23: "-=",  24: "/=",  25: "^=",
}


_INPLACE_ASSIGN_MAP = {
    "INPLACE_ADD": "+=", "INPLACE_SUBTRACT": "-=",
    "INPLACE_MULTIPLY": "*=", "INPLACE_TRUE_DIVIDE": "/=",
    "INPLACE_FLOOR_DIVIDE": "//=", "INPLACE_MODULO": "%=",
    "INPLACE_POWER": "**=", "INPLACE_LSHIFT": "<<=",
    "INPLACE_RSHIFT": ">>=", "INPLACE_AND": "&=",
    "INPLACE_OR": "|=", "INPLACE_XOR": "^=",
    "INPLACE_MATRIX_MULTIPLY": "@=",
}


# --- Value-pushing opcodes (prefixes) ---
# Instructions that push a value, such as the callee of a call before its arguments;
# an inlined `with` exit call never directly follows one of them.
_VALUE_OPS = ("LOAD_", "PUSH_NULL", "CALL", "BINARY_", "BUILD_", "COPY", "GET_", "UNARY_",
              "COMPARE_", "FORMAT_", "MAKE_FUNCTION", "CONTAINS_OP", "IS_OP")
