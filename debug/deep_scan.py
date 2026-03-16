import marshal
import types
import io

def deep_scan(filepath):
    with open(filepath, "rb") as f:
        data = f.read()
    
    print(f"Deep scanning {filepath} ({len(data)} bytes)...")
    successes = []
    for i in range(len(data)):
        f_in = io.BytesIO(data[i:])
        try:
            # Try to load and check if it's a code object
            # We don't use try/except block here to capture the EXACT error later
            obj = marshal.load(f_in)
            if isinstance(obj, types.CodeType):
                print(f"SUCCESS at offset {i}: CodeType '{obj.co_name}'")
                successes.append(i)
            # else:
            #    print(f"Found {type(obj)} at {i}")
        except EOFError:
            pass # Very common
        except Exception as e:
            # print(f"Error at {i}: {e}")
            pass
    
    if not successes:
        print("No CodeType objects found at any offset.")
    return successes

if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        deep_scan(sys.argv[1])
