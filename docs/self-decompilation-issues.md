# pycrefine.py — Self-Decompilation Issue Registry

> **Scope:** Analysis of `pycrefine_v2.py` decompiling its own `.pyc` (Python 3.12, CPython).
> Issues are ranked by severity within each category. Each entry includes the source construct,
> the decompiled output, and a description of the impact.
>
> **Fix classification:**
> - **Track A** — bug in `pycrefine` source itself (Python 3.12 compat gap); fix the handler
> - **Track B** — correct source, wrong decompiled output; fix the decompiler's reconstruction logic
> - **✅ Fixed** — resolved in v2 relative to v1

---

## 🔴 Category 1: Syntax Errors — File Cannot Be Compiled

These issues prevent the decompiled file from being parsed by Python at all.

---

### Issue 1 — Chained augmented-assign expression (FIXED)
**Track B | Priority: P0-C** *(Historical context)*

Consecutive augmented assignment statements were fused into single invalid expressions.

**Current Status:** FIXED in v2. The decompiler now correctly detects when an augmented assignment result is the end of a statement (followed by a store) and emits it as a standalone statement, advancing the program counter past the store. Defensive stack-popping for `STORE_ATTR` prevents residual receivers from leaking into subsequent lines.

**Historical Decompiled output:**
```python
self._append_reconstructed('else:')
(self.indent_level += 1).indent_level = (self.indent_level -= 1)
```

---

### Issue 2 — Negative integer attribute access (FIXED)
**Track B | Priority: P2-B** *(Historical context)*

Attribute access on negative numeric literals produced invalid decimal literals like `-1.offset`.

**Current Status:** FIXED. Implementation in `_op_load_attr` and `_op_call` now uses `_wrap_negative_literal()` to ensure negative literals are parenthesized: `(-1).offset` or `(-1).method()`.

**Historical Decompiled output:**
```python
end_off = -1.offset + 2
```

---

### Issue 3 — Orphaned `except` block from `try` scope nesting (1 site)
**Track B | Priority: P2-A**

A `try` block nested inside a conditional branch has its `except` clause emitted at the wrong indentation level, creating an `except` with no matching `try`.

**Source:**
```python
if balanced and depth == 0:
    try:
        node = ast.parse(inside, mode='eval')
        if not isinstance(node.body, ast.Tuple):
            inner = inside
    except Exception:
        # fallback comma scan
        ...
```

**Decompiled output:**
```python
            else:
                if balanced and depth == 0:
                    try:
                        node = ast.parse(inside, mode='eval')
                        if not isinstance(node.body, ast.Tuple):
                            inner = inside
        except Exception:         # ← one level too shallow
            ...
        finally:
            prefix
            inner
            ')'
```

**Root cause:** The `for char in inside:` loop that precedes the `try` is reconstructed with a spurious `else:` clause (for/else), pushing the `try` one indent level deeper than its `except`. Additionally, the `finally` block emits stray bare-name statements (`prefix`, `inner`, `')'`) from stack residuals.

**Impact:** `SyntaxError: expected 'except' or 'finally' block` at line 319, cascading through the rest of `post_process_source` and making the entire file uncompilable.

---

### Issue 4 — Bare `?` placeholder leaked into live conditions (8 sites)
**Track B | Priority: P0 (SyntaxError + semantic)**

The fallback string `"?"` returned when `self.stack.pop()` is called on an empty stack appears verbatim inside `if`, `while`, and f-string expressions.

**Source:**
```python
if 3410 <= version_id <= 3429:
    dec = Decompiler39(...)
```

**Decompiled output:**
```python
if ? and 3429:
    dec = Decompiler39(...)
```

Other affected patterns:
```python
if ? and self.reconstructed[last_idx].strip() == 'return None':
while ? and self.instructions[look].opname in (...):
self.stack.append(f"({left} ? {right})")   # operator lost
int(ins.arg) if ins.arg is not None else ?(-1, '?')
```

**Root cause:** Chained comparison `3410 <= version_id <= 3429` uses `SWAP 2` + `COPY 2` before the first `COMPARE_OP`, which the decompiler does not distinguish from a plain `and`-shortcircuit. The left-hand result is consumed, leaving the stack empty when the right-hand side pops it. The `?` sentinel surfaces in the output.

**Impact:** `SyntaxError` at 7 of the 8 sites. One site produces a runtime `TypeError`. `_get_python_version_from_magic`, `_op_check_exc_match`, `_op_nop`, and `get_decompiler` are all affected.

---

### Issue 5 — Unterminated triple-quoted f-string (1 site)
**Track B | Priority: P1**

A multi-line conditional `msg +=` block is fused into a single malformed triple-quoted f-string that runs into the next function, causing a tokenization failure.

**Source:**
```python
if input_ver:
    msg += f"\n- Input file appears to be from Python {input_ver}."
else:
    msg += "\n- Input file version is unrecognized or may be corrupt."
```

**Decompiled output:**
```python
msg += f"""
- Input file appears to be from Python ""{input_ver}." if input_ver else """
- Input file version is unrecognized or may be corrupt."""
```

**Root cause:** The conditional `msg +=` branches share a common string-building bytecode prefix; the decompiler merges them into one f-string rather than emitting separate if/else assignments. The resulting literal is unterminated.

**Impact:** `TokenError: EOF in multi-line string` at line 4998. Everything after this point in the file is unreachable.

---

### Issue 6 — `yield` in non-generator functions (FIXED)
**Track A | Priority: P0-A** *(Historical context)*

`LIST_APPEND`, `SET_ADD`, and `MAP_ADD` handlers used to unconditionally emit `yield val`. In Python 3.12 these opcodes appear inline in ordinary methods, causing silent semantic corruption.

**Current Status:** FIXED. The handlers now correctly distinguish between inline comprehensions (in non-generator functions) and actual generator code objects. The `CO_GENERATOR` flag check was removed from the non-comprehension path, allowing regular Functions with inlined comprehensions to use `.append()` / `.add()` / `[]=` correctly.

**Historical Effect on a method containing `[x for x in items]`:**
```python
# Original method (regular function)
def greet(name):
    items = [x.upper() for x in name.split()]
    return ", ".join(items)

# Decompiled output (PRE-FIX: converted to generator)
def greet(name):
    for x in []:
        yield x.upper()
    # items = ...
```

---

### Issue 7 — Stray string literals as statements (15 sites)
**Track B | Priority: P1**

Opcode name strings and bracket characters appear as bare expression-statements in method bodies.

**Decompiled output (inside `_prescan_try_structure`):**
```python
            ins.opname
            'PUSH_EXC_INFO'
            ins.offset
            e.target
            real_push_exc
            e.end
            self._finally_merge_offsets
```

Another site in `post_process_source`:
```python
        finally:
            prefix
            inner
            ')'
```

**Root cause:** Stack-expression sub-results that were assembled as arguments to a `set.add()` or `dict[key] =` call are flushed as bare statements when the consuming instruction fails to reconstruct. The inline-comprehension teardown (`SWAP` + `STORE_FAST` to restore saved variables) also contributes residual values.

**Impact:** Dead code at 15 sites. All are `SyntaxError`-adjacent when string literals have call syntax (`'='(v_str)`, `', '.join(items)('}')`).

---

## 🔴 Category 2: Silent Semantic Corruption

Code compiles but produces wrong results at runtime.

---

### Issue 8 — All `elif` chains collapsed to `if` + `return None` (85 `elif` lost, +207 phantom `return None`)
**Track A | Priority: P0-B** *(Python 3.12 pattern)*

Every `elif` branch whose body does not contain an explicit `return` is emitted as a standalone `if` block terminated by `return None`. The 120 `elif` statements in the source are reduced to 35.

