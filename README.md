# pycrefine 🐍

A Python `.pyc` decompiler built to reconstruct source code from compiled bytecode. Designed with an extensible architecture to support multiple Python versions.

## Features

- **Multi-version Support**: 
  - **Python 3.9**: Full initial support for 3.9 opcodes.
  - **Python 3.11+**: Support for new opcodes like `BINARY_OP` and updated magic numbers (verified on 3.13).
- **Extensible Architecture**: Structured with version-specific decompiler classes to easily add support for future versions.
- **Smart Reconstruction**: Rebuilds variable assignments, expressions, and function calls from the operand stack.
- **PEP 552 Support**: Correctly parses both timestamp-based and hash-based `.pyc` files.

## Usage

To decompile a `.pyc` file, run the script with the path to the file as an argument:

```powershell
python pycrefine.py path/to/compiled_file.pyc
```

### Example

Input (`test_simple.py` compiled to `.pyc`):
```python
x = 10
y = 20
result = x + y
print(result)
```

Decompiled Output:
```python
x = 10
y = 20
result = (x + y)
print(result)
```

## How it Works

1. **Loader**: Parses the `.pyc` header (magic number, bit fields, etc.) and uses a **custom Marshal parser** to deserialize the code object, ensuring compatibility across different Python versions.
2. **Dispatcher**: Selects the appropriate version-specific decompiler based on the magic number.
3. **Bytecode Walker**: Iterates through instructions and maintains an operand stack to reconstruct high-level Python expressions.
