import marshal
import sys

def scan_all(filepath):
    with open(filepath, "rb") as f:
        data = f.read()
    
    print(f"Scanning {filepath} ({len(data)} bytes)...")
    for i in range(len(data)):
        try:
            obj = marshal.loads(data[i:])
            print(f"Offset {i:03}: {type(obj).__name__} -> {repr(obj)[:50]}")
        except:
            continue

if __name__ == "__main__":
    scan_all(sys.argv[1])