**Source:**
```python
def _op_unknown(self, instr):
    opname = instr.opname
    if "BINARY" in opname and opname != "BINARY_SUBSCR":
        self._op_binary(instr)
    elif "INPLACE" in opname:
        self._op_inplace(instr)
    elif "CALL" in opname and opname not in ("CALL_INTRINSIC_1", "CALL_INTRINSIC_2"):
        self._op_call(instr)
    elif "COMPARE_OP" in opname:
        self._op_compare(instr)
    # ... 6 more elif branches
```

**Decompiled output:**
```python
def _op_unknown(self, instr):
    opname = instr.opname
    if 'BINARY' in opname != 'BINARY_SUBSCR':   # ← also a chained-comparison corruption
        self._op_binary(instr)
        return None                              # ← implicit elif exit emitted as return
    if 'INPLACE' in opname:
        self._op_inplace(instr)
        return None
    if 'CALL' in opname not in ('CALL_INTRINSIC_1', 'CALL_INTRINSIC_2'):
        self._op_call(instr)
        return None
    if 'COMPARE_OP' in opname:
        self._op_compare(instr)
        return None
    # ...
```

**Root cause:** Python 3.12 eliminates `JUMP_FORWARD` at the end of `elif` branches when the body has no explicit return. Each branch instead terminates with `RETURN_CONST(None)`. `is_compiler_generated_return` correctly suppresses the final trailing `return None` but incorrectly treats `RETURN_CONST(None)` inside a block as explicit (because the `"if"` block type is in `user_blocks`).

**Fix:** In `_op_return_const`, suppress emission when `instr.starts_line is None` (the instruction does not start a new source line) AND the following instruction is not also `RETURN_CONST(None)`. Instructions that start a new line were explicitly typed by the user; those that don't are compiler-synthesised branch exits.

**Impact:** All multi-branch dispatch methods (`_op_unknown`, `_op_call`, `_op_jump`, `_op_conditional_jump`, `_op_nop`, `Decompiler39._handle_instruction`) fire only their first matching branch and return. All subsequent branches are unreachable.

---

### Issue 9 — `_build_dispatch` table reduced to 1 entry from 102
**Track A | Priority: P0-A** *(comprehension/MAP_ADD bug)*

The entire opcode dispatch dictionary — the central routing table that maps opcode names to handler methods — is destroyed by the `MAP_ADD → yield` issue.

**Source:**
```python
self._dispatch = {
    "LOAD_CONST": self._op_load, "LOAD_NAME": self._op_load, ...,
    # 102 entries total
}
```

**Decompiled output:**
```python
def _build_dispatch(self):
    yield 'LOAD_CONST': self._op_load    # MAP_ADD → yield
    yield 'LOAD_NAME': self._op_load
    # ... 100 more yield statements
    self._dispatch = {**{**{**{**{**{**{}, **{}}, **{}}, **{}}, **{}}, **{}}, **{'NOP': self._op_nop}}
```

**Root cause:** `BUILD_MAP(0)` + repeated `(LOAD_CONST key, LOAD_ATTR method, MAP_ADD 1)` is the bytecode pattern for both a large dict literal with non-constant values and an inline dict comprehension. The `_op_map_add` handler emits `yield` unconditionally, converting every key–value pair into a yield statement. The final `DICT_UPDATE` for a small fragment is the only entry that survives.

**Impact:** `self._dispatch` contains only `{'NOP': self._op_nop}`. All 101 other opcodes fall through to `_op_unknown`, which itself has the `elif`→`if+return None` bug. The decompiler produces empty or garbage output for every Python 3.11–3.13 `.pyc` file.

---

### Issue 10 — `_OPCODES_39` dictionary reduced to 1 entry from 155
**Track A | Priority: P0-A** *(same root cause as Issue 9)*

The opcode-name lookup table for Python 3.9 bytecode is destroyed identically.

**Source:**
```python
_OPCODES_39: Dict[int, str] = {
    1: "POP_TOP", 2: "ROT_TWO", 3: "ROT_THREE", ...,
    # 155 entries
}
```

**Decompiled output:**
```python
_OPCODES_39 = {**{**{**{**{**{**{**{}, **{}}, **{}}, **{}}, **{}}, **{}}, **{}}, **{165: 'DICT_UPDATE'}}
```

**Impact:** `_OPCODES_39.get(opcode, f"OP_{opcode}")` returns `f"OP_{opcode}"` for all 154 missing opcodes. The entire `Decompiler39` disassembly path emits placeholder names like `OP_1`, `OP_90`, `OP_100` for every instruction.

---

### Issue 11 — All `super()` method calls corrupted (FIXED)
**Track B | Priority: B1** *(Historical context)*

`super().method(args)` calls were incorrectly reconstructed as `self(super().method, args)`.

**Current Status:** FIXED. Added specialized `_op_load_super_attr` handler and updated `_op_call` to correctly handle the stack layout of `LOAD_SUPER_ATTR` (including NULL sentinels). Reconstructions now correctly yield `super().method(args)`.

**Historical Decompiled output:**
```python
class Decompiler39(DecompilerGeneric):
    def __init__(self, code_obj, indent_level=0, beautification_level='core'):
        self(super().__init__, code_obj, indent_level, beautification_level)
```

---

### Issue 12 — `_get_python_version_from_magic` always returns `'3.4'`
**Track B | Priority: B2**

All 12 chained comparisons `A <= version_id <= B` are broken into nested `if A <= version_id: if B:`. Since `B` is a non-zero integer literal, `if B:` is always `True`, so every `version_id >= 3310` matches the first branch.

**Source:**
```python
def _get_python_version_from_magic(version_id):
    if 3310 <= version_id <= 3349: return "3.4"
    if 3350 <= version_id <= 3378: return "3.5"
    if 3379 <= version_id <= 3393: return "3.6"
    if 3394 <= version_id <= 3412: return "3.7"
    if 3413 <= version_id <= 3413: return "3.8"
    if 3420 <= version_id <= 3429: return "3.9"
    if 3430 <= version_id <= 3449: return "3.10"
    if 3450 <= version_id <= 3494: return "3.11"
    if 3495 <= version_id <= 3530: return "3.12"
    if 3531 <= version_id <= 3559: return "3.13"
    if version_id >= 3560:         return "3.14+"
    return None
```

**Decompiled output:**
```python
def _get_python_version_from_magic(version_id):
    if 3310 <= version_id:
        if 3349:           # always True (non-zero int)
            return '3.4'   # ← all versions >= 3310 return here
    if 3350 <= version_id:
        if 3378:
            return '3.5'   # unreachable
    # ...
```

**Root cause:** The `COPY 2` + `SWAP 2` pre-sequence that distinguishes a chained comparison from an `and`-shortcircuit is not detected. The decompiler emits `if A <= x:` for the first operand and `if B:` for the second, discarding the middle `x` entirely.

**Impact:** All `.pyc` files from Python 3.9–3.14 are identified as Python 3.4. Version-specific dispatch in `get_decompiler` uses this function, so all version routing is wrong.

---

### Issue 13 — `_is_anonymous_func_body` returns a generator object instead of `bool`
**Track B | Priority: B (already partially fixed as any() wrapping)**

The function wrapping was partially addressed in v2 (`any()` fix), but `_is_anonymous_func_body` itself still returns a generator expression rather than a `bool`.

**Source:**
```python
def _is_anonymous_func_body(first_line: str) -> bool:
    _ANON_TOKENS = ("<genexpr>", "<lambda>", ...)
    return any(tok in first_line for tok in _ANON_TOKENS)
```

**Decompiled output (v1):**
```python
def _is_anonymous_func_body(first_line):
    _ANON_TOKENS = ('<genexpr>', '<lambda>', ...)
    return (tok in first_line for tok in _ANON_TOKENS)   # generator, always truthy
```

**Fixed in v2:**
```python
    return any((tok in first_line for tok in _ANON_TOKENS))  # ✅
```

**Impact (v1):** Generator objects are always truthy in Python. Every function body was classified as anonymous, so decorators were never emitted for any named function. Fixed in v2.

---

