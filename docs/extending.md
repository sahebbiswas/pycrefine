# Extending pycrefine

How to add an opcode handler, add a Python version, and track down a bad decompile. Read
[Architecture](architecture.md) first for the passes and the state they share, and
[CONTRIBUTING.md](../CONTRIBUTING.md) for the checks every pull request runs.

## Add an opcode handler

1. **Find the opcode's shape.** Compile a minimal example and read it under each Python
   that has the opcode:

   ```bash
   python3.12 -c "import dis; dis.dis('x = a if b else c')"
   ```

2. **Write the handler.** A handler is a method `_op_<what it does>(self, instr)` on
   `DecompilerGeneric` (`decompiler/generic.py`), or on the backend class
   (`py39.py`, `py311.py`, `py314.py`) when only that version has the opcode. Handlers
   are named for the work they do, not the opcode, because one handler often serves
   several opcodes (`_op_binary` serves every binary operator). It pops expressions off
   `self.stack` (they are source strings), pushes the result or emits a line with
   `self._append_reconstructed(...)`, and must leave the stack as the opcode would.
   For a made-up two-operand opcode `FOO_BAR` that produces `left @@ right`:

   ```python
   def _op_foo_bar(self, instr: BytecodeInstruction):
       right = self.stack.pop()
       left = self.stack.pop()
       self.stack.append(f"({left} @@ {right})")
   ```

3. **Register it** in `_BASE_DISPATCH`, a module-level dict in `generic.py` from opcode
   name (the string in `dis.opname`) to the handler's method *name*, as a string, not a
   bound method or a function. Add one entry per opcode the handler serves:

   ```python
   "FOO_BAR": "_op_foo_bar",
   ```

   When only one backend has the opcode, or a backend handles it differently, declare it
   in that class's `DISPATCH_OVERRIDES` instead, a class attribute of the same shape.
   Declare it in the class body; the table is built when the class is created, so
   changing either dict at run time has no effect:

   ```python
   class Decompiler311Plus(DecompilerGeneric):
       DISPATCH_OVERRIDES = {"FOO_BAR": "_op_foo_bar"}
   ```

   Opcodes with no effect on the source map to `_op_no_op` (`_op_nop` is the handler for `NOP`, which can mark where a `try` or `while True` begins). An opcode with no entry falls
   to `_op_unknown`, which guesses from the name; a guess is a bug waiting for a
   version that names things differently, so register what you add.
4. **Add a test** in the `tests/` file for the feature area, as a minimal source that
   uses the construct. Use `decompile(src)` from `tests/test_helpers.py` and assert that
   the output has the same behavior (run both) or the same AST. Give a bug fix a test
   that failed before it.
5. **Run the checks**: the tests under every Python the opcode exists in, and
   `python quality/check_correctness.py` plus `python quality/check_differential.py`
   (see [Correctness](correctness.md)). If a result improved, record the baselines on
   every CI Python and remove any `tests/known_failures.json` entry or corpus
   `limitations` item that the change fixes.

`tests/test_dispatch.py` fails when a dispatch entry names a method that does not exist or an
opcode no supported Python has (`test_every_entry_names_a_handler_method` and
`test_registered_names_are_real_opcodes`), so a typo in step 3 shows up as a test failure on
every version. A name is accepted when the running interpreter's `dis.opmap` has it, or when
it is listed in one of two sets at the top of that file:

- `OPCODES_OF_OTHER_VERSIONS`: a real CPython opcode that the interpreter running the tests
  may not have, because it exists only in another supported version (or is a specialised
  form). Add the opcode here when you register one.
- `SYNTHETIC_OPCODES`: an instruction the decompiler builds itself (opcode -1), such as
  `INLINED_COMPREHENSION`.

Check the spelling against `dis.opmap` of the Python the opcode comes from before adding it:
the list exists to allow real names, so a typo added to it would silence the test.

## Add a Python version

Support for a new CPython has these parts, in this order:

1. **Magic numbers.** `versions.py` maps the header's magic number to a version, and
   `loader.get_decompiler` maps the version to a backend. A version that is not in both
   is an error.
2. **A backend.** Start from the closest one: `Decompiler311Plus` covers 3.10–3.12 and
   `Decompiler314` covers 3.13–3.14. Copy only what differs. Opcodes that were renamed
   or fused (the 3.13 `LOAD_FAST_LOAD_FAST` family, 3.14's `LOAD_SPECIAL`) are handled by
   `_split_superinstructions`, `_COMPOUND_EXPR_OPS` in `_constants.py`, and the backend.
3. **Bytecode that other versions cannot read.** 3.10 and later are decoded by the host's
   `dis`, so the new version can only be decompiled under itself until
   [#94](https://github.com/sahebbiswas/pycrefine/issues/94) lands.
4. **CI.** A `ci-py3X.yml` workflow that calls `reusable-test.yml`, with read-only token
   permissions like the others.
5. **Baselines.** `python quality/check_correctness.py --update-baseline` and
   `python quality/check_differential.py --update-baseline` under the new version, each
   gap it exposes filed as an issue and listed in `tests/known_failures.json` (failing
   tests) and in `quality/corpus/manifest.json` (`limitations`).
6. **Docs.** The supported-versions tables in the [README](../README.md) and
   [Limitations](limitations.md), and the version lists in CONTRIBUTING.md.

## Debug a bad decompile

1. **Reduce it.** Cut the source down to the smallest program that still comes out
   wrong, and check `python -m dis` for the version in question.
2. **Look at what pycrefine did.** `python -m pycrefine file.pyc` prints the source. For
   a file that is wrong in a way the gate can see, `python quality/check_correctness.py
   -v --json --no-baseline path/to/file.py` shows the status of each unit and the error.
   `python quality/check_differential.py NAME -v` runs one corpus entry and prints the
   decompiled source next to what differed in behavior.
3. **Find the pass.** The prescans decide structure and the handlers decide text; a wrong
   `if`/`else`/`try` is nearly always a prescan, a wrong expression a handler. The
   [order of the passes](architecture.md#the-order-of-the-passes) and the
   [state table](architecture.md#decompiler-state) say what reads what. Printing the
   suppress sets after `decompile()` runs usually shows which offset was skipped or
   not.
4. **Fix it with a test.** Add the reduced program as a test (and, if it is a shape the
   corpus lacks, as a corpus entry).
5. **Update baselines only for real improvements.** `--update-baseline` records what the
   code does now. Run it when results got better and review the diff; never to make a
   regression disappear.
