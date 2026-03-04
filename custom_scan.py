from pycrefine import MarshalParser
import types
import sys

def debug_scan(filepath):
    with open(filepath, "rb") as f:
        data = f.read()
    
    print(f"Scanning {filepath} with Custom MarshalParser...")
    for i in range(len(data)):
        try:
            parser = MarshalParser(data[i:])
            obj = parser.load()
            if isinstance(obj, types.CodeType):
                print(f"SUCCESS at offset {i}: CodeType '{obj.co_name}'")
        except Exception as e:
            # print(f"Error at {i}: {e}")
            continue

if __name__ == "__main__":
    debug_scan(sys.argv[1])