### Issue 14 — `@dataclass` field annotations emitted as `__annotations__` mutations
**Track B | Priority: P3-A**

Dataclass field annotations are reconstructed as explicit dictionary writes instead of clean PEP-526 field syntax.

**Source:**
```python
@dataclass
class BytecodeInstruction:
    opcode: int
    opname: str
    arg: Optional[int]
    argval: Any
    offset: int
    starts_line: Optional[int]
    is_jump_target: bool
    argrepr: str = ""
```

**Decompiled output:**
```python
@dataclass
class BytecodeInstruction:
    __annotations__['opcode'] = int
    __annotations__['opname'] = str
    __annotations__['arg'] = Optional[int]
    __annotations__['argval'] = Any
    __annotations__['offset'] = int
    __annotations__['starts_line'] = Optional[int]
    __annotations__['is_jump_target'] = bool
    argrepr = ''
    __annotations__['argrepr'] = str
```

**Root cause:** `SETUP_ANNOTATIONS` + `STORE_ANNOTATION` opcodes are not handled as a compound "field declaration" pattern. Each annotation is emitted as a subscript assignment to `__annotations__`.

**Impact:** The `@dataclass` decorator does not recognise `__annotations__['x'] = T` as field declarations. `BytecodeInstruction.__init__`, `__repr__`, `__eq__` are not generated. Every constructor call fails.

---

### Issue 15 — Lambda body: both ternary branches identical
**Track B | ✅ Fixed in v2**

**Source:**
```python
expr = f"lambda {params}: {ret_expr}" if params else f"lambda: {ret_expr}"
```

**v1 decompiled output:**
```python
expr = ret_expr if params else ret_expr   # both branches identical, lambda prefix lost
```

**v2 decompiled output:**
```python
expr = f"lambda {params}: {ret_expr}" if params else f"lambda: {ret_expr}"  # ✅
```

---

### Issue 16 — `cond_str` assignments: `raw_expr` interpolation stripped
**Track B | ✅ Fixed in v2**

**Source:**
```python
cond_str = f"{raw_expr} is None" if is_or_jump else f"{raw_expr} is not None"
cond_str = f"{raw_expr} is not None" if is_or_jump else f"{raw_expr} is None"
cond_str = raw_expr if is_or_jump else f"not {raw_expr}"
cond_str = f"not {raw_expr}" if is_or_jump else raw_expr
```

**v1 decompiled output:**
```python
cond_str = ' is None' if is_or_jump else ' is not None'    # bare strings, no variable
cond_str = ' is not None' if is_or_jump else ' is None'
cond_str = raw_expr if is_or_jump else raw_expr            # both branches identical
cond_str = raw_expr if is_or_jump else raw_expr
```

**v2 decompiled output:** All four lines correct. ✅

---

### Issue 17 — Integer dict keys stringified
**Track B | ✅ Fixed in v2**

**Source:**
```python
_AUG_ASSIGN_MAP = {13: "+=", 14: "&=", 15: "//=", ...}
```

**v1 decompiled output:**
```python
_AUG_ASSIGN_MAP = {'13': '+=', '14': '&=', '15': '//=', ...}
```

**v2 decompiled output:**
```python
_AUG_ASSIGN_MAP = {13: '+=', 14: '&=', 15: '//=', ...}  # ✅
```

**Impact (v1):** `_AUG_ASSIGN_MAP.get(int(lt.arg))` always missed because keys were strings. All augmented-assignment detection returned `None`.

---

### Issue 18 — F-string with inner `str.join` generator mis-parenthesised
**Track B | ✅ Fixed in v2**

**Source:**
```python
return f"({expr})({', '.join(str(a) for a in args)})"
```

**v1 decompiled output:**
```python
return f"{expr})(, '.join{(str(a) for a in args)})"   # completely broken
```

**v2 decompiled output:**
```python
return f"({expr})({', '.join((str(a) for a in args))})"  # ✅ (extra parens around genexpr)
```

---

### Issue 19 — `any()` call stripped to bare generator expression
**Track B | ✅ Fixed in v2**

**Source:**
```python
if any(name in lines[0] for name in _comp_names):
```

**v1 decompiled output:**
```python
if (name in lines[0] for name in _comp_names):   # generator always truthy
```

**v2 decompiled output:**
```python
if any((name in lines[0] for name in _comp_names)):  # ✅
```

**Impact (v1):** The comprehension type detection branch was always entered regardless of function body content, breaking `_render_func_tuple` for all non-comprehension functions.

---

## 🟠 Category 3: Logic and Control Flow Defects

Code compiles but control flow is structurally wrong.

---

### Issue 20 — `_prescan_while_loops` marks ALL instruction offsets as while-body (whole-function suppression)
**Track B | Priority: P1-B**

The backward-jump guard `if not self._is_backward_instruction(jb): continue` is absent. Every instruction is processed as a potential while-loop back-edge, and every offset is added to `_while_body_offsets`.

**Source:**
```python
def _prescan_while_loops(self):
    self._while_header_targets = {}
    self._while_true_ends = set()
    for jb in self.instructions:
        if not self._is_backward_instruction(jb):   # ← guard
            continue
        body_start = self._get_jump_target(jb)
        dup_start = jb.offset
        for ins in self.instructions:
            if body_start <= ins.offset < jb.offset:
                if ins.opname in ("STORE_NAME", ...):
                    dup_start = ins.offset + 2
        # ...
        for ins in self.instructions:
            if dup_start <= ins.offset < jb.offset:
                self._while_body_offsets.add(ins.offset)
```

**Decompiled output:**
```python
def _prescan_while_loops(self):
    self._while_header_targets = {}
    self._while_true_ends = set()
    for jb in self.instructions:          # ← guard missing, all instructions processed
        body_start = self._get_jump_target(jb)
        dup_start = jb.offset
        for ins in self.instructions:
            dup_start = ins.offset + 2    # ← no condition, always overwrites with last
        # ...
        for ins in self.instructions:
            self._while_body_offsets.add(ins.offset)   # ← adds ALL offsets
```

**Impact:** `_handle_instruction` checks `if instr.offset in self._while_body_offsets: return` at the top. With all offsets in the set, every instruction in every function is suppressed and returns immediately. The decompiler produces empty output for all functions.

---

### Issue 21 — Dict membership test converted to full dict iteration (2 sites)
**Track B | Priority: B3**

`if key in dict:` is reconstructed as `for k, v in dict.items():` — iterating all entries instead of checking membership.

**Source:**
```python
if body_start in self._while_header_targets:
    return   # prescan handled it
```

**Decompiled output:**
```python
for bs, go in self._while_header_targets.items():
    if compound_precomputed:
        self._append_reconstructed(f"while {cond}:")
    # ...
    return None
```

**Root cause:** `CONTAINS_OP` on a dict (for the `in` check) is conflated with iteration over `dict.items()`. The loop variables `bs, go` come from a surrounding tuple-unpack that was incorrectly associated with this branch.

**Impact:** The while-loop block classification exits after the first key in the map regardless of whether `body_start` matches. All while loops after the first are mis-classified as `if` blocks.

---

### Issue 22 — `continue` statements near-total loss (83 of 85 missing)
**Track B | Priority: P1-C**

Only 2 of 85 `continue` statements survive in the decompiled output. All loop-body early-exit guards are silently absent.

**Source:**
```python
for jb in self.instructions:
    if not self._is_backward_instruction(jb):
        continue
    if jb.offset in getattr(self, "_exc_handler_jump_offsets", set()):
        continue
    body_start = self._get_jump_target(jb)
```

**Decompiled output:**
```python
for jb in self.instructions:
    if not self._is_backward_instruction(jb):
        pass        # continue replaced with pass
    if jb.offset in getattr(self, '_exc_handler_jump_offsets', set()):
        pass        # continue replaced with pass
    body_start = self._get_jump_target(jb)
```

