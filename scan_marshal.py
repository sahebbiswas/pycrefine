import marshal
import sys
import types
import io

def find_marshal_start(filepath):
    with open(filepath, "rb") as f:
        data = f.read()
    
    print(f"Total file size: {len(data)} bytes")
    # Try different offsets to find which one allows marshal.load
    for i in range(0, min(100, len(data))):
        f_in = io.BytesIO(data[i:])
        try:
            # We use loads with no flags to be as generic as possible
            code = marshal.load(f_in)
            if isinstance(code, types.CodeType):
                print(f"CodeType SUCCESS at offset: {i}")
                print(f"  Name: {code.co_name}")
                return i
            else:
                # print(f"  Found {type(code)} at offset {i}")
                pass
        except:
            continue
    print("Failed to find valid marshal CodeType object")
    return None

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python scan_marshal.py <file.pyc>")
    else:
        find_marshal_start(sys.argv[1])
