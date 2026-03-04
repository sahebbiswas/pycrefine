from pycrefine import MarshalParser
import types
import traceback
import sys

def main():
    filepath = sys.argv[1]
    with open(filepath, "rb") as f:
        data = f.read()
    
    # Python 3.9 pyc has 16-byte header
    parser = MarshalParser(data[16:])
    try:
        obj = parser.load()
        print(f"Successfully loaded: {type(obj)}")
        if isinstance(obj, types.CodeType):
            print(f"Code name: {obj.co_name}")
    except Exception:
        traceback.print_exc()

if __name__ == "__main__":
    main()