**Root cause:** `JUMP_BACKWARD` inside a loop body whose target is the `FOR_ITER` offset of the enclosing loop should emit `continue`. The decompiler currently classifies all `JUMP_BACKWARD` as loop back-edges (emitting nothing or `while`) rather than checking whether the target is the current loop's header.

**Impact:** All tight inner loops that use `continue` to skip non-instruction opcodes (`CACHE`, `RESUME`, etc.) process those opcodes as real instructions, corrupting decompilation output. The `_prescan_*` methods are worst affected.

---

### Issue 23 — Spurious `while` instead of `if` (29 excess `while` statements)
**Track B | Priority: P1-B**

Single-execution conditional blocks are reconstructed as `while` loops. The decompiled output has 102 `while` statements versus 73 in the original.

**Source:**
```python
if not aug_op and "INPLACE_" in lt.opname and lt.opname == le.opname:
    aug_op = _INPLACE_ASSIGN_MAP.get(lt.opname)
if aug_op:
    actual_then_expr.pop()
    else_instrs.pop()
```

**Decompiled output:**
```python
while not aug_op and 'INPLACE_' in lt.opname == le.opname:
    aug_op = _INPLACE_ASSIGN_MAP.get(lt.opname)
while aug_op:
    actual_then_expr.pop()
    else_instrs.pop()
```

**Root cause:** `JUMP_BACKWARD` inside a conditional branch is misidentified as a loop back-edge when its target offset happens to be above the current PC. The fix requires checking whether the backward target is the recorded header of an enclosing `FOR_ITER` / `while` block; if not, it is a dead branch fall-through, not a loop.

**Impact:** `while not aug_op:` with a body that doesn't modify `aug_op` loops infinitely when the condition is true. `while aug_op:` is similarly infinite. Both affect augmented-assignment detection in `_prescan_ternaries`.

---

### Issue 24 — Spurious `for/else` clauses (35 excess `else:` on `for` loops)
**Track B | Priority: medium**

The original source has zero `for/else` constructs. The decompiled output generates 132 spurious `else:` clauses on `for` loops.

**Source:**
```python
if balanced and depth == 0:
    try:
        node = ast.parse(inside, mode='eval')
        if not isinstance(node.body, ast.Tuple):
            inner = inside
    except Exception:
        ...
```

**Decompiled output (fragment):**
```python
for char in inside:
    if char == '(':
        depth += 1
    elif char == ')':
        depth -= 1
    balanced = False
    break
else:                           # ← spurious for/else
    if balanced and depth == 0:
        try:
            ...
```

**Root cause:** Post-loop code that should execute unconditionally is placed inside `else:` because the `JUMP_FORWARD` that skips it when the loop `break`s is misread as a for-else boundary.

**Impact:** Any code inside a spurious `else:` is skipped whenever the loop exits via `break`, producing wrong results for all the balance-checking and pattern-matching loops.

---

### Issue 25 — `main()` try/with nesting completely inverted
**Track B | Priority: high**

The `try`/`with`/`if`/`except` nesting in `main()` is structurally wrong: the `else` branch, the second `try`, and the `except OSError` are all nested inside the `with` block; the outer `except ValueError` and `except Exception` are missing entirely.

**Source:**
```python
try:
    decompiler = get_decompiler(args.input, ...)
    output_text = decompiler.decompile()
    if args.output:
        try:
            with open(args.output, "w", encoding="utf-8") as f:
                f.write(output_text)
            print(f"Decompiled output saved to {args.output}", file=sys.stderr)
        except OSError as e:
            print(f"Error: ...", file=sys.stderr)
            sys.exit(1)
    else:
        if output_text and output_text.strip():
            print(output_text)
        ...
except ValueError as e:
    print(f"Error: {e}", file=sys.stderr)
    sys.exit(1)
except Exception:
    traceback.print_exc()
    sys.exit(1)
```

**Decompiled output:**
```python
try:
    decompiler = get_decompiler(args.input, ...)
    output_text = decompiler.decompile()
    if args.output:
        try:
            with open(args.output, 'w', encoding='utf-8') as f:
                f.write(output_text)
                print(f"Decompiled output saved to {args.output}", file=sys.stderr)
                return None
                if output_text and output_text.strip():  # ← unreachable, inside with
                    print(output_text)
                    return None
                # ...
                except OSError as e:       # ← inside with block, SyntaxError
                    ...
```

**Impact:** Unhandled `ValueError` from `get_decompiler` (e.g. bad magic number) crashes with no error message. `OSError` from file writing is never caught. The `else` branch that prints to stdout is unreachable.

---

### Issue 26 — `nonlocal` declaration missing from `flush_imports` closure
**Track B | Priority: P3-B**

`flush_imports` assigns to `current_imports`, `current_froms`, and `current_indent` — all captured from the enclosing `post_process_source` scope — but the `nonlocal` declaration is absent.

**Source:**
```python
def flush_imports():
    nonlocal current_imports, current_froms, current_indent
    if current_imports:
        ...
        current_imports.clear()
    ...
    current_indent = None
```

**Decompiled output:**
```python
def flush_imports():
    # nonlocal declaration missing
    while current_imports:
        ...
        current_imports.clear()
    ...
    current_indent = None   # assigns to LOCAL variable, not outer scope
```

**Impact:** `flush_imports` creates fresh local variables that shadow the outer ones. Calling `flush_imports()` never clears the outer import state, causing every call to `post_process_source` to emit duplicated import lines in the beautified output.

---

### Issue 27 — `self.offset += n` missing from `MarshalParser._read`
**Track B | Priority: P1**

After reading `n` bytes, the file cursor advance is dropped. The parser re-reads the same bytes on every call.

**Source:**
```python
def _read(self, n: int) -> bytes:
    if self.offset + n > len(self.data):
        raise EOFError(...)
    res = self.data[self.offset: self.offset + n]
    self.offset += n    # ← missing in decompiled
    return res
```

**Decompiled output:**
```python
def _read(self, n):
    if (self.offset + n) > len(self.data):
        raise EOFError(...)
    res = self.data[self.offset:(self.offset + n)]
    return res          # self.offset never advances
```

**Impact:** `MarshalParser` always reads from offset 0. Every `_read_byte()`, `_read_long()`, `_load_code()` call returns the same initial bytes. Parsing any `.pyc` file produces garbage code objects.

---

### Issue 28 — `res += digit * (2**(15 * i))` missing from `_read_long`
**Track B | Priority: P1**

The accumulator update for multi-digit long integers is absent.

**Source:**
```python
res = 0
for i in range(n_digits):
    digit = struct.unpack("<H", self._read(2))[0]
    res += digit * (2**(15 * i))   # ← missing
return -res if size < 0 else res
```

**Decompiled output:**
```python
res = 0
for i in range(n_digits):
    digit = struct.unpack('<H', self._read(2))[0]
    # res += digit * ... is absent
else:
    if size < 0:
        return res    # always returns 0
    return res
```

**Impact:** All long integers in marshal streams return 0. Large integer constants in `.pyc` files are silently replaced with 0.

---

### Issue 29 — 44 `self.indent_level` / `self.pc` mutations missing
**Track B | Priority: P1**

44 augmented assignment statements mutating `self.indent_level` and 3 mutating `self.pc` are absent from the decompiled output (145 in original vs 101 in decompiled).

**Sample missing mutations:**
```python
self.indent_level += 1   # after emitting "if cond:"
self.indent_level -= 1   # after closing block
self.pc += 1             # skip next instruction
self.indent_level += extra_indent
look += 1                # skip the gate
```

**Root cause:** These are either consumed by the chained aug-assign corruption (Issue 1) or silently dropped when the `INPLACE_ADD`/`INPLACE_SUBTRACT` result is left on the virtual stack and the following `POP_TOP` is mishandled.

**Impact:** Indentation tracking drifts throughout decompilation. Every `if`/`for`/`while`/`try` block opened without a corresponding indent increment produces output at the wrong depth for all subsequent lines.

---

