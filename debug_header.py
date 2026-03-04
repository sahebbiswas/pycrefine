import struct
import sys

def inspect_header(filepath):
    with open(filepath, "rb") as f:
        header = f.read(16)
        magic = struct.unpack("<I", header[:4])[0]
        bit_field = struct.unpack("<I", header[4:8])[0]
        
        print(f"File: {filepath}")
        print(f"Magic: {hex(magic)}")
        print(f"Bit Field: {bin(bit_field)}")
        print(f"Header Bytes (Hex): {header.hex(' ')}")
        
        # Try to find the start of the marshal data (should start with a type code)
        remaining = f.read(16)
        print(f"Next 16 Bytes: {remaining.hex(' ')}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python debug_header.py <file.pyc>")
    else:
        inspect_header(sys.argv[1])
