# Support matrix

What pycrefine does with the bytecode of each Python version, read from the version table in
`src/pycrefine/versions.py` and the files the quality gates keep. `tests/test_support_matrix.py`
keeps this page in step with them: it fails when a row changes, and
`python3 tests/test_support_matrix.py --write` rewrites the table.

The support levels, each including the one before it:

- **recognized**: the magic number names the version, so an error can say which Python wrote the file. No source is rebuilt.
- **reconstructable**: a backend exists and rebuilds source.
- **tested**: a backend exists, CI runs the test suite on that Python, and the correctness and differential baselines are recorded for it.

Whether the bytecode can be *parsed* under a host of another minor version is a separate
fact (the "Reads on another host" column): only 3.9 bytecode has a decoder of its own so far
([#94](https://github.com/sahebbiswas/pycrefine/issues/94)).

<!-- support-matrix:start -->
| Python | Support | Backend | Reads on another host | CI workflow | Correctness baseline | Differential baseline |
| --- | --- | --- | --- | --- | --- | --- |
| 3.14 | tested | `Decompiler314` | no | yes | yes | yes |
| 3.13 | tested | `Decompiler314` | no | yes | yes | yes |
| 3.12 | tested | `Decompiler311Plus` | no | yes | yes | yes |
| 3.11 | tested | `Decompiler311Plus` | no | yes | yes | yes |
| 3.10 | tested | `Decompiler311Plus` | no | yes | yes | yes |
| 3.9 | tested | `Decompiler39` | yes | yes | yes | yes |
| 3.8 | recognized | - | no | no | no | no |
| 3.7 | recognized | - | no | no | no | no |
| 3.6 | recognized | - | no | no | no | no |
| 3.5 | recognized | - | no | no | no | no |
| 3.4 | recognized | - | no | no | no | no |
<!-- support-matrix:end -->