### Issue 30 — `next(genexpr, default)` filter conditions stripped
**Track B | Priority: medium**

Generator expression `if`-clauses inside `next()` calls are removed, causing `next()` to always return the first element instead of the first matching element.

**Source:**
```python
target_idx = next(
    (i for i, ins in enumerate(self.instructions) if ins.offset == jump_target),
    -1
)
```

**Decompiled output:**
```python
target_idx = next((i for i, x in enumerate(self.instructions)), -1)
# always returns 0 regardless of jump_target
```

**Root cause:** The `if` clause inside a generator expression passed to `next()` is part of a `POP_JUMP_IF_FALSE` inside the genexpr's code object. When the genexpr is inlined (Python 3.12), the filter is indistinguishable from the outer conditional and is consumed by the `elif`→`if+return None` transformation.

**Impact:** All five `next(genexpr if cond)` calls in `Decompiler39._handle_instruction` use index 0. Jump targets always resolve to the first instruction, breaking all backward-jump and forward-jump resolution in the 3.9 decompilation path.

---

## 🟡 Category 4: Structural and Readability Issues

---

### Issue 31 — All 57 comprehensions destroyed (25 list, 9 set, 3 dict)
**Track A | Priority: P0-A** *(same root cause as Issue 6, different manifestation)*

Beyond the `yield`-emission problem, the comprehension variable itself is assigned the last loop iteration value rather than the built collection.

**Source:**
```python
offset_to_idx = {ins.offset: i for i, ins in enumerate(self.instructions)}
then_sig = [x for x in then_raw if x.opname not in _TERNARY_SKIP]
_try_covered_starts = {e.start for e in _exc_entries}
```

**Decompiled output:**
```python
for i, ins in {}:              # dict comp → iterates empty dict
    yield ins.offset: i
offset_to_idx = i              # ← last loop variable, not the dict

for x in []:                   # list comp → iterates empty list
    yield x
then_sig = x                   # ← last loop variable

for e in set():                # set comp → iterates empty set
    yield e.start
_try_covered_starts = _exc_entries  # ← the iterable, not the result
```

**Impact:** `offset_to_idx`, `then_sig`, `store_idxs`, `push_exc_offs`, `loop_heads`, and 52 other variables that should hold collections hold either a scalar or the wrong object. Lookups and iterations on these variables produce `TypeError` or `AttributeError` at runtime.

---

### Issue 32 — `flatten_elif` and `post_process_source` appear 2-line to function extractors
**Track B | Priority: medium**

`lines = source.split("\n")` is reconstructed as `lines = source.split("""\n""")` — a triple-quoted literal spanning two physical lines — making any tool that extracts functions by scanning for the next non-indented line report both functions as having only 2 lines.

**Source:**
```python
lines = source.split("\n")
```

**Decompiled output:**
```python
lines = source.split("""
""")
```

**Root cause:** The `"\n"` escape sequence is stored as a single-character string constant. When reconstructed, the newline character is materialised inside a triple-quoted delimiter rather than represented as the `\n` escape.

---

### Issue 33 — List literals reconstructed as `[*(tuple)]` (4 sites)
**Track B | Priority: low**

List literals are reconstructed using tuple-spread syntax.

**Source:**
```python
ops = [' <= ', ' >= ', ' < ', ' > ', ' == ', ' != ', ' in ', ' not in ', ' is ', ' is not ']
choices=['none', 'core', 'aggressive']
```

**Decompiled output:**
```python
ops = [*(' <= ', ' >= ', ' < ', ' > ', ' == ', ' != ', ' in ', ' not in ', ' is ', ' is not ')]
choices=[*('none', 'core', 'aggressive')]
```

**Root cause:** `BUILD_LIST` + `LIST_EXTEND` with a tuple constant produces `[*tuple]` rather than recognising the pattern as a plain list literal.

**Impact:** Semantically equivalent at runtime but misleading and diverges from source style.

---

### Issue 34 — All type annotations stripped from function signatures (46 functions)
**Track B | Expected / known limitation**

Every annotated function signature loses its type annotations and return type.

**Source:**
```python
def _prescan_try_structure(self) -> None:
def _read(self, n: int) -> bytes:
def get_decompiler(filepath: str, beautification_level: str = 'core') -> DecompilerBase:
```

**Decompiled output:**
```python
def _prescan_try_structure(self):
def _read(self, n):
def get_decompiler(filepath, beautification_level='core'):
```

**Root cause:** Annotations are stored in `__annotations__` at runtime but are not embedded in bytecode as executable instructions. The decompiler has no mechanism to recover them.

---

### Issue 35 — All docstrings stripped (101 triple-quoted strings lost)
**Track B | Expected / known limitation**

Every function and class docstring is absent from the decompiled output.

**Source:**
```python
def _prescan_try_structure(self) -> None:
    """
    Pre-scan the function's bytecode and classify exception-handling ...
    Populates the following attributes:
        _try_nop_offsets (set[int]): ...
    """
```

**Decompiled output:**
```python
def _prescan_try_structure(self):
    # docstring absent
```

**Root cause:** Docstrings compile to `LOAD_CONST` + `POP_TOP`. The decompiler correctly identifies the initial `LOAD_CONST` of a string as a docstring candidate, but the `has_doc` flag is not being set in all cases, and even when it is, the string is consumed silently rather than emitted.

---

### Issue 36 — 1,134 comments dropped
**Track B | Expected / known limitation**

All inline comments, section headers (`# --- Magic Numbers ---`), and `# type: ignore` pragmas are absent. Comment count improved from 290 (v1) to 423 (v2), but 715 remain missing.

---

### Issue 37 — 530 blank lines dropped
**Track B | Expected / known limitation**

Logical grouping within functions is lost, reducing readability.

---

### Issue 38 — Multi-line `frozenset`/`tuple` literals collapsed to single lines
**Track B | Low priority**

All `_TERNARY_PURE`, `_COMPOUND_EXPR_OPS`, `_TRIPLE_PREFIXES` constants are emitted as single very long lines.

**Source:**
```python
_TERNARY_PURE = frozenset((
    "LOAD_FAST", "LOAD_NAME", "LOAD_GLOBAL", "LOAD_CONST",
    "LOAD_DEREF",
    # ... 40+ entries, multi-line
)) | _TERNARY_SKIP | _TERNARY_TERM
```

**Decompiled output:**
```python
_TERNARY_PURE = ((frozenset(('LOAD_FAST', 'LOAD_NAME', 'LOAD_GLOBAL', 'LOAD_CONST', 'LOAD_DEREF', ... 40+ entries on one line ...)) | _TERNARY_SKIP) | _TERNARY_TERM)
```

---

### Issue 39 — String delimiter normalised to single quotes everywhere
**Track B | Cosmetic**

All `"…"` string literals are emitted as `'…'`. Diverges from original style but has no runtime impact.

---

## Summary Tables

### Issues by Severity

