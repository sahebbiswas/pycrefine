import functools
def traced(fn):
    calls = []
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        calls.append(args)
        return fn(*args, **kwargs)
    wrapper.calls = calls
    return wrapper
@traced
def add(a, b=2):
    return a + b
print(add(1), add(1, b=5), add.__name__, add.calls)
RESULT = add(3)