| Rank | ID | Severity | Category | Fix Track |
|------|----|----------|----------|-----------|
| 1 | 6 | 🔴 | Yield in non-generator (145 sites) | A |
| 2 | 8 | 🔴 | elif→if+return None (85 lost) | A |
| 3 | 9 | 🔴 | _build_dispatch: 1/102 entries | A |
| 4 | 11 | 🔴 | super() calls corrupted (12 sites) | B |
| 5 | 1 | 🔴 | Chained aug-assign SyntaxError (37) | B |
| 6 | 10 | 🔴 | _OPCODES_39: 1/155 entries | A |
| 7 | 12 | 🔴 | _get_python_version always '3.4' | B |
| 8 | 20 | 🟠 | _prescan_while_loops all-offset suppression | B |
| 9 | 31 | 🟠 | All 57 comprehensions destroyed | A |
| 10 | 14 | 🟠 | @dataclass field annotations broken | B |
| 11 | 4 | 🔴 | Bare `?` in conditions (8 sites) | B |
| 12 | 3 | 🔴 | Orphaned except block | B |
| 13 | 2 | 🔴 | -1.offset SyntaxError (2 sites) | B |
| 14 | 5 | 🔴 | Unterminated f-string | B |
| 15 | 22 | 🟠 | continue loss (83/85 missing) | B |
| 16 | 29 | 🟠 | 44 indent_level/pc mutations missing | B |
| 17 | 27 | 🟠 | self.offset += n missing | B |
| 18 | 28 | 🟠 | res += digit missing from _read_long | B |
| 19 | 21 | 🟠 | Dict membership → iteration (2 sites) | B |
| 20 | 23 | 🟠 | while instead of if (29 excess) | B |
| 21 | 24 | 🟠 | Spurious for/else (35 excess) | B |
| 22 | 25 | 🟠 | main() nesting inverted | B |
| 23 | 26 | 🟠 | nonlocal missing from flush_imports | B |
| 24 | 30 | 🟠 | next() filter conditions stripped | B |
| 25 | 7 | 🟡 | Stray string literals (15 sites) | B |
| 26 | 32 | 🟡 | split("\n") → triple-quoted | B |
| 27 | 33 | 🟡 | [*(tuple)] anti-pattern (4 sites) | B |
| 28 | 34 | 🟡 | Type annotations stripped (46 fns) | expected |
| 29 | 35 | 🟡 | Docstrings stripped (101) | expected |
| 30 | 36 | 🟡 | Comments dropped (715 missing) | expected |
| 31 | 37 | 🟡 | Blank lines dropped (530) | expected |
| 32 | 38 | 🟡 | Multi-line literals collapsed | B |
| 33 | 39 | 🟡 | String delimiter normalisation | cosmetic |

### Fixed in v2

| ID | Issue | Status |
|----|-------|--------|
| 13 | `_is_anonymous_func_body` generator vs bool | ✅ Fixed |
| 15 | Lambda ternary branches identical | ✅ Fixed |
| 16 | `cond_str` bare-string assignment | ✅ Fixed |
| 17 | Integer dict keys stringified | ✅ Fixed |
| 18 | F-string `str.join` mis-parenthesised | ✅ Fixed |
| 19 | `any()` stripped to generator | ✅ Fixed |

### Prioritised Fix Roadmap

**Track A — Python 3.12 source compatibility:**

| Priority | What to fix | Expected gain |
|----------|------------|---------------|
| A1 | `_op_list_append`/`_op_set_add`/`_op_map_add`: detect `BUILD_LIST/SET/MAP(0)` on stack, accumulate into expression | Restores all 57 comprehensions + all large dict literals (Issues 6, 9, 10, 31) |
| A2 | Add `LOAD_FAST_AND_CLEAR` handler: push saved variable, mark inline-comp start | Fixes variable restore after comprehension teardown |
| A3 | `is_compiler_generated_return`: suppress `RETURN_CONST(None)` when `instr.starts_line is None` and successor is real code | Restores all 85 `elif` branches, removes 207 phantom `return None` (Issue 8) |
| A4 | Disambiguate `and`-shortcircuit (`COPY 1`+`POP_TOP`) from chained comparison (`SWAP 2`+`COPY 2`) | Fixes `and` compound conditions (Issues 4, 12) |

**Track B — Decompilation reconstruction fixes:**

| Priority | What to fix | Expected gain |
|----------|------------|---------------|
| B1 | Detect `LOAD_SUPER_ATTR` and emit `super().method(args)` | Fixes all 12 super() calls, unblocks 3 subclasses (Issue 11) |
| B2 | Detect `SWAP 2`+`COPY 2` before `COMPARE_OP` as chained comparison | Fixes all 12 chained comparisons, fixes `_get_python_version` (Issues 4, 12) |
| B3 | Discard `INPLACE_*` result with `POP_TOP` rather than leaving on stack | Fixes 37 chained aug-assign SyntaxErrors (Issue 1) |
| B4 | `JUMP_BACKWARD` to current `FOR_ITER` head → emit `continue`, not loop back | Restores 83 missing `continue` statements (Issue 22) |
| B5 | Only emit `while` if backward target is a recorded loop header in `self.blocks` | Removes 29 spurious `while` (Issue 23) |
| B6 | `CONTAINS_OP` on dict → emit `k in d`, not iteration | Fixes 2 membership checks (Issue 21) |
| B7 | Emit `(instrs[-1]).offset` with parens for unary-minus subscript access | Fixes 2 `-1.offset` SyntaxErrors (Issue 2) |
| B8 | `SETUP_ANNOTATIONS` + `STORE_ANNOTATION` → emit `x: T` field syntax | Fixes @dataclass fields (Issue 14) |
| B9 | `STORE_DEREF` in enclosing cell scope → emit `nonlocal varname` at function entry | Fixes missing `nonlocal` (Issue 26) |

---

## 🔴 Category 5: Subclass-Specific Decompilation Failures

These issues are specific to `Decompiler39`, `Decompiler311Plus`, and `Decompiler314` — the version-specialised subclasses.

---

### Issue 40 — `Decompiler314._handle_instruction`: `__exit__` epilogue suppression completely broken
**Track B | Priority: high**

The Python 3.14-specific `with`-statement handler loses its `__exit__` epilogue suppression block and its `STORE_FAST_STORE_FAST` combined-store handler. The entire logic is collapsed into a deeply mis-nested structure.

**Source (key section):**
```python
# Normal-path __exit__ cleanup suppression
if getattr(self, "_with_exit_suppress", set()) and instr.offset in getattr(self, "_with_exit_suppress"):
    return

# Python 3.14 with preamble: COPY 1 + LOAD_SPECIAL(__exit__) + SWAP 2 + SWAP 3 + LOAD_SPECIAL(__enter__) + CALL 0
if opname == "COPY" and instr.arg == 1:
    if self.pc + 4 < len(self.instructions):
        seq = self.instructions[self.pc: self.pc + 5]
        if (seq[0].opcode == 95 and seq[0].arg == 1 and ...):
            ctx_mgr = ...
            # find exception table boundaries
            for e in entries:
                if e.depth == 1:
                    try_end = e.target
                    normal_ends = {x.end for x in entries if x.depth == 0 ...}
                    ...
            self.blocks.append((try_end, 'with_body'))
            # scan for __exit__ epilogue offsets to suppress
            exit_start = next((i for i, ins in enumerate(self.instructions)
                               if ins.offset >= exit_body_start), None)
            ...
```

**Decompiled output (collapsed fragment):**
```python
if opname == 'COPY' and instr.arg == 1:
    if (self.pc + 4) < len(self.instructions):
        ...
        try:
            ...
            for e in entries:
                handler_target = e.target
                break                        # ← exits after first entry, not searching
            else:
                except (AttributeError, ...):   # ← except inside else: SyntaxError
                    pass
                if handler_target is not None:
                    for e in []:             # ← destroyed set comprehension
                        yield e.end
                    else:
                        normal_ends = e      # ← last loop var, not the set
                        ...
                        finally:             # ← orphaned finally
                            try_end('with_body')   # ← try_end called as function!
                            ...
                            ('code', <code object <genexpr> at 0x...>)(enumerate(self.instructions)())
```

**Impact:** Python 3.14 `with` statements are not decompiled. The `__exit__` call suppression set is never populated, so `__exit__` epilogue instructions are emitted as raw code. The `STORE_FAST_STORE_FAST` handler that handles 3.14 combined stores is also unreachable inside the broken nesting, so all 3.14 tuple-unpack assignments produce garbage.

---

### Issue 41 — `Decompiler39._handle_instruction` loses 9 `elif opname ==` dispatch branches
**Track A | Priority: P0-B** *(elif collapse, same root cause as Issue 8)*

The 881-line Python 3.9 opcode dispatcher has its entire `elif` chain collapsed. The `_while_true_body_starts` set is derived from a destroyed set comprehension.

**Source:**
```python
_while_true_body_starts = {
    b for b in (self._while_true_ends or set())
}
if instr.offset in _while_true_body_starts:
    self._append_reconstructed("while True:")
    self.indent_level += 1
    ...

if opname in ("POP_JUMP_IF_FALSE", "POP_JUMP_IF_TRUE"):
    jump_target = self._get_jump_target(instr)
    ...
elif opname == "JUMP_ABSOLUTE":
    ...
elif opname == "FOR_ITER":
    ...
# 6 more elif branches
```

**Decompiled output:**
```python
for bs, go in set():      # ← destroyed set comprehension, iterates nothing
    yield bs
_while_true_body_starts = bs, go    # ← tuple of uninitialized variables

if not 'POP_JUMP_IF_FALSE' in opname:    # ← negated, wrong semantics
    if 'POP_JUMP_IF_TRUE' in opname:
        ...
        target_idx = next((i for i, x in enumerate(self.instructions)), -1)
        # ← filter `if ins.offset == jump_target` stripped, always returns 0
        ...
        return None
if 'JUMP_ABSOLUTE' in opname:    # ← elif → if
    ...
    return None
```

**Impact:** `_while_true_body_starts` is always `(undefined_bs, undefined_go)` — a tuple from uninitialized variables, causing `NameError` on the first `while True:` check. All 9 opcode dispatch branches execute independently and return after the first match. `target_idx` always returns 0, so all jump resolutions point to the first instruction.

---

### Issue 42 — `_load_code`: Python 3.11+ code object silently discarded (no `return`)
**Track B | Priority: high**

The `if vi >= (3, 11):` condition and its `return` keyword are both lost. The `types.CodeType(...)` call for Python 3.11+ bytecode is executed but its result is not returned.

**Source:**
```python
if vi >= (3, 11):
    return types.CodeType(
        argcount, posonlyargcount, kwonlyargcount, nlocals,
        stacksize, flags, code, consts, names, varnames,
        filename, name, name,        # qualname
        firstlineno, lnotab, exceptiontable,
        freevars, cellvars
    )
elif vi >= (3, 8):
    return types.CodeType(...)
else:
    return types.CodeType(...)
```

**Decompiled output:**
```python
            vi              # ← bare statement (condition lost)
            (3, 11)         # ← bare statement (comparand lost)
            types.CodeType(argcount, posonlyargcount, ..., exceptiontable, freevars, cellvars)
            # ← no return keyword — result is discarded
        if vi >= (3, 8):    # ← elif → if
            return types.CodeType(argcount, posonlyargcount, ..., freevars, cellvars)
        return types.CodeType(argcount, kwonlyargcount, ..., freevars, cellvars)
```

**Impact:** For all Python 3.11–3.13 `.pyc` files, `_load_code` falls through to the `if vi >= (3, 8):` branch and constructs a code object *without* `exceptiontable` (Python 3.11+ requires it). This raises `TypeError: CodeType() takes exactly N arguments (M given)` on every 3.11+ `.pyc` parse, making the `MarshalParser` completely non-functional for modern bytecode.

---

### Issue 43 — Raw `('code', <code object ...>)` tuple reprs in live expressions (8 sites)
**Track B | Priority: medium**

Unrendered internal decompiler stack sentinels appear as expression operands in the decompiled output.

**Decompiled output (fragments):**
```python
pei_idx = ('code', <code object <genexpr> at 0x7edf7f390030, ...>)(enumerate(instrs)(), None)

_get_sentinel = ('code', <code object _get_sentinel at 0x7edf7f3947b0, ...>)

to_tuple_strings = ('code', <code object to_tuple_strings at 0x...>)
```

**Root cause:** When `MAKE_FUNCTION` creates a code object and `CALL` is never reached (because the handler returns early due to the `elif`→`if+return None` bug), the `('code', co)` tuple on the stack is flushed as a bare statement or used as the callable in a subsequent `CALL` instruction.

**Impact:** At runtime, `('code', <code_obj>)(args)` raises `TypeError: 'tuple' object is not callable`. All nested function and comprehension calls that involve code objects are silently broken.

---

## 🟡 Category 6: Additional Structural Issues

---

### Issue 44 — `_block_opener_keyword`: `return` missing from single-line header path
**Track B | Priority: medium**

The single-line header detection path computes the keyword but doesn't return it.

**Source:**
```python
for kw in _KW:
    if tail_stripped.startswith(kw):
        return kw.rstrip(" :")
```

**Decompiled output:**
```python
for kw in _KW:
    kw.rstrip(' :')    # ← return keyword missing, result discarded
```

**Impact:** `_block_opener_keyword` always falls through to the multi-line bracket-counting path and returns `None` for all single-line headers. The beautifier's `elif`-collapse (`flatten_elif`) cannot identify block openers, silently producing no collapsing for any `if`/`elif` chains.

---

### Issue 45 — `_prescan_try_structure` bloated to 638 lines vs 457 original
**Track B | Priority: medium**

The method is 40% longer than the original due to for/else proliferation, bare-name statement dumps (85 excess), and the spurious `while` constructs.

**Key sub-issues within `_prescan_try_structure`:**
- `_try_covered_starts = {e.start for e in _exc_entries}` → `for e in set(): yield e.start` / `_try_covered_starts = _exc_entries`
- `push_exc_offs = {ins.offset for ins in instrs if ins.opname == "PUSH_EXC_INFO"}` → `for ins in []: yield ins.offset` / `push_exc_offs = ins`
- 85 bare variable names (`ins.opname`, `merge`, `self._push_exc_to_finally_merge`, etc.) emitted as statements
- The `for merge_off, targets in self._push_exc_to_finally_merge.items():` loop body replaced by a column of bare expressions

**Impact:** `_try_covered_starts`, `push_exc_offs`, and all other set/dict results in this method are wrong scalars. The entire try/except structure pre-scanning produces incorrect results, leading to wrong `try:`/`except:`/`finally:` reconstruction for all code.

---

### Issue 46 — `_prescan_compound_conds` loses 3 set/dict comprehensions
**Track A | Priority: P0-A** *(same root cause as Issue 6)*

**Source (three affected constructs):**
```python
jump_starts = {get_logical_t(g[3]) for g in group}   # set comp
_try_covered_starts = {e.start for e in _exc_entries} # set comp  
off2idx = {ins.offset: i for i, ins in enumerate(instrs)}  # dict comp
```

**Decompiled output:**
```python
for g in set():
    yield get_logical_t(g[3])
jump_starts = g                 # ← wrong

for e in set():
    yield e.start
_try_covered_starts = _exc_entries   # ← the iterable

for i, ins in {}:
    yield ins.offset: i
off2idx = i                     # ← wrong
```

**Impact:** `jump_starts`, `_try_covered_starts`, and `off2idx` hold the wrong values. All compound-condition merging logic produces incorrect or empty results, breaking `and`/`or` chain reconstruction.

---

### Issue 47 — `post_process_source` missing `collapse_chained_comparisons` and lambda cleanup calls
**Track B | Priority: medium**

Two post-processing steps present in the original are absent from the decompiled version's main processing loop.

**Source (present in original):**
```python
if beautification_level in ('core', 'aggressive'):
    text = collapse_chained_comparisons(text, ...)
    # ... lambda/genexpr cleanup regex passes
    text = re.sub(
        r"([ \t]*)([A-Za-z_]...)=\s*\('(func|class)',...",
        lambda m: (f"{m.group(1)}# <{'genexpr/lambda'...}"),
        text, flags=re.MULTILINE | re.DOTALL
    )
```

**Decompiled output:** `collapse_chained_comparisons` call and the three lambda-cleanup `re.sub` passes are absent from `post_process_source`'s body (though the regex patterns are reconstructed at module level).

**Impact:** Chained comparison folding never runs. Lambda/genexpr tuple leakage (`x = ('func', ...)`) in output is never cleaned up, leaving raw decompiler artefacts in the final source.

---

## Updated Numeric Summary

| Metric | Original | v1 Dec | v2 Dec | Trend |
|--------|----------|--------|--------|-------|
| Output lines | 6,880 | 4,769 | 5,083 | ↑ better |
| Distinct SyntaxError root causes | 0 | 2 | 3+ | ↓ worse |
| Chained aug-assign SyntaxError | 0 | 25 | 37 | ↓ worse |
| List comprehensions | 25 | 0 | 0 | ─ same |
| Set comprehensions | 9 | 1 | 1 | ─ same |
| Dict comprehensions | 3 | 0 | 0 | ─ same |
| Spurious `yield` stmts | 0 | 146 | 145 | ↑ slightly better |
| `elif` statements | 120 | 34 | 35 | ↑ slightly better |
| Phantom `return None` | 8 | 216 | 215 | ↑ slightly better |
| `continue` statements | 85 | 2 | 2 | ─ same |
| `while` count (73 expected) | 73 | 96 | 102 | ↓ worse |
| Chained comparisons | 12 | 0 | 0 | ─ same |
| `super()` calls correct | 14 | 2 | 2 | ─ same |
| Stray string literals | 1 | 9 | 15 | ↓ worse |
| Raw `('code',...)` reprs | 0 | 0 | 8 | ↓ new regression |
| Comments preserved | 1,138 | 290 | 423 | ↑ better |
| Type annotations | 46 | 0 | 0 | ─ same |
| `any()` wrapping genexpr | 0 | 0 | 17 | ✅ fixed |
| Lambda ternary | 1 | 0 | 1 | ✅ fixed |
| Integer dict keys | int | str | int | ✅ fixed |
| `cond_str` interpolation | correct | broken | correct | ✅ fixed |

---

## Complete Fix Specification

### Track A — Python 3.12 Source Compatibility Fixes

#### A1: Inline comprehension detection in `_op_list_append` / `_op_set_add` / `_op_map_add`

These three handlers currently emit `yield` unconditionally. The fix requires detecting whether the current context is an inline comprehension frame.

**Current broken handlers:**
```python
def _op_list_append(self, instr):
    val = self.stack.pop() if self.stack else "None"
    self._append_reconstructed(f"yield {val}")   # WRONG

def _op_map_add(self, instr):
    val = self.stack.pop() if self.stack else "None"
    key = self.stack.pop() if self.stack else "None"
    self._append_reconstructed(f"yield {key}: {val}")   # WRONG
```

**Required fix approach:** Add an `_inline_comp_stack` list to `DecompilerGeneric.__init__`. When `BUILD_LIST(0)` / `BUILD_SET(0)` / `BUILD_MAP(0)` is encountered immediately following a `LOAD_FAST_AND_CLEAR` + `SWAP`, push a frame onto `_inline_comp_stack`. In `_op_list_append` etc., check `self._inline_comp_stack`: if populated, accumulate the value into the current comp frame instead of emitting `yield`. When `END_FOR` is reached and a comp frame is active, pop the frame and push the completed expression string onto `self.stack`.

#### A2: `RETURN_CONST(None)` implicit elif exit suppression

**Fix for `_op_return_const`:**
```python
def _op_return_const(self, instr):
    if instr.argval is None:
        # Suppress if this is an implicit elif branch exit:
        # - The instruction does not start a new source line (starts_line is None)
        # - A successor instruction exists and is not itself RETURN_CONST(None)
        idx = self.pc - 1
        starts_new_line = (instr.starts_line is not None)
        if not starts_new_line and idx + 1 < len(self.instructions):
            next_i = self.instructions[idx + 1]
            next_is_implicit_return = (
                next_i.opname == 'RETURN_CONST'
                and next_i.argval is None
                and next_i.starts_line is not None  # final trailing return starts a line
            )
            if not next_is_implicit_return:
                return   # suppress: this is an implicit elif exit
        # ... existing logic for explicit return None and break
```

#### A3: `LOAD_FAST_AND_CLEAR` handler

Add to `_build_dispatch`:
```python
"LOAD_FAST_AND_CLEAR": self._op_load_fast_and_clear,
```

New handler:
```python
def _op_load_fast_and_clear(self, instr):
    # Push the variable's current value (same as LOAD_FAST)
    self.stack.append(instr.argval)
    # Record that this variable needs restoring after END_FOR
    if not hasattr(self, '_saved_comp_vars'):
        self._saved_comp_vars = []
    self._saved_comp_vars.append(instr.argval)
```

#### A4: `and`-shortcircuit vs chained comparison disambiguation

In the compound-condition builder, before emitting a chained comparison, check the instruction sequence:
- If `COPY 1` + `POP_JUMP_IF_FALSE` + `POP_TOP` precedes the second operand → emit `A and B`
- If `SWAP 2` + `COPY 2` precedes `CONTAINS_OP` → emit `A in B` as part of a chained comparison

---

### Track B — Decompilation Reconstruction Fixes

#### B1: `super()` call reconstruction

Detect the `LOAD_SUPER_ATTR` opcode (available in Python 3.12+) which pushes both `super()` and the looked-up bound method. When encountered:
- Do not treat the two stack values as callable + first argument
- Instead emit `super().{attr_name}(...)` directly

For older Python versions using `LOAD_GLOBAL super` + `CALL 0` + `LOAD_ATTR method` pattern, detect the sequence and emit the same form.

#### B2: Chained comparison reconstruction

Detect the `SWAP 2` + `COPY 2` sequence that precedes `CONTAINS_OP` or `COMPARE_OP` in a chained comparison. When this prefix is present, emit `A op B op C` rather than `A op B and B op C`.

#### B3: Augmented assignment result discarding

After `INPLACE_ADD` / `INPLACE_SUBTRACT` etc., the result is left on the stack. The next instruction is always `STORE_FAST`/`STORE_NAME`/`STORE_ATTR` (the assignment itself) or `POP_TOP`. The decompiler must not treat the `INPLACE_*` result as an object with attributes to store into. If the next instruction is `STORE_*`, emit `var op= val`; if it is `POP_TOP`, pop and discard.

#### B4: `continue` detection

`JUMP_BACKWARD` whose target offset equals the `FOR_ITER` offset of the innermost active `for` loop (recorded in `self.blocks`) should emit `continue`. Currently all `JUMP_BACKWARD` are treated as loop back-edges or while-loop guards.

#### B5: `while` vs `if` disambiguation

Only emit a `while` header when the backward jump target is recorded in `self._while_header_targets`. If not, the branch is a conditional `if` (possibly mis-identified as a loop), not a loop back-edge.

#### B6: `CONTAINS_OP` on dict → membership, not iteration

When `CONTAINS_OP` is applied to a value that was produced by loading a `dict`-type variable, emit `k in d`. Do not conflate with `for k, v in d.items()`.

#### B7: Parenthesize numeric literal attribute access and negative subscripts

Attribute access on negative numeric literals (integers or floats) or complex subscript expressions requires parentheses to avoid SyntaxError:
- `(-1).offset`
- `(-1).method()`
- `(instrs[-1]).offset`

*   **Fix:** Implementations in `_op_load_attr` and `_op_call` now explicitly check for negative numeric literals and wrap them. Subscript-parenthesization is handled by ensuring expressions containing unary operators are wrapped before attribute access.

#### B8: `SETUP_ANNOTATIONS` + `STORE_ANNOTATION` → dataclass field

When inside a `@dataclass` class body, detect the sequence `SETUP_ANNOTATIONS` / `LOAD_CONST type` / `STORE_ANNOTATION name` and emit `name: type` (or `name: type = default` when followed by a default value assignment).

#### B9: `STORE_DEREF` in closure → `nonlocal` declaration

When a function contains `STORE_DEREF` for a cell variable that was defined in an enclosing scope (i.e., it appears in `co_freevars` of the current code object, not in `co_cellvars`), emit `nonlocal varname` at the start of the function body.
